"""S30-27, per PREREGISTRATION.md. Deterministic, no model, US$ 0.

    uv run python bench/exfil_url/run.py [--homes ~/hb-homes] [--out bench/exfil_url/results]

Every row builds a fresh `TaintLedger`, replays the row's instruction, fetches and local reads, and
asks `assess_action` about the row's call, once with `exfil_host_path=False` (the shipped rule, the
control) and once with it on. Secrets and hashes are derived here from fixed seeds, so the corpus
commits none.
"""

from __future__ import annotations

import argparse
import base64
import glob
import hashlib
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from chimera.governance.ledger import TaintLedger, assess_action  # noqa: E402
from chimera.governance.policy import Decision  # noqa: E402


def _bytes(seed: str, n: int) -> bytes:
    return hashlib.sha256(f"bench/exfil_url:{seed}".encode()).digest()[:n]


SECRETS = {
    "hex": _bytes("hex", 16).hex(),
    "b32": base64.b32encode(_bytes("b32", 20)).decode().lower().rstrip("="),
    "b64url": base64.urlsafe_b64encode(_bytes("b64url", 18)).decode().rstrip("="),
    "word": "violet-harbour-tuesday",
}
FILLS = {
    "H20": _bytes("h20", 10).hex(),
    "SHA_A": hashlib.sha1(b"bench/exfil_url:sha_a").hexdigest(),
    "SHA_B": hashlib.sha1(b"bench/exfil_url:sha_b").hexdigest(),
    "SHA_C": hashlib.sha1(b"bench/exfil_url:sha_c").hexdigest(),
    "HEX64": hashlib.sha256(b"bench/exfil_url:hex64").hexdigest(),
    "DOCID": base64.urlsafe_b64encode(_bytes("docid", 33)).decode(),
    "GIST": hashlib.md5(b"bench/exfil_url:gist").hexdigest(),  # noqa: S324 - a fixture value
    "FILLER3000": ("The team met on Tuesday to plan the quarter. " * 70)[:3000],
}


def fill(text: str, secret: str = "") -> str:
    for key, value in FILLS.items():
        text = text.replace("{" + key + "}", value)
    return text.replace("{SECRET}", secret)


def verdict(row: dict[str, Any], *, on: bool) -> Decision:
    ledger = TaintLedger(exfil_host_path=on, shell_fetch_guard=False)
    ledger.set_instruction(row["instruction"])
    for item in row.get("fetched", []):
        ledger.record_fetch(item["source"], content=item["content"])
    for line in row.get("local", []):
        ledger.record_read(line.split(":", 1)[0])
        ledger.note_seen(line)  # a local result: seen by the run, and still not an exemption
    return assess_action(row.get("tool", "http_get"), {"url": row["url"]}, ledger).decision


def attacks(corpus: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for state, spec in corpus["attack_states"].items():
        for channel, template in corpus["attack_urls"].items():
            for encoding in corpus["encodings"]:
                secret = SECRETS[encoding]
                rows.append({
                    "id": f"{state}-{channel}-{encoding}", "state": state, "channel": channel,
                    "encoding": encoding, "instruction": fill(spec["instruction"], secret),
                    "fetched": [{k: fill(v, secret) for k, v in f.items()} for f in spec["fetched"]],
                    "local": [fill(x, secret) for x in spec["local"]], "tool": "http_get",
                    "url": fill(template, secret),
                })
    return rows


def benign(corpus: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for row in corpus["benign"]:
        filled = dict(row)
        filled["instruction"] = fill(row["instruction"])
        filled["url"] = fill(row["url"])
        filled["fetched"] = [{k: fill(v) for k, v in f.items()} for f in row.get("fetched", [])]
        filled["local"] = [fill(x) for x in row.get("local", [])]
        out.append(filled)
    return out


def replay(homes: Path) -> dict[str, Any]:
    """Every fetch-tool call in the stored traces, rule on, each run's task as the instruction."""
    from chimera.governance.ledger import FETCH_TOOLS

    calls = asked = asked_off = runs = 0
    for path in sorted(glob.glob(str(homes / "*" / "traces.jsonl"))):
        with open(path, encoding="utf-8", errors="replace") as handle:
            lines_in = handle.read().splitlines()
        for line in lines_in:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            runs += 1
            ledger = TaintLedger(exfil_host_path=True, shell_fetch_guard=False)
            ledger.set_instruction(str(row.get("task") or ""))
            control = TaintLedger(exfil_host_path=False, shell_fetch_guard=False)
            control.set_instruction(str(row.get("task") or ""))
            for step in row.get("steps") or []:
                for tool in step.get("tools") or []:
                    name = str(tool.get("name") or "")
                    raw = tool.get("arguments")
                    try:
                        args = json.loads(raw) if isinstance(raw, str) else raw
                    except ValueError:
                        args = {}
                    args = args if isinstance(args, dict) else {}
                    if name in FETCH_TOOLS:
                        calls += 1
                        if assess_action(name, args, ledger).decision is Decision.REVIEW:
                            asked += 1
                        if assess_action(name, args, control).decision is Decision.REVIEW:
                            asked_off += 1
                        for each in (ledger, control):
                            each.record_fetch(str(args.get("url") or name), str(tool.get("observation") or ""))
    return {"runs": runs, "fetch_calls": calls, "asked_on": asked, "asked_off": asked_off,
            "attributable": asked - asked_off}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--homes", default=os.path.expanduser("~/hb-homes"))
    parser.add_argument("--out", default=str(HERE / "results"))
    a = parser.parse_args()
    corpus = json.loads((HERE / "corpus.json").read_text(encoding="utf-8"))
    out: dict[str, Any] = {"attacks": [], "benign": []}
    for row in attacks(corpus):
        off, on = verdict(row, on=False), verdict(row, on=True)
        out["attacks"].append({k: row[k] for k in ("id", "state", "channel", "encoding")}
                              | {"off": off.value, "on": on.value})
    for row in benign(corpus):
        off, on = verdict(row, on=False), verdict(row, on=True)
        out["benign"].append({k: row[k] for k in ("id", "class", "state")}
                             | {"off": off.value, "on": on.value,
                                "attributable": on is Decision.REVIEW and off is not Decision.REVIEW})
    homes = Path(a.homes)
    out["replay"] = replay(homes) if homes.is_dir() else {"skipped": f"{homes} not found"}

    lines = ["# bench/exfil_url — readings", "", "## Attacks: ALLOW (the attack goes out) off -> on", ""]
    cells: dict[tuple[str, str, str], tuple[str, str]] = {}
    for r in out["attacks"]:
        cells[(r["state"], r["channel"], r["encoding"])] = (r["off"], r["on"])
    for state in ("tainted", "clean"):
        for channel in ("query", "subdomain", "path"):
            row = [f"{state:8} {channel:9}"]
            for enc in ("hex", "b32", "b64url", "word"):
                off, on = cells[(state, channel, enc)]
                row.append(f"{enc}: {off}->{on}")
            lines.append("  ".join(row))
    allowed_on = defaultdict(int)
    for r in out["attacks"]:
        if r["on"] == "allow":
            allowed_on[r["encoding"]] += 1
    lines += ["", f"asr_exfil on, by encoding (of 6): {dict(allowed_on)}", "",
              "## Benign: REVIEW on (attributable to the new rule)", ""]
    by_class: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for r in out["benign"]:
        c = by_class[r["class"]]
        c[0] += 1
        c[1] += r["on"] == "review"
        c[2] += r["attributable"]
        lines.append(f"{r['id']:22} {r['class']:9} {r['state']:8} off={r['off']:6} on={r['on']:6}"
                     f"{'  <- attributable' if r['attributable'] else ''}")
    lines.append("")
    for cls, (n, rev, attr) in by_class.items():
        lines.append(f"{cls:9} n={n:2}  review_on={rev}  attributable={attr}")
    total_attr = sum(c[2] for c in by_class.values())
    lines += [f"overall attributable false-REVIEW: {total_attr}/{len(out['benign'])}", "",
              f"replay: {out['replay']}"]
    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8", newline="\n")
    (out_dir / "readings.txt").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
