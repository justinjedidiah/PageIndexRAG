# 06. Tests, the fake model, and scripts

Files covered: everything in `tests/`, plus `scripts/build_samples.py` and `pyproject.toml`.

Why this matters: most of this project talks to a paid AI service, which is slow, costs money and gives different answers each time. Testing it directly would be unreliable. So the project is tested in **three layers**, each answering a different question.

| Layer | Command | Question it answers | Needs a key? | Speed |
|---|---|---|---|---|
| Unit tests (62) | `python -m pytest` | Does each piece of logic do what it says? | No | ~5 seconds |
| Browser test (20 checks) | `python tests/e2e/run_e2e.py` | Does the whole app work when clicked through in a real browser? | No (fake model) | ~3 minutes |
| Real use | `reflex run` with your key | Does Claude actually do a good job? | **Yes** | you |

The first two never contact Anthropic. The third is the only one that tells you about answer *quality*, and it has not been done yet.

---

## `pyproject.toml`: how the project is defined

```toml
[project]
name = "pageindex-rag"
requires-python = ">=3.11"
dependencies = ["pydantic>=2.7", "python-docx>=1.1", "pillow>=10", "python-dotenv>=1.0",
                "langchain-core>=0.3", "langchain-anthropic>=0.3", "langgraph>=0.2"]

[project.optional-dependencies]
app = ["reflex>=0.7"]
dev = ["pytest>=8", "playwright>=1.45"]

[project.scripts]
pageindex = "pageindex_rag.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- `dependencies` are what the **core** needs (data models, Word reading, images, config, AI libraries).
- `optional-dependencies`: **extras** installed on request: `app` adds Reflex; `dev` adds the test tools. That is what `pip install -e ".[dev,app]"` means: install this project in *editable* mode (`-e`: changes to the code take effect immediately, no reinstall) plus both extras.
- `[project.scripts]` creates the `pageindex` command and points it at the `main` function in `cli.py`.
- `where = ["src"]`: the importable code lives in the `src/` folder ("src layout"), so tests must import the *installed* package rather than accidentally picking up loose files.
- `testpaths`: where pytest looks.

---

## Unit tests (`tests/test_*.py`)

pytest finds every function named `test_...` in files named `test_*.py`, runs each, and reports. A test uses plain `assert`: if the condition is false, the test fails. Tests receive helpers by **naming them as parameters** (called *fixtures*): `tmp_path` is a built-in fixture that gives each test its own empty temporary folder, deleted afterwards.

### `conftest.py`: shared helpers

`conftest.py` is a file pytest loads automatically; things defined in it are available to every test.

```python
def make_png(width=2, height=2) -> bytes:
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    rows = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")
```

Builds a **valid PNG image from scratch** with no imaging library, so tests have real images to use. A PNG file is: an 8-byte signature, then "chunks", each = length + name + data + a checksum (`zlib.crc32`). `IHDR` holds width/height/colour type; `IDAT` holds the pixel rows compressed with `zlib`; `IEND` ends the file. Each row starts with a filter byte (`\x00` = none), then `width` pixels of 3 bytes (`\xff\x00\x00` = red). So `make_png(480, 240)` is a solid red rectangle.

`make_noisy_png` is the same but fills pixels with random bytes (a fixed seed, so it's repeatable). Noise doesn't compress, so the file is large; tests use it when a "big, real-looking image" is needed.

```python
@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(data_dir=tmp_path / "data", index_model="test-index", ..., max_leaf_tokens=50, agent_max_steps=5)
```
A fixture giving tests a `Settings` that writes into the temp folder and uses a **tiny `max_leaf_tokens` of 50** so that splitting kicks in with just a few paragraphs.

### What each test file checks

| File | What it proves |
|---|---|
| `test_tree.py` | `build_tree` nests blocks correctly, including skipped heading levels, images becoming placeholders, and `split_large_nodes` creating generated parts that rejoin to the original text. Small nodes are left alone. Splitting is balanced, cuts a giant paragraph on sentences, cuts a big table by rows with its header repeated, keeps an image with its paragraph, and hard-cuts a word longer than the limit. |
| `test_refine.py` | A good outline comes back unchanged in every mode; `off` changes nothing; merge joins only adjacent tiny leaves within the size limit; single-child chains collapse (the root keeps an outline); fold turns small subtrees into one leaf; a heading-less document becomes one level of parts; previews; presets. |
| `test_extract.py` | The Markdown reader (code fences are not headings, images load) and the Word reader (headings, tables to Markdown with `\|` escaped, an embedded image) on a `.docx` the test builds with `python-docx`. |
| `test_pipeline.py` | `index_document` end to end without AI (`use_llm=False`): ids, source ToC, generated parts, the image file saved and linked to its section, the saved index reloading identical, and re-indexing keeping a single library entry. |
| `test_agent.py` | The `Navigator`/`LibraryNavigator` behavior and the whole agent loop with a scripted model (below). |
| `test_enrich.py` | Stage 2 with a fake model: bottom-up order, progress counts, the visitor's key reaching every model, generated nodes renamed, failures propagating, and `friendly_error` never echoing the raw error. |
| `test_images.py` | A flat chart is described even though its file is tiny; icons and thin rules are skipped; unreadable/unsupported images skipped; big images shrunk; transparency stays PNG. |
| `test_library.py` | Samples + session merging, samples first, a visitor with no live session seeing only samples, deleting, and the image-path safety rules (traversal attempts, non-images, odd ids all return nothing). |
| `test_outline.py` | The outline rows and `node_markdown` (images inlined, alt text cleaned, unknown placeholders vanish), and `describe_duration`. |
| `test_sessions.py` | Creation and expiry using a fake clock, restarting an expired session, the sweep removing only expired ones, deletion, and invalid tokens (including `../`) being rejected. |
| `test_pdf.py` | PDFs drawn in code with reportlab: lines joined into paragraphs, hyphenated words rejoined, bookmarks as the outline (placed at their title, or where they point; junk ones ignored), page ranges with printed labels (roman front matter), split parts with their own pages, no page marker left in any text; running footers and page numbers removed; a chart rendered as a picture with its labels out of the text; a ruled table as Markdown; embedded pictures cut out (icons and backgrounds skipped); a framed box keeping its text; two columns read in order; a figure cited by its own page; the saved read reused, and refused for other bytes; refusals: password, scanned, too many pages, too much text (for Word too). |
| `test_uploads.py` | Real docx/pdf accepted; `.zip` and other types refused; files lying about their content refused; empty/oversized refused; a zip bomb refused; `safe_filename` on hostile names. |

### `test_agent.py`: testing an AI agent without an AI

```python
class ScriptedModel(BaseChatModel):
    replies: list[AIMessage]
    seen: list = Field(default_factory=list)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=self.replies.pop(0))])

    def bind_tools(self, tools, **kwargs):
        return self

def call(name, **args) -> AIMessage:
    return AIMessage("", tool_calls=[{"name": name, "args": args, "id": f"call-{name}"}])
```

A **puppet model**: it subclasses LangChain's model base class and, each time the agent asks it something, returns the **next pre-written reply** (`self.replies.pop(0)` removes and returns the first). It also records every conversation it was shown in `seen`. `call("read_node", node_id="1.1")` builds the kind of reply Claude would send when requesting that tool. `bind_tools` just returns itself, since a puppet needs no tool schema.

With this, a test can write the *entire conversation in advance* and check what the agent code does with it. For example, the test for backtracking scripts: list top level, list a branch, read the wrong section, `go_up`, read the right one, then a plain-text reply with markers. The test then asserts the cleaned answer, that only real, read citations survived (a made-up id and a section that was never read are dropped), and the image reference. Other tests check: routing between two documents (including the exact step labels streamed), that the system prompt lists the documents and the history appears in order as human/AI messages, that the step budget produces "budget used up", `not_found`, and a plain-text reply being accepted.

This tests **our code's behavior** (the loop, the navigator, the result handling) and nothing about Claude's judgement.

### `test_enrich.py`: a fake for the indexing calls

```python
class FakeLLM:
    def with_structured_output(self, schema):
        return RunnableLambda(lambda prompt: self._respond(schema, prompt))
```
`with_structured_output` returns a runnable that answers `_NodeSummary` or `_ImageDescription` requests from the prompt text (e.g. `About <title>.`), keeping a thread-safe record of every prompt. The tests assert, for example, that the **root's prompt contains the children's summaries** (proof of bottom-up order) and that the progress numbers run 1, 2, 3 ... up to the estimated total. `monkeypatch.setattr("pageindex_rag.llm.chat_model", ...)` temporarily replaces the real model factory for one test (and puts it back afterwards); this is why `enrich_index` imports `chat_model` *inside* its body.

---

## `fake_llm.py`: the offline Claude

This is the "fake model" used by the browser test (not a pytest file, a reusable stand-in). It is a `BaseChatModel` subclass `FakeChat`, handed out by `make_model(model)`, and selected via the `PAGEINDEX_LLM_FACTORY` variable (file 04). It handles the three kinds of request the app makes:

1. **Writing summaries** (`structured`, for `with_structured_output`). It reads the prompt text the real summarizer sends ("Section title: ...", "Section text: ...", "Subsections: ...") and returns the title plus a "summary" made by slicing the text: the section's own first 300 characters plus the first 240 characters of each child line (so a parent's summary carries the start of every child). For images it returns a fixed "bar chart" description.
2. **Anything that isn't the agent** (e.g. the API-key check) gets `AIMessage("ok")`: it recognises the agent only by a phrase in its system prompt.
3. **Acting as the agent** (`next_message`): a tiny rule-based policy that reads the conversation so far and decides the next tool call:
   - first move: if no document is open, pick the one whose listing shares the most words with the question (`best`); else list the top level;
   - after a listing: parse the `- [id] title (leaf|N subsections): summary` lines with a regular expression, choose the best-matching child by shared words, and `read_node` it if it is a leaf or `list_children` it otherwise;
   - after reading a section (recognised by the `[doc:id] title (...)` header): reply in plain text with the first 400 characters of the text plus "(Answered by the offline test model.)", followed by a `[doc:id]` marker for that section (and `[img:...]` markers if the question contains a picture-ish word).
   `words()` lowercases and drops stop-words; `best` counts overlapping words (first wins ties).

It is intentionally dumb. Its purpose is to make the app produce *some* complete, plausible answer so every screen and handler can be exercised. It is **not** a measure of how the real system answers.

(A leftover constant `OPEN_FAIL_KEY` in this file is unused: remnant of an idea to simulate a rejected key.)

---

## `tests/e2e/run_e2e.py`: the browser test

Run it with `python tests/e2e/run_e2e.py`. It is a script, not a pytest test (it starts servers and takes minutes). What it does:

**Set-up (`main`)**
1. Makes a temp folder and sets environment variables: use the fake model, store data in the temp folder, use a temp samples folder, and **remove any real `ANTHROPIC_API_KEY`** from the environment (so a real key can never be used by accident).
2. Runs `scripts/build_samples.py --index` to build a sample library **with the fake model**.
3. Starts `reflex run` as a background process (`subprocess.Popen`), logging to `server.log`, and waits until both the back end (`/ping`) and the front end answer (`wait_for` polls a URL until it responds).
4. `finally:` always kills the whole server process tree (`kill_tree` with `psutil`: Reflex starts child processes) and deletes the temp folder.

**The browser steps (`run_browser`)**
- Uses **Playwright** to drive your installed Chrome (`channel="chrome"`: no browser download), headless (no visible window).
- A **warm-up** page load first, because a freshly started dev server re-optimizes and hot-swaps modules on its first visit, which logs warnings unrelated to the app.
- A helper `step(name, fn, screenshot)` runs one check, records PASS/FAIL, takes a failure screenshot on error and optional success screenshot. Checks use `expect(...)` (Playwright's retrying assertions: they wait up to the timeout for something to appear).
- Browser console errors and uncaught JavaScript errors are collected the whole time, tagged with the step they happened in (this is how the hooks bug was traced to "a question streams its path").

The 20 checks, in order: page loads with 3 samples and locked chat; browse an outline and read a section without a key; close the panel; upload locked until a key is set; key accepted; key never in the page; a question streams its path and answers; steps are shown; a citation opens the source; panel closes; an image question shows the picture (including that the image **actually loaded**: `naturalWidth > 0`); a fake `.docx` refused by content; a real `.docx` (built in the script with `python-docx`) shows the cost estimate; indexing completes and the document appears; the uploaded document can be searched; **the key string is not found in any file under the data folder**; deleting a document; ending the session removes the session folder; the phone-sized viewport doesn't scroll sideways; no JavaScript errors.

**Rough edges** (honest list): after your other session redesigned the page, this file was edited to match (the "dialog" steps now close the source *panel*, and `dismiss_dialog` carries some leftover debugging output in its assertion message). One assertion in `path_and_citation` (`assert chip.count() >= 0`) is meaningless because it can never fail. And the **test has not been re-run since your redesign** as far as I know.

---

## `scripts/build_samples.py`: creating the sample library

Run: `python scripts/build_samples.py` (writes the Word files) and `--index` (also indexes them). It builds the three sample documents from scratch so nothing is downloaded or copyrighted.

- `bar_chart_png(values)`: draws the handbook's chart **pixel by pixel** in a Python list of lists (white background, two axis lines, one coloured rectangle per value scaled to the largest), then encodes it as a PNG with the same chunk/CRC/zlib method as `conftest.py`. Bars are scaled with integer arithmetic (`plot_height * value // max(values)`).
- `STATION`, `REPORT`, `SOLAR`: the content of each document as a title and a list of tuples such as `("h1", "Overview")`, `("p", "text...")`, `("table", header, rows)` and `("chart", values, caption)`.
- `build_docx(path, title, items)`: loops over the tuples and calls `python-docx`: `add_heading` (level 0 = the Title style, levels 1 and 2 = Heading 1/2), `add_paragraph`, `add_table` with the `Table Grid` style, `add_picture` (the generated chart from memory with `io.BytesIO`).
- `main()`: argument flags `--force` (overwrite existing files), `--index`, `--no-llm` (index without AI, for tests) and `--out` (where to write the index, default `samples/index`). It skips files that already exist, **because regenerating a file changes its content fingerprint and therefore its document id**, which would make the committed index stale. With `--index` it indexes every `.docx` in `samples/source` into `--out` using `dataclasses.replace(get_settings(), data_dir=args.out)`, which makes a copy of the frozen settings with one field changed.

The indexing step uses **your key** (the real model) unless the fake factory variable is set.

---

## Rough edges in the project, collected

These came up while writing this walkthrough. None break anything today; they are the places that are not as thorough as they should be:

1. **Documents with no heading styles get no real structure** (file 02): the "generate a table of contents with AI" idea is not built.
2. **PDF reading is basic** (no running headers, tables or images yet).
3. **Two constants must match but nothing enforces it:** the 12,000-character cut-off in `estimate.py` and `summarize.py`.
4. **A few leftovers:** the unused `OPEN_FAIL_KEY` in `fake_llm.py`; the dictionary values of `ALLOWED` in `uploads.py` are never read (only the keys are); the no-op assertion and debug text in `run_e2e.py`.
5. **Two things named `Image`:** the `Image` block dataclass in `blocks.py` and Pillow's `Image` (used in `summarize.py`) share a name; they never meet in one file today, but it is confusing.
6. **No rate limiting, no HTTPS handling, state in memory** (file 05).
7. **Citations are checked to exist and to have been read, but not to support the sentence they are on** (file 03).
8. **Nothing has run against the real Claude yet.** Summary quality, routing quality and cost are all unmeasured.
9. **The browser test predates your UI redesign.**
