"""A freshly updated app reported the version it used to be.

Measured on a real Windows machine, immediately after a real in-place update from 0.48.0 to 0.49.0:

    chimera-desktop.exe   ->  0.49.0     (the shell updated)
    /api/version          ->  0.48.0     (the backend did not)

The backend process was fresh — spawned by the new shell, same second — so nothing had survived.
What had survived was a FILE. The frozen sidecar is a PyInstaller bundle whose names carry versions,
NSIS overwrites what it is given and leaves the rest, and `_internal` ended up holding both::

    chimera_agent-0.48.0.dist-info      (from the previous install)
    chimera_agent-0.49.0.dist-info      (from this one)

`importlib.metadata.version("chimera-agent")` answers with the first it finds. Renaming the stale
directory and restarting made the same binary report 0.49.0 and `update_available=False`, which is
what turns this from an argument into a diagnosis.

**Why it shipped, and the part worth keeping:** the pipeline already has a step called *"the frozen
sidecar knows its own version"*. It runs the binary and refuses `0.0.0*`. It passed — because CI
builds into an empty tree, where there is exactly one dist-info. The defect needs a previous install
to merge with, and CI never has one. A check that runs where the effect cannot occur produces no
evidence about it, however green it is.

That guard also encodes "not zero" rather than "the right one": a sidecar reporting 0.48.0 inside a
v0.49.0 build would have passed it too. Both halves are tightened here.

**What this file does NOT do**, said plainly rather than left to be assumed: it does not exercise an
upgrade. Nothing automated does. The only check that would have caught this is installing the
previous release, installing the new one over it, and asking the binary — on a Windows runner, twice
per release. This asserts the fix is present and the guard is honest; it cannot assert that the fix
works, and no test in this repository can.
"""

from __future__ import annotations

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
GANCHOS = RAIZ / "apps" / "desktop" / "src-tauri" / "installer-hooks.nsh"
FLUXO = RAIZ / ".github" / "workflows" / "desktop-release.yml"


def _preinstall() -> str:
    texto = GANCHOS.read_text(encoding="utf-8")
    corpo = texto.split("!macro NSIS_HOOK_PREINSTALL", 1)[1]
    return corpo.split("!macroend", 1)[0]


def test_the_installer_removes_the_previous_sidecar_bundle() -> None:
    """Installing over it merges two releases; the older names win the metadata lookup."""
    corpo = _preinstall()

    assert "RMDir /r" in corpo and "sidecar-dist" in corpo, (
        "the pre-install hook no longer wipes the sidecar bundle, so an upgrade merges the old "
        "release into the new one and the app reports the version it used to be"
    )


def test_it_wipes_after_killing_what_holds_the_files_open() -> None:
    """Order, not presence.

    Windows will not delete a directory a running process has files open in. Wiping before the
    `taskkill` would fail silently on exactly the machines that have the problem — the ones with
    Chimera running, which is every machine being upgraded from inside the app.
    """
    corpo = _preinstall()

    assert corpo.index("taskkill") < corpo.index("RMDir /r"), (
        "the bundle is wiped before the processes holding it are killed, so on a running install "
        "the delete quietly does nothing"
    )
    assert corpo.index("Sleep") < corpo.index("RMDir /r"), (
        "no pause between killing the processes and deleting their files — Windows releases "
        "handles asynchronously, which is the reason that Sleep exists at all"
    )


def test_the_pipeline_checks_the_version_it_is_building_not_merely_a_nonzero_one() -> None:
    """The existing guard rejects `0.0.0*` and nothing else.

    It was written for a real defect — every installer once reported `0.0.0+source` — and it fixed
    that symptom. But a sidecar reporting 0.48.0 inside a v0.49.0 build passes it, which is a
    version wrong in the way that actually happens: plausibly, not obviously.
    """
    texto = FLUXO.read_text(encoding="utf-8")
    passo = texto.split("The frozen sidecar knows its own version", 1)
    assert len(passo) == 2, "the pipeline no longer asks the frozen sidecar its version"
    corpo = passo[1].split("- name:", 1)[0]

    # The COMPARISON, not a mention of the tag. The first version of this assertion looked for
    # `github.event.release.tag_name` anywhere in the step — and a sabotage that replaced the whole
    # condition with `if false` left that line untouched and sailed through. Naming the input is not
    # the property; using it to refuse is.
    esperada = re.search(r'ESPERADA="\$\{\{ github\.event\.release\.tag_name \}\}"', corpo)
    assert esperada, "the expected version is no longer taken from the release tag"

    comparacao = re.search(r'"\$VERSION" != "\$ESPERADA"', corpo)
    assert comparacao, (
        "the step no longer compares the frozen sidecar's version against the release being built, "
        "so a sidecar stamped with the PREVIOUS version passes — which is how a version is wrong "
        "in practice: plausibly, not obviously"
    )

    # And that the mismatch is fatal rather than merely printed.
    depois = corpo[comparacao.end():]
    assert "exit 1" in depois.split("fi", 1)[0], (
        "a mismatched version is reported and not refused — the build would ship it"
    )
