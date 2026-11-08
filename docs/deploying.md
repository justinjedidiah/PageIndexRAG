# Deploying TOC RAG

The app runs as one Docker container: the backend serves the built pages too, so a single port is enough. The live demo runs on [Railway](https://railway.com); any host that runs a Dockerfile works the same way.

## The rules

- **Never set an API key on the server** (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`). The app only ever uses the key each visitor enters.
- **Run exactly one instance.** Each visitor's session (key, chat, uploads) and the rate limits live in that one process's memory and disk. A second replica would not know the visitors of the first.
- **No volume, no Redis.** Uploads live inside the container and are deleted after 2 hours, so nothing needs to survive a restart. Don't set `REDIS_URL`: visitor state, the key included, must stay in memory.
- **Set `API_URL`** to the public address (for example `https://toc-rag.example.com`) so the page knows where to reach the backend. On Railway it is filled in from the public domain.
- **Memory:** about 220 MB idle and up to about 1.5 GB at the busiest (two large PDFs read at once), measured on a heavy 300-page PDF. Give it 2 GB, or 1 GB with `TOC_RAG_MAX_PARALLEL_READS=1`.

## Railway

`railway.json` tells Railway to build the `Dockerfile` and to wait up to 5 minutes for `/ping` on start (the pages are built when the container starts).

1. **New project → Deploy from GitHub repo**, and pick this repository.
2. In the service: **Settings → Networking → Generate domain**. The pages are built with that address, so redeploy once if you generate it after the first deploy.
3. Set no variables. Railway provides `PORT` (the app listens on whatever it is, often 8080) and `RAILWAY_PUBLIC_DOMAIN` itself.
4. Check: 1 replica, no volume, a memory limit. Leave Serverless (sleeping) off if a fast first load matters: a cold start rebuilds the pages.

The home page is at the domain's root and the app at `/app`.

## Any other host

```bash
docker build -t toc-rag .
docker run -p 8000:8000 -e API_URL=https://your.domain toc-rag
```

Put it behind HTTPS. Rate limits identify visitors by the `X-Real-IP` or `X-Forwarded-For` header your proxy sets, so check that your proxy sets one (Railway does).

## Settings

All optional. The defaults suit a small public demo.

| Variable | Default | What it does |
| --- | --- | --- |
| `TOC_RAG_MAX_UPLOAD_MB` | 15 | Largest file accepted |
| `TOC_RAG_MAX_PDF_PAGES` | 300 | Longest PDF accepted, checked before reading |
| `TOC_RAG_MAX_DOC_TOKENS` | 500000 | Most text one document may hold |
| `TOC_RAG_MAX_SESSION_DOCS` | 5 | Documents one visitor can keep |
| `TOC_RAG_SESSION_TTL_SECONDS` | 7200 | How long a visitor's uploads live |
| `TOC_RAG_MAX_SESSIONS` | 100 | Visitors with uploads at once |
| `TOC_RAG_MAX_PARALLEL_READS` | 2 | Documents read at once, for everyone together |
| `TOC_RAG_READ_TIMEOUT_SECONDS` | 120 | A read taking longer is stopped (a heavy 300-page PDF takes about 70 s on 2 CPUs) |
| `TOC_RAG_UPLOADS_PER_IP` | 20 | Uploads one visitor may send per 10 minutes |
| `TOC_RAG_PAGE_RENDERS_PER_IP` | 300 | PDF page pictures one visitor may load per 10 minutes |

To check the rate limits identify visitors correctly after a deploy: set `TOC_RAG_UPLOADS_PER_IP=2`, upload three files from one device (the third is refused), then upload from a phone on mobile data (it should work). Remove the variable afterwards.
