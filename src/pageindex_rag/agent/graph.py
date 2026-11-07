"""LangGraph loop over a library: the model picks a navigation tool, we run it, repeat until it replies in plain text."""

import logging
from collections.abc import Iterator
from typing import Annotated, Literal, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool, tool
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

from ..config import Settings, get_settings
from ..models import DocumentIndex
from ..providers import LLMConfig
from .citations import extract_markers, visible_prefix
from .navigator import LibraryNavigator, Step

log = logging.getLogger(__name__)

EMPTY_REPLY_RETRIES = 2  # extra tries when the model answers with nothing at all
REFUSED_TEXT = "The model declined to answer this request. Try rephrasing your question."
# How each provider says a reply was blocked: Anthropic's stop_reason, OpenAI's and Gemini's finish_reason.
_REFUSAL_REASONS = {"refusal", "content_filter", "SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION"}

SYSTEM_PROMPT = """You answer questions about a library of documents by navigating each document's table of contents.

Documents in the library:
{documents}

How to work:
- {start}
- Before each tool call, say in one short sentence what you are about to do and why.
- Use list_children to look inside a section, going into the subsection whose summary best matches the question. \
Tools take the ids shown in square brackets in listings. Indented lines in a listing are deeper subsections (titles \
only): you can read_node or list_children any id you have seen, without stepping through each level.
- A summary marked (preview) is just the first sentences of the section, not a summary of all of it.
- Use read_node to read a section's text. Sections with subsections usually have little text of their own.
- After reading, decide whether the text answers the question. If it does not, use go_up and try another branch, \
or open another document. Avoid sections marked visited unless you have a reason to return.
- When you can answer, reply in plain text without calling a tool. After each claim, put its source in square \
brackets in the qualified form shown at the top of each section you read, like [doc_id:1.2]. Cite only sections you \
read with read_node. If the user asked for a picture, add the matching image id as [img:doc_id:img-1].
- If the library does not contain the answer, say so in plain text and put no bracketed ids in the reply.
- Earlier messages are conversation context. For a new factual question, navigate and read; do not answer from \
memory of earlier turns.
Answer only from what you read. You have at most {max_steps} steps."""

START_SINGLE = 'The document is already open. Start with list_children("root").'
START_MANY = (
    "No document is open yet. Open the document whose summary best matches the question with open_document, "
    'then use list_children("root").'
)


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class Citation(BaseModel):
    doc_id: str
    doc_title: str
    node_id: str
    title: str
    location: str


class ImageRef(BaseModel):
    doc_id: str
    image_id: str
    file: str  # relative to the document's data folder
    description: str = ""


class AnswerResult(BaseModel):
    found: bool  # at least one valid citation
    text: str
    # The text was written by the app, not the model (refusal notice, step budget, empty reply).
    # Such replies are left out of the history sent with later questions; real replies are kept, cited or not.
    error: bool = False
    citations: list[Citation] = Field(default_factory=list)
    images: list[ImageRef] = Field(default_factory=list)


class Draft(BaseModel):
    """The reply so far, while the model is still writing it (markers removed). Each one replaces the last."""

    text: str


class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    steps: int
    result: AnswerResult | None


def make_tools(navigator: LibraryNavigator) -> list[BaseTool]:
    """The navigation tools the model can call. Each one runs on the navigator."""

    @tool
    def list_documents() -> str:
        """List the documents in the library with their summaries."""
        return navigator.list_documents()

    @tool
    def open_document(doc_id: str) -> str:
        """Open a document and list its top-level sections."""
        return navigator.open_document(doc_id)

    @tool
    def list_children(node_id: str) -> str:
        """List the subsections of a node in the open document, with summaries. Use "root" for the top level."""
        return navigator.list_children(node_id)

    @tool
    def read_node(node_id: str) -> str:
        """Read the text of a node in the open document, plus descriptions of any images in it."""
        return navigator.read_node(node_id)

    @tool
    def go_up(node_id: str) -> str:
        """List the siblings of a node (the subsections of its parent), to try another branch."""
        return navigator.go_up(node_id)

    return [list_documents, open_document, list_children, read_node, go_up]


def build_graph(navigator: LibraryNavigator, llm: BaseChatModel, max_steps: int):
    tools = make_tools(navigator)
    by_name = {t.name: t for t in tools}
    model = llm.bind_tools(tools)
    system = SystemMessage(
        SYSTEM_PROMPT.format(
            documents=navigator.documents_listing(),
            start=START_SINGLE if navigator.current else START_MANY,
            max_steps=max_steps,
        )
    )

    def call_model(state: AgentState) -> dict:
        write = get_stream_writer()
        for attempt in range(1 + EMPTY_REPLY_RETRIES):
            response = None
            for chunk in model.stream([system, *state["messages"]]):
                response = chunk if response is None else response + chunk
                write({"text": _message_text(response)})  # the reply so far; stream_agent turns it into a Draft
            if response is None:
                response = AIMessage("")
            if _refused(response):
                # A deliberate safety decision, not a glitch: asking again gets the same answer.
                category = (response.response_metadata.get("stop_details") or {}).get("category")
                log.warning("Model refused the request (category=%s)", category)
                break
            if response.tool_calls or _message_text(response).strip():
                break
            # No text and no tool call: ask again instead of ending the run with nothing to show.
            blocks = [b.get("type") for b in response.content if isinstance(b, dict)] if isinstance(response.content, list) else []
            log.warning(
                "Model returned an empty reply (stop_reason=%s, content blocks=%s, unparsed tool calls=%d, attempt %d)",
                response.response_metadata.get("stop_reason"),
                blocks,
                len(response.invalid_tool_calls),
                attempt + 1,
            )
        return {"messages": [response], "steps": state["steps"] + 1}

    def run_tools(state: AgentState) -> dict:
        messages = []
        for call in state["messages"][-1].tool_calls:
            name = call["name"]
            if name not in by_name:
                content = f"Unknown tool {name!r}."
            else:
                try:
                    content = by_name[name].invoke(call["args"])
                except Exception:
                    content = f"Invalid arguments for {name}."
            messages.append(ToolMessage(content, tool_call_id=call["id"]))
        return {"messages": messages}

    def finish(state: AgentState) -> dict:
        last = state["messages"][-1]
        if _refused(last):
            return {"result": AnswerResult(found=False, text=REFUSED_TEXT, error=True)}
        return {"result": _answer_result(navigator, _message_text(last))}

    def give_up(state: AgentState) -> dict:
        return {
            "result": AnswerResult(found=False, text="Stopped: step budget used up before finding an answer.", error=True)
        }

    def after_model(state: AgentState) -> str:
        return "tools" if state["messages"][-1].tool_calls else "finish"

    def after_tools(state: AgentState) -> str:
        return "give_up" if state["steps"] >= max_steps else "agent"

    graph = StateGraph(AgentState)
    graph.add_node("agent", call_model)
    graph.add_node("tools", run_tools)
    graph.add_node("finish", finish)
    graph.add_node("give_up", give_up)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", after_model, ["tools", "finish"])
    graph.add_conditional_edges("tools", after_tools, ["give_up", "agent"])
    graph.add_edge("finish", END)
    graph.add_edge("give_up", END)
    return graph.compile()


def stream_agent(
    navigator: LibraryNavigator,
    llm: BaseChatModel,
    question: str,
    max_steps: int,
    history: list[ChatTurn] | None = None,
) -> Iterator[Step | Draft | AnswerResult]:
    """Yield each navigation Step as it happens, Drafts of the reply as it is written, then the final AnswerResult.

    A Draft that is followed by a Step was only the model thinking aloud before a tool call; callers should discard it.
    """
    graph = build_graph(navigator, llm, max_steps)
    messages = [*_history_messages(history or []), HumanMessage(question)]
    updates = graph.stream(
        {"messages": messages, "steps": 0, "result": None},
        {"recursion_limit": max_steps * 2 + 10},
        stream_mode=["updates", "custom"],
    )
    emitted, result, last_draft, note = 0, None, "", ""
    for mode, update in updates:
        if mode == "custom":
            if (draft := visible_prefix(update["text"])) and draft != last_draft:
                last_draft = draft
                yield Draft(text=draft)
            continue
        last_draft = ""
        for node_name, changes in update.items():
            if node_name == "agent":
                reply = changes["messages"][-1]
                note = extract_markers(_message_text(reply))[0] if reply.tool_calls else ""
            if node_name == "tools":
                for i, step in enumerate(navigator.trace[emitted:]):
                    yield step.model_copy(update={"note": note}) if i == 0 else step  # the note goes with the first
                emitted = len(navigator.trace)
            if isinstance(changes, dict) and changes.get("result") is not None:
                result = changes["result"]
    yield result or AnswerResult(found=False, text="No answer was produced.", error=True)


def run_agent(
    navigator: LibraryNavigator,
    llm: BaseChatModel,
    question: str,
    max_steps: int,
    history: list[ChatTurn] | None = None,
) -> AnswerResult:
    *_, result = stream_agent(navigator, llm, question, max_steps, history)
    return result


def stream_ask(
    indexes: list[DocumentIndex],
    question: str,
    settings: Settings | None = None,
    history: list[ChatTurn] | None = None,
    api_key: str | None = None,
    llm_config: LLMConfig | None = None,
) -> Iterator[Step | AnswerResult]:
    """Answer over a library. `llm_config` is the provider, key and models; without it, settings plus `api_key`."""
    from ..llm import chat_model
    from ..llm import llm_config as config_from_settings

    settings = settings or get_settings()
    config = llm_config or config_from_settings(settings, api_key)
    llm = chat_model(config.agent_model, config.api_key, provider=config.provider)
    navigator = LibraryNavigator(indexes, listing_depth=settings.agent_listing_depth)
    return stream_agent(navigator, llm, question, settings.agent_max_steps, history)


def ask(
    indexes: list[DocumentIndex],
    question: str,
    settings: Settings | None = None,
    history: list[ChatTurn] | None = None,
    api_key: str | None = None,
) -> AnswerResult:
    *_, result = stream_ask(indexes, question, settings, history, api_key)
    return result


def _history_messages(history: list[ChatTurn]) -> list[AnyMessage]:
    return [HumanMessage(turn.content) if turn.role == "user" else AIMessage(turn.content) for turn in history]


def _answer_result(navigator: LibraryNavigator, reply: str) -> AnswerResult:
    """Turn the model's final reply into an answer. Only sections and images it actually read can be cited."""
    text, section_refs, image_refs = extract_markers(reply)
    citations = []
    for ref in section_refs:
        if found := navigator.find_node(ref):
            index, node = found
            if navigator.has_read(index.doc_id, node.id):
                citations.append(
                    Citation(
                        doc_id=index.doc_id,
                        doc_title=index.title,
                        node_id=node.id,
                        title=node.title,
                        location=node.location.label(),
                    )
                )
    images = []
    for ref in image_refs:
        if found := navigator.find_image(ref):
            index, record = found
            if record.node_id and navigator.has_read(index.doc_id, record.node_id):
                images.append(
                    ImageRef(doc_id=index.doc_id, image_id=record.id, file=record.file, description=record.description)
                )
    return AnswerResult(
        found=bool(citations),
        text=text or "The model sent an empty reply. Please ask again.",
        error=not text,
        citations=citations,
        images=images,
    )


def _refused(message: AIMessage) -> bool:
    """True if the provider blocked the reply on safety grounds (a deliberate decision, not a glitch)."""
    meta = message.response_metadata or {}
    reasons = {str(meta.get("stop_reason")), str(meta.get("finish_reason"))}
    return bool(reasons & _REFUSAL_REASONS) or bool((message.additional_kwargs or {}).get("refusal"))


def _message_text(message: AIMessage) -> str:
    if isinstance(message.content, str):
        return message.content
    return "".join(block.get("text", "") for block in message.content if isinstance(block, dict))
