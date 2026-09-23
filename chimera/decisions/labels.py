"""From the decision log to a refitted map, and a report of what the log holds — study 22, phase 2.

**Refit.** Answers are grouped on everything a map is keyed on — decision, backend, model, instrument
hash and the build that answered — and fitted on the **raw** number (``raw_p``), never on the
calibrated one the card showed: a map fitted on its own output learns nothing. Two rules decide
whether a group gets a map:

* **Pool when thin.** With fewer than :data:`MIN_PER_CLASS` labels of either class a Platt fit
  swings on each label (crash narratives, arXiv 2609.24052: pool families with fewer than 20
  positives). A group whose map the package ships is then fitted on its own rows **plus** the rows
  the shipped map came from (:data:`~chimera.decisions.maps.SHIPPED_ROWS`) — which matter twice
  over, because a deployment's labels come mostly from the cards the band raised, i.e. the top of the
  range, and the bench rows cover all of it. A thin group with nothing to pool with gets no map.
* **Same build only.** Pooling crosses rows only when the build matches the shipped map's; rows from
  another quantisation are another instrument (study 21 §2ad).

Nothing is written here; ``chimera decisions refit --write`` saves through
:meth:`CalibrationMaps.save`, where a refit replaces the shipped map on the same four keys.

**Report.** Per group: how many answers, halts and cache hits; how the answers fell across the band
— the **review budget**, cards per hundred decisions, which is what the band costs a person; how many
carry a label and from which region of the band (a label set that is all REVIEW-region cannot say
anything about the ALLOW region); and, on the labelled rows, catch and false refusal at the REVIEW
threshold. Calibration is reported in-sample and says so.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from chimera.decisions.calibration import CalibrationMaps, PlattMap
from chimera.decisions.log import Row
from chimera.decisions.maps import SHIPPED_MAPS, SHIPPED_ROWS

MIN_PER_CLASS = 20
ECE_BINS = 5

GroupKey = tuple[str, str, str, str, str]
"""(decision, backend, model, prompt_hash, resolved_model)."""


def group_key(answer: Mapping[str, Any]) -> GroupKey:
    return (
        str(answer.get("decision") or ""), str(answer.get("backend") or ""), str(answer.get("model") or ""),
        str(answer.get("prompt_hash") or ""), str(answer.get("resolved_model") or ""),
    )


def brier(pairs: Sequence[tuple[float, int]]) -> float | None:
    if not pairs:
        return None
    return sum((p - y) ** 2 for p, y in pairs) / len(pairs)


def ece(pairs: Sequence[tuple[float, int]], bins: int = ECE_BINS) -> float | None:
    """Equal-width bins, weighted by count — the form `bench/jev_decisions` reports."""
    if not pairs:
        return None
    total = 0.0
    for i in range(bins):
        lo, hi = i / bins, (i + 1) / bins
        inside = [(p, y) for p, y in pairs if (lo <= p < hi) or (i == bins - 1 and p == 1.0)]
        if inside:
            conf = sum(p for p, _ in inside) / len(inside)
            acc = sum(y for _, y in inside) / len(inside)
            total += len(inside) / len(pairs) * abs(conf - acc)
    return total


def labelled_pairs(rows: Iterable[Row]) -> dict[GroupKey, list[tuple[float, int]]]:
    out: dict[GroupKey, list[tuple[float, int]]] = {}
    for row in rows:
        if row.label is None or row.raw_p is None or row.answer.get("halt"):
            continue
        out.setdefault(group_key(row.answer), []).append((row.raw_p, row.label))
    return out


def _shipped_for(key: GroupKey) -> PlattMap | None:
    decision, backend, model, digest, build = key
    for m in SHIPPED_MAPS:
        same_instrument = (m.decision, m.backend, m.model, m.prompt_hash) == (decision, backend, model, digest)
        if same_instrument and (not build or not m.resolved_model or build == m.resolved_model):
            return m
    return None


@dataclass(frozen=True)
class Refit:
    key: GroupKey
    n_own: int
    positives_own: int
    n_pool: int
    map: PlattMap | None
    reason: str
    brier_before: float | None
    brier_after: float | None
    ece_before: float | None
    ece_after: float | None


def refit(
    rows: Iterable[Row],
    maps: CalibrationMaps,
    *,
    min_per_class: int = MIN_PER_CLASS,
    pool: Mapping[str, Sequence[tuple[float, int]]] = SHIPPED_ROWS,
    fitted_at: str = "",
) -> list[Refit]:
    """One :class:`Refit` per group that has labels — with a map, or with the reason it has none."""
    out: list[Refit] = []
    when = fitted_at or time.strftime("%Y-%m-%d")
    for key, own in sorted(labelled_pairs(rows).items()):
        decision, backend, model, digest, build = key
        positives = sum(y for _, y in own)
        negatives = len(own) - positives
        current = maps.find(decision, backend, model, digest)
        before = [(current.apply(p) if current is not None else p, y) for p, y in own]
        pooled: Sequence[tuple[float, int]] = ()
        if min(positives, negatives) >= min_per_class:
            reason = f"own rows: {positives} positive, {negatives} negative"
        else:
            shipped = _shipped_for(key)
            pooled = pool.get(shipped.id, ()) if shipped is not None else ()
            if not pooled:
                out.append(Refit(
                    key, len(own), positives, 0, None,
                    f"too thin to fit alone ({positives} positive, {negatives} negative; {min_per_class} of each "
                    "needed) and no shipped rows of the same instrument and build to pool with",
                    brier(before), None, ece(before), None,
                ))
                continue
            reason = (
                f"pooled: {positives} positive, {negatives} negative of our own + {len(pooled)} rows the "
                f"shipped map was fitted on (fewer than {min_per_class} of a class)"
            )
        fit_rows = list(own) + list(pooled)
        try:
            new = PlattMap.fit(
                fit_rows, decision=decision, backend=backend, model=model, prompt_hash=digest,
                source=f"decision log — {len(own)} labelled answers" + (f" + {len(pooled)} shipped rows" if pooled else ""),
                note=reason, fitted_at=when, resolved_model=build,
            )
        except ValueError as exc:
            out.append(Refit(key, len(own), positives, len(pooled), None, str(exc), brier(before), None, ece(before), None))
            continue
        after = [(new.apply(p), y) for p, y in own]
        out.append(Refit(key, len(own), positives, len(pooled), new, reason, brier(before), brier(after), ece(before), ece(after)))
    return out


@dataclass(frozen=True)
class GroupReport:
    key: GroupKey
    answers: int
    halts: int
    cached: int
    calibrated: int
    regions: dict[str, int]
    """Answers by region of the band: ``review`` (p ≥ review_at), ``uncertain``, ``allow``, ``no_p``."""
    review_per_100: float | None
    labelled: int
    positives: int
    labelled_by_region: dict[str, int]
    catch: tuple[int, int] | None
    """(caught, positives) at review_at on the labelled rows."""
    false_refusal: tuple[int, int] | None
    """(stopped, negatives) at review_at on the labelled rows."""
    brier: float | None
    ece: float | None


def _region(p: float | None, review_at: float, allow_below: float) -> str:
    if p is None:
        return "no_p"
    if p >= review_at:
        return "review"
    return "allow" if p < allow_below else "uncertain"


def report(rows: Iterable[Row], *, review_at: float, allow_below: float) -> list[GroupReport]:
    groups: dict[GroupKey, list[Row]] = {}
    for row in rows:
        groups.setdefault(group_key(row.answer), []).append(row)
    out: list[GroupReport] = []
    for key, members in sorted(groups.items()):
        regions = {"review": 0, "uncertain": 0, "allow": 0, "no_p": 0}
        by_region = {"review": 0, "uncertain": 0, "allow": 0, "no_p": 0}
        scored: list[tuple[float, int]] = []
        halts = cached = calibrated = 0
        for row in members:
            a = row.answer
            halts += bool(a.get("halt"))
            cached += bool(a.get("cached"))
            is_cal = bool(a.get("calibrated"))
            calibrated += is_cal
            # Only a calibrated number is placed in the band — the band itself does the same.
            p = float(a["p"]) if is_cal and isinstance(a.get("p"), (int, float)) else None
            region = _region(p, review_at, allow_below)
            regions[region] += 1
            if row.label is not None:
                by_region[region] += 1
                if p is not None:
                    scored.append((p, row.label))
        labelled = sum(by_region.values())
        positives = sum(1 for r in members if r.label == 1)
        pos = [s for s in scored if s[1] == 1]
        neg = [s for s in scored if s[1] == 0]
        with_p = sum(v for k, v in regions.items() if k != "no_p")
        out.append(GroupReport(
            key=key, answers=len(members), halts=halts, cached=cached, calibrated=calibrated, regions=regions,
            review_per_100=(100.0 * regions["review"] / with_p) if with_p else None,
            labelled=labelled, positives=positives, labelled_by_region=by_region,
            catch=(sum(1 for p, _ in pos if p >= review_at), len(pos)) if pos else None,
            false_refusal=(sum(1 for p, _ in neg if p >= review_at), len(neg)) if neg else None,
            brier=brier(scored), ece=ece(scored),
        ))
    return out
