# Changelog digest

Point Chimera at any git repository; it inspects recent commit history and diff stats, drafts
structured release notes (`release_notes.md`) citing commit hashes, and distills a 3-line
upgrade summary (`digest.md`). Read-only except for the two markdown outputs.

## Run it

```bash
chimera workflow examples/changelog_digest/digest.yaml -w /path/to/your/repo
cat /path/to/your/repo/release_notes.md
cat /path/to/your/repo/digest.md
```

By default the workflow inspects the last 20 commits (`git log -n 20 --oneline`). Edit the
`task:` prompt in `digest.yaml` to target a tag range (for example `v0.1.0..HEAD`) or a time
window (`--since="7 days ago"`).

## Schedule a weekly release digest

`chimera cron` cannot target an external repository or run a workflow file yet (scheduled jobs run
free-text tasks inside their own `.chimera/cron/<job-id>/` folder). To schedule the workflow itself
along with its `verify:` gates, use a system `crontab` entry:

```cron
# Every Friday at 17:00
0 17 * * 5  cd /path/to/your/repo && chimera workflow /path/to/examples/changelog_digest/digest.yaml -w .
```

## Honest notes

- Uses `run_shell` to run `git log` inside the workspace. It does not push,
  tag, or modify tracked files.
- Commit messages determine how much context the model has; pairing `git log -n 20 --oneline` with
  `git log --stat -n 5` helps the agent ground vague commit titles in the actual files touched.
- Both steps are gated by executable `verify:` checks so empty or unformatted outputs are retried.
