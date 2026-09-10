# Pre-registration — the app chat's taint lever

Written **2026-09-10, before the arm existed and before a line of the fix was written.** The
before-number this is measured against is already published: `RESULTS.md` (#400) states that
`CHIMERA_TAINT_AUTHORITY` "remains inert there — measured here, not fixed here", and
`chimera/api/posture.py` says why in its own words: *"The mode travels; the instruction cannot."*

## What is being tested

`guard_chat_registry` builds the desktop chat's registry once per session and constructs its
`TaintLedger` with `authority=settings.taint_authority`. It never calls `set_instruction`, so
`requester_of` answers `unknown` for every fetch, and the `authority` mode — whose whole purpose is
to treat a page **the person named themselves** as the user's own — has nothing to act on.

The same shape reaches two more surfaces through a different door: `serve` and `_serve_platform`
call `governed_profile(...)` without an `instruction=`, and that function sets one only when the
argument is given.

## The instrument

A new `app_chat` arm in `bench/right_hand_governance/run_terminal_vs_governed.py`, built by calling
`guard_chat_registry` — the shipped function, through the same stub-registry seam every other arm
uses. Offline, stub tools, US$0.

## Predictions, in the order they will be checked

1. **Before the fix: `CHIMERA_TAINT_AUTHORITY=authority` moves 0 rows on `app_chat`.** This is the
   claim under test. If it moves any row, the published statement in `RESULTS.md` is wrong and this
   whole task is misconceived — that outcome gets written up, not buried.

2. **`CHIMERA_TRUST_WORKSPACE=0` moves rows on `app_chat` BEFORE the fix.** This is the control, and
   it is what separates "the lever is inert" from "the arm is inert". That setting reaches the same
   ledger without needing an instruction, so if it also moves 0 rows the arm is measuring nothing
   and prediction 1 is worthless.

3. **After the fix: `authority` moves > 0 rows**, and they are the rows whose establishing read
   targets a URL or path named in the instruction — the same mechanism, and the same count, as the
   terminal arm's 9 where the corpus rows are comparable.

4. **The exec tools are ABSENT on this arm, before and after.** `guard_chat_registry` resolves
   `Posture(reach=DEFAULT_REACH)`, which denies `EXEC_TOOLS` unconditionally. So attack rows that
   act through `run_shell` will read `absent`, **not** BLOCKED. Registered in advance precisely so
   that it cannot afterwards be read as a governance result: a tool that is not there did not refuse
   anything. This arm answers whether the setting moves, and nothing about block rate.

5. **The fix is invisible with the hook unused.** A `ChatSession` built without the new callback
   behaves byte-identically — asserted, not assumed, because that is what makes this safe for the
   messaging gateway and `/v1/chat/completions`, which share this registry.

## Refutation criteria

- Prediction 1 failing means the premise is wrong; the write-up says so and the fix is withdrawn.
- Prediction 2 failing means the arm cannot show the effect (§2q) and no number from it counts.
- Prediction 3 failing after the wire is in means the mechanism is not what this document says it
  is, and the diagnosis has to be redone rather than the number reported.

## What this cannot show

The arm builds the registry the app's chat builds; it is not the app. No SSE, no session manager, no
messaging gateway. It also cannot show whether `authority` is a setting anyone should turn on — it
measures that the lever is connected, not that pulling it is wise.
