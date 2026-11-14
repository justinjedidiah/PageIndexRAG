"""Settings, read from environment variables (and .env)."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    index_model: str
    vision_model: str
    agent_model: str
    max_leaf_tokens: int
    agent_max_steps: int
    min_leaf_tokens: int = 150
    provider: str = "anthropic"  # for the CLI and dev; the web app uses each visitor's own choice
    agent_listing_depth: int = 2  # tree levels one listing shows (1-3); deeper levels show titles only
    # Web app
    session_ttl_seconds: int = 2 * 60 * 60
    max_upload_bytes: int = 15 * 1024 * 1024
    max_session_docs: int = 5
    max_pdf_pages: int = 300  # checked before reading the text: a small file can hold thousands of pages
    max_doc_tokens: int = 500_000  # of extracted text, any file type: what really costs time to index
    index_workers: int = 6
    # Abuse limits for the public app, all visitors together unless said otherwise.
    max_parallel_reads: int = 2  # documents read (parsed) at once; reading a PDF is the CPU-heavy step
    read_timeout_seconds: int = 120  # a read taking longer is stopped (it runs in its own process)
    max_sessions: int = 100  # live sessions holding uploads; caps disk use (each holds up to max_session_docs)
    uploads_per_ip: int = 20  # uploads one address may send per 10 minutes
    page_renders_per_ip: int = 300  # PDF page pictures one address may have rendered per 10 minutes
    samples_dir: Path = PROJECT_ROOT / "samples" / "index"

    @property
    def sessions_dir(self) -> Path:
        return self.data_dir / "sessions"


@dataclass(frozen=True)
class DevKey:
    """A provider and key from the environment, to fill the web app's key form in local dev."""

    provider: str
    key: str = field(repr=False)  # a printed DevKey never shows the key


def dev_key_prefill() -> DevKey | None:
    """The key to fill in for local dev (`reflex run`, `docker compose up`), or None.

    Only when `TOC_RAG_PROVIDER` is set explicitly and that provider's key variable is too, and never on
    Railway. The public server sets neither, so every visitor there brings their own key.
    """
    from .providers import PROVIDERS

    provider = os.getenv("TOC_RAG_PROVIDER", "").strip()
    if provider not in PROVIDERS or os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_ENVIRONMENT_NAME"):
        return None
    key = os.getenv(PROVIDERS[provider].key_env, "").strip()
    return DevKey(provider, key) if key else None


def get_settings() -> Settings:
    from .providers import PROVIDERS

    provider = os.getenv("TOC_RAG_PROVIDER", "").strip() or "anthropic"  # empty (as compose passes it) = unset
    if provider not in PROVIDERS:
        raise ValueError(f"TOC_RAG_PROVIDER must be one of {', '.join(PROVIDERS)}, not {provider!r}.")
    # No default models: the web app's visitors choose theirs; the dev CLI reads them from the environment.
    index_model = os.getenv("TOC_RAG_INDEX_MODEL", "")
    return Settings(
        data_dir=Path(os.getenv("TOC_RAG_DATA_DIR", "data")),
        provider=provider,
        index_model=index_model,
        vision_model=os.getenv("TOC_RAG_VISION_MODEL", index_model),
        agent_model=os.getenv("TOC_RAG_AGENT_MODEL", ""),
        max_leaf_tokens=int(os.getenv("TOC_RAG_MAX_LEAF_TOKENS", "2000")),
        agent_max_steps=int(os.getenv("TOC_RAG_AGENT_MAX_STEPS", "15")),
        min_leaf_tokens=int(os.getenv("TOC_RAG_MIN_LEAF_TOKENS", "150")),
        agent_listing_depth=min(3, max(1, int(os.getenv("TOC_RAG_LISTING_DEPTH", "2")))),
        session_ttl_seconds=int(os.getenv("TOC_RAG_SESSION_TTL_SECONDS", str(2 * 60 * 60))),
        max_upload_bytes=int(os.getenv("TOC_RAG_MAX_UPLOAD_MB", "15")) * 1024 * 1024,
        max_session_docs=int(os.getenv("TOC_RAG_MAX_SESSION_DOCS", "5")),
        max_pdf_pages=int(os.getenv("TOC_RAG_MAX_PDF_PAGES", "300")),
        max_doc_tokens=int(os.getenv("TOC_RAG_MAX_DOC_TOKENS", "500000")),
        index_workers=int(os.getenv("TOC_RAG_INDEX_WORKERS", "6")),
        max_parallel_reads=max(1, int(os.getenv("TOC_RAG_MAX_PARALLEL_READS", "2"))),
        read_timeout_seconds=max(1, int(os.getenv("TOC_RAG_READ_TIMEOUT_SECONDS", "120"))),
        max_sessions=int(os.getenv("TOC_RAG_MAX_SESSIONS", "100")),
        uploads_per_ip=int(os.getenv("TOC_RAG_UPLOADS_PER_IP", "20")),
        page_renders_per_ip=int(os.getenv("TOC_RAG_PAGE_RENDERS_PER_IP", "300")),
        samples_dir=Path(os.getenv("TOC_RAG_SAMPLES_DIR", str(PROJECT_ROOT / "samples" / "index"))),
    )
