"""Process-wide settings and the session manager, shared by the state, the routes and the sweep."""

from toc_rag import redact
from toc_rag.config import get_settings
from toc_rag.sessions import SessionManager

redact.install()  # first thing the app does: no log line, from any library, carries a key or a client token

settings = get_settings()
sessions = SessionManager(settings.sessions_dir, settings.session_ttl_seconds)
