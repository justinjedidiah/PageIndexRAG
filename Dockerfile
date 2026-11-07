# PageIndex RAG: the Reflex web app in one container, frontend and backend on one port.
#
#   docker build -t pageindex-rag .
#   docker run -p 8000:8000 pageindex-rag            # then open http://localhost:8000
#
# Never pass an API key to this container: the app only ever uses the key each visitor enters.
# On a public host, set API_URL to the app's public address (on Railway it is filled in for you).

FROM node:24-bookworm-slim AS node

FROM python:3.13-slim-bookworm

# Node runs the frontend build (Reflex needs 22.22 or newer). Reflex downloads its own bun, which needs curl and unzip.
COPY --from=node /usr/local/bin/node /usr/local/bin/node
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl unzip ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TELEMETRY_ENABLED=false \
    PORT=8000

RUN useradd --create-home --uid 1000 app \
    && mkdir /app && chown app:app /app
WORKDIR /app

# The Python packages first, so a change to the app's code does not reinstall them.
COPY pyproject.toml ./
COPY src ./src
RUN pip install ".[app]"

COPY --chown=app:app . .
USER app

# Warm-up build: downloads bun and the frontend's packages into the image, so starting the container only
# rebuilds the pages (with the right API_URL, which is only known at run time).
RUN reflex export --frontend-only --no-zip --env prod

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
    CMD curl -fsS "http://localhost:${PORT}/ping" || exit 1

# Prod mode serves the built frontend from the backend, so one port is enough.
CMD ["sh", "-c", "exec reflex run --env prod --frontend-port ${PORT} --backend-port ${PORT}"]
