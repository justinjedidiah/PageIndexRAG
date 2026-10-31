from langchain_core.language_models import BaseChatModel
import json

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import Field

from pageindex_rag.agent import (
    AnswerResult,
    ChatTurn,
    Draft,
    LibraryNavigator,
    Navigator,
    Step,
    run_agent,
    stream_agent,
)
from pageindex_rag.agent.citations import extract_markers
from pageindex_rag.indexing.blocks import Heading, Image, Paragraph
from pageindex_rag.indexing.tree import build_tree
from pageindex_rag.models import DocumentIndex, ImageRecord


class ScriptedModel(BaseChatModel):
    """Returns preset replies in order, so the graph can be tested without an API."""

    replies: list[AIMessage]
    seen: list = Field(default_factory=list)  # the messages passed to each call

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.seen.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=self.replies.pop(0))])

    def bind_tools(self, tools, **kwargs):
        return self


class StreamingModel(ScriptedModel):
    """Like ScriptedModel, but sends text replies in 4-character pieces and tool calls as streamed chunks."""

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(list(messages))
        reply = self.replies.pop(0)
        if reply.tool_calls:
            calls = [
                {"name": c["name"], "args": json.dumps(c["args"]), "id": c["id"], "index": i}
                for i, c in enumerate(reply.tool_calls)
            ]
            chunks = [AIMessageChunk(reply.content, tool_call_chunks=calls)]
        else:
            chunks = [AIMessageChunk(reply.content[i : i + 4]) for i in range(0, len(reply.content), 4)]
        for chunk in chunks:
            yield ChatGenerationChunk(message=chunk)


def call(name: str, **args) -> AIMessage:
    return AIMessage("", tool_calls=[{"name": name, "args": args, "id": f"call-{name}"}])


def make_ships() -> DocumentIndex:
    blocks = [
        Heading(1, "Ship"),
        Heading(2, "Crew"),
        Paragraph("The captain is Ada."),
        Image(b"x", "png"),
        Heading(2, "Engines"),
        Paragraph("Two fusion engines."),
    ]
    root, _ = build_tree(blocks, "Ships", "markdown")
    image = ImageRecord(id="img-1", file="images/img-1.png", mime="image/png", node_id="1.1", description="crew photo")
    return DocumentIndex(
        doc_id="ships", title="Ships", source_path="ships.md", kind="markdown", summary="About ships.", root=root,
        images=[image],
    )


def make_cars() -> DocumentIndex:
    blocks = [Heading(1, "Cars"), Heading(2, "Engines"), Paragraph("A V8 engine.")]
    root, _ = build_tree(blocks, "Cars", "markdown")
    return DocumentIndex(
        doc_id="cars", title="Cars", source_path="cars.md", kind="markdown", summary="About cars.", root=root
    )


def test_navigator_marks_visited_and_lists_images():
    nav = Navigator(make_ships())
    assert "[1] Ship" in nav.list_children("root")
    nav.read_node("1.2")
    listing = nav.go_up("1.2")
    assert "[1.2] Engines (leaf, visited)" in listing
    assert "[1.1] Crew (leaf)" in listing
    assert "img-1: crew photo" in nav.read_node("1.1")
    assert nav.go_up("root") == "Already at the top of the document."
    assert nav.read_node("9.9") == "Unknown node id '9.9'."


def test_library_navigator_requires_an_open_document():
    nav = LibraryNavigator([make_ships(), make_cars()])
    assert "No document is open" in nav.list_children("root")
    assert "[ships] Ships (markdown): About ships." in nav.list_documents()
    assert "Unknown document id" in nav.open_document("nope")

    opened = nav.open_document("cars")
    assert 'Opened "Cars"' in opened and "[1] Cars" in opened
    # Qualified ids from a section header work as tool input too.
    assert "A V8 engine." in nav.read_node("cars:1.1")
    assert "switch documents" in nav.go_up("root")


def test_single_document_library_starts_open():
    nav = LibraryNavigator([make_ships()])
    assert nav.current == "ships"
    assert "[1] Ship" in nav.list_children("root")


def test_agent_navigates_backtracks_and_answers():
    model = ScriptedModel(
        replies=[
            call("list_children", node_id="root"),
            call("list_children", node_id="1"),
            call("read_node", node_id="1.2"),  # wrong branch
            call("go_up", node_id="1.2"),
            call("read_node", node_id="1.1"),
            AIMessage(
                "The captain is Ada [ships:1.1] [bogus:9.9] [ships:1, ships:9.9]. "
                "[img:ships:img-1] [img:ships:img-9]"
            ),
        ]
    )

    result = run_agent(LibraryNavigator([make_ships()]), model, "Who is the captain?", max_steps=10)

    assert result.found
    assert result.text == "The captain is Ada."
    assert [(c.doc_id, c.node_id) for c in result.citations] == [("ships", "1.1")]
    assert result.citations[0].doc_title == "Ships"
    assert result.citations[0].location == "Ship > Crew"
    assert [(i.doc_id, i.image_id, i.file) for i in result.images] == [("ships", "img-1", "images/img-1.png")]


def test_agent_routes_between_documents_and_streams_steps():
    model = ScriptedModel(
        replies=[
            call("open_document", doc_id="cars"),  # wrong document
            call("list_children", node_id="1"),
            call("list_documents"),
            call("open_document", doc_id="ships"),
            call("read_node", node_id="1.1"),
            AIMessage("Ada. [ships:1.1]"),
        ]
    )

    events = list(stream_agent(LibraryNavigator([make_ships(), make_cars()]), model, "Who is the captain?", 10))

    *steps, result = events
    steps = [event for event in steps if not isinstance(event, Draft)]  # a model that cannot stream sends one Draft
    assert all(isinstance(step, Step) for step in steps)
    assert isinstance(result, AnswerResult)
    assert [step.label for step in steps] == [
        'Opened "Cars"',
        'Looked inside "Cars"',
        "Listed documents",
        'Opened "Ships"',
        'Read "Crew"',
    ]
    assert steps[-1].doc_id == "ships" and steps[-1].node_id == "1.1"
    assert [(c.doc_id, c.node_id) for c in result.citations] == [("ships", "1.1")]


def test_agent_prompt_lists_documents_and_includes_history():
    model = ScriptedModel(replies=[AIMessage("Nothing.")])
    history = [ChatTurn(role="user", content="Hi"), ChatTurn(role="assistant", content="Hello")]

    run_agent(LibraryNavigator([make_ships(), make_cars()]), model, "And cars?", max_steps=5, history=history)

    system, *conversation = model.seen[0]
    assert "[ships] Ships" in system.content and "[cars] Cars" in system.content
    assert "No document is open yet" in system.content
    assert [type(m) for m in conversation] == [HumanMessage, AIMessage, HumanMessage]
    assert [m.content for m in conversation] == ["Hi", "Hello", "And cars?"]


def test_agent_gives_up_after_step_budget():
    model = ScriptedModel(replies=[call("list_children", node_id="root") for _ in range(3)])
    result = run_agent(LibraryNavigator([make_ships()]), model, "Anything?", max_steps=3)
    assert not result.found
    assert "budget" in result.text


def test_agent_not_found():
    model = ScriptedModel(replies=[AIMessage("The library has no info about cargo.")])
    result = run_agent(LibraryNavigator([make_ships()]), model, "Cargo?", max_steps=5)
    assert not result.found
    assert result.text == "The library has no info about cargo."
    assert result.citations == []


def test_agent_cannot_cite_what_it_did_not_read():
    model = ScriptedModel(
        replies=[
            call("list_children", node_id="1"),
            call("read_node", node_id="1.2"),
            AIMessage("Engines [ships:1.2]. Crew [ships:1.1]. [img:ships:img-1]"),  # 1.1 and its image were never read
        ]
    )
    result = run_agent(LibraryNavigator([make_ships()]), model, "?", max_steps=10)
    assert [c.node_id for c in result.citations] == ["1.2"]
    assert result.images == []


def test_agent_unknown_tool_and_bad_arguments_go_back_to_the_model():
    model = ScriptedModel(
        replies=[
            call("fly"),
            AIMessage("", tool_calls=[{"name": "read_node", "args": {}, "id": "call-bad"}]),
            AIMessage("Done."),
        ]
    )
    result = run_agent(LibraryNavigator([make_ships()]), model, "?", max_steps=5)
    tool_results = [m.content for m in model.seen[-1] if isinstance(m, ToolMessage)]
    assert tool_results == ["Unknown tool 'fly'.", "Invalid arguments for read_node."]
    assert result.text == "Done." and not result.found


def test_answer_streams_as_growing_drafts_without_markers():
    model = StreamingModel(
        replies=[
            AIMessage("Checking.", tool_calls=[{"name": "read_node", "args": {"node_id": "1.1"}, "id": "c1"}]),
            AIMessage("The captain is Ada [ships:1.1]."),
        ]
    )

    events = list(stream_agent(LibraryNavigator([make_ships()]), model, "Who is the captain?", 5))

    *middle, result = events
    drafts = [e.text for e in middle if isinstance(e, Draft)]
    assert drafts[0] == "Checking."  # thinking aloud before the tool call
    answer_drafts = drafts[1:]
    assert len(answer_drafts) > 3  # arrived in pieces
    assert all(later.startswith(earlier) for earlier, later in zip(answer_drafts, answer_drafts[1:]))
    assert not any("[" in d for d in drafts)  # no marker, not even half of one
    assert answer_drafts[-1] == "The captain is Ada." == result.text
    step = next(e for e in middle if isinstance(e, Step))
    assert step.note == "Checking."  # the sentence before the tool call travels with its step
    # The tool call that was streamed in chunks still ran: the Step comes before the answer's drafts.
    kinds = [type(e).__name__ for e in middle]
    assert kinds.index("Step") < len(kinds) - 1 and isinstance(middle[-1], Draft)
    assert [c.node_id for c in result.citations] == ["1.1"]


def test_agent_asks_again_after_an_empty_reply():
    model = ScriptedModel(
        replies=[
            call("read_node", node_id="1.1"),
            AIMessage(""),  # nothing at all: no text, no tool call
            AIMessage("The captain is Ada [ships:1.1]."),
        ]
    )
    result = run_agent(LibraryNavigator([make_ships()]), model, "Who?", max_steps=5)
    assert result.found and result.text == "The captain is Ada."
    assert len(model.seen) == 3  # the empty reply was retried


def test_agent_reports_a_reply_that_stays_empty():
    model = ScriptedModel(replies=[AIMessage(""), AIMessage(""), AIMessage("")])
    result = run_agent(LibraryNavigator([make_ships()]), model, "Who?", max_steps=5)
    assert not result.found and "empty reply" in result.text


def test_agent_does_not_retry_a_refusal():
    refusal = AIMessage(
        "", response_metadata={"stop_reason": "refusal", "stop_details": {"category": "reasoning_extraction"}}
    )
    model = ScriptedModel(replies=[refusal, AIMessage("never reached")])
    result = run_agent(LibraryNavigator([make_ships()]), model, "Who?", max_steps=5)
    assert not result.found and "declined" in result.text
    assert len(model.seen) == 1


def test_extract_markers():
    text = "Fact [a:1.2], more [b:3, a:1.2] at [10:30] and a [link](x). [img:a:img-1]\n\n[a:2]"
    clean, sections, images = extract_markers(text)
    assert sections == ["a:1.2", "b:3", "10:30", "a:2"]  # a time like [10:30] looks like a ref; it is dropped later
    assert images == ["a:img-1"]
    assert clean == "Fact, more at and a [link](x)."
