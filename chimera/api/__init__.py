"""Desktop app backend: the FastAPI HTTP+SSE API the React frontend (``apps/desktop``) consumes.

Opt-in (``pip install chimera-agent[desktop]``). Importing this package pulls FastAPI; the core CLI
never imports it unless the user runs ``chimera app``.
"""

from __future__ import annotations

from chimera.api.app import build_api_app
from chimera.api.sessions import SessionManager, SessionMeta, SessionStore

__all__ = ["build_api_app", "SessionManager", "SessionMeta", "SessionStore"]
