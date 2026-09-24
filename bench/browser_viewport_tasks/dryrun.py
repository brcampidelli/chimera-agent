"""The dry-run: every check the apparatus must pass before a paid solve, and no model call.

1. **Suite** — 24 tasks, unique ids, the registered strata; each checker accepts three right answers
   and rejects three wrong ones (a decoy code or value, the right one next to a wrong one, a
   non-answer).
2. **Site** — the generator rebuilds the same bytes the manifest pins, and the files on disk match.
3. **Server** — every page answers 200, a `/go/` page shows its code, a missing path is a 404.
4. **Codes** — the JavaScript in the page and the Python checker derive the same code from a key.
5. **Placement, measured** — on the real browser, each task's target sits where its stratum says:
   an `in_view` target is in the first viewport, a `below` target is below it and nowhere in view.
6. **Reachability, both arms** — the scripted solution reaches the answer through the product's tool
   in each arm, and the checker accepts it. A target the script cannot reach viewport-first would
   mean the variant made it unreachable.
7. **The paid path, without the model** — `solve_one.solve` run end to end (agent loop, counted tool,
   server, checker, receipt) with a scripted stand-in backend, once per arm; the arm's schema must be
   what reaches the backend.
8. **Cost projection** — tokens along the scripted path per arm, priced at the model's worst seen
   rate, times the registered inflation for a model's detours.
"""

from __future__ import annotations

import json
import statistics
import time
import urllib.error
import urllib.request
from typing import Any

from bench.browser_viewport_tasks import build_site
from bench.browser_viewport_tasks.common import code_for
from bench.browser_viewport_tasks.harness import ARMS, browser_for
from bench.browser_viewport_tasks.server import HOMES, SiteServer
from bench.browser_viewport_tasks.tasks import PAGES, TASKS, Script

INFLATE = 3.0
"""Registered assumption: a model spends three times the scripted path's tokens (detours, re-reads,
a wrong click and back). The step-1 pages give the path; this factor is the guess on top of it."""
CHARS_PER_TOKEN = 4.0
"""Registered assumption for English page text and JSON: four characters a token."""
CALL_OVERHEAD_CHARS = 120
"""The tool call's own JSON (name, arguments, id) that the next prompt carries, per call."""
COMPLETION_PER_CALL, COMPLETION_FINAL = 60, 60


class Failures(list[str]):
    def need(self, ok: bool, what: str) -> bool:
        if not ok:
            self.append(what)
        return ok


def check_suite(fail: Failures) -> dict[str, Any]:
    ids = [t.id for t in TASKS]
    fail.need(len(TASKS) == 24, f"expected 24 tasks, found {len(TASKS)}")
    fail.need(len(set(ids)) == len(ids), "duplicate task ids")
    for t in TASKS:
        for good in t.right():
            fail.need(t.check.passes(good), f"{t.id}: checker rejects the right answer {good!r}")
        for bad in t.wrong:
            fail.need(not t.check.passes(bad), f"{t.id}: checker accepts the wrong answer {bad!r}")
        fail.need(len(t.wrong) >= 3, f"{t.id}: fewer than three wrong answers")
    return {
        "tasks": len(TASKS),
        "in_view": sum(t.stratum == "in_view" for t in TASKS),
        "below": sum(t.stratum == "below" for t in TASKS),
        "hurt_prone": [t.id for t in TASKS if t.hurt_prone],
        "checkers": {k: sum(t.check.kind == k for t in TASKS) for k in ("code", "number", "phrase", "substring")},
    }


def check_site(fail: Failures) -> dict[str, Any]:
    rebuilt = build_site.manifest(build_site.build().items())
    pinned = json.loads((build_site.SITE / "MANIFEST.json").read_text(encoding="utf-8"))
    fail.need(rebuilt == pinned, "the generator no longer writes the bytes the manifest pins")
    on_disk = build_site.manifest(
        (rel, (build_site.SITE / rel).read_bytes().decode("utf-8")) for rel in pinned
    )
    fail.need(on_disk == pinned, "the files on disk differ from the manifest")
    return {"pages": len(pinned), "manifest_matches": rebuilt == pinned and on_disk == pinned}


def check_server(origin: str, fail: Failures) -> None:
    def get(path: str) -> tuple[int, str]:
        try:
            with urllib.request.urlopen(origin + path, timeout=10) as resp:  # noqa: S310 — our own local server
                return resp.status, resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, ""

    for home, _ in HOMES.values():
        fail.need(get(home)[0] == 200, f"{home} is not served")
    status, body = get("/go/wiki/wave-power")
    fail.need(status == 200 and code_for("go/wiki/wave-power") in body, "a /go/ page does not show its code")
    fail.need(get("/no/such/page.html")[0] == 404, "a missing path is not a 404")


def check_js_codes(driver: Any, origin: str, fail: Failures) -> None:
    driver.navigate(origin + PAGES["gov"])
    keys = ["cart|p37", "gov-contact|maria.silva@example.org|address change", "go/wiki/x", "a", ""]
    for key in keys:
        js = driver._page.evaluate("(k) => m7code(k)", key)
        fail.need(js == code_for(key), f"JS and Python disagree on {key!r}: {js} vs {code_for(key)}")


def check_placement(driver: Any, origin: str, fail: Failures) -> dict[str, Any]:
    """From the driver's own snapshot of each start page, freshly loaded (scrolled to the top)."""
    out: dict[str, Any] = {}
    for t in TASKS:
        name, occurrence = t.target
        els = [e for e in driver.navigate(origin + PAGES[t.site]) if e.name == name]
        if not fail.need(bool(els), f"{t.id}: no element named {name!r} on its start page"):
            continue
        where = els[occurrence].where
        places = sorted({e.where or "?" for e in els})
        if t.stratum == "in_view":
            fail.need(where == "in", f"{t.id}: target {name!r} is {where}, registered in view")
        else:
            fail.need(where == "below", f"{t.id}: target {name!r} is {where}, registered below")
            if occurrence == 0:
                fail.need("in" not in places, f"{t.id}: {name!r} is ALSO in view, so it is not below-only")
        out[t.id] = {"target": name, "where": where, "all_places": places}
    return out


def page_stats(driver: Any, origin: str) -> dict[str, Any]:
    from chimera.tools.browser import render_elements, render_viewport_first

    rows = {}
    for site, path in PAGES.items():
        els = driver.navigate(origin + path)
        rows[site] = {
            "elements": len(els),
            "offscreen": sum(1 for e in els if e.where != "in"),
            "chars_today": len(render_elements(els)),
            "chars_viewport": len(render_viewport_first(els)),
        }
    counts = [r["elements"] for r in rows.values()]
    return {
        "pages": rows,
        "elements_median": statistics.median(counts),
        "offscreen_share_median": statistics.median(r["offscreen"] / r["elements"] for r in rows.values()),
    }


def run_scripts(origin: str, fail: Failures) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {arm: {} for arm in ARMS}
    for arm in ARMS:
        with browser_for(origin, arm) as (tool, driver):
            for t in TASKS:
                before = len(tool.log.calls)
                started = time.monotonic()
                try:
                    answer = t.solve(Script(tool, arm, origin, driver))
                    error = ""
                except Exception as exc:  # noqa: BLE001 — reported as an unreachable target
                    answer, error = "", f"{type(exc).__name__}: {exc}"
                calls = tool.log.calls[before:]
                ok = fail.need(not error and t.check.passes(answer),
                               f"{t.id} [{arm}]: scripted answer {answer!r} not accepted {error}".rstrip())
                out[arm][t.id] = {
                    "answer": answer, "accepted": ok, "error": error,
                    "calls": [c["action"] for c in calls], "chars": [c["chars"] for c in calls],
                    "seconds": round(time.monotonic() - started, 2),
                }
    return out


def project(scripts: dict[str, dict[str, Any]], k: int, model: str) -> dict[str, Any]:
    from chimera.core.agent import DEFAULT_SYSTEM_PROMPT
    from chimera.providers.catalog import CATALOG
    from chimera.tools.browser import BrowserTool

    entry = next(e for e in CATALOG if e.slug == model)
    price_in = max([entry.input_per_m or 0.0] + [p[0] for p in entry.also_seen])
    price_out = max([entry.output_per_m or 0.0] + [p[1] for p in entry.also_seen])
    per_arm: dict[str, Any] = {}
    for arm, rows in scripts.items():
        schema = json.dumps(BrowserTool(viewport_first=(arm == "viewport")).to_openai_schema())
        prompts, usds = [], []
        for t in TASKS:
            row = rows[t.id]
            base = (len(DEFAULT_SYSTEM_PROMPT) + len(schema) + len(t.prompt("http://127.0.0.1:65535"))) / CHARS_PER_TOKEN
            carried, total = 0.0, 0.0
            for chars in row["chars"] + [None]:  # one model call per tool call, and one to answer
                total += base + carried
                if chars is not None:
                    carried += (chars + CALL_OVERHEAD_CHARS) / CHARS_PER_TOKEN
            completion = COMPLETION_PER_CALL * len(row["chars"]) + COMPLETION_FINAL
            prompts.append(total)
            usds.append((total * price_in + completion * price_out) / 1e6)
        per_arm[arm] = {
            "scripted_prompt_tokens_mean": round(statistics.mean(prompts)),
            "scripted_prompt_tokens_max": round(max(prompts)),
            "scripted_usd_per_solve_mean": statistics.mean(usds),
            "projected_usd_per_solve": statistics.mean(usds) * INFLATE,
        }
    solves_per_arm = len(TASKS) * k
    total = sum(per_arm[a]["projected_usd_per_solve"] * solves_per_arm for a in per_arm)
    return {
        "model": model, "price_in_per_m": price_in, "price_out_per_m": price_out, "inflate": INFLATE,
        "k": k, "solves": solves_per_arm * len(per_arm), "per_arm": per_arm, "projected_total_usd": total,
    }


class StandIn:
    """Plays a model through W1 — navigate, click the target by the ref it READS in the observation,
    read the page, answer — so `solve_one.solve` runs end to end with no model and no network. It
    answers as a local model (`ollama/...`), which the receipt prices at zero, never as unknown."""

    TARGET = "Rance Tidal Power Station"

    def __init__(self) -> None:
        self.turn = 0
        self.schemas: list[str] = []

    @staticmethod
    def _text(message: Any) -> str:
        return str(message.get("content") if isinstance(message, dict) else getattr(message, "content", ""))

    def complete(self, messages: list[Any], *, tools: Any = None, **_: Any) -> Any:
        import re

        from chimera.providers import CompletionResult, ToolCall

        self.schemas.append(json.dumps(tools or []))
        self.turn += 1
        text = self._text(messages[-1])
        args: dict[str, Any] | None = None
        if self.turn == 1:
            url = re.search(r"\((http://127\.0\.0\.1:\d+/[^)\s]+)\)", text)
            args = {"action": "navigate", "url": url.group(1) if url else ""}
        elif self.turn == 2:
            ref = re.search(r"\[(e\d+)\] link: " + re.escape(self.TARGET), text)
            args = {"action": "click", "ref": ref.group(1) if ref else "e0"}
        elif self.turn == 3:
            args = {"action": "read_text"}
        if args is not None:
            call = ToolCall(id=f"c{self.turn}", name="browser", arguments=args)
            return CompletionResult(content="", model="ollama/stand-in", tool_calls=[call],
                                    prompt_tokens=100, completion_tokens=10)
        code = re.search(r"\b[A-Z]{2}\d{4}[A-Z]\b", text)
        return CompletionResult(content=code.group(0) if code else "no code", model="ollama/stand-in",
                                prompt_tokens=100, completion_tokens=10)


def check_solve_path(fail: Failures) -> dict[str, Any]:
    """`solve_one.solve` — the paid path — with the stand-in: a record per arm, success, a zero
    receipt, the arm's schema reaching the model (scroll offered viewport-first only)."""
    from bench.browser_viewport_tasks.solve_one import MODEL, solve

    out: dict[str, Any] = {}
    for arm in ARMS:
        stand_in = StandIn()
        rec = solve("W1", arm, 0, MODEL, backend=stand_in)
        offers_scroll = '"scroll"' in (stand_in.schemas[0] if stand_in.schemas else "")
        fail.need(not rec.get("error"), f"solve path [{arm}]: error {rec.get('error')!r}")
        fail.need(rec.get("success") is True, f"solve path [{arm}]: answer {rec.get('answer')!r} not accepted")
        fail.need(rec.get("usd") == 0.0 and rec.get("cap_usd") is not None, f"solve path [{arm}]: receipt {rec.get('usd')!r}")
        fail.need(rec.get("browser_actions") == {"navigate": 1, "click": 1, "read_text": 1},
                  f"solve path [{arm}]: actions {rec.get('browser_actions')}")
        fail.need(offers_scroll == (arm == "viewport"), f"solve path [{arm}]: scroll offered = {offers_scroll}")
        out[arm] = {k: rec.get(k) for k in ("success", "answer", "steps", "usd", "cap_usd", "observation_chars",
                                             "browser_actions", "error")} | {"offers_scroll": offers_scroll}
    return out


def dry_run(k: int, model: str, max_usd: float) -> tuple[dict[str, Any], list[str]]:
    fail = Failures()
    report: dict[str, Any] = {"suite": check_suite(fail), "site": check_site(fail)}
    with SiteServer() as origin:
        check_server(origin, fail)
        with browser_for(origin, "today") as (_tool, driver):
            check_js_codes(driver, origin, fail)
            report["placement"] = check_placement(driver, origin, fail)
            report["pages"] = page_stats(driver, origin)
        report["scripts"] = run_scripts(origin, fail)
    report["solve_path"] = check_solve_path(fail)
    report["projection"] = project(report["scripts"], k, model)
    fail.need(report["projection"]["projected_total_usd"] < max_usd,
              f"projected cost US$ {report['projection']['projected_total_usd']:.2f} is not under the cap")
    report["failures"] = list(fail)
    return report, list(fail)
