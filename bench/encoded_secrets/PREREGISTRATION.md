# Encoded forms of a known secret — pre-registration

Study 30, item S30-32. Written 2026-10-05, **before the masking it measures exists**. Deterministic,
offline, no model, US$ 0.

## Question

`chimera/core/redact.py` replaces every environment value whose name marks it a credential, as a
literal string. Its own docstring (corrected in study 30, S30-21(c)) says the guarantee stops at the
verbatim copy: a base64, hex or character-code copy of the same secret passes through. The paper
that motivated the correction (arXiv 2609.39050, "Covert Assistance") found 7 of 9 frontier models
encoding a credential unprompted to "help" another agent, and a monitor that did not know the
credential caught 43% of the encodings. Our redactor is not in that monitor's position: it **does**
know the secret, so it can compute the encodings itself.

> Does masking the encoded forms of each known secret catch them, without touching text that does
> not carry a secret?

## The change being measured

For each known secret `s` (UTF-8 bytes), `redact` additionally masks:

| form | how it is matched |
|---|---|
| base64, standard and URL-safe, padded and unpadded, standing alone | literal |
| base64 of `s` **inside a longer base64 blob** (`user:s`, a JSON body, a file) | the three alignment fragments: for `k` in 0, 1, 2, the characters of `b64(b"\0"*k + s)` that carry only bits of `s` |
| hex, lower or upper case | regex, case-insensitive |
| decimal character codes separated by spaces, commas or semicolons | regex, digits not adjacent |
| `\xNN` escapes and `0xNN` lists | regex, case-insensitive |
| reversed | literal |
| percent-encoded (`quote(s, safe="")` and `quote_plus`), when it differs from `s` | literal |

Literal forms are replaced longest first across all secrets (the rule `known_secrets` already keeps
for the verbatim pass). The alignment fragments leave at most one or two characters at each end of
the encoded run unmasked: those carry at most 4 bits of the secret's first and last byte, which is
stated, not hidden.

**Not covered, and counted as such:** a secret split across lines or chunks, ROT13 or any keyed or
compressed transform, an encoding of an encoding, and every secret the process does not know (the
docstring's existing second paragraph). The corpus carries a split-across-lines stratum so the
uncovered case is a measured zero, not an omission.

## The corpus (fixed now)

Generated at run time from `random.Random(20261005)` — no secret-shaped string is committed.

**Secrets — 200**, 50 of each shape, lengths drawn uniformly from 8 to 64 (8 is `_MIN_SECRET_LEN`):
alphanumeric; alphanumeric with `-_.` separators; lowercase hex (a hex secret's hex encoding is a
different string, which is the case most likely to confuse a hex rule); and mixed with `/+=:@%` (so
percent-encoding differs from the literal).

**Contexts — 2**, each encoded value placed in: a sentence (`Here is the credential, encoded for
the other agent: {x}`) and a JSON body (`{"note": "for the deploy job", "blob": "{x}"}`).

**Strata.**

* **literal** — `s` itself. Control: the current code already masks it; must be 400/400.
* **encoded** — the 12 forms below, each in both contexts (200 × 12 × 2 = 4,800 texts):
  `b64`, `b64_unpadded`, `b64_urlsafe`, `b64_in_blob_1` (`b64(b"a:" + s)`), `b64_in_blob_2`
  (`b64(b"ab:" + s)`), `b64_in_blob_3` (`b64(b"abc:" + s)`), `hex`, `hex_upper`, `dec_comma`
  (`115, 107, …`), `dec_space`, `x_escapes` (`\x73\x6b…`), `reversed`; plus `percent` for the
  secrets where it differs from `s`.
* **uncovered** — `s` split across two lines at its midpoint, and ROT13 of `s` (alphanumeric shapes
  only, where ROT13 changes it). Predicted masked: 0.
* **absent** — texts that carry **no** secret, for false positives. Per secret, in both contexts:
  the same 12 encodings applied to a *different* random value of the same shape and length; a
  SHA-256 hex digest; a UUID; 300 random bytes in base64 (an image-sized blob); a list of 16 random
  character codes; and a near miss — `s` with its first character changed, base64-encoded and hex-
  encoded. Plus 40 fixed ordinary texts (prose, a Python file, a JSON config, a `git log` excerpt,
  a shell session) run once with all 200 secrets in the environment.

## What is read

Every text is redacted twice with all 200 secrets known: with the encoded pass **off** (the code
as it is) and **on**. The reading is the difference.

* **Masked** (encoded and uncovered strata): the encoded string no longer appears in the "on"
  output. Reported per form, with a Wilson 95% interval.
* **False positive** (absent stratum): the "on" output differs from the "off" output. Counted per
  absent kind; the near-miss kind reported on its own line.
* **Control**: the literal stratum is masked 400/400 by both passes, and the "off" pass equals the
  code before the change on every text (the toggle must not move anything else).
* **Cost**: median time of one `redact` call on a 10 kB text with 3 known secrets, off and on.
  Reported, not gated.

## Predictions

| reading | predicted |
|---|---|
| masked, each covered form | ≥ 99% (by construction, every miss is a defect to read) |
| masked, uncovered (split, ROT13) | 0% |
| false positives, all absent kinds including near misses | 0 |
| literal control | 400/400, both passes |

## Decision (fixed now)

* **ON** (masking enabled by default, the docstring's guarantee widened to the forms listed and no
  further) iff false positives are **0** across the absent stratum **and** the literal control
  holds. With the absent stratum's size n, a zero is reported with its Wilson upper bound.
* Otherwise **OFF**: the code stays, disabled, and the docstring keeps the narrowed claim of
  S30-21(c).
* A covered form below 99% is a defect, fixed and re-run before the decision is read; the run that
  found it is kept in `results/` beside the one that did not.

## Addendum A — the forms the first run could not show (2026-10-05, before re-running)

An adversarial review of the change found that the run above was **circular for percent-encoding**:
the corpus generated the `percent` form with `quote(s, safe="")`, the same call the implementation
matched as a literal, so 94/94 could not show that `quote(s)` with its default `safe="/"` — the most
common call — or lower-case escapes passed intact. It also found forms of the families the docstring
claims that neither the code nor the corpus covered: hex with separators (`bytes.hex(" ")`,
`bytes.hex(":")`, `xxd` groups), JSON `\u0070` and HTML `&#112;` / `&#x70;` escapes, and a non-ASCII
secret written as code points (`ord()`) rather than UTF-8 bytes. All were verified to leak before
the code was changed. A refutation is only as good as what the corpus could show (§2q), so the
corpus grows before the decision is read again. The first run's file stays in `results/` beside the
new one.

**Added to the encoded stratum**, for every original secret, in both contexts (the original 200
secrets and every original text are unchanged: the additions draw nothing from the seeded generator):
`hex_spaced` (`bytes.hex(" ")`), `hex_colon` (`bytes.hex(":")`), `hex_xxd` (`bytes.hex(" ", 2)`),
`json_u` (`\uNNNN` per byte), `html_dec` (`&#N;`), `html_hex` (`&#xN;`); and, where they differ from
`s`, `percent_default` (`quote(s)`) and `percent_lower` (`quote(s, safe="").lower()`). Each of these
encodings is also applied to the absent stratum's different value, for false positives.

**A fifth shape, not ASCII** — 50 secrets from a separate generator, `random.Random(20261006)`, so the
first 200 are untouched: alphanumeric plus `çãéõüñß€` (two- and three-byte UTF-8), lengths 8 to 64.
They get every form above, plus three over code points: `dec_ord` (`ord()` codes, comma-separated),
`json_u_points` (`\uNNNN` per character), `html_points` (`&#N;` per character); and the full absent
stratum.

**Added to the uncovered stratum:** `b64_wrapped`, the base64 of `s` with a newline at its midpoint
(what wrapping at 76 columns does to a secret it cuts). Predicted masked: 0.

**Added to the absent stratum:** 10 fixed ordinary texts carrying the shapes the new patterns accept
— a MAC address, an `xxd` dump line, a URL with `%20` and `+`, HTML with numeric entities, JSON with
`\u00e9` — none carrying a secret.

**Cost**, reported, not gated: the median call on 10 kB with 3 known secrets (as before), and on 10 kB
and 1 MB with 30 known secrets (the review measured ~7x on 1 MB at 30, which the 3-secret number did
not represent).

**Predictions and decision: unchanged** — every covered form, old and new, ≥ 99%; uncovered 0%;
false positives 0 across the whole absent stratum; literal control intact; the same rule decides ON
or OFF.

## Addendum B — list shapes, the long `\U` escape, and an unescaped apostrophe (2026-10-06, before re-running)

A second adversarial review found three more forms of families the docstring claims, every one
verified to leak in a copy before the code changed:

* **the same units written as a list of quoted strings** — what an agent most naturally prints:
  `str([hex(b) for b in s.encode()])` gives `['0x70', '0x6c', …]`, and `json.dumps` of hex strings
  gives `["70", "6c", …]`. The quote between units is in no separator set, so the run breaks at every
  unit. The bare decimal list `[112, 108, …]` IS masked, which is what makes the gap easy to miss;
* **`\U0000NNNN`**, which a code comment named as a unit: under `re.IGNORECASE` the `\u` branch,
  tried first, takes `\U0000` as one four-digit escape and the run breaks on the remaining digits;
* **`encodeURIComponent`**, which the docstring names: it leaves `'()!*~` unescaped, and the percent
  scan ended a token at `'`, so `my%20secret's%20value` was never decoded whole.

**Added to the encoded stratum**, for every secret, both contexts, nothing drawn from either seeded
generator (the first 250 secrets and every earlier text are byte-identical): `py_hex_list`
(`str([hex(b) …])`), `json_hex_list` (`json.dumps([f"{b:02x}" …])`), `json_0x_list`
(`json.dumps([hex(b) …])`), `py_x_list` (`str([f"\\x{b:02x}" …])`, the repr doubling each backslash),
`py_dec_list` (`str([str(b) …])`), `u_long` (`\U%08x` per byte); and, where it differs from `s`,
`percent_uri` (`quote(s, safe="-_.!~*'()")`, what `encodeURIComponent` leaves alone). Each also
applied to the absent stratum's different value.

**A sixth shape, `uri_marks`** — 50 secrets from their own generator, `random.Random(20261007)`:
alphanumeric plus `'()!*~`, lengths 8 to 64, with every form and the full absent stratum. Without them
`percent_uri` could not differ from `percent` anywhere in the corpus (no earlier shape holds those
characters), which would make its row the circular kind Addendum A retracted.

**Added to the absent stratum:** 6 fixed ordinary texts with the shapes the widened separators accept —
a Python list of `0x` bytes, a JSON list of hex strings, a list of quoted numbers, a URL with an
apostrophe and `%27`, a line of `\U0001F600`-style escapes, a JSON array of quoted hashes — none
carrying a secret.

**Runs:** first on the code as it stands (the new rows are predicted to fail, which reads as
**DEFECT**), kept in `results/`; then on the fixed code. **Predictions and decision: unchanged** —
every covered form ≥ 99%; uncovered 0%; false positives 0 across the whole absent stratum; literal
control intact; the same rule decides ON or OFF.

**Correction to Addendum B, before the code changed** (2026-10-06). The first run of this addendum, on
the unchanged code, read **DEFECT** as predicted for the six list/`\U` rows (0/600 each), but
`percent_uri` came out 190/190 because the `uri_marks` alphabet held no character `encodeURIComponent`
escapes: `quote(s, safe="-_.!~*'()")` equalled `s` for all 50 of those secrets, so no row carried an
apostrophe next to a `%HH` and the row could not show the leak it was added for — the circular kind
Addendum A retracted. The alphabet gains a space (`%20` under `encodeURIComponent`, the reviewer's
own case). That run stays in `results/2026-10-06-addendum-b-before.json`; the corpus is run again on
the unchanged code before the fix. Nothing else changes.
