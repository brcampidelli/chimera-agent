# Releasing Chimera

**One stable release a week. Daily work ships as `rc` prereleases.**

## Why this exists

On 2026-07-16 this project cut **nine** releases in a single day (`v0.28.0` → `v0.34.0`), three of
which — `v0.32.0`, `v0.32.1`, `v0.32.2` — were patches fixing the *previous* release's updater CI in
public. That is not iterating fast; it is debugging in front of users, and it turns the two assets
this project trades on (a careful changelog, a claim to be trustworthy) into noise. Thirty-four
"minor" versions in sixteen days also drains the meaning out of semver.

The root cause of the patch storm was mechanical, not a lapse: the job that assembles `latest.json`
only ran on `release`, so **the only way to test it was to publish**. That's fixed — see the dry run
below. The cadence rule is the other half.

## The rule

| | When | What |
|---|---|---|
| **`rc` prerelease** | any day, as often as you like | `vX.Y.0rcN`, marked **prerelease** on GitHub |
| **stable** | at most once a week | `vX.Y.0`, normal release |
| **patch** | only for a real user-facing bug | `vX.Y.Z` |

A patch release is for something **users hit**. A patch that only fixes CI belongs on `main` and
rides the next weekly — CI is not a shipped artifact.

### Why `rc` is safe (verified, not assumed)

All three claims were checked against the live services **after** cutting `v0.36.0rc1` — the first rc
this project ever published — not merely predicted:

- **The updater ignores it.** The desktop updater reads
  `releases/latest/download/latest.json`, and GitHub's *latest* is by definition "the most recent
  **non-prerelease**, non-draft release". A prerelease never becomes *latest*, so an `rc` is never
  offered to installed apps. (With `v0.36.0rc1` published, `releases/latest` still returned `v0.35.0`,
  `prerelease: false`.)
- **pip ignores it.** With `0.36.0rc1` live on PyPI, `pip install chimera-agent` still resolved to
  `0.35.0`; only `--pre` or an exact pin reaches the rc.
- The release CI still runs for a prerelease and attaches installers + a `latest.json` **to that rc's
  own release page** — useful for testing, invisible to everyone else. (All four platforms plus the
  `.sig` files landed on the rc's page.)

An rc is also the cheapest place to discover that a *release mechanism* is broken. `v0.36.0rc1` is what
proved the semver prerelease spelling survives a real Cargo/Tauri build — a thing no local check in this
repo can tell you, since the desktop is only ever built in CI.

## Before cutting a stable release

1. **Dry-run the installers + manifest** — this is the step whose absence caused the patch storm:

   ```bash
   gh workflow run desktop-release.yml --ref main     # builds all 4 platforms, no release
   ```

   It builds Windows / macOS arm64 / macOS Intel / Linux, signs the updater artifacts, assembles
   `latest.json`, and uploads it as the **`latest-json-dryrun`** artifact (it is *not* published).
   Download it — or read the `--- latest.json ---` dump in the job log — and check **each platform
   points at the right updater artifact**:

   | target | must end in |
   |---|---|
   | `windows-x86_64` | `.exe` (nsis) |
   | `darwin-aarch64` | `_aarch64.app.tar.gz` |
   | `darwin-x86_64` | `_x86_64.app.tar.gz` |
   | `linux-x86_64` | `.AppImage` — **never** the `.deb` |

   Both real updater bugs this project shipped would have died here: a macOS build that emitted no
   `.sig` (the fragment step fails loudly), and a Linux url pointing at the `.deb` (visible on sight).

2. **Full gate**: `uv run --no-sync ruff check . && uv run --no-sync mypy chimera && uv run --no-sync pytest -q`,
   plus `npm --prefix apps/desktop run test && npm --prefix apps/desktop run build`.
3. **OpenAPI drift**: `python -m chimera.api.schema_dump > apps/desktop/openapi.json` then
   `npm --prefix apps/desktop run gen:api` — a second dump must be byte-identical.

## Cutting it

```bash
# 1. bump all three version strings — they must agree on the VERSION, not on the literal text.
#    Python and Rust disagree on how a prerelease is spelled, and neither will accept the other's:
#      pyproject.toml                       PEP 440   0.36.0   ·  0.36.0rc1
#      apps/desktop/src-tauri/Cargo.toml    semver    0.36.0   ·  0.36.0-rc.1
#      apps/desktop/src-tauri/tauri.conf.json  semver 0.36.0   ·  0.36.0-rc.1
#    (For a stable the three strings do end up identical; only prereleases diverge.)
# 2. refresh the installed metadata, then regenerate the shipped snapshots (they embed __version__)
uv pip install -e . --no-deps
uv run --no-sync python -m chimera.eval.maturity_snapshot
uv run --no-sync python -m chimera.eval.benchmark_snapshot
#    THREE snapshots, not two — the CLI one embeds the version too, and CI checks it. Cutting
#    v0.44.0 with only the first two left `test_the_committed_snapshot_is_current` red, which the
#    full suite caught and this list would not have.
uv run --no-sync python -m chimera.cli.schema_dump > chimera/_cli_snapshot.json
# 3. CHANGELOG: rename [Unreleased] -> [X.Y.Z] - <date>   (STABLE ONLY — an rc previews the
#    [Unreleased] section and leaves it alone; the stable is what names and dates it)
# 4. commit, push, then:
gh release create vX.Y.0 --title "..." --notes "..."            # stable
gh release create vX.Y.0rc1 --prerelease --title "..." --notes "..."   # rc
```

Publishing fires two workflows: **publish.yml** (PyPI, via OIDC — no token) and
**desktop-release.yml** (installers + signed updater artifacts + `latest.json`).

## Writing the notes

Name the release after what a user gets ("Stop a batch", "Intel Macs can install"), not the version.
State honest caveats in the notes themselves — unsigned installers warn on first run; cancellation is
cooperative. **Don't quote counts that rot** (a v0.33.0 note said "the 50 tests" for a release that
shipped 51). Describe the guard, not the tally.

## Telling the website

The download page resolves its links at build time, so the site only learns about a release when it
is rebuilt. `desktop-release.yml` sends it a `repository_dispatch` as the **last step of the manifest
job** — after every installer and `latest.json` are attached, never at publish time: the site drops a
release whose assets are still uploading, so an early notice would make it skip the new version and
keep advertising the previous one until the nightly.

The site has listened for that event since its deploy workflow was written. Nothing sent it until
2026-08-19, so the nightly cron at 04:17 UTC was doing the whole job and a fresh release could sit
for hours behind a download page showing the last one.

**It needs a token, and it is optional.** Create a fine-grained PAT with **Contents: read and write**
on `brcampidelli/chimera-site` (that is what `POST /dispatches` requires) and store it here as
`SITE_DISPATCH_TOKEN`. Without the secret the step is skipped — a release must never go red because
a website was not notified — and the site falls back to the nightly, which is where it was before.

Verify it on the next release: the step prints `notified chimera-site about vX.Y.Z`, and the site's
Deploy workflow should show a run whose event is `repository_dispatch` within a minute.

## The signing key

Updates are Minisign-signed by `TAURI_SIGNING_PRIVATE_KEY` / `..._PASSWORD` (repo secrets). **GitHub
secrets are write-only — they are not a backup.** If the key is lost, every installed app rejects
future updates and each user must reinstall by hand. Keep the recoverable copy in a password manager.
