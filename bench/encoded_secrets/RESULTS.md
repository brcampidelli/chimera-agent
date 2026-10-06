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
>
> **Second correction (Addendum B, 2026-10-06).** Addendum A's corpus could not show either: a list
> of quoted units (`str([hex(b) …])`), the long `\U` escape and `encodeURIComponent` with an
> apostrophe all passed whole. Read again on a corpus that holds them — still **ON** — at the end.

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
and the run did not finish in ten minutes. That run's code cached per secret SET and compiled one
alternation, which is what `known_secrets()` returning the same list on every call allows. **That
mechanism is gone:** the shipped code still caches per set, but scans for decoded runs instead of
compiling an alternation (Addendum A below says why, and the module's `_RUNS` comment describes it).

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
*(Addendum B: it did not name them all. A list of quoted units — the separator was the quote — the
long `\U` escape and an unescaped apostrophe were neither covered nor named. See below.)*

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

## Addendum B — list shapes, the long `\U` escape, and `encodeURIComponent`

Registered in `PREREGISTRATION.md` (Addendum B, commit `0f24eb3c`, corrected in `4624c60a`) before the
masking changed. A second adversarial review found three forms of claimed families that still leaked,
each verified in a copy first: a **list of quoted units** (`str([hex(b) …])` → `['0x70', '0x6c', …]`,
`json.dumps` of hex strings), which broke the run at every quote while the bare `[112, 108, …]` was
masked; **`\U0000NNNN`**, which a code comment named as a unit but the `\u` branch took first under
IGNORECASE; and **`encodeURIComponent`**, which leaves `'` unescaped where the percent scan ended a
token. Three runs, all kept:

* `results/2026-10-06-addendum-b-before.json` — unchanged code. The six list/`\U` rows 0/600 as
  predicted, but `percent_uri` 190/190: the new `uri_marks` alphabet held no character
  `encodeURIComponent` escapes, so the row never held an apostrophe beside a `%HH` — a **corpus**
  defect, the circular kind Addendum A retracted. The alphabet gained a space, stated in the
  pre-registration before the fix.
* `results/2026-10-06b-addendum-b-before.json` — unchanged code, corrected corpus. **DEFECT**: the six
  rows 0/600 each, `percent_uri` 206/230 (all 24 misses on `uri_marks`).
* `results/2026-10-06c-addendum-b.json` — the fixed code (quotes as separators in the hex, decimal and
  escape families, a backslash too between escapes; `\U` matched first and case-sensitively; `'` no
  longer ends a percent token). **Decision: ON**, by the unchanged rule.

300 secrets (the earlier 250 byte-identical, plus 50 `uri_marks`), 37,482 texts, 2 min 37 s, US$ 0.

| check | result |
|---|---|
| `old == off` on every text | 37,482 / 37,482 |
| literal secret masked, `off` / `on` | 600 / 600, 600 / 600 |
| false positives | **0 of 19,422** (Wilson upper 0.0002), 0 in each of 40 absent kinds, including 6 ordinary texts with a Python list of `0x` bytes, a JSON list of hex strings, quoted port numbers, a URL with `O'Brien%27s`, `\U0001F600` escapes and a JSON array of hashes |

| form | masked | n |
|---|---:|---:|
| every earlier byte form, each | 600 | 600 |
| `str([hex(b) …])`, `json.dumps` of `02x` strings, of `hex(b)`, `str` of `\x` strings, `str` of quoted codes | 600 each | 600 |
| `\U%08x` per byte | 600 | 600 |
| `quote(s, safe="")` / `quote(s)` / lower-case escapes | 284 / 274 / 284 | same |
| `encodeURIComponent` (`quote(s, safe="-_.!~*'()")`) | 230 | 230 |
| code-point forms (non-ASCII only), each | 96 | 96 |
| **uncovered:** split across lines / ROT13 / base64 cut by a newline | **0** / **0** / **0** | 600 / 500 / 600 |

**What this corpus still cannot show** is what Addendum A said, one review later: every row is a
generator written for the corpus. A form no generator writes is unmeasured. The docstring's list of
what passes (split, wrapped, ROT13, compressed, keyed, double-encoded, surrogate pairs, an escape mixed
with plain characters) is the list of the ones found so far, not of all of them.

**Cost** (median, one machine, not gated): 10 kB prose at 3 secrets 2,384 → 4,854 µs (2.0×); at 30
secrets 2,544 → 4,299 µs (1.7×); 1 MB at 30 secrets 246 → 487 ms (2.0×); the 10 kB log text at 30
secrets 2,653 → 7,049 µs (2.7×). Within the spread Addendum A reported for the same shapes.
