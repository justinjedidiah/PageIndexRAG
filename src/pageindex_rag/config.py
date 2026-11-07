"""Settings, read from environment variables (and .env)."""

import os
from dataclasses import dataclass
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
    samples_dir: Path = PROJECT_ROOT / "samples" / "index"

    @property
    def sessions_dir(self) -> Path:
        return self.data_dir / "sessions"


def get_settings() -> Settings:
    from .providers import PROVIDERS

    provider = os.getenv("PAGEINDEX_PROVIDER", "anthropic")
    if provider not in PROVIDERS:
        raise ValueError(f"PAGEINDEX_PROVIDER must be one of {', '.join(PROVIDERS)}, not {provider!r}.")
    # No default models: the web app's visitors choose theirs; the dev CLI reads them from the environment.
    index_model = os.getenv("PAGEINDEX_INDEX_MODEL", "")
    return Settings(
        data_dir=Path(os.getenv("PAGEINDEX_DATA_DIR", "data")),
        provider=provider,
        index_model=index_model,
        vision_model=os.getenv("PAGEINDEX_VISION_MODEL", index_model),
        agent_model=os.getenv("PAGEINDEX_AGENT_MODEL", ""),
        max_leaf_tokens=int(os.getenv("PAGEINDEX_MAX_LEAF_TOKENS", "2000")),
        agent_max_steps=int(os.getenv("PAGEINDEX_AGENT_MAX_STEPS", "15")),
        min_leaf_tokens=int(os.getenv("PAGEINDEX_MIN_LEAF_TOKENS", "150")),
        agent_listing_depth=min(3, max(1, int(os.getenv("PAGEINDEX_LISTING_DEPTH", "2")))),
        session_ttl_seconds=int(os.getenv("PAGEINDEX_SESSION_TTL_SECONDS", str(2 * 60 * 60))),
        max_upload_bytes=int(os.getenv("PAGEINDEX_MAX_UPLOAD_MB", "15")) * 1024 * 1024,
        max_session_docs=int(os.getenv("PAGEINDEX_MAX_SESSION_DOCS", "5")),
        max_pdf_pages=int(os.getenv("PAGEINDEX_MAX_PDF_PAGES", "300")),
        max_doc_tokens=int(os.getenv("PAGEINDEX_MAX_DOC_TOKENS", "500000")),
        index_workers=int(os.getenv("PAGEINDEX_INDEX_WORKERS", "6")),
        samples_dir=Path(os.getenv("PAGEINDEX_SAMPLES_DIR", str(PROJECT_ROOT / "samples" / "index"))),
    )
