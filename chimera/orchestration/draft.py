"""Turn a plain-language description into a project spec.

The project orchestrator is the most capable thing in this application — it plans, works one card
at a time, verifies each requirement mechanically and stops when the spec is satisfied — and its
only door was a text field asking for the path of a YAML file. Everyone who cannot write that YAML
was standing outside it.

The spec is the ACCEPTANCE AUTHORITY: it is the only thing that decides whether the project is
done. So drafting one for somebody carries a duty the rest of the app does not have — the sentence
they read has to be the sentence that gets checked.

**Why ``command`` is refused here.** A ``command`` requirement is a shell command run on the
owner's machine by :class:`~chimera.sandbox.LocalSandbox`. Measured on three layman descriptions
with no constraint at all, one draft in three emitted one, and it was this::

    text:   "The main page must open properly in a web browser."
    target: python3 -c "...urlopen('file://$(pwd)/index.html')..." || curl -s ... | grep -i '<html'

Command substitution, a shell fallback, a network client — under a sentence that mentions none of
it. That is the whole problem in one requirement: the plain-language line somebody approves is not
a description of what will run. A person who writes that command into a YAML file by hand has
chosen it; a person clicking OK on a screen built because they cannot read YAML has not. The other
three checks read files and cannot do anything.

The dropped ones are counted and reported rather than removed quietly — a spec that verifies less
than the model intended is a weaker acceptance authority, and the owner should know by how much.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from chimera.governance.drift import Requirement, Spec

#: Bounds on a drafted spec. A spec with one requirement barely verifies anything; a spec with
#: twenty is a wall of text nobody reads before approving, which is the same as no review at all.
MIN_REQUIREMENTS = 2
MAX_REQUIREMENTS = 8

_SYSTEM = """You turn a plain-language description of a piece of software into a project spec.

The spec is the ACCEPTANCE AUTHORITY: it is the only thing that decides whether the project is
finished. Each requirement is checked mechanically against the files in the project folder.

Answer with a JSON object and nothing else:

{"name": "short project name",
 "requirements": [
   {"id": "kebab-case-id",
    "text": "one sentence a non-programmer understands",
    "check": "contains" | "defines" | "absent",
    "target": "...",
    "required": true}]}

The three checks, and they are the only ones:
  contains - `target` is a REGULAR EXPRESSION searched across every text file in the folder.
  defines  - `target` is a function or class NAME that must exist.
  absent   - `target` is a regular expression that must NOT appear anywhere.

Rules:
  - Between 3 and 6 requirements. Each one must be checkable by reading the files.
  - Keep every regex simple and close to literal text. A regex that is too precise will never
    match and the project will never finish.
  - `text` is what a non-programmer reads to decide whether this spec is right, so it must
    describe what `target` actually checks. Write it in the same language as the request.
  - Do not invent a technology the request did not name unless the request cannot be built
    without one.
"""


@dataclass(frozen=True)
class Drafted:
    """A drafted spec and what had to be done to it on the way out."""

    spec: Spec
    #: How many ``command`` requirements were refused. Reported, never hidden: it is the
    #: difference between the spec the model wrote and the spec that will judge the project.
    refused_commands: int = 0
    #: Ids of the refused ones, so the screen can name what is no longer being checked.
    refused_ids: list[str] = field(default_factory=list)
    tokens: int = 0
    estimated: bool = False


class DraftError(ValueError):
    """The description did not produce a spec that could judge anything."""


def _strip_fence(raw: str) -> str:
    text = raw.strip()
    if not text.startswith("```"):
        return text
    text = text.strip("`")
    if text.startswith("json"):
        text = text[4:]
    return text.strip()


def _slug(value: str, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or fallback


def parse_draft(raw: str) -> Drafted:
    """Read the model's answer into a spec, refusing what must not be run.

    Separated from the model call so the refusal has a test that costs nothing, and so a change to
    the prompt and a change to the rule cannot be mistaken for each other.
    """
    try:
        data = json.loads(_strip_fence(raw))
    except (json.JSONDecodeError, ValueError) as exc:
        raise DraftError("the draft was not valid JSON") from exc
    if not isinstance(data, dict):
        raise DraftError("the draft was not an object")

    items = data.get("requirements")
    if not isinstance(items, list) or not items:
        raise DraftError("the draft listed no requirements")

    requirements: list[Requirement] = []
    refused: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(items[:MAX_REQUIREMENTS]):
        if not isinstance(item, dict):
            continue
        check = str(item.get("check", "")).strip().lower()
        target = str(item.get("target", "")).strip()
        if not target:
            continue
        req_id = _slug(str(item.get("id", "")), f"req-{index + 1}")
        if check == "command":
            refused.append(req_id)
            continue
        if check not in ("contains", "defines", "absent"):
            continue
        if check in ("contains", "absent"):
            try:
                re.compile(target)
            except re.error:
                # An invalid regex would raise inside check_drift, mid-project, as a traceback
                # rather than a verdict. Dropping it here is the difference between a spec that
                # verifies less and a project that crashes on its first check.
                continue
        while req_id in seen:
            req_id = f"{req_id}-{index + 1}"
        seen.add(req_id)
        requirements.append(
            Requirement(
                id=req_id,
                text=str(item.get("text", "")).strip(),
                check=check,  # type: ignore[arg-type]
                target=target,
                required=bool(item.get("required", True)),
            )
        )

    if len(requirements) < MIN_REQUIREMENTS:
        raise DraftError(
            f"only {len(requirements)} usable requirement(s) — not enough to judge a project"
        )
    if not any(r.required for r in requirements):
        # The orchestrator refuses to start on this, and rightly: a spec of optional requirements
        # reports done having verified nothing. Caught here so it is a sentence, not a 500.
        raise DraftError("no requirement was marked required — the spec would verify nothing")

    name = _slug(str(data.get("name", "")), "projeto")
    return Drafted(
        spec=Spec(name=name, requirements=requirements),
        refused_commands=len(refused),
        refused_ids=refused,
    )


def draft_spec(description: str, backend: Any, *, temperature: float = 0.2) -> Drafted:
    """Draft a spec from a description. One model call; no files are written."""
    text = description.strip()
    if not text:
        raise DraftError("nothing was described")
    result = backend.complete(
        [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": text}],
        temperature=temperature,
    )
    drafted = parse_draft(result.content or "")
    usage = getattr(result, "usage", None)
    tokens = int(getattr(usage, "total_tokens", 0) or 0) if usage else 0
    return Drafted(
        spec=drafted.spec,
        refused_commands=drafted.refused_commands,
        refused_ids=drafted.refused_ids,
        tokens=tokens,
        estimated=bool(getattr(usage, "estimated", False)) if usage else False,
    )


def to_yaml(spec: Spec) -> str:
    """Serialize a spec the way a person would have written it by hand.

    Round-tripped through :func:`~chimera.governance.drift.load_spec` in the tests, because the
    file this produces is the acceptance authority — one that does not load back is a project that
    cannot start, and one that loads back DIFFERENT is worse.
    """
    import yaml

    payload: dict[str, Any] = {
        "name": spec.name,
        "requirements": [
            {
                "id": r.id,
                "text": r.text,
                "check": r.check,
                "target": r.target,
                "required": r.required,
            }
            for r in spec.requirements
        ],
    }
    return str(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False))


def write_spec(spec: Spec, workspace: Path, *, filename: str | None = None) -> Path:
    """Write the spec into the project folder and return where it landed.

    Into the folder, deliberately: the spec decides when the project is done, so it belongs beside
    the code, versioned and reviewable, where somebody can read it later and see what was actually
    agreed. That placement used to make the spec satisfy its own ``contains`` checks — see
    ``tests/test_spec_is_not_its_own_evidence.py``; the scan now excludes it.
    """
    ws = Path(workspace).expanduser().resolve()
    ws.mkdir(parents=True, exist_ok=True)
    name = filename or f"{_slug(spec.name, 'projeto')}.spec.yaml"
    # The name is derived from a model-written string, so it is treated as untrusted: only the
    # basename, and it must land inside the workspace.
    path = (ws / Path(name).name).resolve()
    if path.parent != ws:
        raise DraftError("refusing to write the spec outside the project folder")
    path.write_text(to_yaml(spec), encoding="utf-8")
    return path
