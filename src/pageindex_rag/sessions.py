"""Per-visitor storage that deletes itself.

Each session gets a folder under the sessions directory, named by the client token.
A session lives for a fixed time from its creation, then it is wiped. Reflex gives apps
no reliable "visitor left" event (a dropped connection or a reload is not a departure),
so the expiry sweep is what guarantees cleanup; `delete` is the early exit.
"""

import json
import re
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .storage import Store

_TOKEN = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_MARKER = "session.json"


@dataclass(frozen=True)
class Session:
    token: str
    created: float
    expires_at: float
    was_reset: bool = False  # an earlier session with this token had expired and was wiped


def describe_duration(seconds: float) -> str:
    """'2 hours', '1 hour 5 minutes', '12 minutes', 'under a minute'."""
    minutes = int(seconds // 60)
    hours, minutes = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    return " ".join(parts) or "under a minute"


class SessionManager:
    def __init__(self, root: Path | str, ttl_seconds: int, clock: Callable[[], float] = time.time):
        self.root = Path(root)
        self.ttl = ttl_seconds
        self.clock = clock

    def session_dir(self, token: str) -> Path:
        if not _TOKEN.match(token):
            raise ValueError("Invalid session token.")
        return self.root / token

    def store(self, token: str) -> Store:
        return Store(self.session_dir(token))

    def uploads_dir(self, token: str) -> Path:
        return self.session_dir(token) / "uploads"

    def get(self, token: str) -> Session | None:
        """The live session for this token, or None if there is none or it has expired."""
        created = self._created(self.session_dir(token))
        if created is None or self.clock() - created >= self.ttl:
            return None
        return Session(token, created, created + self.ttl)

    def ensure(self, token: str) -> Session:
        """Return the live session, starting a new one (and wiping an expired one) if needed."""
        directory = self.session_dir(token)
        session = self.get(token)
        if session:
            return session
        was_reset = directory.exists()
        shutil.rmtree(directory, ignore_errors=True)
        directory.mkdir(parents=True)
        now = self.clock()
        (directory / _MARKER).write_text(json.dumps({"created": now}), encoding="utf-8")
        return Session(token, now, now + self.ttl, was_reset)

    def delete(self, token: str) -> None:
        shutil.rmtree(self.session_dir(token), ignore_errors=True)

    def sweep(self) -> int:
        """Delete every expired session folder. Returns how many were removed."""
        if not self.root.is_dir():
            return 0
        removed = 0
        now = self.clock()
        for directory in self.root.iterdir():
            if not directory.is_dir():
                continue
            created = self._created(directory)
            if created is None or now - created >= self.ttl:
                shutil.rmtree(directory, ignore_errors=True)
                removed += 1
        return removed

    @staticmethod
    def _created(directory: Path) -> float | None:
        marker = directory / _MARKER
        try:
            return float(json.loads(marker.read_text(encoding="utf-8"))["created"])
        except (OSError, ValueError, KeyError, TypeError):
            return None
