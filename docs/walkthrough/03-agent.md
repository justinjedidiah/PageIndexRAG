# 03. The agent: how a question gets answered

Files covered: `src/toc_rag/agent/navigator.py` and `src/toc_rag/agent/graph.py` (plus the tiny `agent/__init__.py`, which only re-exports names).

This is the heart of the project: the part that replaces vector search. The idea in one sentence: **give Claude a few tools to walk the document tree, and let it decide where to look.**

---

## The big picture before any code

A chat model can only produce text. To let it *do* things, the model API supports **tool calling**:

1. You tell the model "these tools exist" (name, description, parameters).
2. The model replies, not with prose, but with "please call tool `read_node` with `node_id="2.1"`".
3. **Your code** runs the tool and sends the result back as a new message.
4. The model reads the result and decides its next move: another tool, or a plain-text final answer (a reply with no tool call ends the loop).

That loop is the agent. The conversation the model sees looks like this for *"Who is the commander?"*:

```
System     You answer questions about a library... Documents: - [meridian-...] Meridian Station ...: <summary>
Human      Who is the commander of Meridian Station?
AI         (tool call) open_document(doc_id="meridian-...")
Tool       Opened "meridian-...". Subsections of [root] ...: - [1] Overview (2 subsections): ... - [2] Crew and Roles ...
AI         (tool call) list_children(node_id="2")
Tool       Subsections of [2] Crew and Roles: - [2.1] Roster (leaf): ... - [2.2] Shift schedule (leaf): ...
AI         (tool call) read_node(node_id="2.1")
Tool       [meridian-...:2.1] Roster (Crew and Roles > Roster)\n\nThe current four-person crew rotates... | Ada Okonkwo | Commander | Day |
AI         Ada Okonkwo is the commander and works the day shift. [meridian-...:2.1]
```

Two files make it work:

- `navigator.py` = **what the tools do** (pure Python over the tree, no AI).
- `graph.py` = **the loop** (when to call the model, when to run tools, when to stop).

---

## `navigator.py`: the tools' real behavior

### `Navigator`: one document

```python
class Navigator:
    def __init__(self, index: DocumentIndex, prefix: str = ""):
        self.index = index
        self.prefix = prefix
        self.nodes = index.node_map()
        self.parents = index.parent_map()
        self.images_by_node = defaultdict(list)
        for record in index.images:
            if record.node_id:
                self.images_by_node[record.node_id].append(record)
        self.visited: set[str] = set()
```

Set-up, done once per question:
- `nodes`: id -> Node, for instant lookup. `parents`: id -> parent id (for `go_up`). Both come from `DocumentIndex` (file 01).
- `images_by_node`: for each section id, its images. `defaultdict(list)` is a dictionary that creates an empty list the first time you touch a missing key, so `.append` just works.
- `visited`: the set of section ids the agent has already looked at. It exists so the agent can be told "you've been here" and not loop forever.
- `prefix` is added in front of ids **shown for citation** (`"meridian-...:"`), explained below.

**`list_children`** (the tool "look inside this section"):

```python
def list_children(self, node_id):
    node = self.nodes.get(node_id)
    if node is None:
        return f"Unknown node id {node_id!r}."
    self.visited.add(node_id)
    if not node.children:
        return f"[{node.id}] {node.title} has no subsections. Use read_node to read it."

    lines = [f"Subsections of [{node.id}] {node.title}:"]
    for child in node.children:
        shape = "leaf" if not child.children else f"{len(child.children)} subsections"
        visited = ", visited" if child.id in self.visited else ""
        lines.append(f"- [{child.id}] {child.title} ({shape}{visited}): {child.summary or 'no summary'}")
    return "\n".join(lines)
```

It returns a **string**, because a tool result goes back to the model as text. A real result looks like:

```
Subsections of [3] Life Support:
- [3.1] Oxygen supply (leaf): Oxygen supply. Oxygen is produced by electrolysis of recycled water ...
- [3.2] Water recycling (leaf): ...
- [3.3] Temperature and pressure (leaf): ...
```

Each line has the **id in square brackets** (what the model will pass back), the title, whether it is a `leaf` or has N subsections (a hint: leaves are for reading, branches for opening), a `visited` flag if it was seen, and **the summary written at index time**: the thing the model uses to choose. Errors (unknown id) are also returned as text instead of raising: the model sees "Unknown node id" and can correct itself. An exception here would crash the whole question.

**Several levels at once (`listing_depth`).** Each tree level the agent steps through costs one tool call, out of a budget of about 15. So a `Navigator` can show more than one level per listing: with `listing_depth=2` (the default, from `TOC_RAG_LISTING_DEPTH`) every child line is followed by its own children, indented, with **titles and ids only** (no summaries, to keep the listing short):

```
Subsections of [root] Meridian Station Operations Handbook:
- [1] Overview (2 subsections): The station's mission and layout ...
  - [1.1] Mission (leaf)
  - [1.2] Station layout (leaf)
- [2] Crew and Roles (leaf): Who is on board and their shifts ...
```

The model may `read_node("1.2")` straight away, without first listing `[1]`. Deeper lines are capped at 60 (`MAX_DEEPER_LINES`), with a "+N more" note; only the node actually listed is marked visited. A summary that is only a free preview (Free preset) is shown as `(preview) ...`, and the system prompt explains both conventions. `LibraryNavigator(indexes, listing_depth)` passes the depth to every document; the web page has an "Outline levels per look" selector next to the chat box, and the CLI has `ask --listing-depth`. With depth 1 the listing is exactly the one-level form above.

**`read_node`** (the tool "give me this section's actual text"):

```python
def read_node(self, node_id):
    node = self.nodes.get(node_id)
    if node is None: return f"Unknown node id {node_id!r}."
    self.visited.add(node_id)
    text = node.text or "(No text of its own. Its content is in its subsections.)"
    out = f"[{self.prefix}{node.id}] {node.title} ({node.location.label()})\n\n{text}"
    images = self.images_by_node.get(node_id)
    if images:
        listed = "\n".join(f"- {self.prefix}{r.id}: {r.description or 'no description'}" for r in images)
        out += f"\n\nImages in this section:\n{listed}"
    return out
```

The header line `[meridian-...:2.1] Roster (Crew and Roles > Roster)` is where the **qualified id** comes from: prefix + node id. The system prompt tells the model to cite using exactly that form. After the text, images in the section are listed with their qualified ids and descriptions, which is what lets "show me the chart" work: the model sees `- meridian-...:img-1: A bar chart with four colored bars...` and can request it in its answer.

**`go_up`**:

```python
def go_up(self, node_id):
    parent_id = self.parents.get(node_id)
    if parent_id is None:
        return AT_TOP
    return self.list_children(parent_id)
```
"I'm in the wrong branch." It lists the **parent's** children, i.e. the node's siblings, so the model can pick another. At the top (`root` has no parent) it returns the constant `AT_TOP` text.

### `Step`: a record for the UI

```python
class Step(BaseModel):
    tool: Literal["list_documents", "open_document", "list_children", "read_node", "go_up"]
    doc_id: str | None = None
    node_id: str | None = None
    label: str
```

One navigation action in display-friendly form. The page shows the model's `note` as a line of text, then `label` ("Read "Roster"") as a small line under it, in order, above the answer.

### `LibraryNavigator`: many documents

`Navigator` only knows one document. The app has a **library**, so this class wraps one `Navigator` per document and adds "which document is open".

```python
def __init__(self, indexes):
    self.indexes = {index.doc_id: index for index in indexes}
    self.docs = {index.doc_id: Navigator(index, prefix=f"{index.doc_id}:") for index in indexes}
    self.current = indexes[0].doc_id if len(indexes) == 1 else None
    self.trace: list[Step] = []
```

- Each document's `Navigator` is created with `prefix="<doc_id>:"`, so ids it shows are automatically qualified.
- `current` is the open document. **With exactly one document it starts open** (nothing to choose); with several it starts as `None` and the agent must call `open_document`.
- `trace` collects a `Step` each time a tool does something. The graph reads it to stream the path to the UI.

Each tool method follows the same pattern, e.g.:

```python
def read_node(self, node_id):
    nav = self._open()                      # the open document's Navigator, or None
    if nav is None:
        return NO_DOCUMENT                  # "No document is open. Use open_document first..."
    node_id = self._local(nav, node_id)     # tolerate a qualified id: strip "<doc_id>:" if present
    result = nav.read_node(node_id)
    self._record("read_node", nav, node_id, "Read")   # add a Step to the trace
    return result
```

`_local` uses `str.removeprefix`, because models sometimes pass back the qualified id they saw in the header (`meridian-...:2.1`) when the tool wants the plain `2.1`. `_record` only adds a step if the node exists (so typos don't show as steps).

`open_document` sets `self.current`, adds a step `Opened "<title>"`, and returns the document's top-level listing immediately (so the model doesn't waste a step asking for it). `go_up` at the top returns a hint to use `list_documents`/`open_document` to switch documents.

**Resolving citations**

```python
def find_node(self, ref):      # "doc_id:node_id", or a bare node id in the open document
    doc_id, local = self._split(ref)
    nav = self.docs.get(doc_id) if doc_id else None
    node = nav.nodes.get(local) if nav else None
    return (nav.index, node) if nav and node else None
```

When the model's final answer cites a section, this turns the ref into the real document and node, **or returns `None` if it does not exist**. `_split` uses `ref.partition(":")` (document ids contain no colon, so the first colon is the divider; with no colon it falls back to the open document). `find_image` does the same for image ids. `has_read(doc_id, node_id)` says whether `read_node` was called on that node in this run (`Navigator.read`, next to the older `visited`, which `list_children` also fills).

---

## `graph.py`: the loop

### The tools

```python
def make_tools(navigator):
    @tool
    def read_node(node_id: str) -> str:
        """Read the text of a node in the open document, plus descriptions of any images in it."""
        return navigator.read_node(node_id)
    ...
    return [list_documents, open_document, list_children, read_node, go_up]
```

**What `@tool` does.** `@tool` (from LangChain) takes a Python function and turns it into a *tool* for the model: the **name** comes from the function name, the **description** from the docstring, and the **parameters and their types** from the type hints. `llm.bind_tools(tools)` sends all of that to Claude with every request. When Claude asks for a tool, the API just returns "call `read_node` with `{node_id: "2.1"}`"; our loop (`run_tools`, below) then calls the function, which forwards to the `LibraryNavigator` method of the same name.

There are five tools, all for moving around the tree. They are defined inside `make_tools` so each one closes over the `navigator` of the current question. There is no tool for finishing: the model ends the run by replying in plain text.

(Earlier versions had two more tools, `answer` and `not_found`, plus empty stub functions that raised `NotImplementedError` and were only used to describe the tools. Those are gone. `NotImplementedError` now appears once in the project, in `indexing/pipeline.py` for PDF files, where it honestly means "PDF reading is not written yet".)

### The result types

```python
class Citation(BaseModel): doc_id, doc_title, node_id, title, location   # one source section
class ImageRef(BaseModel): doc_id, image_id, file, description            # one picture to show
class AnswerResult(BaseModel):
    found: bool
    text: str
    citations: list[Citation]
    images: list[ImageRef]
class ChatTurn(BaseModel): role: "user" | "assistant"; content: str       # a past message
```

`AnswerResult` is what the whole agent finally returns to the web page. `found=False` means "no answer with a source" (the text is the reply or the reason).

### The system prompt

```python
SYSTEM_PROMPT = """You answer questions about a library of documents by navigating each document's table of contents.

Documents in the library:
{documents}

How to work:
- {start}
- Before each tool call, say in one short sentence what you are about to do and why.
- Use list_children to look inside a section, going into the subsection whose summary best matches the question. ...
- Use read_node to read a section's text. ...
- After reading, decide whether the text answers the question. If it does not, use go_up and try another branch, or open another document. Avoid sections marked visited unless you have a reason to return.
- When you can answer, reply in plain text without calling a tool. After each claim, put its source in square brackets in the qualified form shown at the top of each section you read, like [doc_id:1.2]. Cite only sections you read with read_node. If the user asked for a picture, add the matching image id as [img:doc_id:img-1].
- If the library does not contain the answer, say so in plain text and put no bracketed ids in the reply.
- Earlier messages are conversation context. For a new factual question, navigate and read; do not answer from memory of earlier turns.
Answer only from what you read. You have at most {max_steps} steps."""
```

This is the agent's entire "personality" and instruction set. `{documents}`, `{start}` and `{max_steps}` are filled in by Python's `str.format`. The trailing backslash at the end of lines inside the triple-quoted string joins the lines. Key instructions: navigate by *summaries*, back up with `go_up` on a wrong branch, **cite with bracketed qualified ids**, say so in plain words rather than guess, **do not answer follow-up facts from memory** (this forces a fresh lookup each question instead of repeating something that might be stale or wrong).

`START_SINGLE` / `START_MANY` are the two variants for `{start}` (document already open vs. choose one first).

### The state and the graph (LangGraph)

LangGraph models an agent as a **graph**: *nodes* are steps (Python functions), *edges* are "what runs next", and a shared **state** is passed along.

```python
class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    steps: int
    result: AnswerResult | None
```

The state has three fields. `messages` is the conversation so far; `Annotated[..., add_messages]` attaches a **reducer** meaning "when a node returns new messages, *append* them" (without it, returning a list would *replace* the old one). `steps` counts model calls. `result` stays `None` until there is a final answer.

```python
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
```

As a picture:

```
START ─> agent ──(model asked for tools)──> tools ──(otherwise)──────────────────> agent  (loop)
           │                                  └─(step budget used up)──> give_up ──> END
           └──(model replied in plain text)──> finish ──> END
```

This is the standard loop: it ends when the model stops calling tools. The four nodes, defined inside `build_graph` (closures over `navigator`, `model`, `max_steps`):

```python
def call_model(state):
    write = get_stream_writer()
    response = None
    for chunk in model.stream([system, *state["messages"]]):
        response = chunk if response is None else response + chunk
        write({"text": _message_text(response)})
    return {"messages": [response], "steps": state["steps"] + 1}
```
**`agent`**: sends the system prompt plus the whole conversation to Claude and appends its reply; counts one step. It uses `model.stream` instead of `invoke`, so the reply arrives in small pieces. `response + chunk` adds pieces together (LangChain's `AIMessageChunk` supports `+`, and a tool call sent in fragments is reassembled the same way), so after the loop `response` is the complete message, exactly what `invoke` would have returned. After each piece, `write(...)` (from `get_stream_writer`) pushes the text so far onto LangGraph's `custom` stream, which `stream_agent` picks up. A model that cannot stream just gives one piece. (The system prompt is re-sent on each call; caching it to save cost is not done yet.)

```python
def run_tools(state):
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
```
**`tools`** is where tools execute. The last message is Claude's reply; `.tool_calls` is the list of requests in it (it can ask for several at once). Each is looked up in `by_name` (the tools from `make_tools`) and invoked with the arguments Claude gave. An unknown tool name, or arguments that do not fit (say `read_node` with no `node_id`), produce a short message back to the model instead of crashing, so it can correct itself. Each result is wrapped in a `ToolMessage` carrying the `tool_call_id`, so the model knows which request it answers.

```python
def after_model(state): return "tools" if state["messages"][-1].tool_calls else "finish"
def after_tools(state): return "give_up" if state["steps"] >= max_steps else "agent"
```
The **routing** functions decide the next node. After the model: tool calls -> run them; none -> the model is done. After tools: **budget exhausted** (default 15 model calls) -> `give_up`; else -> ask the model again.

```python
def finish(state):
    return {"result": _answer_result(navigator, _message_text(state["messages"][-1]))}
def give_up(state):
    return {"result": AnswerResult(found=False, text="Stopped: step budget used up before finding an answer.")}
```
`finish` takes the model's last message, which is its answer, and turns it into an `AnswerResult` (below). And the step budget in `give_up` **guarantees the loop ends**: an agent that wanders cannot run (and bill) forever; it says it didn't find the answer.

### Running it and streaming the path

```python
def stream_agent(navigator, llm, question, max_steps, history=None):
    graph = build_graph(navigator, llm, max_steps)
    messages = [*_history_messages(history or []), HumanMessage(question)]
    updates = graph.stream({"messages": messages, "steps": 0, "result": None},
                           {"recursion_limit": max_steps * 2 + 10}, stream_mode=["updates", "custom"])
    emitted, result, last_draft = 0, None, ""
    for mode, update in updates:
        if mode == "custom":
            if (draft := visible_prefix(update["text"])) and draft != last_draft:
                last_draft = draft
                yield Draft(text=draft)
            continue
        last_draft = ""
        for node_name, changes in update.items():
            if node_name == "tools":
                yield from navigator.trace[emitted:]
                emitted = len(navigator.trace)
            if isinstance(changes, dict) and changes.get("result") is not None:
                result = changes["result"]
    yield result or AnswerResult(found=False, text="No answer was produced.")
```

- The starting messages are the **recent chat history** (`_history_messages` converts `ChatTurn`s to `HumanMessage`/`AIMessage`) plus the new question.
- `graph.stream(..., stream_mode=["updates", "custom"])` runs the graph and hands back two kinds of events, each tagged with its mode. `"updates"` are **each node's output as soon as that node finishes**, as `{node_name: what it returned}`; that is how steps appear one by one. `"custom"` are the pushes from `call_model`'s `write(...)`: the reply so far, while the model is still typing.
- Each `custom` push goes through `visible_prefix` (in `citations.py`): it holds back a `[` that has no closing `]` yet and strips completed markers, so the visitor never sees half a marker. The result is yielded as a `Draft`. `last_draft` skips repeats (a push that adds only a marker changes nothing visible).
- A `Draft` holds the **whole** visible text so far, not just the new piece, so each one replaces the previous (no ordering or gap problems).
- When the `tools` node finishes, any new entries in `navigator.trace` (not yet sent, tracked by `emitted`) are yielded: those are the `Step`s ("Opened...", "Read..."). `yield from` hands out each item of a list one at a time.
- When some node reports a `result`, it is remembered. After the loop, the final `AnswerResult` is yielded last.
- `recursion_limit = max_steps * 2 + 10` is LangGraph's own runaway guard: it counts node executions, and each agent step is two (`agent` + `tools`), plus slack. It is a second line of defense behind our step budget.

This is a **generator**: the caller can `for event in stream_agent(...)` and receives `Step`s and `Draft`s as they happen, then one `AnswerResult` last. A `Draft` followed by a `Step` was the model saying something like "Let me check the roster" before a tool call (the system prompt asks for one sentence). `stream_agent` remembers that sentence (`note`, taken from the `agent` node's update when the reply has tool calls) and attaches it to the first `Step` of that turn (`Step.note`), so the page can show it beside the step; the page clears the draft text when the `Step` arrives. Drafts after the last `Step` are the answer being written, and the final `AnswerResult` replaces them with the cleaned text plus citations.

```python
def run_agent(...):
    *_, result = stream_agent(...)
    return result
```
For callers who only want the answer. `*_, result = ...` is **starred unpacking**: run the generator to the end, throw everything away (`*_`) except the last item.

```python
def stream_ask(indexes, question, settings=None, history=None, api_key=None):
    settings = settings or get_settings()
    llm = chat_model(settings.agent_model, api_key)
    return stream_agent(LibraryNavigator(indexes), llm, question, settings.agent_max_steps, history)
```
The public entry point the web app uses: it builds the model from settings (**Sonnet** by default) with the visitor's key, wraps the documents in a `LibraryNavigator` (new, with an empty `visited` and `trace` for every question), and starts the stream. `ask` is the same but returns only the final answer.

### Turning the model's final reply into an answer

The model ends by writing plain text with source markers: `Ada Okonkwo is the commander [meridian-...:2.1].` Two small functions turn that into an `AnswerResult`.

`citations.py` pulls the markers out:

```python
_BRACKET = re.compile(r"[ \t]*\[([^\[\]]+)\]")
_REF = re.compile(r"^(img:)?[\w-]+:[\w.-]+$")

def extract_markers(text):
    sections, images = [], []
    def take(match):
        refs = [part.strip() for part in re.split(r"[,;]", match.group(1))]
        if not all(_REF.match(ref) for ref in refs):
            return match.group(0)            # not a marker (e.g. a Markdown link): leave it alone
        for ref in refs:
            if ref.startswith("img:"): images.append(ref.removeprefix("img:"))
            else: sections.append(ref)
        return ""                            # remove the marker from the text
    clean = _BRACKET.sub(take, text).strip()
    return clean, list(dict.fromkeys(sections)), list(dict.fromkeys(images))
```

`_BRACKET` finds any `[...]` (plus the spaces before it, so removing it leaves no gap). `take` splits the inside on commas so `[a:1, a:2]` counts as two refs. Only if every part has the shape `doc:node` (or `img:doc:img-1`) is it treated as a marker. `dict.fromkeys(...)` removes duplicates while keeping order. A time like `[10:30]` has the same shape, so it is stripped too; it then fails the lookup below and is simply not a citation.

Then `_answer_result` in `graph.py` decides which markers count:

```python
def _answer_result(navigator, reply):
    text, section_refs, image_refs = extract_markers(reply)
    citations = []
    for ref in section_refs:
        if found := navigator.find_node(ref):
            index, node = found
            if navigator.has_read(index.doc_id, node.id):
                citations.append(Citation(...))
    images = []
    for ref in image_refs:
        if found := navigator.find_image(ref):
            index, record = found
            if record.node_id and navigator.has_read(index.doc_id, record.node_id):
                images.append(ImageRef(...))
    return AnswerResult(found=bool(citations), text=text or "No answer was produced.", citations=citations, images=images)
```

`if found := ...` is the **walrus operator**: assign and test in one step. A marker becomes a citation only if **both** hold: the node exists (`find_node` returns `None` for an invented id) and the agent actually read it (`has_read`, backed by `Navigator.read`, which `read_node` fills and `list_children` does not). So the page can never show a fake citation, nor one for a section the agent only saw a summary of. An image counts only if the section that contains it was read.

`found` is `True` exactly when at least one citation survived. A reply with no markers (the library had no answer, or the model forgot them) is shown as a plain grey message and is left out of the chat history the next question gets.

The text shown to the visitor has the markers removed; the citations travel separately as chips.

---

## What this design does not do (limits)

1. **Nothing verifies that a cited section supports the sentence it is attached to.** Citations are checked to exist and to have been *read* in this run (see the last section above), but not to back up the claim. A model could still attach `[doc:2.1]` to a sentence the section does not say. The original plan mentioned a final "does the cited text really back this up?" check; it is not built.
2. **Chat memory is only the text** of earlier questions and answers (last 6 turns), not the tool calls that produced them. Each question re-navigates from scratch.
3. Only one open document at a time, and sections are read whole (no search inside a very long section; that is why big sections are split at index time).
4. The behaviour with a real model has been tested only through scripted fakes and an offline model, never with real Claude navigating real summaries. Routing quality depends on summary quality (file 02).
5. Prompt caching (reusing the system prompt and tree listing across steps to cut cost) is planned, not done.
