"""Process-wide settings and the session manager, shared by the state, the routes and the sweep."""

from pageindex_rag.config import get_settings
from pageindex_rag.sessions import SessionManager

settings = get_settings()
sessions = SessionManager(settings.sessions_dir, settings.session_ttl_seconds)
