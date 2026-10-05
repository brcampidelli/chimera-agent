"""The paired encoded-secret corpus registered in `PREREGISTRATION.md` (study 30, S30-32).

    python bench/encoded_secrets/run.py --out bench/encoded_secrets/results/<date>.json

Deterministic, offline, US$ 0. Every text is redacted three ways with the same 200 synthetic secrets
in the environment: by `chimera/core/redact.py` as it was at the base commit (``old``), by today's
module with the encoded pass off (``off``) and on (``on``). ``old == off`` on every text is the
control that the toggle moves nothing else; ``on`` against ``off`` is the reading.

The secrets are generated here from a fixed seed and live only in this process's environment, under
names that end in ``_TOKEN``; nothing secret-shaped is written to the results file (it holds counts).
"""

from __future__ import annotations

import argparse
import base64
import codecs
import hashlib
import json
import math
import os
import random
import re
import string
import subprocess
import sys
import time
import types
import uuid
from collections import Counter, defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

import chimera.core.redact as redact_module  # noqa: E402

SEED = 20261005
BASE_COMMIT = "ff1f983e"  # origin/main when the corpus was registered: the code before the change
N_PER_SHAPE = 50
CONTEXTS = (
    "Here is the credential, encoded for the other agent: {x}",
    '{{"note": "for the deploy job", "blob": "{x}"}}',
)


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


ENCODINGS: dict[str, Callable[[str], str]] = {
    "b64": lambda s: _b64(s.encode()),
    "b64_unpadded": lambda s: _b64(s.encode()).rstrip("="),
    "b64_urlsafe": lambda s: base64.urlsafe_b64encode(s.encode()).decode("ascii"),
    "b64_in_blob_1": lambda s: _b64(b"a:" + s.encode()),
    "b64_in_blob_2": lambda s: _b64(b"ab:" + s.encode()),
    "b64_in_blob_3": lambda s: _b64(b"abc:" + s.encode()),
    "hex": lambda s: s.encode().hex(),
    "hex_upper": lambda s: s.encode().hex().upper(),
    "dec_comma": lambda s: ", ".join(str(b) for b in s.encode()),
    "dec_space": lambda s: " ".join(str(b) for b in s.encode()),
    "x_escapes": lambda s: "".join(f"\\x{b:02x}" for b in s.encode()),
    "reversed": lambda s: s[::-1],
    # Addendum A: the forms the first run could not show. None draws from the generator, so the
    # original 200 secrets and their texts are byte-identical to the first run.
    "hex_spaced": lambda s: s.encode().hex(" "),
    "hex_colon": lambda s: s.encode().hex(":"),
    "hex_xxd": lambda s: s.encode().hex(" ", 2),
    "json_u": lambda s: "".join(f"\\u{b:04x}" for b in s.encode()),
    "html_dec": lambda s: "".join(f"&#{b};" for b in s.encode()),
    "html_hex": lambda s: "".join(f"&#x{b:x};" for b in s.encode()),
}
#: Percent forms, read only where they differ from `s` (an unreserved secret IS its own encoding).
PERCENT: dict[str, Callable[[str], str]] = {
    "percent": lambda s: quote(s, safe=""),
    "percent_default": lambda s: quote(s),
    # The escapes in lower case, the characters kept: the first addendum run used `.lower()` on the
    # whole string, which lowercases the secret's own letters and so encodes a DIFFERENT value
    # (0/398 there, a corpus defect, kept in results/2026-10-05b-addendum-a.json).
    "percent_lower": lambda s: re.sub(r"%[0-9A-F]{2}", lambda m: m.group().lower(), quote(s, safe="")),
}
#: Addendum A: forms over code points, which differ from the byte forms only for a non-ASCII secret.
CODE_POINTS: dict[str, Callable[[str], str]] = {
    "dec_ord": lambda s: ", ".join(str(ord(c)) for c in s),
    "json_u_points": lambda s: "".join(f"\\u{ord(c):04x}" for c in s),
    "html_points": lambda s: "".join(f"&#{ord(c)};" for c in s),
}


def _b64_cut(s: str) -> str:
    blob = _b64(s.encode())
    return blob[: len(blob) // 2] + "\n" + blob[len(blob) // 2 :]


UNCOVERED: dict[str, Callable[[str], str]] = {
    "split_lines": lambda s: s[: len(s) // 2] + "\n" + s[len(s) // 2 :],
    "rot13": lambda s: codecs.encode(s, "rot13"),
    "b64_wrapped": _b64_cut,
}
SEED_NOT_ASCII = 20261006
NOT_ASCII = string.ascii_letters + string.digits + "çãéõüñß€"
#: Addendum A: ordinary texts with the shapes the widened patterns accept, none carrying a secret.
ORDINARY_ADDENDUM = (
    "eth0: link/ether 3c:22:fb:9a:10:4e brd ff:ff:ff:ff:ff:ff",
    "00000000: 7f45 4c46 0201 0100 0000 0000 0000 0000  .ELF............",
    "00000010: 0300 3e00 0100 0000 1010 0000 0000 0000  ..>.............",
    "GET https://example.test/search?q=caf%C3%A9+com+leite&page=2 HTTP/1.1",
    "<p>Pre&#231;o: R&#36; 12,90 &#x2014; caf&eacute; &amp; p&#227;o</p>",
    '{"nome": "Jos\\u00e9", "cidade": "S\\u00e3o Paulo", "id": 4815162342}',
    "bytes: 0x7f 0x45 0x4c 0x46 0x02 0x01 0x01 0x00",
    "Checksums: 9a0364b9e99bb480dd25e1f0284c8555 d41d8cd98f00b204e9800998ecf8427e",
    "codes = [72, 101, 108, 108, 111, 44, 32, 119, 111, 114, 108, 100]",
    "SELECT * FROM t WHERE name LIKE '%25off%' AND price > 10;",
)

ALNUM = string.ascii_letters + string.digits
SHAPES: dict[str, str] = {
    "alnum": ALNUM,
    "separated": ALNUM + "-_.",
    "hex": "0123456789abcdef",
    "mixed": ALNUM + "/+=:@%",
}


def _value(rng: random.Random, alphabet: str, n: int) -> str:
    return "".join(rng.choice(alphabet) for _ in range(n))


def secrets(rng: random.Random) -> list[tuple[str, str]]:
    out = []
    for shape, alphabet in SHAPES.items():
        for _ in range(N_PER_SHAPE):
            out.append((shape, _value(rng, alphabet, rng.randint(8, 64))))
    return out


def ordinary_texts(rng: random.Random) -> list[str]:
    """Forty ordinary texts: prose, Python, JSON, `git log`, a shell session — with real-looking
    hashes, numbers and base64 in them, none of which carries a secret."""
    pieces = [
        "The build finished in 41 s; 1,848 tests passed and 2 were skipped.",
        "def checksum(data: bytes) -> str:\n    return hashlib.sha256(data).hexdigest()\n",
        '{"name": "chimera", "version": "0.64.4", "ports": [8080, 8443], "retries": 3}',
        "$ ls -la\ntotal 48\ndrwxr-xr-x  5 user user 4096 Oct  5 08:47 .\n",
        "Reverse the list with xs[::-1] and join it with ', '.",
        "ASCII codes for 'hello': 104, 101, 108, 108, 111",
        "data:image/png;base64,iVBORw0KGgo",
    ]
    texts = []
    for i in range(40):
        sha = hashlib.sha1(f"commit-{i}".encode()).hexdigest()
        blob = _b64(rng.randbytes(48))
        texts.append(f"commit {sha}\n{pieces[i % len(pieces)]}\n{blob}\n{uuid.UUID(int=rng.getrandbits(128))}")
    return texts


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def _old_module() -> types.ModuleType:
    source = subprocess.run(
        ["git", "show", f"{BASE_COMMIT}:chimera/core/redact.py"], cwd=REPO, check=True, capture_output=True,
    ).stdout.decode("utf-8")
    module = types.ModuleType("redact_at_base")
    exec(compile(source, "redact_at_base.py", "exec"), module.__dict__)
    return module


def _passes(old: types.ModuleType) -> dict[str, Callable[[str], str]]:
    def off(text: str) -> str:
        redact_module.MASK_ENCODED = False
        try:
            return redact_module.redact(text)
        finally:
            redact_module.MASK_ENCODED = True

    def on(text: str) -> str:
        redact_module.MASK_ENCODED = True
        return redact_module.redact(text)

    return {"old": old.redact, "off": off, "on": on}


def build(rng: random.Random, known: list[tuple[str, str]], *, ordinary: bool = True,
          first: int = 0) -> list[dict[str, Any]]:
    """Every text of the corpus, with what it carries — never the secret itself."""
    rows: list[dict[str, Any]] = []
    alphabets = {**SHAPES, "not_ascii": NOT_ASCII}
    for i, (shape, s) in enumerate(known, start=first):
        other = _value(rng, alphabets[shape], len(s))
        near = ("Q" if s[0] != "Q" else "R") + s[1:]
        for c, context in enumerate(CONTEXTS):
            def add(stratum: str, kind: str, form: str, shape: str = shape, i: int = i, c: int = c,
                    context: str = context) -> None:
                rows.append({"stratum": stratum, "kind": kind, "shape": shape, "secret": i, "context": c,
                             "form": form, "text": context.format(x=form)})

            add("literal", "literal", s)
            for name, enc in ENCODINGS.items():
                add("encoded", name, enc(s))
                add("absent", f"other_{name}", enc(other))
            for name, enc in PERCENT.items():
                if enc(s) != s:
                    add("encoded", name, enc(s))
                if enc(other) != other:
                    add("absent", f"other_{name}", enc(other))
            for name, enc in CODE_POINTS.items():
                if s.isascii():
                    continue  # identical to a byte form already in the corpus
                add("encoded", name, enc(s))
                add("absent", f"other_{name}", enc(other))
            add("uncovered", "split_lines", UNCOVERED["split_lines"](s))
            add("uncovered", "b64_wrapped", UNCOVERED["b64_wrapped"](s))
            if shape != "hex" and codecs.encode(s, "rot13") != s:
                add("uncovered", "rot13", UNCOVERED["rot13"](s))
            add("absent", "sha256_of_secret", hashlib.sha256(s.encode()).hexdigest())
            add("absent", "uuid", str(uuid.UUID(int=rng.getrandbits(128))))
            add("absent", "b64_random_300", _b64(rng.randbytes(300)))
            add("absent", "codes_random_16", ", ".join(str(rng.randint(32, 126)) for _ in range(16)))
            add("absent", "near_miss_b64", _b64(near.encode()))
            add("absent", "near_miss_hex", near.encode().hex())
    if ordinary:
        for j, text in enumerate(ordinary_texts(rng)):
            rows.append({"stratum": "absent", "kind": "ordinary", "shape": "-", "secret": -1, "context": j,
                         "form": "", "text": text})
    return rows


def cost(passes: dict[str, Callable[[str], str]], rng: random.Random, *, n_secrets: int = 3,
         size: int = 10_000, reps: int = 200, line: str = "") -> dict[str, float]:
    """Median microseconds of one call on a ``size``-character text with ``n_secrets`` known."""
    saved = {k: v for k, v in os.environ.items() if k.startswith("ENC_BENCH_")}
    for k in saved:
        del os.environ[k]
    try:
        for n in range(n_secrets):
            os.environ[f"ENC_BENCH_COST_{n}_TOKEN"] = _value(rng, ALNUM, 32)
        line = line or "The build finished in 41 s; 1,848 tests passed. "
        text = (line * (size // len(line) + 1))[:size]
        out: dict[str, float] = {}
        for name in ("off", "on"):
            passes[name](text)  # warm the caches
            times = []
            for _ in range(reps):
                t0 = time.perf_counter()
                passes[name](text)
                times.append(time.perf_counter() - t0)
            times.sort()
            out[name] = round(times[len(times) // 2] * 1e6, 1)
        return out
    finally:
        for n in range(n_secrets):
            os.environ.pop(f"ENC_BENCH_COST_{n}_TOKEN", None)
        os.environ.update(saved)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    rng = random.Random(SEED)
    known = secrets(rng)
    for k in [k for k in os.environ if any(m in k.upper() for m in redact_module._SECRET_MARKERS)]:
        del os.environ[k]  # only the corpus's secrets are known, so the reading is about them
    for i, (_shape, s) in enumerate(known):
        os.environ[f"ENC_BENCH_{i:03d}_TOKEN"] = s
    rows = build(rng, known)
    # Addendum A: 50 secrets that are not ASCII, from their own generator so the first 200 and every
    # text built from them are byte-identical to the first run; and the ordinary texts with the
    # shapes the widened patterns accept.
    rng_na = random.Random(SEED_NOT_ASCII)
    extra = [("not_ascii", _value(rng_na, NOT_ASCII, rng_na.randint(8, 64))) for _ in range(N_PER_SHAPE)]
    for i, (_shape, s) in enumerate(extra, start=len(known)):
        os.environ[f"ENC_BENCH_{i:03d}_TOKEN"] = s
    rows += build(rng_na, extra, ordinary=False, first=len(known))
    for j, text in enumerate(ORDINARY_ADDENDUM):
        rows.append({"stratum": "absent", "kind": "ordinary_addendum", "shape": "-", "secret": -1,
                     "context": j, "form": "", "text": text})
    known = known + extra
    passes = _passes(_old_module())

    masked: Counter[str] = Counter()
    total: Counter[str] = Counter()
    fp: Counter[str] = Counter()
    control_breaks = 0
    literal_ok = {"off": 0, "on": 0}
    by_shape: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        out = {name: fn(row["text"]) for name, fn in passes.items()}
        if out["old"] != out["off"]:
            control_breaks += 1
        kind = row["kind"]
        total[kind] += 1
        if row["stratum"] == "literal":
            for name in ("off", "on"):
                literal_ok[name] += row["form"] not in out[name]
        elif row["stratum"] in ("encoded", "uncovered"):
            hit = row["form"] not in out["on"]
            masked[kind] += hit
            by_shape[kind][row["shape"]] += 0 if hit else 1
        elif out["on"] != out["off"]:
            fp[kind] += 1

    absent_kinds = sorted({r["kind"] for r in rows if r["stratum"] == "absent"})
    n_absent = sum(total[k] for k in absent_kinds)
    n_fp = sum(fp.values())
    result: dict[str, Any] = {
        "seed": SEED, "base_commit": BASE_COMMIT, "secrets": len(known), "texts": len(rows),
        "control": {"old_equals_off_breaks": control_breaks, "literal_masked": literal_ok, "literal_n": total["literal"]},
        "masked": {
            k: {"masked": masked[k], "n": total[k], "rate": round(masked[k] / total[k], 4),
                "wilson95": [round(x, 4) for x in wilson(masked[k], total[k])],
                "misses_by_shape": dict(by_shape[k])}
            for k in [*ENCODINGS, *PERCENT, *CODE_POINTS, *UNCOVERED]
        },
        "false_positives": {k: {"fp": fp[k], "n": total[k]} for k in absent_kinds},
        "false_positives_total": {"fp": n_fp, "n": n_absent,
                                  "wilson95_upper": round(wilson(n_fp, n_absent)[1], 5)},
        "cost_us_median_10kB_3_secrets": cost(passes, rng),
        # Addendum A: the deployment knows tens of credentials, not three; and a log is not prose.
        "cost_us_median_10kB_30_secrets": cost(passes, rng, n_secrets=30),
        "cost_us_median_1MB_30_secrets": cost(passes, rng, n_secrets=30, size=1_000_000, reps=7),
        "cost_us_median_10kB_log_30_secrets": cost(
            passes, rng, n_secrets=30, line="\n".join(ORDINARY_ADDENDUM) + "\n",
        ),
    }
    covered = [*ENCODINGS, *PERCENT, *CODE_POINTS]
    decision_on = n_fp == 0 and literal_ok["off"] == literal_ok["on"] == total["literal"] and control_breaks == 0
    below = [k for k in covered if masked[k] / total[k] < 0.99]
    result["decision"] = ("ON" if decision_on and not below else
                          "DEFECT: covered form below 99%, fix and re-run" if decision_on else "OFF")
    result["covered_below_99"] = below
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
