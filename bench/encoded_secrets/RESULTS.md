# Encoded forms of a known secret — results

Registered in `PREREGISTRATION.md` (commit `38657ed1`) before the masking existed. Run 2026-10-05,
`python bench/encoded_secrets/run.py --out bench/encoded_secrets/results/2026-10-05.json`, 22 s,
US$ 0. 200 synthetic secrets, 13,234 texts, each redacted by the code at the base commit (`old`), by
today's code with the encoded pass off (`off`) and on (`on`).

## Decision: **ON**

Every condition of the registered rule held: zero false positives, the literal control intact, and
the toggle moving nothing else. `chimera/core/redact.py` ships `MASK_ENCODED = True`, and a test holds
that constant equal to this run's `decision` field.

> **Correction (Addendum A, same day).** The `percent` row below (94/94) was **circular**: the corpus
> generated the form with `quote(s, safe="")`, the same call the code then matched as a literal, so it
> could not show that `quote(s)` with its default `safe="/"` — the common call — or lower-case escapes
> passed intact. They did, and so did spaced hex (`bytes.hex(" ")`, `xxd`), JSON `\u` and HTML `&#`
> escapes, and a non-ASCII secret written as `ord()` codes. An adversarial review found it; the
> corpus was widened in `PREREGISTRATION.md` (Addendum A) before the code changed, and the decision
> was read again — still **ON**, on the wider corpus, in the section *Addendum A* at the end. Read the
> first run's numbers as what that corpus could show, not as coverage of "percent-encoding".

## Control

| check | result |
|---|---|
| `old == off` on every text | 13,234 / 13,234 (0 breaks) |
| literal secret masked, `off` | 400 / 400 |
| literal secret masked, `on` | 400 / 400 |

## Masked (the encoded string no longer appears in the `on` output)

| form | masked | n | Wilson 95% |
|---|---:|---:|---|
| base64 | 400 | 400 | 0.990 – 1 |
| base64, unpadded | 400 | 400 | 0.990 – 1 |
| base64, URL-safe | 400 | 400 | 0.990 – 1 |
| base64 inside a blob, offset 2 (`a:` + s) | 400 | 400 | 0.990 – 1 |
| base64 inside a blob, offset 0 (`ab:` + s) | 400 | 400 | 0.990 – 1 |
| base64 inside a blob, offset 1 (`abc:` + s) | 400 | 400 | 0.990 – 1 |
| hex | 400 | 400 | 0.990 – 1 |
| hex, upper case | 400 | 400 | 0.990 – 1 |
| decimal codes, comma | 400 | 400 | 0.990 – 1 |
| decimal codes, space | 400 | 400 | 0.990 – 1 |
| `\x` escapes | 400 | 400 | 0.990 – 1 |
| reversed | 400 | 400 | 0.990 – 1 |
| percent-encoded (the 94 texts where it differs) | 94 | 94 | 0.961 – 1 |
| **uncovered:** split across two lines | **0** | 400 | 0 – 0.010 |
| **uncovered:** ROT13 | **0** | 300 | 0 – 0.013 |

The uncovered rows are the prediction holding, not a miss: they are the forms the docstring now
names as passing through.

**What the registered reading cannot show.** For the three in-blob forms, "the encoded string no
longer appears" is satisfied as soon as any part of the blob is masked, so 400/400 there says the
secret's run was found, not how much of it was covered. That part is pinned by
`tests/test_an_encoded_secret_is_still_the_secret.py`, which asserts that the mask covers all but at
most one partial character at each end of the run — at most four bits of the first and last byte.

## False positives (the `on` output differs from `off` on a text that carries no secret)

**0 of 7,240**, Wilson upper bound 0.00053. Per kind, 0 in each: the 12 encodings of a different
value of the same shape and length (400 each), a SHA-256 of the secret, a UUID, 300 random bytes in
base64, 16 random character codes, the near miss (first character changed) in base64 and in hex
(400 each), and 40 ordinary texts with real-looking hashes, numbers and base64 in them.

## Cost

Median of one `redact` call on a 10 kB text with 3 known secrets: **1,273 µs off, 1,842 µs on**
(+45%, one machine, not gated). The first version cached the encodings per secret with a bound of
128 and compiled each pattern separately; with this corpus's 200 secrets that thrashed both caches
and the run did not finish in ten minutes. It now caches per secret SET and compiles one alternation,
which is what `known_secrets()` returning the same list on every call allows.

## Addendum A — the forms the first run could not show

Registered in `PREREGISTRATION.md` (Addendum A, commit `84c67901`) before the masking changed. Two
runs, both kept:

* `results/2026-10-05b-addendum-a.json` — the first fix (one regex alternation per secret, never committed). Decision
  **DEFECT**: `json_u`, `html_dec`, `html_hex` 404/500 (all 96 misses on the non-ASCII secrets, written
  as `\u00c3\u00a7` per UTF-8 byte, a reading that code did not have) and `percent_lower` 0/398. The
  second is a **corpus** defect, not a masking one: the registered generator `quote(s, safe="").lower()`
  lowercases the secret's own letters too, so it encodes a different value. `run.py` now lowercases
  only the `%HH` escapes; this is the one change to a registered definition, and it is stated here
  rather than folded in. That run also took over ten minutes (`re` tries every branch of a 250-secret
  alternation at every position), which is what moved the code to the run-decoding design.
* `results/2026-10-05c-addendum-a.json` — the shipped code. **Decision: ON**, by the unchanged rule.

250 secrets (the original 200, byte-identical, plus 50 not ASCII), 24,660 texts, 1 min 22 s, US$ 0.

| check | result |
|---|---|
| `old == off` on every text | 24,660 / 24,660 |
| literal secret masked, `off` / `on` | 500 / 500, 500 / 500 |
| false positives | **0 of 12,912** (Wilson upper 0.0003), 0 in each of 32 absent kinds, including the 10 ordinary texts with MAC addresses, `xxd` lines, `%`-URLs, HTML entities and JSON `\u` escapes |

| form | masked | n |
|---|---:|---:|
| the 12 original forms, each | 500 | 500 |
| hex spaced / colon / `xxd` groups | 500 each | 500 |
| JSON `\u` per byte, HTML `&#N;`, HTML `&#xN;` | 500 each | 500 |
| `quote(s, safe="")` | 190 | 190 |
| `quote(s)`, default `safe="/"` | 180 | 180 |
| percent, lower-case escapes | 190 | 190 |
| `ord()` codes, JSON `\u` per character, `&#N;` per character (non-ASCII only) | 96 each | 96 |
| **uncovered:** split across lines | **0** | 500 |
| **uncovered:** ROT13 | **0** | 400 |
| **uncovered:** base64 cut by a newline (76-column wrapping) | **0** | 500 |

**What this corpus still cannot show.** Every encoded form here is generated by a function written
for the corpus, so each row says the code matches what that function writes. A form no generator
writes — a surrogate-pair escape, an escape mixed with plain characters, an encoder with a separator
not listed in the docstring — is unmeasured, and the docstring names the known ones as not covered.

**Cost** (median of one call, one machine, not gated; this machine's `off` baseline moved by ~40%
between runs, so read the ratios):

| text, known secrets | off | on | ratio |
|---|---:|---:|---:|
| 10 kB prose, 3 | 2,872 µs | 4,818 µs | 1.7× |
| 10 kB prose, 30 | 2,793 µs | 4,927 µs | 1.8× |
| 1 MB prose, 30 | 271 ms | 495 ms | 1.8× |
| 10 kB log (hex dumps, URLs, entities), 30 | 3,662 µs | 8,943 µs | 2.4× |

The review had measured ~7× at 30 secrets on the first fix; that design's own run here gave 8.7× on
the log text. The first run's "+45%" was at 3 secrets on prose only and did not represent a
deployment that knows tens of credentials.
