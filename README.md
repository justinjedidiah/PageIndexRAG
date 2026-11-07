# TOC RAG

**Retrieval without vectors.** An AI agent answers questions about your documents by navigating their table of contents, the way a person would: open the document, scan the outline, step into the right section, read it, and cite it. No chunking, no embeddings, no similarity search.

Upload a Word document or a PDF (or try the sample library), bring your own API key for **Anthropic, OpenAI or Google Gemini**, and chat. Every answer shows the path the agent took through the documents and links each claim to the section it came from.

<!-- Screenshot: add docs/images/screenshot.png (a real answer with its navigation path and citations) and uncomment:
![An answer with the agent's navigation path and citations](docs/images/screenshot.png)
-->

> **Status:** works end to end locally. Word (.docx) and text PDFs are supported (PDF: bookmarks or a printed contents page, page-number citations with a view of the pages, charts and pictures as images, ruled tables, two columns). Ready to deploy on Railway (see below).

## How it works

1. **Index once.** Each document becomes a tree of sections built from its own headings. Sections too big to read in one go are split; runs of tiny ones are merged. The original outline is kept wherever it is already good.
2. **Summarise.** A small model writes a short summary of every section, saying what questions it can answer, and describes the images. (Or skip this entirely: the free preset shows each section's first sentences instead.)
3. **Navigate.** For each question, the agent gets a few tools (open a document, list a section's subsections, read a section, go back up) and walks the tree, choosing branches by their summaries.
4. **Cite.** The answer cites sections as it goes. A citation is only kept if the agent actually read that section, so the app never shows a source the model only guessed at.

### Why not vector RAG?

| | Vector RAG | This project |
|---|---|---|
| Finds text by | similarity of embedded chunks | following the document's own structure |
| Context the model sees | isolated chunks | whole sections, in their place in the outline |
| Explains its choice | no (a similarity score) | yes: every step of the path is shown |
| Needs | an embedding model and a vector store | nothing beyond the chat model |

The trade-off: navigation takes a few model calls per question, and it depends on documents having (or being given) a sensible structure. Comparing the two on accuracy, cost and speed is on the roadmap.

## Features

- **Bring your own key** for Anthropic, OpenAI or Google Gemini. The app lists the models your key can use and you pick one for indexing and one for chat. The key stays in server memory for your session only: never written to disk or logs.
- **Indexing presets with a cost estimate before anything is spent:** Free (no model calls at all), Budget (short summaries) and Full (detailed summaries and image descriptions), plus advanced options for how the tree is refined.
- **Live answers:** the agent's navigation path streams as it works, then the answer, with clickable citations that open the source section, and images from the documents shown inline.
- **Your library is private and temporary:** each visitor gets their own space, deleted when they end the session or after 2 hours (with a countdown).
- **Sample library** of three documents to try without uploading anything.

## Run it locally

Needs Python 3.11+ and Node 22.22 or newer (for the Reflex frontend).

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev,app]"     # on macOS/Linux: .venv/bin/pip
reflex run
```

Open http://localhost:3000 for the home page, or http://localhost:3000/app for the app itself, and paste an API key there. No `.env` is needed for the web app: it only ever uses the key a visitor enters.

## Run with Docker

```bash
docker compose up --build      # stop with: docker compose down
```

or without Compose:

```bash
docker build -t toc-rag .
docker run -p 8000:8000 toc-rag
```

Open http://localhost:8000 (the app is at `/app`; stop `reflex run` first if it is running, since it uses port 8000 too). The first start takes a minute or two while the pages are built. One container serves both the page and the backend, on the port in `PORT` (default 8000).

When it runs on a public address, set `API_URL` to that address (for example `https://toc-rag.example.com`), so the page knows where to reach the backend. On Railway this is filled in automatically from the app's public domain. Never set an API key on the server: the app only uses the key each visitor enters. Uploads live inside the container and are deleted after 2 hours, so no volume is needed.

## Deploy on Railway

`railway.json` tells Railway to build the `Dockerfile` and to wait up to 5 minutes for `/ping` on start (the pages are built when the container starts).

1. Push the repository to GitHub.
2. In Railway: **New project → Deploy from GitHub repo**, and pick this repository.
3. In the service: **Settings → Networking → Generate domain**. The page is built with that address, so redeploy once after generating it.
4. Set no variables: Railway provides `PORT` and `RAILWAY_PUBLIC_DOMAIN` itself. Never add an API key.

The home page is at the domain's root and the app at `/app`.

## Tests

```bash
.venv/Scripts/python -m pytest                # unit tests, no API key needed
.venv/Scripts/python tests/e2e/run_e2e.py     # the whole app in a real Chrome, with an offline fake model
```

The end-to-end run starts the app with a stand-in model (`tests/fake_llm.py`), drives it like a visitor (keys, uploads, indexing, chat, citations, ending the session) and saves screenshots.

## Project layout

```
src/toc_rag/
  indexing/     Word/PDF/Markdown -> section tree -> refine (split, merge) -> summaries
  agent/        the navigation tools and the LangGraph agent loop
  providers.py  Anthropic, OpenAI and Gemini: models, model lists
toc_app/  the Reflex web app
tests/          unit tests, the offline fake model, the end-to-end browser run
samples/        the sample documents and their indexes
```

## Documentation

- [DESIGN.md](DESIGN.md): the design, its decisions and the roadmap.
- [docs/walkthrough/](docs/walkthrough/00-start-here.md): a guided tour of every module.

## Roadmap

- Generated structure for documents without headings
- An evaluation against a vector-RAG baseline

## Credits

Inspired by [PageIndex](https://github.com/VectifyAI/PageIndex) by VectifyAI; an independent project, not affiliated with VectifyAI. Built with [Reflex](https://reflex.dev), [LangGraph](https://www.langchain.com/langgraph) and [LangChain](https://www.langchain.com).

<!-- Licence: none chosen yet. Add a LICENSE file and a line here when it is. -->
