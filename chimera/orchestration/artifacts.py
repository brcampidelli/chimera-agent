"""Artifact store: bulky worker output lives on disk, the orchestrator gets a reference.

Evidence (Anthropic multi-agent system): a subagent may burn tens of thousands of
tokens internally but should return only a distilled summary; bulky artifacts
"bypass the coordinator" via persistent storage and come back as references. This
module is that store, plus :func:`build_envelope` — the single place where a raw
worker output becomes a bounded :class:`~chimera.orchestration.spec.ResultEnvelope`.

Design notes:
- Content-addressed (sha256) inside a per-run namespace: identical outputs dedup
  to one file; refs are relative, portable paths.
- Small results are NOT taxed: if the raw output fits the summary cap it *is* the
  summary and nothing is written to disk.
- Pure pathlib — Windows-safe.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from chimera.orchestration.spec import SUMMARY_MAX_CHARS, ResultEnvelope, TaskSpec
from chimera.telemetry import get_logger

_log = get_logger("orchestration.artifacts")

_TRUNCATION_MARKER = "[truncated — full output in evidence]"


@dataclass(frozen=True)
class ArtifactRef:
    """A reference to one stored artifact. ``path`` is relative to the store root."""

    path: str
    sha256: str
    chars: int


class ArtifactStore:
    """Content-addressed file store for delegation artifacts.

    ``root`` is typically ``settings.home / "artifacts"``. Use :meth:`for_run` to
    namespace one orchestration run's artifacts under a subdirectory so runs can
    be inspected (and cleaned) independently.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def for_run(self, run_id: str) -> ArtifactStore:
        """A store namespaced under ``root/run_id`` (created lazily on first put)."""
        safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in run_id) or "run"
        return ArtifactStore(self.root / safe)

    def put(self, content: str, *, kind: str = "output", task_id: str = "") -> ArtifactRef:
        """Store ``content``; identical content dedups to the same file."""
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        safe_task = "".join(c if c.isalnum() or c in "-_." else "-" for c in task_id)
        stem = f"{safe_task}-{kind}" if safe_task else kind
        name = f"{stem}-{digest[:12]}.txt"
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / name
        if not target.exists():
            target.write_text(content, encoding="utf-8")
            _log.debug("artifact stored: %s (%d chars)", name, len(content))
        return ArtifactRef(path=name, sha256=digest, chars=len(content))

    def get(self, ref: str | ArtifactRef) -> str:
        """Read an artifact back by reference (raises FileNotFoundError if missing)."""
        name = ref.path if isinstance(ref, ArtifactRef) else ref
        return (self.root / name).read_text(encoding="utf-8")

    def refs(self) -> list[ArtifactRef]:
        """All artifacts currently in this store (non-recursive)."""
        if not self.root.exists():
            return []
        out: list[ArtifactRef] = []
        for path in sorted(self.root.iterdir()):
            if path.is_file():
                content = path.read_text(encoding="utf-8")
                out.append(
                    ArtifactRef(
                        path=path.name,
                        sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                        chars=len(content),
                    )
                )
        return out


def _distill(raw: str, cap: int) -> str:
    """Head+tail truncation with an explicit marker — honest, never silent.

    If the worker structured its output with a leading summary (the TaskSpec
    result contract asks for one), the head slice naturally captures it.
    """
    head = raw[: int(cap * 0.7)].rstrip()
    tail = raw[-int(cap * 0.15) :].lstrip()
    return f"{head}\n\n{_TRUNCATION_MARKER}\n\n{tail}"


def build_envelope(
    spec: TaskSpec,
    raw_output: str,
    store: ArtifactStore,
    *,
    status: str = "ok",
    gaps: list[str] | None = None,
    summary_max_chars: int = SUMMARY_MAX_CHARS,
) -> ResultEnvelope:
    """Turn a raw worker output into the bounded envelope the orchestrator receives.

    Small output (fits the cap) IS the summary — no artifact, no tax. Anything
    bigger is spilled to the store in full and the summary becomes a distilled
    head+tail slice with an explicit truncation marker; the verifier (M16-A5)
    can always pull the raw artifact via ``evidence_refs`` without the content
    ever entering the orchestrator's context.
    """
    raw = raw_output or ""
    envelope_status = status if status in ("ok", "partial", "failed") else "ok"
    if len(raw) <= summary_max_chars:
        return ResultEnvelope(
            task_id=spec.task_id,
            status=envelope_status,  # type: ignore[arg-type]
            summary=raw,
            gaps=list(gaps or []),
        )
    ref = store.put(raw, kind="output", task_id=spec.task_id)
    summary = _distill(raw, summary_max_chars - 200)  # headroom for the marker
    return ResultEnvelope(
        task_id=spec.task_id,
        status=envelope_status,  # type: ignore[arg-type]
        summary=summary,
        evidence_refs=[ref.path],
        gaps=list(gaps or []),
    )
