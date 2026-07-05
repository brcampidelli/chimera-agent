# Email triage — inbox to digest in one command

The most-requested consumer agent task: read your inbox, separate what matters from
cold-sales noise, and hand you a ten-second digest. This example does it end-to-end
with the tools already shipped in Chimera — no extra dependencies.

**What it does (and doesn't):** reads your 10 most recent emails over IMAP, classifies
each as `URGENT / PERSONAL / NEWSLETTER / COLD-SALES`, writes `triage_report.md` +
`digest.md`. It is **read-only**: nothing is deleted, moved, replied to or sent.

## 1. Configure (once, ~2 minutes)

Add your IMAP credentials to `.env` in the repo root (or export them):

```env
CHIMERA_IMAP_HOST=imap.gmail.com        # your provider's IMAP host
CHIMERA_IMAP_USER=you@example.com
CHIMERA_IMAP_PASSWORD=your-app-password
```

> Gmail/Outlook: use an **app password** (regular passwords are rejected when 2FA is
> on). Gmail: Google Account → Security → 2-Step Verification → App passwords.

You also need any model key (a free OpenRouter model works):

```env
CHIMERA_OPENROUTER_API_KEY=sk-or-...
```

Sanity-check the wiring first:

```bash
chimera doctor
```

## 2. Run it

```bash
chimera workflow examples/email_triage/triage.yaml -w ./triage_workspace
cat triage_workspace/digest.md
```

First step fetches + classifies (retried once if the report comes out empty); second
step writes the digest. Both are gated by an executable check, so a silent failure
can't masquerade as success.

## 3. Make it daily (optional)

```bash
chimera cron add "morning email triage" "0 8 * * *" \
  "Run the email triage: use read_email for the 10 most recent emails, classify URGENT/PERSONAL/NEWSLETTER/COLD-SALES, and produce a 5-line digest."
chimera serve   # the scheduler daemon runs jobs and delivers output
```

With the messaging gateway configured (Discord/Telegram/Slack — see `docs/deploy.md`),
the digest lands in your chat every morning.

## 4. Optional: email yourself the digest

If you also set `CHIMERA_SMTP_HOST / _USER / _PASSWORD`, you can extend the cron action
with "then send digest.md to me with send_email". Sending is opt-in by design — the
default example never writes anything outward.

## Honest notes

- Classification is a model judgment: expect occasional miscategorized emails,
  especially borderline newsletter-vs-cold-sales. The digest cites senders so you can
  spot-check in seconds.
- Email bodies are untrusted input. Run with governance if you extend this beyond
  read-only: `chimera solve --taint --guard` fences fetched content, tracks provenance,
  and holds any learned skill from a tainted run for review (see `SECURITY.md`).
- Free models are slower and blunter at classification than paid ones; the workflow
  works with both.
