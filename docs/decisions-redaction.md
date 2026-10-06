# What a typed decision sends, and what stays behind

A **decision** is a question put to a model about a piece of text (its *state*): is this shell action
dangerous, is this answer supported by these sources. Which model answers is a setting
(`CHIMERA_DECISION_BACKEND`), and that decides whether the state leaves the machine at all.

This page says, per surface, what the state carries and what a hosted backend therefore sees, so the
choice of a backend is made knowing it. It was written for study 27, phase 3, from the code, not from
memory of it.

## Which backends send the state anywhere

| Backend | Where the state goes | Redacted before it goes |
|---|---|---|
| `local_logprob` (default) | your own Ollama, on this machine | not needed: it does not leave |
| `hosted_verbalized` | the provider the gateway routes to (your key, your spend) | yes |
| `openrouter_decisions` | OpenRouter, to the Decisions API (your key, your spend) | yes |

**The net** is `chimera.core.redact`, the one that already keeps secrets out of the trace. Three
layers, in order of confidence:
1. every environment value whose variable *name* looks like a credential (`*API_KEY*`, `*SECRET*`,
   `*TOKEN*`, `*PASSWORD*`, `*CREDENTIAL*`, `*PRIVATE_KEY*`) is replaced verbatim wherever it appears,
   and so are its encoded copies: base64 on one line (alone or inside a longer blob), hex with or
   without separators, decimal character codes, `0x`/`\x`/`\u`/`\U`/HTML escapes, any of those
   written as a list of quoted units (`str([hex(b) …])`, `json.dumps` of hex strings), the reversed
   string, and percent-encoding with any choice of escaped characters (`encodeURIComponent`'s
   unescaped apostrophe included); a non-ASCII secret as UTF-8 bytes and as code points
   (`bench/encoded_secrets`, Addendum B: every listed form caught, no false positive in 19,422
   texts; the module docstring is the exact list). A copy split across lines (wrapped base64
   included), ROT13'd, compressed or encoded twice is not, and neither is any form no reviewer has
   generated yet — two reviews in a row found ones the list had missed;
2. secrets given away by *where* they sit: URL userinfo, a query parameter named like a credential,
   an `Authorization` header, a cookie, a database DSN, a webhook path;
3. narrow key shapes (`sk-…`, `ghp_…`, JWTs and a few others).

It keeps context (`api_key=[redacted]`), so a masked command is still a command the model can judge.

**What it does not guarantee.** A token minted at runtime by a remote API, a password typed into a
prompt, a key in a file the agent read: none of those is in the environment and no pattern list finds
all of them. The first layer is a guarantee over what this process knows; the other two are nets. Treat a
hosted backend as something that may see text you would rather it did not.

## What each surface puts in the state

| Surface | The state is | Caps |
|---|---|---|
| **The REVIEW band** (`governance/band.py`) | the *action alone*: the shell command, and nothing else. Not the task, not the tool output, not the sentence a request may carry (that is the instrument the map was fitted on, and a sentence in the request must not grade the request). | none on the command itself |
| **Verified answers** (`fusion/verified.py`) | the excerpts the product handed the model (attachments, recalled memory, retrieved chunks), the question, and the draft answer | sources up to 14,000 characters; text the check withheld is recorded up to 4,000 |
| **`chimera decide`, `POST /api/decide`, the agent's `decide` tool** | whatever the caller supplies | at most 50 states per tool call |

A command is exactly where a bearer token sits, which is why the band's state goes through the net
before a hosted backend reads it.

## What is stored on this machine

The decision log (`<home>/decisions/decisions.jsonl`) keeps, per answer, the receipt (the number, the
band region, the model and the build that answered, cost and latency) and **the first 500 characters
of the state**, redacted and *then* cut, so a token straddling the cut is masked rather than left as
a fragment no pattern recognises. The state's hash is of the original text, so a refit can still join
rows on it, and a hash is not a way back to the text.

`GET /api/decisions` serves the first 300 characters of what the log holds.

## What this page does not claim

- **No path or id substitution.** An earlier plan for this phase listed opaque-id substitution for
  paths and store ids. It is not done and is not planned as written: a state whose paths are replaced
  is a state the danger question can no longer judge (`rm -rf /` and `rm -rf ./build` differ by
  exactly a path). If a surface ever needs it, it needs its own bench first.
- **No "coarse features only" mode.** There is no setting that sends less than the state. The choice
  that exists is the backend: local sends nothing anywhere.
- **The state is not scrubbed for personal data**, only for credentials. Content marked as a person's
  is a separate question for the surfaces that carry documents, and the per-surface table above is the
  place to look for what that surface hands over.

## How this is held

`tests/test_a_credential_in_a_decision_state_never_leaves_the_machine.py` plants a known secret (an
environment value, a header, a query parameter and a token shape) and checks the request a hosted
backend would send and the line the log would write. Removing the net from any of the three places
fails it.
