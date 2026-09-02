"""For twenty-five minutes after every release, auto-update was broken for everyone.

The desktop updater asks GitHub for `releases/latest/download/latest.json`, which resolves to
whatever GitHub calls the latest release. Published the ordinary way, a release becomes latest the
*instant* it is created — and `latest.json` is attached by the last job of `desktop-release.yml`,
after four platforms have finished building. Measured on v0.49.0: the endpoint returned **404** while
the installers were still going up.

Nobody had noticed, and the reason is the same property that makes the check pleasant: the startup
check swallows every error so it never nags. A window in which no update can be found looks exactly
like a window in which there is no update.

The fix is two halves that have to stay together, which is what this file is for:

  1. `RELEASING.md` says to create the release with ``--latest=false``;
  2. the last job of the workflow promotes it with ``gh release edit --latest``, AFTER attaching
     the manifest.

Either half alone is worse than neither. The flag without the promotion leaves every release
permanently not-latest — no client is ever offered anything again. The promotion without the flag is
a no-op on a release that was already latest, and the window stays.

Read out of the two files rather than asserted against a run, for the reason the neighbouring
workflow test gives: a test cannot cut a release, but it can check the line that was missing.
"""

from __future__ import annotations

from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent.parent
FLUXO = RAIZ / ".github" / "workflows" / "desktop-release.yml"
GUIA = RAIZ / "RELEASING.md"


def _passos_do_manifesto() -> list[dict]:
    dados = yaml.safe_load(FLUXO.read_text(encoding="utf-8"))
    return dados["jobs"]["manifest"]["steps"]


def _indice(passos: list[dict], agulha: str) -> int:
    for i, passo in enumerate(passos):
        if agulha in str(passo.get("run", "")) or agulha in str(passo.get("with", "")):
            return i
    raise AssertionError(f"nenhum passo do job `manifest` contém {agulha!r}")


def _indice_do_anexo(passos: list[dict]) -> int:
    """The step that ATTACHES the manifest to the release — not the one that writes it locally.

    `latest.json` appears in four steps of this job: the one that builds it, the one that attaches
    it, the dry-run upload, and a comment. Anchoring the order assertion on the first match compared
    against the BUILD step, which meant moving the promotion to just before the attach still read as
    "after" and passed. The sabotage that proves it is the one this helper exists for.
    """
    for i, passo in enumerate(passos):
        if "action-gh-release" in str(passo.get("uses", "")) and "latest.json" in str(
            passo.get("with", "")
        ):
            return i
    raise AssertionError("nenhum passo anexa latest.json à release")


def test_the_guide_tells_you_to_hold_the_release_back() -> None:
    """The stable `gh release create` line carries `--latest=false`."""
    linhas = [
        linha
        for linha in GUIA.read_text(encoding="utf-8").splitlines()
        if linha.startswith("gh release create") and "--prerelease" not in linha
    ]

    assert linhas, "RELEASING.md no longer shows how to create a stable release"
    for linha in linhas:
        assert "--latest=false" in linha, (
            "a stable release created without `--latest=false` becomes the updater's target "
            f"before its manifest exists, and the endpoint 404s until the build ends: {linha}"
        )


def test_the_workflow_promotes_it_once_the_manifest_is_attached() -> None:
    """And the ORDER is the fix, not the presence of the step."""
    passos = _passos_do_manifesto()
    anexa = _indice_do_anexo(passos)
    promove = _indice(passos, "--latest")

    assert promove > anexa, (
        "the release is marked latest BEFORE its manifest is attached, which is the whole defect "
        f"this guards (attach at step {anexa}, promote at step {promove})"
    )


def test_the_website_is_told_after_the_release_is_promoted() -> None:
    """The site resolves download links at build time, so it must see a finished release.

    Its own comment already says the notice is last because a half-uploaded release makes the site
    skip it. Being latest is part of finished — a site notified first could publish a page for a
    release the updater still cannot find.
    """
    passos = _passos_do_manifesto()

    # Matched on the endpoint the notice POSTs to, not on the phrase `repository_dispatch` — that
    # phrase appears only in the comment above the step, and a comment is not the thing that runs.
    # The first version of this assertion looked for it and failed on correct code.
    assert _indice(passos, "/dispatches") > _indice(passos, "--latest"), (
        "the website is told before the release is promoted to latest"
    )


def test_a_prerelease_is_left_alone() -> None:
    """An rc needs no flag — `--prerelease` already keeps it out of "latest".

    The control for the first test: a rule that demanded `--latest=false` everywhere would also
    demand it where it means nothing, and the guide would start teaching noise.
    """
    linhas = [
        linha
        for linha in GUIA.read_text(encoding="utf-8").splitlines()
        if linha.startswith("gh release create") and "--prerelease" in linha
    ]

    assert linhas, "RELEASING.md no longer shows how to cut an rc"
    for linha in linhas:
        assert "--latest=false" not in linha, f"redundant on a prerelease: {linha}"
