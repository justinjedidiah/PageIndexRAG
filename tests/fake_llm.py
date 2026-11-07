"""An offline stand-in for Claude, for running the app end to end without an API key.

    TOC_RAG_LLM_FACTORY=tests.fake_llm:make_model

It writes summaries from the section text and navigates by keyword overlap with the question.
It is deliberately simple: the point is to exercise the app, not to answer well.
"""

import json
import os
import re
import time
import uuid

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.runnables import RunnableLambda

STOPWORDS = {"the", "a", "an", "of", "is", "are", "what", "who", "how", "do", "does", "and", "to", "in", "for", "me", "my", "i", "show", "from", "was", "on", "at", "often", "should"}
PICTURE_WORDS = {"picture", "image", "photo", "chart", "show"}
OPEN_FAIL_KEY = "bad-key"  # a key containing this is rejected, to exercise the error path


def make_model(model: str) -> BaseChatModel:
    return FakeChat()


def list_models(provider: str, api_key: str) -> list[str]:
    """What the provider would list for a key: two models to choose from. A key containing OPEN_FAIL_KEY is rejected."""
    from langchain_core.exceptions import ModelAuthenticationError

    if OPEN_FAIL_KEY in api_key:
        raise ModelAuthenticationError("rejected")
    return [f"{provider}-fake-large", f"{provider}-fake-small"]


def words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower())) - STOPWORDS


def best(question: str, candidates: list[tuple[str, str]]) -> str:
    """The id whose text shares the most words with the question (the first on a tie)."""
    asked = words(question)
    return max(candidates, key=lambda c: len(asked & words(c[1])))[0]


def tool_call(name: str, **args) -> AIMessage:
    note = f"Next: {name.replace('_', ' ')}."  # like a real model, say a sentence before the call
    return AIMessage(note, tool_calls=[{"name": name, "args": args, "id": f"call-{uuid.uuid4().hex[:12]}"}])


def structured(schema, prompt):
    # Optional slowness for indexing calls, so a test can act while indexing is still running.
    time.sleep(float(os.environ.get("TOC_RAG_FAKE_INDEX_DELAY", "0")))
    if schema.__name__ == "_ImageDescription":
        return schema(description="A bar chart with four colored bars of different heights.", tags=["chart", "bars"])
    title = re.search(r"Section title: (.*)", prompt).group(1)
    text = prompt.split("Section text:\n", 1)[1].split("\n\nSubsections:", 1)[0]
    subsections = prompt.split("Subsections:\n", 1)[1].split("\n\nWrite the summary", 1)[0]
    own = "" if text.startswith("(no text") else text[:300]
    # A parent's summary carries the start of every child, so a question can be routed by what is deeper down.
    children = "" if subsections == "(none)" else " ".join(line[:240] for line in subsections.split("\n"))[:1500]
    return schema(title=title, summary=f"{title}. {re.sub(r'\s+', ' ', f'{own} {children}')}".strip())


def next_message(messages: list) -> AIMessage:
    system = next((m.content for m in messages if isinstance(m, SystemMessage)), "")
    if "navigating each document's table of contents" not in system:
        return AIMessage("ok")  # key check and anything else that is not the agent

    question = next(m.content for m in reversed(messages) if isinstance(m, HumanMessage))
    last_human = max(i for i, m in enumerate(messages) if isinstance(m, HumanMessage))
    results = [m.content for m in messages[last_human + 1 :] if isinstance(m, ToolMessage)]

    if not results:
        if "No document is open yet" in system:
            docs = re.findall(r"^- \[([^\]]+)\] (.*)$", system, re.MULTILINE)
            return tool_call("open_document", doc_id=best(question, docs))
        return tool_call("list_children", node_id="root")

    last = results[-1]
    read = re.match(r"^\[([^\]]+)\] (.+?) \(.*?\)\n\n(.*)", last, re.DOTALL)
    if read and "Subsections of" not in last:
        ref, _, body = read.groups()
        body = body.split("\n\nImages in this section:", 1)[0]
        text = re.sub(r"\[image:[^\]]*\]", "", body).strip()[:400]
        images = re.findall(r"^- ([^:\s]+:img-\d+):", last, re.MULTILINE) if words(question) & PICTURE_WORDS else []
        markers = f"[{ref}]" + "".join(f" [img:{image}]" for image in images)
        return AIMessage(f"{text}\n\n(Answered by the offline test model.) {markers}")

    children = re.findall(r"^- \[([^\]]+)\] (.*?) \((leaf|\d+ subsections)[^)]*\): (.*)$", last, re.MULTILINE)
    # Deeper levels of a multi-level listing: indented, titles only. The agent may jump straight to them.
    children += [(i, t, shape, "") for i, t, shape in re.findall(r"^ +- \[([^\]]+)\] (.*?) \((leaf|\d+ subsections)\)$", last, re.MULTILINE)]
    if not children:
        return AIMessage("I could not find anything relevant in that section.")
    asked = words(question)
    # The best match wins; on a tie a leaf wins (it can be read now), then the earlier line.
    chosen_id = max(children, key=lambda c: (len(asked & words(f"{c[1]} {c[3]}")), c[2] == "leaf"))[0]
    chosen = next(c for c in children if c[0] == chosen_id)
    return tool_call("read_node" if chosen[2] == "leaf" else "list_children", node_id=chosen_id)


class FakeChat(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "fake"

    def with_structured_output(self, schema, **kwargs):
        return RunnableLambda(lambda prompt: structured(schema, prompt))

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=next_message(messages))])

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        """Send the reply a few characters at a time, like a real model, so the UI's live text is exercised."""
        reply = next_message(messages)
        if reply.tool_calls:
            calls = [
                {"name": c["name"], "args": json.dumps(c["args"]), "id": c["id"], "index": i}
                for i, c in enumerate(reply.tool_calls)
            ]
            pieces = [AIMessageChunk(reply.content, tool_call_chunks=calls)]
        else:
            pieces = [AIMessageChunk(reply.content[i : i + 12]) for i in range(0, len(reply.content), 12)]
        for piece in pieces:
            time.sleep(0.02)
            yield ChatGenerationChunk(message=piece)
