# Encoded forms of a known secret — results

Registered in `PREREGISTRATION.md` (commit `38657ed1`) before the masking existed. Run 2026-10-05,
`python bench/encoded_secrets/run.py --out bench/encoded_secrets/results/2026-10-05.json`, 22 s,
US$ 0. 200 synthetic secrets, 13,234 texts, each redacted by the code at the base commit (`old`), by
today's code with the encoded pass off (`off`) and on (`on`).

## Decision: **ON**

Every condition of the registered rule held: zero false positives, the literal control intact, and
the toggle moving nothing else. `chimera/core/redact.py` ships `MASK_ENCODED = True`, and a test holds
that constant equal to this run's `decision` field.

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
