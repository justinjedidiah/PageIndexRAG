# PageIndexRAG

Vectorless RAG: an LLM agent navigates a document's table-of-contents tree to find the right section, instead of vector similarity. Supports PDF and Word, with or without a ToC (generated when missing). The product is a public BYOK portfolio web app (chat over an uploaded library); visitors bring an Anthropic, OpenAI or Google Gemini key. The CLI is dev-only. Never write a visitor's API key to disk or logs.

Full design and decisions: @DESIGN.md
Code-level walkthrough of every module (read `docs/walkthrough/00-start-here.md` first); it can lag behind the code, so the code wins.

## Stack
- Python, Reflex (UI), LangGraph (agent)

## Layout
- `src/pageindex_rag/indexing/`: extractors (docx, pdf, markdown) -> blocks -> tree -> refine (merge/fold, then `split.py`) -> PDF page ranges (`pages.py`) -> previews or summarize -> pipeline. PDF reading uses pypdfium2 + pdfplumber (never PyMuPDF: AGPL); `pdf_layout.py` finds charts, tables, pictures and columns. pdfium calls go through `PDFIUM_LOCK` (not thread-safe). `options.py` holds the presets (Free / Budget / Full) over `IndexOptions` in `models.py`
- `src/pageindex_rag/agent/`: `Navigator` (one document), `LibraryNavigator` (many documents, step trace) and the LangGraph loop in `graph.py` (`stream_agent` yields steps then the answer)
- `src/pageindex_rag/models.py`: Node, Location, ImageRecord, DocumentIndex, LibraryEntry
- `src/pageindex_rag/`: also `sessions.py` (per-visitor folders, 2h expiry sweep), `uploads.py` (pdf/docx validation), `library.py` (samples + session docs), `outline.py`, `errors.py` (provider-neutral safe error messages), `providers.py` (Anthropic / OpenAI / Gemini: model creation and live model listing, no default models; `LLMConfig` = provider + key + models), `llm.py` (thin wrapper)
- `pageindex_app/`: Reflex UI: `state.py` (events), `views.py` (page), `files.py` (image route), `runtime.py`. Imports the core package, never the reverse. State helper methods start with `_` so Reflex does not make them events.

## Commands
- Setup: `python -m venv .venv`, then `.venv/Scripts/pip install -e ".[dev,app]"`; copy `.env.example` to `.env` and set the key for `PAGEINDEX_PROVIDER` (default `anthropic`: `ANTHROPIC_API_KEY`; or `OPENAI_API_KEY`, `GOOGLE_API_KEY`)
- Tests (no API key needed): `.venv/Scripts/python -m pytest`
- Dev-only CLI: `pageindex index <file> [--preset free|budget|full] [--small-sections off|merge|fold] [--no-split] [--max-leaf N] [--min-leaf N] [--summaries llm|preview] [--summary-length short|detailed] [--no-images] [--no-llm]`, `pageindex list`, `pageindex tree <doc_id>`, `pageindex ask "<question>" [--doc <doc_id>] [--listing-depth 1-3]`
- UI: `reflex run` (needs Node 22.22 or newer: Reflex 0.10's frontend refuses older Node)
- Sample library: `python scripts/build_samples.py --index` (needs a real key and `PAGEINDEX_INDEX_MODEL`; writes `samples/index`)
- End-to-end UI check in real Chrome with the offline fake model, no key needed: `.venv/Scripts/python tests/e2e/run_e2e.py [output_dir]`
- Reflex gotchas: `reflex run` restarts the backend when any file under the project root changes (only .json/.txt and similar are ignored), so `rxconfig.py` excludes the data folder from hot reload; don't write runtime files elsewhere in the project. Never start a second `reflex run` in this folder while one is running: it rewrites `.web` and points the open page at the wrong backend. Keep objects out of state that worker threads mutate (Reflex proxies them; store ids and reload from disk). Put `rx.markdown` in an `@rx.memo` component, never inline in a `foreach` or `cond` (it calls a hook while building).
- Offline model hook: `PAGEINDEX_LLM_FACTORY=tests.fake_llm:make_model`. Never set `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` or `GOOGLE_API_KEY` on a public server: the web app must only use the visitor's key. Don't hard-code model names: the visitor picks both models from the list their key can use (`providers.list_models`); nothing is pre-selected. The dev CLI and `build_samples.py` need `PAGEINDEX_INDEX_MODEL` / `PAGEINDEX_AGENT_MODEL` in `.env`.

## Rules
- Indexing code must not import Reflex; keep it testable from a plain script.
- The original ToC is stored as data (`source_toc`) but the navigable tree is our own. Keep it shallow (each level costs the agent a tool call) and change the original ToC only where a section breaks a limit; every change is a user option.
- Normalize node content to plain text; keep citation info (page range or heading path) in a separate `location` field.
- No OCR in v1.
- Uploads in the UI are strictly .pdf and .docx, validated by content. Visitor libraries live on the server per session and are deleted at session end or after 2 hours.
- `samples/` is committed: `samples/source` holds the sample documents and `samples/index` their indexes.
