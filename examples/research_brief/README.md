# Daily research brief

Give Chimera a topic; get back a sourced, 5-point brief plus a 3-line digest. Uses
`arxiv_search` (always available) and `web_search` (if you've set a Tavily key).

## Run it

```bash
chimera workflow examples/research_brief/brief.yaml -w ./brief_workspace
cat brief_workspace/digest.md
```

Change the topic by editing the `task:` line in `brief.yaml`. Each step is gated by an
executable check, so an empty or malformed brief is retried rather than passed off.

## Make it a morning habit

```bash
chimera cron add "daily research brief" "0 7 * * *" \
  "Research 'small language models on-device 2026' with arxiv_search and web_search; write a 5-bullet sourced brief and a 3-line digest."
chimera serve   # the scheduler runs it and (with a bot configured) delivers the digest to chat
```

## Honest notes

- `arxiv_search` covers papers; broad web coverage needs `CHIMERA_TAVILY_API_KEY`. Without
  it the brief still runs, just narrower.
- "5 findings" is a target, not a guarantee — the verifier checks the brief is non-trivial,
  not that every bullet is gold. The digest names its top pick so you can judge fast.
- Free models are fine here; a stronger model gives sharper synthesis.
