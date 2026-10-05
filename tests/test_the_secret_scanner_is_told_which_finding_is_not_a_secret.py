"""An allowlist entry is silent by construction, so nothing but this file will notice it going stale.

`.gitleaksignore`'s first fingerprint exists because a doc comment in `ApprovalCard.tsx`
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

The other four (2026-10-05) came from the weekly full-history scan, which reads every ref and not a
pull request's range. They are fake credentials written into tests and a bench to prove a credential
is redacted or never leaves. Their reason is a different one, so they are argued for in `FIXTURES`
and the tests at the end of this file, and none of their values is written here.
"""

from __future__ import annotations

import math
import re
import subprocess
from collections import Counter
from dataclasses import dataclass
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


def test_the_ignore_file_names_exactly_the_findings_argued_for_here() -> None:
    """An allowlist that grows is a rule being retired one line at a time, without anyone deciding
    to retire it. The entries are asserted by IDENTITY and in order, not just counted, so a new one
    has to argue for itself here (in `FIXTURES` or beside `COMMIT`) rather than slip in beside the
    others, and a removed one has to be removed here too."""
    entradas = _entradas()
    esperadas = [f"{COMMIT}:{ARQUIVO}:{REGRA}:{LINHA}"] + [
        f"{f.commit}:{f.arquivo}:{f.regra}:{f.linha}" for f in FIXTURES
    ]

    assert entradas == esperadas, (
        f".gitleaksignore has {len(entradas)} entries, and they are not the {len(esperadas)} this "
        f"file argues for. Each one exempts a finding from the secret scanner for every future run. "
        f"If another is genuinely needed, say which finding and why in this test; the file is not "
        f"a place to put things until the gate is quiet.\n" + "\n".join(entradas)
    )


def test_the_entry_is_a_fingerprint_and_not_a_path() -> None:
    """The distinction the repo's own skill draws: exempt by MEANING, not by file.

    A path-only entry would silence every rule in that file for every commit, including a real
    credential added tomorrow. The four fields are what keep the exemption to the one finding —
    commit, file, rule and line — so all four are asserted, not just the file.
    """
    for outra in _entradas():
        partes = outra.split(":")
        assert (
            len(partes) == 4
            and re.fullmatch(r"[0-9a-f]{40}", partes[0]) is not None
            and partes[3].isdigit()
        ), (
            f"not a full fingerprint (40-hex commit:file:rule:line): {outra!r}. A shorter entry is "
            f"a wider exemption than the one finding it was added for."
        )

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


# --------------------------------------------------------------- the fake credentials of 2026-10-05


@dataclass(frozen=True)
class _Ficticio:
    """One fingerprinted finding that is a fake credential, and why it is fake."""

    commit: str
    arquivo: str
    regra: str
    linha: int
    #: Text that must still be on the reported line at that commit: the NAME the value was bound
    #: to, never the value, so a wrong line number cannot pass for the right one.
    marca: str
    #: Whether the file on this branch is the one the finding came from, so the literal can be
    #: asserted gone from it. False only for the bench file whose commit lives on a side branch.
    limpo_na_arvore: bool
    motivo: str


#: The weekly full-history scan's findings, each read and classified before being listed. Every value
#: is a fixture whose whole job is to be caught: a test that a credential is redacted or never shown
#: needs a credential-shaped string to fail on.
FIXTURES: tuple[_Ficticio, ...] = (
    _Ficticio(
        "59b05e5ac5b670d1b01a319126f4c050dce861ea",
        "tests/test_every_way_into_this_machine_is_on_one_card.py",
        "generic-api-key", 47, "SERVER_TOKEN = ", True,
        "the bearer token the access card must never echo more than four characters of",
    ),
    _Ficticio(
        "59b05e5ac5b670d1b01a319126f4c050dce861ea",
        "tests/test_the_privacy_card_says_who_reads_your_prompts.py",
        "generic-api-key", 173, "secret = ", True,
        "the provider key the privacy card must not show, whole or as a hint",
    ),
    _Ficticio(
        "59b05e5ac5b670d1b01a319126f4c050dce861ea",
        "tests/test_the_storage_and_diagnostics_routes_ask_before_removing_and_scrub_what_they_show.py",
        "gcp-api-key", 238, "in a url", True,
        "the Google-shaped key the diagnostics scrubber must mask (two matches, one fingerprint)",
    ),
    _Ficticio(
        "957ff402dee2a2e6cf04c6350421fd3601f796e9",
        "bench/browser_taint_cards/run.py",
        "generic-api-key", 28, "SECRET = ", False,
        "the bait a hostile page asks the browser agent to send; on bench/study24-browser-reads only",
    ),
)


def _linha_em(f: _Ficticio) -> str:
    """The reported line at the reported commit. Skips only where a shallow checkout explains it."""
    r = subprocess.run(
        ["git", "cat-file", "-e", f"{f.commit}^{{commit}}"], cwd=REPO, capture_output=True
    )
    if r.returncode != 0:
        motivo = _ausencia_explicavel()
        assert motivo is not None, (
            f"{f.commit[:9]} is gone from a FULL clone, so the entry for {f.arquivo} exempts nothing. "
            f"If its branch was deleted (957ff40 lives only on bench/study24-browser-reads), the "
            f"finding went with it: remove the entry here and in .gitleaksignore."
        )
        pytest.skip(motivo)
    linhas = _git("show", f"{f.commit}:{f.arquivo}").splitlines()
    assert len(linhas) >= f.linha, f"{f.arquivo} at {f.commit[:9]} has no line {f.linha}"
    return linhas[f.linha - 1]


def _valor(linha: str) -> str:
    """The longest key-shaped run on the line: what the rule captured. Never printed."""
    candidatos = re.findall(r"[A-Za-z0-9_\-]{16,}", linha)
    assert candidatos, "the reported line holds no key-shaped run; the line number is wrong"
    return max(candidatos, key=len)


def _nome(f: _Ficticio) -> str:
    return f.arquivo.rsplit("/", 1)[-1]


@pytest.mark.parametrize("f", FIXTURES, ids=_nome)
def test_each_fake_credential_is_still_on_the_line_its_fingerprint_names(f: _Ficticio) -> None:
    """A fingerprint whose line moved exempts nothing while looking like it does. The NAME is checked,
    not the value, so this file never has to hold the value to know the entry still lands."""
    linha = _linha_em(f)

    assert f.marca in linha, (
        f"line {f.linha} of {f.arquivo} at {f.commit[:9]} does not carry {f.marca!r}; the fingerprint "
        f"names a different line than the one the scanner reported ({f.motivo})."
    )


@pytest.mark.parametrize("f", [f for f in FIXTURES if f.limpo_na_arvore], ids=_nome)
def test_the_current_tree_no_longer_holds_the_fingerprinted_value(f: _Ficticio) -> None:
    """The fingerprint covers the commit, never the file. If the same literal is still in the tree,
    the next edit near it is a new commit with a new finding that no entry exempts, and the gate goes
    red for a reason that looks identical to the one these entries were added for."""
    valor = _valor(_linha_em(f))
    atual = (REPO / f.arquivo).read_text(encoding="utf-8")

    assert valor not in atual, (
        f"{f.arquivo} still holds the literal its fingerprint covers ({len(valor)} characters, "
        f"not printed). Build the value at run time or lower its entropy, as the other fixtures do."
    )


@pytest.mark.parametrize("f", FIXTURES, ids=_nome)
def test_the_ignore_file_does_not_carry_the_value_it_exempts(f: _Ficticio) -> None:
    """Explaining a finding by quoting it reproduces it one file over; the first entry learnt that at
    this file's own line 4. The fake values stay in the history they came from."""
    texto = (REPO / ".gitleaksignore").read_text(encoding="utf-8")

    assert _valor(_linha_em(f)) not in texto, (
        ".gitleaksignore quotes a value it exempts; describe it instead"
    )
