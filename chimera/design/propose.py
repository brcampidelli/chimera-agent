"""Ask a model for a design proposal, a refinement or an audit — and never apply what it says unchecked.

The model is outside the rendering path. Its reply goes: text → :func:`extract_json` → a Pydantic
:class:`Proposal` (strict, unknown keys refused) → :func:`remap_new_ids` (the ids it invented become
server ids) → a dry run of :func:`apply_operations` with ``allow_lock_changes=False``. What comes back
is an outcome with the proposal, the preview document and the inverse, or the reason it failed. These
functions never save; the caller decides, after the person has seen the preview.

**One re-ask, then stop.** When the reply is not JSON or does not fit the schema, the model gets the
validation error once and one more chance. A dry-run failure is not re-asked: it is reported with the
proposal, so a person can see what the model tried, instead of a loop spending calls to get an
operation past a check.

**Everything that came from a person or a document is fenced.** The brief, the refinement request, the
constraints, comments and the document summary (which contains canvas text and token names a person
typed) go through :func:`sanitize_untrusted` and :func:`fence`. The base instruction tells the model
what the fence means. Fencing is a known-imperfect mitigation, not a boundary; the boundary is that
nothing the model returns is executed, only validated operations are applied, and only by the caller.

**The model sees a summary, not the document.** Ids, types, names, rounded frames and the first
characters of each text, capped in count — enough to refer to elements by id, small enough that a large
document does not become a large prompt.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, TypeVar

from pydantic import BaseModel, Field, StringConstraints, ValidationError

from chimera.design import prompts
from chimera.design.export_html import TokenIssue, check_against_tokens
from chimera.design.ids import new_id, remap_new_ids
from chimera.design.model import (
    DesignDocument,
    DesignError,
    DesignNode,
    GroupNode,
    ImageNode,
    SafeId,
    ShapeNode,
    TextNode,
    _Strict,
    wire,
)
from chimera.design.ops import DesignOperation, apply_operations, short_errors
from chimera.governance.sanitize import fence, sanitize_untrusted
from chimera.providers.gateway import SupportsComplete
from chimera.review.finder import extract_json
from chimera.telemetry import get_logger

_log = get_logger("design.propose")

MAX_BRIEF = 8_000
MAX_SUMMARY_NODES = 300
TEXT_EXCERPT = 120
MAX_SELECTED = 50
MAX_COMMENTS_SENT = 50
COMMENT_EXCERPT = 500
#: Output budget for one reply: a page of JSON, enough for the proposal cap below. The first live
#: proposal (0.79.0) was cut off at 8k, and the owner waited two minutes for "the reply did not
#: contain a JSON object". The catalogue can lower this for a model that serves less (see
#: _reply_budget); it is never sent as a route filter above what the route serves.
MAX_REPLY_TOKENS = 16_000
#: What a re-ask after a cut-off reply asks for: fewer operations, so the reply fits the budget.
MAX_RETRY_OPERATIONS = 60
#: A proposal is a draft a person reviews, so it is capped below what one manual batch may hold
#: (``MAX_OPS_PER_CALL``, the desktop's limit).
MAX_PROPOSAL_OPS = 200

Line = Annotated[str, StringConstraints(max_length=500)]


class Proposal(_Strict):
    summary: Annotated[str, StringConstraints(max_length=2_000)]
    assumptions: Annotated[list[Line], Field(max_length=3)] = Field(default_factory=list)
    operations: Annotated[list[DesignOperation], Field(max_length=MAX_PROPOSAL_OPS)] = Field(
        default_factory=list
    )
    checks: Annotated[list[Line], Field(max_length=30)] = Field(default_factory=list)
    questions: Annotated[list[Line], Field(max_length=5)] = Field(default_factory=list)


class AuditIssue(_Strict):
    severity: Literal["high", "medium", "low"]
    node_id: SafeId | None = None
    evidence: Annotated[str, StringConstraints(min_length=1, max_length=1_000)]
    fix: Annotated[str, StringConstraints(max_length=1_000)] = ""


class Audit(_Strict):
    issues: Annotated[list[AuditIssue], Field(max_length=50)] = Field(default_factory=list)
    passed_checks: Annotated[list[Line], Field(max_length=30)] = Field(default_factory=list)
    needs_user_decision: Annotated[list[Line], Field(max_length=10)] = Field(default_factory=list)


@dataclass
class ProposalOutcome:
    """What a proposal call produced. ``ok`` only when the operations applied cleanly in a dry run."""

    proposal: Proposal | None = None
    preview: DesignDocument | None = None
    inverse: list[Any] = field(default_factory=list)
    id_map: dict[str, str] = field(default_factory=dict)
    error: str | None = None
    attempts: int = 0

    @property
    def ok(self) -> bool:
        return self.error is None and self.proposal is not None


@dataclass
class AuditOutcome:
    audit: Audit | None = None
    findings: list[TokenIssue] = field(default_factory=list)
    #: Issues the model attributed to an element id the document does not have — dropped, counted.
    unverifiable: int = 0
    error: str | None = None
    attempts: int = 0


_M = TypeVar("_M", bound=BaseModel)


# ---- the three calls ----------------------------------------------------------------------------


def propose(
    backend: SupportsComplete,
    doc: DesignDocument,
    brief: str,
    constraints: Mapping[str, Any] | None = None,
    *,
    model: str | None = None,
    id_factory: Callable[[str], str] = new_id,
) -> ProposalOutcome:
    """A first proposal for ``brief`` against ``doc`` (usually empty)."""
    user = prompts.GENERATE.format(
        brief=_untrusted(brief[:MAX_BRIEF]),
        constraints=_untrusted(_dumps(dict(constraints or {}))),
        tokens=_untrusted(_dumps(wire(doc.tokens))),
        document=_untrusted(_dumps(summarize(doc))),
        operations=prompts.OPERATIONS_GUIDE,
    )
    return _proposal_call(backend, doc, user, model=model, id_factory=id_factory)


def refine(
    backend: SupportsComplete,
    doc: DesignDocument,
    request: str,
    selected_ids: Iterable[str] = (),
    *,
    model: str | None = None,
    id_factory: Callable[[str], str] = new_id,
) -> ProposalOutcome:
    """A targeted change for ``request``, scoped by the selection and the comments on it."""
    selected = [nid for nid in dict.fromkeys(selected_ids) if nid in doc.nodes][:MAX_SELECTED]
    wanted = set(selected)
    comments = [
        {"id": c.id, "artboardId": c.artboard_id, "nodeId": c.node_id, "text": c.text[:COMMENT_EXCERPT]}
        for c in doc.comments
        if not c.resolved and (not wanted or c.node_id in wanted)
    ][:MAX_COMMENTS_SENT]
    user = prompts.REFINE.format(
        request=_untrusted(request[:MAX_BRIEF]),
        selected=_untrusted(_dumps([_node_summary(doc.nodes[nid]) for nid in selected])),
        comments=_untrusted(_dumps(comments)),
        tokens=_untrusted(_dumps(wire(doc.tokens))),
        document=_untrusted(_dumps(summarize(doc))),
        operations=prompts.OPERATIONS_GUIDE,
    )
    return _proposal_call(backend, doc, user, model=model, id_factory=id_factory)


def audit(
    backend: SupportsComplete, doc: DesignDocument, brief: str, *, model: str | None = None
) -> AuditOutcome:
    """The model's review of ``doc`` against ``brief``, beside the deterministic token checks."""
    findings = check_against_tokens(doc)
    user = prompts.AUDIT.format(
        brief=_untrusted(brief[:MAX_BRIEF]),
        tokens=_untrusted(_dumps(wire(doc.tokens))),
        document=_untrusted(_dumps(summarize(doc))),
        findings=_untrusted(_dumps([f.__dict__ for f in findings[:100]])),
    )
    parsed, error, attempts = _ask(backend, user, Audit, model=model)
    if parsed is None:
        return AuditOutcome(findings=findings, error=error, attempts=attempts)
    kept = [i for i in parsed.issues if i.node_id is None or i.node_id in doc.nodes]
    dropped = len(parsed.issues) - len(kept)
    result = parsed.model_copy(update={"issues": kept})
    return AuditOutcome(audit=result, findings=findings, unverifiable=dropped, attempts=attempts)


# ---- shared machinery ---------------------------------------------------------------------------


def _proposal_call(
    backend: SupportsComplete,
    doc: DesignDocument,
    user: str,
    *,
    model: str | None,
    id_factory: Callable[[str], str],
) -> ProposalOutcome:
    proposal, error, attempts = _ask(backend, user, Proposal, model=model)
    if proposal is None:
        return ProposalOutcome(error=error, attempts=attempts)
    raw_ops = [wire(op) for op in proposal.operations]
    remapped, id_map = remap_new_ids(raw_ops, id_factory)
    try:
        proposal = Proposal.model_validate({**wire(proposal), "operations": remapped})
        preview, inverse = apply_operations(
            doc, remapped, max_ops=MAX_PROPOSAL_OPS, allow_lock_changes=False
        )
    except ValidationError as exc:
        return ProposalOutcome(proposal=proposal, id_map=id_map, attempts=attempts,
                               error=f"the operations do not apply: {short_errors(exc)}")
    except DesignError as exc:
        return ProposalOutcome(proposal=proposal, id_map=id_map, attempts=attempts,
                               error=f"the operations do not apply: {exc}")
    return ProposalOutcome(proposal=proposal, preview=preview, inverse=inverse, id_map=id_map,
                           attempts=attempts)


def _ask(
    backend: SupportsComplete, user: str, schema: type[_M], *, model: str | None
) -> tuple[_M | None, str | None, int]:
    """Call the model, parse its JSON into ``schema``; on failure re-ask once with the reason.

    A reply cut off at the output budget is named as such, not as "no JSON": the fix is a shorter
    reply, and the re-ask asks for one. JSON mode is requested only where the provider lists it.
    """
    effective = _effective_model(model)
    budget = _reply_budget(effective)
    json_mode = _json_object_mode(effective)
    messages: list[Any] = [
        {"role": "system", "content": prompts.BASE_INSTRUCTION},
        {"role": "user", "content": user},
    ]
    error = "no answer"
    for attempt in (1, 2):
        result = backend.complete(messages, model=model, temperature=0.2, max_tokens=budget, **json_mode)
        content = result.content or ""
        data = extract_json(content)
        if isinstance(data, dict):
            try:
                return schema.model_validate(data), None, attempt
            except ValidationError as exc:
                error = f"the JSON does not match the schema: {short_errors(exc)}"
        elif result.finish_reason == "length" or _unclosed_object(content):
            error = (f"the reply was cut off at {budget} tokens before its JSON was complete; "
                     f"send at most {MAX_RETRY_OPERATIONS} operations")
        else:
            error = "the reply did not contain a JSON object"
        _log.info("design reply rejected (attempt %d): %s", attempt, error)
        if attempt == 1:
            messages += [
                {"role": "assistant", "content": content[:20_000]},
                {"role": "user", "content": prompts.REASK.format(error=error)},
            ]
    return None, error, 2


def _effective_model(model: str | None) -> str | None:
    """The model the gateway will call: the caller's, else the configured default, without an effort suffix.

    Mirrors ``LLMGateway._resolve_model``. The live design turn passes ``None``, so without this the
    budget and JSON-mode decisions would be taken for a model nobody is calling.
    """
    from chimera.providers.catalog import base_slug

    if model:
        return base_slug(model)
    try:
        from chimera.config import get_settings

        default = get_settings().default_model
    except Exception:  # noqa: BLE001 - unreadable settings: no catalogue fact to use, keep the default budget
        return None
    return base_slug(default) if default else None


def _reply_budget(model: str | None) -> int:
    """``MAX_REPLY_TOKENS``, lowered to what the model's catalogue entry says it serves.

    A ``max_tokens`` the caller sends goes out as chosen, and OpenRouter reads it as a route filter
    (see ``LLMGateway._bounded``): a budget above a route's limit selects another provider rather than
    bounding this one. The catalogue is the only place that limit is recorded.
    """
    from chimera.providers.catalog import max_output_for

    served = max_output_for(model) if model else None
    return MAX_REPLY_TOKENS if served is None else min(MAX_REPLY_TOKENS, served)


def _json_object_mode(model: str | None) -> dict[str, Any]:
    """``response_format`` when LiteLLM lists it for the model's provider, otherwise nothing.

    The gateway's blocking call passes every keyword through to LiteLLM and does not set
    ``drop_params``, so a parameter the provider does not take would fail the whole call. LiteLLM
    lists parameters per provider; OpenRouter ignores one that a route does not serve unless the
    request asks it to require them, which this one does not.
    """
    if not model:
        return {}
    from litellm import get_supported_openai_params

    provider = model.split("/", 1)[0] if "/" in model else None
    try:
        supported = get_supported_openai_params(model=model, custom_llm_provider=provider) or []
    except Exception:  # noqa: BLE001 - a model LiteLLM does not know is no reason to fail a proposal
        return {}
    return {"response_format": {"type": "json_object"}} if "response_format" in supported else {}


def _unclosed_object(content: str) -> bool:
    """A reply that opens a JSON object and never closes it: how a cut-off reply looks when the
    provider does not report ``finish_reason=length``."""
    body = content.strip()
    return body.count("{") > body.count("}")


def _untrusted(text: str) -> str:
    return fence(sanitize_untrusted(text))


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def summarize(doc: DesignDocument, *, max_nodes: int = MAX_SUMMARY_NODES) -> dict[str, Any]:
    """A compact view of ``doc`` for a prompt: structure and ids, short text, capped element count."""
    budget = [max_nodes]
    pages = []
    for page in doc.pages:
        boards = []
        for board in page.artboards:
            boards.append({
                "id": board.id, "name": board.name, "size": [round(board.width), round(board.height)],
                "background": board.background,
                "nodes": _summaries(doc, board.node_ids, budget),
            })
        pages.append({"id": page.id, "name": page.name, "artboards": boards})
    shown = max_nodes - budget[0]
    out: dict[str, Any] = {"id": doc.id, "title": doc.title, "pages": pages}
    if shown < len(doc.nodes):
        out["omitted_elements"] = len(doc.nodes) - shown
    return out


def _summaries(doc: DesignDocument, ids: list[str], budget: list[int]) -> list[Any]:
    out = []
    for nid in ids:
        if budget[0] <= 0:
            break
        budget[0] -= 1
        node = doc.nodes[nid]
        entry = _node_summary(node)
        if isinstance(node, GroupNode):
            entry["children"] = _summaries(doc, node.child_ids, budget)
        out.append(entry)
    return out


def _node_summary(node: DesignNode) -> dict[str, Any]:
    f = node.frame
    entry: dict[str, Any] = {
        "id": node.id, "type": node.type, "name": node.name,
        "frame": [round(f.x), round(f.y), round(f.width), round(f.height)],
    }
    if node.locked:
        entry["locked"] = True
    if not node.visible:
        entry["hidden"] = True
    if isinstance(node, TextNode):
        entry["text"] = node.text[:TEXT_EXCERPT] + ("…" if len(node.text) > TEXT_EXCERPT else "")
        entry["fontToken"] = node.font_token
        entry["colorToken"] = node.color_token
    elif isinstance(node, ShapeNode):
        entry["shape"] = node.shape
        entry["fill"] = node.style.fill
    elif isinstance(node, ImageNode):
        entry["assetId"] = node.asset_id
        entry["alt"] = node.alt[:TEXT_EXCERPT]
    return entry
