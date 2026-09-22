"""An allowlist entry is silent by construction, so nothing but this file will notice it going stale.

`.gitleaksignore` carries one fingerprint. It exists because a doc comment in `ApprovalCard.tsx`
wrote an i18n key as an example of a literal map's entry, and the `generic-api-key` rule read the
shape — a keyword, a separator, a quoted token — and kept it on entropy: 3.6818807, against a
threshold of 3.5. The string is the name of a test file, not a credential.

Rewording the comment cleans the tree. It does not clean the HISTORY, and the scanner reads a range
of commits (`--no-merges --first-parent base^..head`, in `gitleaks-action`), so the finding survives
in the commit that introduced it and the `supply-chain` job goes red on a pull request whose tree is
clean. That asymmetry — a green tree and a red gate — is the reason the entry is a fingerprint
rather than a rewording alone.

What this file asserts is the part a comment cannot: that the entry is still TRUE. The line it names
has to still be the line it names, the file has to still exist, and the text there has to still be
the thing that triggered the rule. Every one of those can rot:

  * someone edits the doc comment and shifts line 128 — the fingerprint stops matching, the gate
    goes red again, and the entry sits there looking like it is doing something;
  * someone deletes `ApprovalCard.tsx` — the entry now names nothing, and the next real leak in
    whatever replaces it is not what this file is about;
  * someone rewrites the sentence so it triggers the rule AGAIN, in a new commit — the old
    fingerprint still matches the old commit, so the entry would be hiding nothing while the gate
    is red for a reason that looks identical to the one it was added for.

None of those is hypothetical; they are the three ways this entry stops earning its place. The last
one is why the second test reads the CURRENT tree for the shape, and not just the history.

What this cannot show: whether the scanner's entropy threshold moves. The rule is a heuristic inside
a pinned binary, and a version bump could change what it flags. `GITLEAKS_VERSION` in `ci.yml` is
pinned precisely so that bump is deliberate, and this file would fail on the pull request that
makes it.
"""

from __future__ import annotations

import math
import re
import subprocess
from collections import Counter
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

#: The commit that introduced the finding. Its identity is the whole point of a fingerprint entry:
#: change this and the entry stops exempting anything, silently.
COMMIT = "7190c2f9c663febb2b9ef4ed6207b12ee2bc41b4"
ARQUIVO = "apps/desktop/src/components/code/ApprovalCard.tsx"
REGRA = "generic-api-key"
LINHA = 128

#: The value the rule captured, and the threshold it had to clear to be reported at all. Both are
#: asserted rather than described, because the entry's justification is exactly these two numbers.
SEGREDO = "i18n.reachable.test"
LIMIAR_ENTROPIA = 3.5


def _entropia(texto: str) -> float:
    contagem = Counter(texto)
    n = len(texto)
    return -sum((v / n) * math.log2(v / n) for v in contagem.values())


#: The keyword family `generic-api-key` fires on and the separators it accepts between that keyword
#: and the token. Deliberately NOT a copy of the rule: the real one is entropy-gated inside a
#: pinned binary, and a test may not download it. What is asserted instead is both directions —
#: this matches the line that was actually reported, and does not match the current tree.
PALAVRAS_CHAVE = "key|secret|token|password|passwd|api|auth|credential"
SEPARADORES = r"[\s:=<>\"'`]"
_FORMA = re.compile(rf"\b(?:{PALAVRAS_CHAVE})\b{SEPARADORES}{{0,10}}{re.escape(SEGREDO)}", re.IGNORECASE)


def _forma_casada(texto: str) -> bool:
    """Does `texto` write the shape the scanner flagged: a keyword, a separator, then the token?

    A stand-in for the scanner, not a reimplementation of it — its whole claim is that it fires on
    line 128 of the commit that was really reported (asserted below) and stays quiet on the tree as
    it stands now. A detector that answered False to everything would fail the first of those, which
    is what keeps it from being a rubber stamp.

    Matched per LINE, because the rule is line-scoped: a keyword on one line and the token on the
    next are not a match. Treating the file as one string would make the current tree look infected,
    since its line 160 writes the token BEFORE the word `key` — a shape no scanner reports.
    """
    return any(_FORMA.search(linha) for linha in texto.splitlines())


def _commit_existe() -> bool:
    r = subprocess.run(
        ["git", "cat-file", "-e", f"{COMMIT}^{{commit}}"], cwd=REPO, capture_output=True
    )
    return r.returncode == 0


def _ausencia_explicavel() -> str | None:
    """Why the commit named by the entry might be absent, or None when its absence is a defect.

    `quality` checks out with the default `fetch-depth: 1`, so a pull request's test run contains
    only the tip and never `7190c2f`. `ci.yml` states the difference itself: `supply-chain` sets
    `fetch-depth: 0` and says gitleaks needs the history. Failing there would be a red about the
    checkout, not about the assertion.

    A FULL clone missing the commit is the opposite fact and a serious one — the fingerprint would
    name a commit that no longer exists, exempting nothing — so it returns None and the caller
    fails instead of skipping.
    """
    r = subprocess.run(
        ["git", "rev-parse", "--is-shallow-repository"], cwd=REPO, capture_output=True, text=True
    )
    if r.returncode == 0 and r.stdout.strip() == "true":
        return (
            f"shallow checkout (fetch-depth: 1): {COMMIT[:9]} was never fetched, so its content "
            f"cannot be checked here. This passes on any full clone — locally, and in the "
            f"`supply-chain` job, which sets fetch-depth: 0."
        )
    return None


def _entradas() -> list[str]:
    """The non-comment lines of `.gitleaksignore`, in order."""
    linhas = (REPO / ".gitleaksignore").read_text(encoding="utf-8").splitlines()
    return [linha.strip() for linha in linhas if linha.strip() and not linha.lstrip().startswith("#")]


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout


def test_the_ignore_file_names_exactly_one_finding() -> None:
    """An allowlist that grows is a rule being retired one line at a time, without anyone deciding
    to retire it. This one is a single entry and the count is asserted, so a second entry has to
    argue for itself here rather than slip in beside the first."""
    entradas = _entradas()

    assert len(entradas) == 1, (
        f".gitleaksignore has {len(entradas)} entries, not 1. Each one exempts a finding from the "
        f"secret scanner for every future run. If a second is genuinely needed, say which finding "
        f"and why in this test — the file is not a place to put things until the gate is quiet.\n"
        + "\n".join(entradas)
    )


def test_the_entry_is_a_fingerprint_and_not_a_path() -> None:
    """The distinction the repo's own skill draws: exempt by MEANING, not by file.

    A path-only entry would silence every rule in that file for every commit, including a real
    credential added tomorrow. The four fields are what keep the exemption to the one finding —
    commit, file, rule and line — so all four are asserted, not just the file.
    """
    entrada = _entradas()[0]
    campos = entrada.split(":")

    assert len(campos) == 4, (
        f"the entry is not a full fingerprint: {entrada!r}. Four colon-separated fields are needed "
        f"(commit, file, rule, line). Fewer means a wider exemption: without the line it covers "
        f"every finding of that rule in that commit, without the rule every finding in that file."
    )
    sha, arquivo, regra, linha = campos

    assert sha == COMMIT, (
        f"the entry names commit {sha!r}, not {COMMIT!r}. A fingerprint is commit-scoped, so if "
        f"this is a deliberate re-point it must come with the finding that forced it — otherwise "
        f"the entry now exempts nothing and the old finding is unexempted."
    )
    assert arquivo == ARQUIVO
    assert regra == REGRA
    assert linha == str(LINHA), (
        f"the entry names line {linha!r}, not {LINHA}. The line is what makes the exemption this "
        f"narrow, and it moves whenever the doc comment above it gains or loses a line."
    )


def test_the_named_file_still_exists_and_the_line_still_holds_the_finding() -> None:
    """A fingerprint that outlives its target exempts nothing while looking like it exempts
    something. This reads the commit the entry names, not the working tree, because that commit is
    what the scanner will scan on every future pull request."""
    if not _commit_existe():
        motivo = _ausencia_explicavel()
        assert motivo is not None, (
            f"{COMMIT[:9]} is gone from a FULL clone. `.gitleaksignore` names it, so the exemption "
            f"now points at nothing and the gate is red for a reason this file cannot describe. "
            f"Restore the commit or re-point the entry on purpose — do not skip past it."
        )
        pytest.skip(motivo)

    conteudo = _git("show", f"{COMMIT}:{ARQUIVO}")
    linhas = conteudo.splitlines()

    assert len(linhas) >= LINHA, (
        f"{ARQUIVO} at {COMMIT[:9]} has {len(linhas)} lines, so line {LINHA} does not exist. The "
        f"entry's line number is wrong and the exemption is not landing on anything."
    )
    alvo = linhas[LINHA - 1]

    assert SEGREDO in alvo, (
        f"line {LINHA} at {COMMIT[:9]} no longer contains {SEGREDO!r}. It reads:\n  {alvo}\n"
        f"The fingerprint is commit-scoped, so this commit's content cannot change — which means "
        f"either the entry was written against the wrong line, or it is naming the wrong finding. "
        f"Re-derive it with `gitleaks git --log-opts=... .` and read the reported line number."
    )


def test_the_finding_was_an_example_of_a_key_and_not_a_key() -> None:
    """Why the entry is legitimate, in one assertion: the captured value is a filename.

    This is the sentence the repo's skill says to write down before adding an exemption — "if you
    cannot, the check may be right and the code wrong". It holds here because the token is the name
    of a test in the same tree, so the file it names is asserted to exist.
    """
    alvo = (REPO / "apps/desktop/src/lib" / f"{SEGREDO}.ts")

    assert alvo.exists(), (
        f"{SEGREDO!r} is not the name of a test file in src/lib. That is the entire claim: the "
        f"scanner captured a FILENAME out of a comment, not a credential. If the file was renamed "
        f"or removed, the exemption's reason is gone and it should be re-examined rather than kept."
    )


def test_the_value_clears_the_threshold_only_by_being_a_dotted_name() -> None:
    """The rule is entropy-based, so the exemption is defensible only if the entropy is real.

    Asserted rather than asserted-about: if the captured string were long and random-looking it
    WOULD be indistinguishable from a key, and this file should say so instead of excusing it. A
    dotted, lowercase, repeated-letter name is the opposite of that.
    """
    entropia = _entropia(SEGREDO)

    assert entropia >= LIMIAR_ENTROPIA, (
        f"{SEGREDO!r} scores {entropia:.4f}, below the {LIMIAR_ENTROPIA} the rule requires — so it "
        f"would not be flagged and this entry exempts nothing. The threshold or the rule changed."
    )
    assert re.fullmatch(r"[a-z0-9.]+", SEGREDO), (
        f"{SEGREDO!r} is not a plain dotted lowercase name. If the captured value ever looks like a "
        f"key, the honest move is to fix the text that produced it, not to widen this entry."
    )


def test_the_detector_still_fires_on_the_line_that_was_actually_reported() -> None:
    """The control that keeps `_forma_casada` from being a rubber stamp.

    Every other use of that helper asserts it returns False — and a helper hardcoded to False
    satisfies all of them while detecting nothing. This is the direction that costs something: it
    reads the reported line out of history and requires the detector to find the shape there. Drop
    it and the tests below degrade into asserting that a function call returns False.
    """
    if not _commit_existe():
        motivo = _ausencia_explicavel()
        assert motivo is not None, (
            f"{COMMIT[:9]} is gone from a FULL clone, so the detector's only real example went with "
            f"it. Failing rather than skipping: without this control the tests below prove nothing."
        )
        pytest.skip(motivo)

    alvo = _git("show", f"{COMMIT}:{ARQUIVO}").splitlines()[LINHA - 1]

    assert _forma_casada(alvo), (
        f"the detector no longer recognises the shape that was actually reported. Line {LINHA} at "
        f"{COMMIT[:9]} reads:\n  {alvo}\nIt pairs a keyword with a separator and the token, which is "
        f"the whole basis for the tests below. Widen PALAVRAS_CHAVE/SEPARADORES, or admit the "
        f"detector no longer models the rule."
    )


def test_the_current_tree_does_not_reintroduce_the_shape() -> None:
    """The entry names one commit. A NEW commit that trips the same rule is a different finding and
    is not exempt — so the tree must not contain the shape the comment used to have.

    This is the test that would have caught the first draft of this very change: the explanatory
    comment in `.gitleaksignore` wrote the offending text out to describe it, and the scanner
    reported the ignore file itself, at its own line 4. Explaining a false positive in its own
    syntax reproduces it one file over.
    """
    atual = (REPO / ARQUIVO).read_text(encoding="utf-8")

    assert not _forma_casada(atual), (
        f"{ARQUIVO} still writes the flagged shape in the working tree — a keyword, a separator and "
        f"{SEGREDO!r} right after it. That is a NEW finding in a new commit, and the fingerprint "
        f"above does not exempt it: the gate goes red while the entry sits there looking like it "
        f"should have prevented it. Reword the text."
    )


def test_the_ignore_file_does_not_reproduce_the_shape_inside_itself() -> None:
    """Same rule, applied to the file that carries the exemption — because that is exactly where it
    bit, and a comment explaining a scanner's false positive is the most natural place to retype it.
    """
    texto = (REPO / ".gitleaksignore").read_text(encoding="utf-8")

    assert not _forma_casada(texto), (
        ".gitleaksignore writes out the text the scanner flagged, so the exemption file is itself "
        "reported. Describe the shape instead of quoting it — see this file's docstring."
    )
