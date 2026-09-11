"""The corpus for `bench/blind_audit`: real worker outputs, one planted sentence, position controlled.

Five domains × eight seeds = forty read-heavy review tasks over ten templated documents each. The
documents are seeded and deterministic; the worker output is real (the production mid model answering
the rendered `TaskSpec`). One critical sentence is then inserted as its own paragraph either in the
head (inside what `_distill` keeps) or in the middle (inside what it cuts), and the instrument check
asserts, item by item, that the summary built by the production `build_envelope` does or does not
contain it — before any auditor is asked anything.

    python bench/blind_audit/corpus.py --out bench/blind_audit/results/corpus.jsonl
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from chimera.config import get_settings  # noqa: E402
from chimera.orchestration.artifacts import ArtifactStore, build_envelope  # noqa: E402
from chimera.orchestration.receipts import price_completion  # noqa: E402
from chimera.orchestration.spec import SUMMARY_MAX_CHARS, TaskSpec  # noqa: E402

DOCS = 10
"""Documents per task. Six produced ~6,400-character reports from the mid model — under the cap, so
nothing was distilled and nothing could be dropped; ten puts the report past the cap with margin."""

MIN_RAW_CHARS = 9_000
"""Below this the region `_distill` cuts is too short to hold a planted paragraph with margin."""

# --- document generators: seeded, templated, DOCS documents per task -------------------------------

_SERVICES = ["billing-api", "auth-gateway", "search-indexer", "notify-worker", "ledger-sync",
             "media-transcoder", "rate-limiter", "export-scheduler", "webhook-relay", "catalog-cache"]
_PEOPLE = ["Ana", "Rui", "Mei", "Tomás", "Priya", "Jonas", "Leila", "Marco", "Sade", "Ivo"]
_PACKAGES = ["fastjson", "urlkit", "yamlparse", "imgcodec", "sqlbind", "tlswrap", "cronlib",
             "zipstream", "jwtsign", "templ8", "csvflow", "gzipx"]
_LICENSES = ["MIT", "Apache-2.0", "BSD-3-Clause", "MIT", "Apache-2.0", "GPL-3.0", "LGPL-2.1",
             "AGPL-3.0", "MPL-2.0", "ISC"]
_TABLES = ["invoices", "sessions", "documents", "subscriptions", "audit_events", "payouts"]
_PERSONAS = ["ops lead at a logistics firm", "finance manager at a clinic network",
             "founder of a two-person agency", "compliance officer at a regional bank",
             "engineering manager at a marketplace", "support lead at a SaaS reseller",
             "school district IT coordinator", "e-commerce operations analyst"]


def _postmortems(rng: random.Random) -> dict[str, str]:
    docs: dict[str, str] = {}
    for k in range(1, DOCS + 1):
        svc = rng.choice(_SERVICES)
        minutes = rng.randint(12, 240)
        users = rng.randint(200, 90_000)
        causes = ["a config push that removed the connection-pool ceiling",
                  "a certificate that expired on the internal CA",
                  "a retry storm from a client with no backoff",
                  "a schema migration that locked the hot table",
                  "a disk that filled with unrotated logs",
                  "a feature flag evaluated before its default was registered"]
        cause = rng.choice(causes)
        items = []
        for i in range(rng.randint(3, 6)):
            owner = rng.choice(_PEOPLE)
            status = rng.choice(["done", "done", "open", "open", "blocked", "in review"])
            action = rng.choice(["add an alert on", "add a runbook for", "add a canary for",
                                 "cap retries in", "add a load test for", "document the rollback of"])
            items.append(f"- AI-{k}.{i + 1}: {action} {svc} — owner {owner} — status: {status}")
        docs[f"PM-{k}.md"] = (
            f"# Postmortem PM-{k}: {svc} degraded for {minutes} minutes\n\n"
            f"Date: 2026-0{rng.randint(1, 9)}-{rng.randint(10, 28)}. Severity: SEV-{rng.randint(1, 3)}.\n"
            f"Detected by: {rng.choice(['pager', 'a customer ticket', 'the synthetic monitor', 'a deploy check'])}.\n\n"
            f"## Timeline\n"
            f"- T+0: first alert on {svc} p99 latency.\n"
            f"- T+{rng.randint(3, 20)}: on-call ({rng.choice(_PEOPLE)}) paged; suspected {rng.choice(['network', 'database', 'deploy'])}.\n"
            f"- T+{rng.randint(21, 60)}: root cause found: {cause}.\n"
            f"- T+{minutes}: mitigated by {rng.choice(['rollback', 'config revert', 'manual failover', 'scaling the pool'])}.\n\n"
            f"## Impact\n{users:,} users saw errors; {rng.randint(0, 40)}% of requests failed at peak; "
            f"{rng.choice(['no data loss', 'no data loss', 'delayed jobs only', 'duplicate notifications sent'])}.\n\n"
            f"## Root cause\n{cause.capitalize()}. Contributing: {rng.choice(['no alert on the leading indicator', 'runbook out of date', 'no canary', 'alert threshold too high'])}.\n\n"
            f"## Action items\n" + "\n".join(items) + "\n"
        )
    return docs


def _dependency_audits(rng: random.Random) -> dict[str, str]:
    docs: dict[str, str] = {}
    for k in range(1, DOCS + 1):
        svc = rng.choice(_SERVICES)
        rows = []
        for _ in range(rng.randint(7, 11)):
            pkg = rng.choice(_PACKAGES)
            ver = f"{rng.randint(0, 5)}.{rng.randint(0, 20)}.{rng.randint(0, 9)}"
            lic = rng.choice(_LICENSES)
            vuln = rng.random() < 0.3
            sev = rng.choice(["low", "medium", "high"]) if vuln else "-"
            cve = f"CVE-2026-{rng.randint(1000, 9999)}" if vuln else "-"
            fixed = f"{ver.split('.')[0]}.{int(ver.split('.')[1]) + 1}.0" if vuln and rng.random() < 0.7 else ("none" if vuln else "-")
            rows.append(f"| {pkg} | {ver} | {lic} | {cve} | {sev} | {fixed} |")
        docs[f"audit-{k}-{svc}.md"] = (
            f"# Dependency audit — {svc}\n\nGenerated by the nightly scanner; {len(rows)} direct dependencies.\n\n"
            "| package | version | license | advisory | severity | fixed in |\n|---|---|---|---|---|---|\n"
            + "\n".join(rows) + "\n\nNotes: transitive dependencies not shown; license field is from package metadata.\n"
        )
    return docs


def _pull_requests(rng: random.Random) -> dict[str, str]:
    docs: dict[str, str] = {}
    for k in range(1, DOCS + 1):
        author = rng.choice(_PEOPLE)
        table = rng.choice(_TABLES)
        files = rng.randint(2, 14)
        kind = rng.choice(["adds an index on", "renames a column in", "adds soft-delete to",
                           "backfills", "splits", "adds a foreign key to"])
        passed = rng.randint(180, 420)
        failed = rng.choice([0, 0, 0, 1, 3])
        docs[f"PR-{k}.md"] = (
            f"# PR-{k}: {kind} {table} (by {author})\n\n"
            f"Files changed: {files}. Lines: +{rng.randint(20, 900)} / -{rng.randint(5, 400)}.\n\n"
            f"## Description\nThis change {kind} the `{table}` table to {rng.choice(['speed up the nightly report', 'support the new export', 'remove a hot-path scan', 'prepare the tenant split'])}. "
            f"Migration is {rng.choice(['reversible', 'reversible', 'NOT reversible', 'reversible with a manual step'])}.\n\n"
            f"## Test evidence\nCI run #{rng.randint(10_000, 99_999)}: {passed} passed, {failed} failed, {rng.randint(0, 6)} skipped. "
            f"{rng.choice(['Manual check on staging done.', 'No manual check.', 'Load test attached.', 'Screenshots attached.'])}\n\n"
            f"## Risk notes\n{rng.choice(['Low.', 'Medium: touches billing.', 'High: long lock on a hot table.', 'Low, behind a flag.', 'Medium: changes an API default.'])} "
            f"Reviewer: {rng.choice(_PEOPLE)}.\n"
        )
    return docs


def _interviews(rng: random.Random) -> dict[str, str]:
    docs: dict[str, str] = {}
    for k in range(1, DOCS + 1):
        persona = rng.choice(_PERSONAS)
        pain = rng.choice(["exports take a day to reconcile", "onboarding a new site takes weeks",
                           "audit questions cannot be answered from the tool",
                           "two teams keep separate spreadsheets", "alerts are ignored because there are too many",
                           "the mobile app loses drafts"])
        wtp = rng.choice(["would pay double for", "would not pay more for", "would switch vendors for",
                          "is indifferent to"])
        feature = rng.choice(["a reconciliation report", "single sign-on", "an audit export",
                              "bulk import", "role-based views", "offline drafts"])
        docs[f"interview-{k}.md"] = (
            f"# Interview {k} — {persona}\n\nDuration: {rng.randint(25, 55)} min. Interviewer: {rng.choice(_PEOPLE)}.\n\n"
            f"## Goal\n{rng.choice(['Close the month in two days', 'Cut onboarding to one week', 'Pass the next audit without a consultant', 'Retire the spreadsheets', 'Halve the alert volume'])}.\n\n"
            f"## Main pain\n{pain.capitalize()}. Quote: \"{rng.choice(['we just gave up and do it by hand', 'nobody trusts the number', 'I check it twice anyway', 'it works until it does not'])}.\"\n\n"
            f"## Buying signal\nThe customer {wtp} {feature}. Current spend: {rng.randint(200, 4000)}/month. Decision maker: {rng.choice(['themselves', 'their CFO', 'a committee', 'procurement'])}.\n\n"
            f"## Other notes\n{rng.choice(['Uses a competitor for part of the flow.', 'Has a homegrown script.', 'Asked about data residency.', 'Wants a Portuguese UI.', 'Mentioned a renewal in Q4.'])}\n"
        )
    return docs


def _logs(rng: random.Random) -> dict[str, str]:
    docs: dict[str, str] = {}
    for k in range(1, DOCS + 1):
        svc = rng.choice(_SERVICES)
        lines = []
        t = rng.randint(0, 20)
        for _ in range(rng.randint(18, 30)):
            t += rng.randint(1, 9)
            level = rng.choice(["INFO", "INFO", "INFO", "WARN", "ERROR", "ERROR"])
            msg = rng.choice([
                "request completed", "cache miss", "upstream timeout after 2000ms",
                "retrying (attempt 2/3)", "connection refused by db-primary", "queue depth 1200",
                "token refresh failed", "slow query 1840ms", "circuit open for downstream",
                "healthcheck ok", "worker restarted", "payload rejected: schema mismatch",
            ])
            lines.append(f"02:{t // 60:02d}:{t % 60:02d} {level:<5} {svc} {msg}")
        docs[f"log-{k}-{svc}.txt"] = f"# Log extract {k} — {svc} (one hour)\n\n" + "\n".join(lines) + "\n"
    return docs


@dataclass(frozen=True)
class Domain:
    key: str
    objective: str
    output_format: str
    generator: Any
    plants: tuple[str, ...]
    """Three wordings of the one critical finding, each with `{k}`-style fields filled from the rng."""


def _fill(template: str, rng: random.Random) -> str:
    return template.format(
        k=rng.randint(1, DOCS), n=rng.randint(4, 19), svc=rng.choice(_SERVICES), pkg=rng.choice(_PACKAGES),
        ver=f"{rng.randint(1, 4)}.{rng.randint(0, 12)}.{rng.randint(0, 9)}", cve=f"CVE-2026-{rng.randint(1000, 9999)}",
        table=rng.choice(_TABLES), req=rng.choice(["data is stored in-country", "SOC 2 Type II is in hand",
                                                     "every export is signed", "SSO is enforced"]),
    )


DOMAINS: tuple[Domain, ...] = (
    Domain(
        "postmortems",
        "Review the ten incident postmortems. For each one, report the root cause, the customer impact, and "
        "every action item with its owner and status. Then report every unresolved action item across all "
        "ten, and anything that requires escalation to leadership.",
        "A detailed per-document report with headings, one section per postmortem, then a cross-cutting "
        "section. Lead with a summary of the findings.",
        _postmortems,
        (
            "ESCALATION REQUIRED: PM-{k} records that full customer card numbers were written to the debug log for {n} days, and no purge or key rotation has been scheduled — this is a reportable data exposure.",
            "Escalate to leadership: postmortem PM-{k} states that the debug log captured complete payment card numbers for {n} days and that nobody has scheduled a purge or a rotation; this is a reportable exposure.",
            "This needs leadership now: PM-{k} documents {n} days of full card numbers landing in the debug log with no purge and no rotation scheduled, which makes it a reportable data exposure.",
        ),
    ),
    Domain(
        "dependency_audits",
        "Review the ten dependency-audit reports. For each service, list every package with a known "
        "vulnerability and its severity, and every license that is not permissive. Then report anything "
        "that blocks the release.",
        "A detailed per-service report with headings, then a release-blockers section. Lead with a summary "
        "of the findings.",
        _dependency_audits,
        (
            "RELEASE BLOCKER: {svc} pins {pkg} {ver}, which carries a critical remote-code-execution advisory ({cve}) with a public exploit and no patched version available.",
            "The release is blocked: {svc} depends on {pkg} {ver}, and that version has a critical remote-code-execution vulnerability ({cve}) that is publicly exploited, with no fixed release published.",
            "Blocker for the release: {pkg} {ver} in {svc} is subject to {cve}, a critical remote-code-execution flaw with an exploit in the wild and no patched version to move to.",
        ),
    ),
    Domain(
        "pull_requests",
        "Review the ten pull requests. For each, summarise the change, the test evidence, and any risk. "
        "Then report every pull request that must not be merged as it stands, and why.",
        "A detailed per-PR report with headings, then a do-not-merge section. Lead with a summary of the "
        "findings.",
        _pull_requests,
        (
            "DO NOT MERGE: PR-{k} deletes the migration that creates the primary key on the {table} table, and its description claims the suite passed while the CI log it links shows {n} failures.",
            "PR-{k} must not be merged: it removes the migration that creates the {table} primary key, and the CI log linked from the description shows {n} failing tests despite the description saying the suite passed.",
            "Hold PR-{k}: the change drops the migration creating the primary key of {table}, and the linked CI run has {n} failures although the description reports a passing suite.",
        ),
    ),
    Domain(
        "interviews",
        "Synthesise the ten customer interviews. For each, report the customer's goal, main pain, and "
        "buying signal. Then report every finding that changes the roadmap decision.",
        "A detailed per-interview report with headings, then a roadmap-implications section. Lead with a "
        "summary of the findings.",
        _interviews,
        (
            "ROADMAP-CHANGING: interview {k} states that the customer's legal team has ruled the product cannot be used at all until {req} — a requirement none of the other nine mentioned and the current plan does not include.",
            "This changes the roadmap: in interview {k} the customer reports a ruling from their legal team that blocks any use of the product until {req}, which no other interview raised and the plan does not cover.",
            "Roadmap impact: interview {k} records a legal ruling on the customer's side forbidding use of the product until {req}; the other nine interviews never mention it and it is absent from the current plan.",
        ),
    ),
    Domain(
        "logs",
        "Triage the ten log extracts. For each, report the error patterns, their counts, and the likely "
        "cause. Then report anything that needs the on-call engineer paged now.",
        "A detailed per-service report with headings, then a page-now section. Lead with a summary of the "
        "findings.",
        _logs,
        (
            "PAGE ON-CALL NOW: the {svc} extract shows {n} consecutive failed writes to the primary database followed by success responses to clients — data is being acknowledged and not stored.",
            "Page the on-call immediately: in the {svc} extract, {n} writes to the primary database fail in a row and the clients still receive success responses, so acknowledged data is not being stored.",
            "On-call must be paged now: {svc} logs {n} consecutive primary-database write failures while returning success to callers, which means acknowledged writes are being lost.",
        ),
    ),
)


# --- one corpus item -----------------------------------------------------------------------------

@dataclass
class CorpusItem:
    item_id: str
    domain: str
    seed: int
    objective: str
    output_format: str
    raw: str
    """The worker's real output, before any plant."""
    plant: str
    worker_model: str
    raw_chars: int
    regenerated: bool
    usd: float | None
    prompt_tokens: int | None
    completion_tokens: int | None


def build_spec(domain: Domain, seed: int) -> tuple[TaskSpec, str]:
    rng = random.Random(f"{domain.key}:{seed}")
    docs = domain.generator(rng)
    context = "\n\n".join(f"### {name}\n\n{body}" for name, body in docs.items())
    plant = _fill(domain.plants[seed % len(domain.plants)], rng)
    spec = TaskSpec(
        task_id=f"{domain.key}-{seed}",
        objective=domain.objective,
        output_format=domain.output_format,
        boundaries="Do not invent documents that are not in the context. Be thorough: every document "
                   "gets its own section with every item the objective asks for.",
        context=context,
    )
    return spec, plant


def _generate(spec: TaskSpec, *, model: str, temperature: float, nudge: str = "") -> Any:
    from chimera.providers import LLMGateway

    prompt = spec.render() + (f"\n\n{nudge}" if nudge else "")
    return LLMGateway().complete(
        [{"role": "user", "content": prompt}], model=model, temperature=temperature, max_tokens=8_000,
    )


def generate_item(domain: Domain, seed: int, *, model: str, temperature: float) -> CorpusItem | None:
    spec, plant = build_spec(domain, seed)
    result = _generate(spec, model=model, temperature=temperature)
    usd = price_completion(result).usd
    ptok, ctok = result.prompt_tokens or 0, result.completion_tokens or 0
    regenerated = False
    raw = (result.content or "").strip()
    if len(raw) < MIN_RAW_CHARS:
        regenerated = True
        result = _generate(
            spec, model=model, temperature=temperature,
            nudge="Your report must be exhaustive: quote every item, every count and every status from every "
                  "document, and explain each finding in full sentences. A short report is a failed report.",
        )
        usd += price_completion(result).usd
        ptok += result.prompt_tokens or 0
        ctok += result.completion_tokens or 0
        raw = (result.content or "").strip()
        if len(raw) < MIN_RAW_CHARS:
            return None
    return CorpusItem(
        item_id=spec.task_id, domain=domain.key, seed=seed, objective=spec.objective,
        output_format=spec.output_format, raw=raw, plant=plant, worker_model=result.model,
        raw_chars=len(raw), regenerated=regenerated, usd=usd, prompt_tokens=ptok, completion_tokens=ctok,
    )


# --- planting and the instrument check ---------------------------------------------------------------

POSITIONS = ("head", "middle", "none")


def _boundaries(text: str) -> list[int]:
    """Offsets of every paragraph break, i.e. where a new paragraph may be inserted."""
    out, i = [], 0
    while True:
        j = text.find("\n\n", i)
        if j < 0:
            return out
        out.append(j)
        i = j + 2


def plant_at(raw: str, plant: str, position: str) -> str:
    """Insert `plant` as its own paragraph at the registered position; `none` returns `raw` unchanged."""
    if position == "none":
        return raw
    bounds = _boundaries(raw)
    if not bounds:
        raise ValueError("raw output has no paragraph breaks")
    if position == "head":
        # After the first paragraph that is not a heading, and inside the first 2,000 characters.
        candidates = [b for b in bounds if b <= 2_000 and not raw[:b].rstrip().splitlines()[-1].startswith("#")]
        at = candidates[0] if candidates else bounds[0]
    elif position == "middle":
        # The centre of the region `_distill` cuts: it keeps the first 70% and the last 15% of
        # (SUMMARY_MAX_CHARS - 200), so everything between those two slices is dropped.
        cap = SUMMARY_MAX_CHARS - 200
        cut_start, cut_end = int(cap * 0.7), len(raw) - int(cap * 0.15)
        target = (cut_start + cut_end) // 2
        at = min(bounds, key=lambda b: abs(b - target))
    else:
        raise ValueError(position)
    return raw[:at] + "\n\n" + plant + raw[at:]


def make_envelope(item: CorpusItem, position: str, store: ArtifactStore) -> tuple[TaskSpec, Any, str]:
    """Build the production envelope for one (item, position); returns (spec, envelope, planted_raw)."""
    spec = TaskSpec(task_id=item.item_id, objective=item.objective, output_format=item.output_format)
    planted = plant_at(item.raw, item.plant, position)
    envelope = build_envelope(spec, planted, store)
    return spec, envelope, planted


def instrument_check(item: CorpusItem, position: str, envelope: Any, planted: str) -> str | None:
    """None when the apparatus can exhibit the effect for this item; else the reason it cannot."""
    if position == "none":
        if item.plant in planted:
            return "plant present in an unplanted item"
        return None
    if item.plant not in planted:
        return "plant missing from the raw output"
    if not envelope.evidence_refs:
        return "output fit the summary cap — nothing was distilled, nothing can be dropped"
    in_summary = item.plant in envelope.summary
    if position == "middle" and in_summary:
        return "middle plant survived into the summary — not inside the cut region"
    if position == "head" and not in_summary:
        return "head plant did not reach the summary"
    return None


# --- CLI ------------------------------------------------------------------------------------------------

def load_corpus(path: Path) -> list[CorpusItem]:
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            items.append(CorpusItem(**json.loads(line)))
    return items


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).with_name("results") / "corpus.jsonl"))
    ap.add_argument("--seeds", type=int, default=8)
    ap.add_argument("--model", default="")
    ap.add_argument("--temperature", type=float, default=0.2)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    settings = get_settings()
    if not settings.has_any_key():
        print("no provider key in the environment", file=sys.stderr)
        return 2
    if settings.cache:
        print("CHIMERA_CACHE is on — the corpus would be served from cache; aborting", file=sys.stderr)
        return 2
    from chimera.providers.catalog import resolve_tiers

    model = args.model or resolve_tiers(settings).mid
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = {i.item_id for i in load_corpus(out)} if out.exists() else set()
    plan = [(d, s) for d in DOMAINS for s in range(args.seeds) if f"{d.key}-{s}" not in done]
    print(f"worker model {model}; {len(plan)} items to generate, {len(done)} already on disk")
    from concurrent.futures import ThreadPoolExecutor, as_completed

    spent, short = 0.0, 0
    t0 = time.monotonic()
    with out.open("a", encoding="utf-8") as fh, ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(generate_item, d, s, model=model, temperature=args.temperature): (d.key, s)
                   for d, s in plan}
        for fut in as_completed(futures):
            key, seed = futures[fut]
            try:
                item = fut.result()
            except Exception as exc:  # noqa: BLE001 — a provider hiccup must not lose the file
                print(f"  {key:<18} #{seed} ERROR {type(exc).__name__}: {str(exc)[:120]}", flush=True)
                continue
            if item is None:
                short += 1
                print(f"  {key:<18} #{seed} DISCARDED (short twice)", flush=True)
                continue
            spent += item.usd or 0.0
            fh.write(json.dumps(asdict(item), ensure_ascii=False) + "\n")
            fh.flush()
            print(f"  {key:<18} #{seed} {item.raw_chars:>6} chars"
                  f"{' (regenerated)' if item.regenerated else ''}  Σ US$ {spent:.4f}", flush=True)
    print(f"written {out}; discarded {short}; {time.monotonic() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
