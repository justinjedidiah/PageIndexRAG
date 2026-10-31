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
    # Web app
    session_ttl_seconds: int = 2 * 60 * 60
    max_upload_bytes: int = 15 * 1024 * 1024
    max_session_docs: int = 5
    index_workers: int = 6
    samples_dir: Path = PROJECT_ROOT / "samples" / "index"

    @property
    def sessions_dir(self) -> Path:
        return self.data_dir / "sessions"


def get_settings() -> Settings:
    return Settings(
        data_dir=Path(os.getenv("PAGEINDEX_DATA_DIR", "data")),
        index_model=os.getenv("PAGEINDEX_INDEX_MODEL", "claude-haiku-5-5"),
        vision_model=os.getenv("PAGEINDEX_VISION_MODEL", "claude-haiku-5-5"),
        agent_model=os.getenv("PAGEINDEX_AGENT_MODEL", "claude-sonnet-5-5"),
        max_leaf_tokens=int(os.getenv("PAGEINDEX_MAX_LEAF_TOKENS", "2000")),
        agent_max_steps=int(os.getenv("PAGEINDEX_AGENT_MAX_STEPS", "15")),
        session_ttl_seconds=int(os.getenv("PAGEINDEX_SESSION_TTL_SECONDS", str(2 * 60 * 60))),
        max_upload_bytes=int(os.getenv("PAGEINDEX_MAX_UPLOAD_MB", "15")) * 1024 * 1024,
        max_session_docs=int(os.getenv("PAGEINDEX_MAX_SESSION_DOCS", "5")),
        index_workers=int(os.getenv("PAGEINDEX_INDEX_WORKERS", "6")),
        samples_dir=Path(os.getenv("PAGEINDEX_SAMPLES_DIR", str(PROJECT_ROOT / "samples" / "index"))),
    )
