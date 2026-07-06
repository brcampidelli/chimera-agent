# Repo watchdog

Point Chimera at a repo; it runs the test suite and writes a short health report naming
any failing tests. Read-only except for `watchdog.md` — it never edits your source.

## Run it

```bash
chimera workflow examples/repo_watchdog/watch.yaml -w /path/to/your/repo
cat /path/to/your/repo/watchdog.md
```

The agent runs the tests itself with `run_shell`, so it reads the real output and reports
counts + failing test names. Adjust the test command in the `task:` line if your project
doesn't use pytest.

## Run it on every push / every hour

```bash
# hourly
chimera cron add "repo watchdog" "0 * * * *" \
  "Run the test suite in the repo with run_shell (python -m pytest -q) and write watchdog.md: PASS/FAIL, counts, failing tests, one next step."
chimera serve
```

Fire it on a webhook (e.g. from a CI or a git hook) instead of a schedule:

```bash
chimera cron add "repo watchdog" ci-done --webhook \
  "Run the tests and write watchdog.md."
# then POST /webhook/ci-done to the running `chimera serve`
```

## Honest notes

- Running tests executes your code. For an untrusted repo, run it sandboxed
  (`CHIMERA_SANDBOX=docker`) — see `SECURITY.md`.
- The watchdog reports; it does not fix. Pair it with `chimera solve` if you want it to
  attempt a repair (that path edits files and is gated by verify-or-revert).
