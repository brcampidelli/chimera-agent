"""The URL that actually supplied the content of one fetch tool call.

A ContextVar, not a process-wide dict. Parallel reads run each call in its own copy of the context
(``chimera.core.agent``), so two calls in one run cannot overwrite each other's answer. The ledger
clears the value before it calls a tool and reads it after, so a value left by an earlier call on
the same thread is never attributed to this one. Empty means "unknown": the caller must treat the
content as untrusted, never fall back to the URL the model asked for.
"""

from __future__ import annotations

from contextvars import ContextVar

_FINAL_FETCH_URL: ContextVar[str] = ContextVar("chimera_final_fetch_url", default="")


def set_final_url(url: str | None) -> None:
    """Record the final URL of the fetch this call performed (``""`` when it is not known)."""
    _FINAL_FETCH_URL.set(str(url or ""))


def final_url() -> str:
    """The final URL recorded by the current call, or ``""`` if none was recorded."""
    return _FINAL_FETCH_URL.get()


def clear_final_url() -> None:
    """Forget any URL recorded before a new call starts."""
    _FINAL_FETCH_URL.set("")
