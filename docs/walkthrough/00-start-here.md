# Code walkthrough: start here

This folder explains the whole project down to the code, one area per file. Read them in order; each builds on the one before.

| File | Covers |
|---|---|
| **00-start-here.md** (this file) | The map, the two journeys through the code, a glossary |
| [01-data-model.md](01-data-model.md) | `models.py`, `blocks.py`, `storage.py`: the shapes of the data and how it is saved |
| [02-indexing.md](02-indexing.md) | `indexing/*`: turning a file into a searchable tree |
| [03-agent.md](03-agent.md) | `agent/*`: how a question is answered (the loop, the tools, how citations are parsed) |
| [04-support-modules.md](04-support-modules.md) | `config`, `llm`, `errors`, `sessions`, `uploads`, `library`, `outline`, `cli` |
| [05-web-app.md](05-web-app.md) | `toc_app/*`: the website (Reflex) |
| [06-tests-and-scripts.md](06-tests-and-scripts.md) | Tests, the offline fake model, the browser test, the sample builder, and a list of rough edges |

These explain the code **as it is now**, including the parts your other session redesigned. If code changes, the explanations can fall out of date; the code is always the truth.

---

## The idea in one paragraph

Instead of chopping documents into chunks and searching by similarity (vector RAG), the project turns each document into a **tree of sections** (like a table of contents), has an AI model write a short **summary of every section** once, and then answers questions with an **agent**: Claude is given a few tools (open a document, list a section's subsections, read a section, go back up) and walks the tree itself, reading summaries to choose where to go, then reading the section that should hold the answer. You can see every step it took.

---

## The map of the project

```
TOC-RAG/
├─ src/toc_rag/           THE CORE. No web code in here. Usable from a script.
│   ├─ models.py                the data shapes: Node, DocumentIndex, ImageRecord...      (01)
│   ├─ storage.py               save/load those shapes as JSON files                      (01)
│   ├─ indexing/
│   │   ├─ blocks.py            flat "blocks" every file reader produces                  (01, 02)
│   │   ├─ docx_extract.py      Word -> blocks                                            (02)
│   │   ├─ markdown_extract.py  Markdown -> blocks (developer use)                        (02)
│   │   ├─ tree.py              blocks -> tree of sections                                (02)
│   │   ├─ refine.py            merge tiny sections, fold small ones, then split big ones (02)
│   │   ├─ split.py             cut very large sections into balanced parts               (02)
│   │   ├─ preview.py           free previews (first sentences) when summaries are off    (02)
│   │   ├─ options.py           presets: Free / Budget / Full                             (02)
│   │   ├─ estimate.py          how many Claude calls will indexing need?                 (02)
│   │   ├─ summarize.py         the Claude calls: describe images, summarize sections     (02)
│   │   └─ pipeline.py          runs the two indexing stages                              (02)
│   ├─ agent/
│   │   ├─ navigator.py         what the agent's tools actually do                        (03)
│   │   └─ graph.py             the agent loop (LangGraph)                                (03)
│   ├─ config.py                all settings, from environment variables                  (04)
│   ├─ providers.py             Anthropic / OpenAI / Gemini: models, defaults, model lists (04)
│   ├─ llm.py                   thin wrapper: chat_model() and the settings' LLMConfig    (04)
│   ├─ errors.py                safe error messages                                       (04)
│   ├─ sessions.py              per-visitor folders that expire                           (04)
│   ├─ uploads.py               checking uploaded files                                   (04)
│   ├─ library.py               what one visitor sees: samples + their own documents      (04)
│   ├─ outline.py               outline rows and section text for display                (04)
│   └─ cli.py                   developer command line (`tocrag ...`)                  (04)
├─ toc_app/               THE WEBSITE (Reflex). Imports the core, never the reverse.
│   ├─ state.py                 every button's behaviour                                  (05)
│   ├─ views.py                 what the app page (/app) looks like                       (05)
│   ├─ home.py                  the product page at /, no state                           (05)
│   ├─ files.py                 serves document images                                    (05)
│   ├─ viewmodels.py            small data shapes for the page                            (05)
│   ├─ runtime.py               shared settings + session manager                         (05)
│   └─ toc_app.py               the app, its two pages, the 5-minute cleanup loop         (05)
├─ rxconfig.py, assets/app.css  Reflex configuration, theme, stylesheet                   (05)
├─ samples/source, samples/index   the 3 sample documents and their indexes               (06)
├─ scripts/build_samples.py     makes the sample documents and indexes them               (06)
├─ tests/                       62 unit tests, the fake model, the browser test           (06)
├─ data/                        runtime files (ignored by git): visitor sessions          
├─ CLAUDE.md, DESIGN.md         notes for Claude Code and design decisions
└─ docs/walkthrough/            these files
```

**The most important rule in the layout:** the core never imports the website. That is why the core can be unit-tested in 5 seconds and used from the command line.

---

## Journey 1: a document goes in

Follow this with the files open. (`->` means "calls".)

```
Visitor drops a .docx on the page and presses Upload
 -> state.handle_upload                                    (05)
     -> safe_filename, validate_upload                     (04)  refuse fakes, zips, zip bombs, oversize
     -> sessions.ensure(token)                             (04)  create the visitor's private folder (2-hour clock starts)
     -> pipeline.build_index                               (02)  STAGE 1, free
          -> extract_docx            file -> blocks
          -> build_tree              blocks -> nested sections
          -> refine_tree             merge tiny sections, split oversized ones
          -> store.save_image        images written to disk
     -> estimate_index                                     (02)  "about 17 Claude calls"
     -> writes pending.json, shows the cost and the Index it / Cancel buttons

Visitor presses "Index it"
 -> state.confirm_index (background task)                  (05)
     -> pipeline.enrich_index                              (02)  STAGE 2, uses the visitor's key
          -> describe_images     Claude describes each image          (parallel)
          -> summarize_tree      Claude summarizes every section,     (parallel per level,
                                 deepest first                          children before parents)
     -> store.save_index                                   (01)  index.json + library.json
     -> page shows the document in the library
```

## Journey 2: a question comes out

```
Visitor types a question and presses Enter
 -> state.ask (background task)                            (05)
     -> Library.for_session(...).indexes()                 (04)  samples + the visitor's documents
     -> stream_ask(indexes, question, history, api_key)    (03)
          -> LibraryNavigator + the agent loop (LangGraph)
               loop:  Claude picks a tool -> navigator runs it -> result goes back to Claude
                      open_document / list_children / read_node / go_up
               ends:  a plain-text reply with [doc:node] markers, or the 15-step budget runs out
          -> yields a Step for each action, then the final AnswerResult
     -> each Step (with the model's one-line note) is added to the message live; the answer appears with citation chips
Visitor clicks a citation
 -> state.open_source -> node_markdown                     (05, 04)  the section text appears in the side panel
```

---

## Glossary

| Term | Meaning here |
|---|---|
| **RAG** | "Retrieval-augmented generation": answer questions by first *finding* relevant text and giving it to the AI. |
| **Vector RAG** | The usual way: find text by numeric similarity. Not used here. |
| **Index / indexing** | Preparing a document once so it can be searched: tree + summaries. Saved as `index.json`. |
| **Node** | One section of a document (a heading and its text). Documents are trees of nodes. |
| **Leaf** | A node with no subsections. |
| **Summary** | 2-4 sentences Claude writes about a node at index time. The agent navigates by these. |
| **Token** | The unit AI models read and bill by; roughly 3/4 of an English word. |
| **LLM** | Large language model. The visitor chooses Anthropic (Claude), OpenAI or Google Gemini, and the models. Where these pages say "Claude", read "the chosen model": Claude is the default provider. |
| **Haiku / Sonnet** | Two Claude sizes. Haiku (cheap, fast) does indexing; Sonnet (stronger) runs the agent. |
| **Tool calling** | The model asks your code to run a function (e.g. `read_node`) and receives the result. |
| **Agent** | A loop of "model chooses a tool, code runs it, repeat until done". |
| **Structured output** | Asking the model to reply with data matching a defined shape instead of prose. |
| **BYOK** | "Bring your own key": the visitor supplies their own Anthropic, OpenAI or Gemini API key and pays for their own usage. |
| **Session** | A visitor's private, temporary workspace (a folder, deleted after 2 hours or on "End session"). |
| **Reflex** | The Python framework used to build the website. |
| **State** | Reflex's server-side object holding what each visitor's page currently shows. |
| **Event handler** | A method that runs on the server when something happens in the browser (a click, a submit). |
| **Websocket** | A permanent two-way connection between browser and server, used to push live updates. |
| **Generator** | A Python function that `yield`s values one at a time (used to stream agent steps). |
| **Pydantic** | A library of typed classes that validate themselves and convert to/from JSON. |
| **Dataclass** | A lightweight class that only stores fields. |
| **Thread / `asyncio.to_thread`** | Running slow work (network calls, file work) beside the main program so the website does not freeze. |
| **Fixture / mock / fake** | Test helpers: a stand-in that behaves like the real thing so a test can run fast and free. |

---

## Where to look when you want to change something

| You want to... | Look at |
|---|---|
| Change how sections are summarized | `summarize.py` `_prompt` (02) |
| Change what the agent is told | `graph.py` `SYSTEM_PROMPT` (03) |
| Change how many steps/parallel calls/limits | `config.py` (04) and the env table there |
| Improve PDF reading | `indexing/pdf_extract.py` (02): lines to paragraphs, bookmarks; page ranges in `indexing/pages.py` |
| Change the page's look | `views.py` (app), `home.py` (home page), `rxconfig.py`, `assets/app.css` (05) |
| Change what a button does | the handler in `state.py` (05) |
| Change the session lifetime | `TOC_RAG_SESSION_TTL_SECONDS` (04) |
| Add a test | the matching `tests/test_*.py` (06) |
