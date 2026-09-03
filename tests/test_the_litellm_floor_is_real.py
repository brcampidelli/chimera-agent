"""The litellm pin has a FLOOR now, and nothing was holding it.

The ceiling used to be the guard: `litellm<1.92`, because 1.92 moved to a compiled Rust core and
published wheels only for manylinux — a Windows or macOS user fell back to the sdist and needed a
Rust toolchain to install. That ceiling has a dedicated comment explaining itself, and the comment
says to lift it the moment upstream ships the wheels again.

Upstream did. Checked against PyPI on 2026-09-03, release by release::

    1.92.0  1.92.1                            manylinux only
    1.93.0                                    manylinux only
    1.93.1  1.94.0  1.94.1  1.95.0            Windows, no macOS
    1.96.2 and every release after            Windows AND macOS AND manylinux AND musllinux

So the guard moved from the ceiling to the FLOOR, and the floor is the part with no test behind it.
`>=1.40,<1.99` — which is what the bot proposed — is green on Linux CI and still admits 1.93.0,
where a macOS install breaks exactly the way the old comment describes. A range is only a promise
about its worst member.

`mcp` has had this shape of test since its own ceiling was set (`test_mcp_dependency_is_real`). This
is the same guard for the same class of mistake, on the dependency that is harder to notice because
CI runs on the one platform the bad versions support.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]

#: The first release of the CONTINUOUS run that ships Windows AND macOS wheels. Continuity is the
#: property that matters, not recency: a resolver may pick any member of the range, so one broken
#: version below the floor is enough to break an install.
PISO = (1, 96, 2)


def _spec_litellm() -> str:
    dados = tomllib.loads((RAIZ / "pyproject.toml").read_text(encoding="utf-8"))
    deps = dados["project"]["dependencies"]
    achados = [d for d in deps if d.replace(" ", "").startswith("litellm")]
    assert achados, "litellm is no longer a declared dependency"
    return achados[0]


def _versao(texto: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", texto)[:3])


def test_the_pin_has_a_floor_at_all() -> None:
    spec = _spec_litellm()
    assert re.search(r">=\s*\d", spec), f"{spec!r} has no lower bound, so any version resolves"


def test_the_floor_excludes_every_release_without_windows_and_macos_wheels() -> None:
    spec = _spec_litellm()
    m = re.search(r">=\s*([\d.]+)", spec)
    assert m, spec
    declarado = _versao(m.group(1))
    assert declarado >= PISO, (
        f"{spec!r} admits litellm {'.'.join(map(str, declarado))}, below {'.'.join(map(str, PISO))}. "
        "Releases under that floor ship manylinux-only or Windows-only wheels, so a macOS or "
        "Windows install falls back to the sdist and needs a Rust toolchain. CI runs on Linux, "
        "where every one of them installs fine — this test is the only thing that sees it."
    )


def test_the_ceiling_is_now_an_ordinary_major_bound() -> None:
    """The platform workaround is gone; what remains should be a plain major bound, and be one."""
    spec = _spec_litellm()
    m = re.search(r"<\s*([\d.]+)", spec)
    assert m, f"{spec!r} has no upper bound"
    assert _versao(m.group(1))[0] >= 2, (
        f"{spec!r} still caps inside 1.x. The reason for that cap — manylinux-only wheels — no "
        "longer holds from 1.96.2 on, so a 1.x cap now excludes working releases for a reason that "
        "expired. If a NEW reason appears, write it above the pin rather than leaving this one."
    )


def test_the_comment_records_why_the_floor_is_where_it_is() -> None:
    """A number with no reason beside it is a number the next person will 'tidy up'."""
    texto = (RAIZ / "pyproject.toml").read_text(encoding="utf-8")
    trecho = texto[max(0, texto.find('"litellm') - 1600):texto.find('"litellm')]
    assert "1.96.2" in trecho, "the floor is not explained where it is set"
    assert "macOS" in trecho or "macos" in trecho, "the platform the floor protects is not named"


def test_the_python_bound_no_longer_claims_litellm_as_its_reason() -> None:
    """It used to track the litellm ceiling. That ceiling is gone, so the stated reason had to move.

    The bound itself stays — the test matrix runs 3.11 to 3.13 — but a bound whose written reason
    has expired is the shape that survives long after anyone can check it.
    """
    texto = (RAIZ / "pyproject.toml").read_text(encoding="utf-8")
    i = texto.find("requires-python")
    contexto = texto[max(0, i - 1200):i]
    # Not "the old reason is unmentioned" — the first draft of this test asserted that, and it
    # failed on a comment that mentions the old ceiling in the PAST tense, which is precisely the
    # right thing to write. What must be true is narrower: the expired reason is marked as expired,
    # and the reason that holds today is named.
    assert "1.92" not in contexto or "lifted" in contexto or "used to" in contexto, (
        "the Python bound cites the litellm ceiling without saying it has been lifted, so a reader "
        "cannot tell a live reason from a dead one"
    )
    assert "ci.yml" in contexto or "matrix" in contexto, (
        "the bound needs to say what actually holds it now — the tested versions"
    )
