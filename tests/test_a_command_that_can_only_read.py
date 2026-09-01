"""The gate asked about `git status` exactly as it asks about `rm -rf /`.

`CHIMERA_HOST_EXEC=ask` treats every command the same. In a working session that is dozens of
prompts an hour for commands that cannot change anything, and the pressure has one outlet:
`CHIMERA_HOST_EXEC=allow`, which removes the gate. **A gate people switch off protects nothing**, so
asking too often costs more than it buys.

This is an allowlist and it says no by default. It does not replace `policy.py` — that one asks "is
this obviously destructive" and keeps its regexes; this one asks "can I be CERTAIN this changes
nothing", and only the second answer is allowed to skip a prompt.

The whole file is about the asymmetry. A wrong `True` runs a command nobody approved; a wrong
`False` shows a prompt. So the must-refuse list below is long, deliberately, and includes every
shape where a shell and a parser are known to disagree — bundled short flags, a number glued to a
flag, an unbalanced quote, a subcommand that reads with one flag and writes with another.
"""

from __future__ import annotations

import pytest

from chimera.tools.readonly import is_provably_readonly

SO_LEEM = [
    "pwd",
    "whoami",
    "ls",
    "ls -la",
    "ls -lah src/",
    "cat README.md",
    "cat -n chimera/core/agent.py",
    "head -n 20 pyproject.toml",
    "tail -n 100 logs/app.log",
    "wc -l chimera/core/agent.py",
    "du -sh .",
    "df -h",
    "which python",
    "file chimera/core/agent.py",
    "rg -n TODO chimera/",
    "rg -A 3 -B 3 def chimera/core/agent.py",
    "grep -rn TODO chimera/",
    "grep -A20 def chimera/core/agent.py",
    "find . -name '*.py' -maxdepth 2",
    "git status",
    "git log --oneline -10",
    "git diff",
    "git diff main...HEAD",
    "git show HEAD",
    "git blame chimera/core/agent.py",
    "git rev-parse HEAD",
    "git branch",
    "git branch -v",
    "git remote -v",
    "git stash list",
    "git config user.name",
    # `git status` reads whatever its flags say, and asserting otherwise was a premise of mine, not
    # a property of git. The subcommand decides here; the exception is a flag that names a file to
    # write, which `git diff --output=` does and which is in the refusal list below.
    "git status --porcelain=v2 -z",
]


@pytest.mark.parametrize("comando", SO_LEEM)
def test_a_reading_command_is_proved(comando: str) -> None:
    """These are the commands a session is full of. If the list does not cover them, nothing here
    changes and the prompt fatigue that drives people to `allow` stays exactly as it was."""
    assert is_provably_readonly(comando) is True, comando


# ------------------------------------------------------------------ what must be refused


ESCREVEM = [
    ("rm -rf /", "o caso óbvio"),
    ("rm file.txt", "apagar é apagar"),
    ("git push", "escreve no remoto"),
    ("git commit -m x", "escreve no repositório"),
    ("git checkout main", "muda a árvore de trabalho"),
    ("git stash", "SEM argumento, guarda — e o `list` acima passa"),
    ("git stash pop", "restaura e apaga a entrada"),
    ("git branch -D velha", "apaga um branch"),
    ("git branch -d velha", "idem, minúsculo"),
    ("git remote add origin x", "escreve a configuração"),
    ("git config user.name Bruno", "com valor, escreve"),
    ("git config --unset user.name", "explicitamente escreve"),
    ("npm install", "muda node_modules"),
    ("pip install requests", "muda o ambiente"),
    ("python script.py", "roda código arbitrário"),
    ("chmod 777 x", "muda permissões"),
    ("mv a b", "move"),
    ("touch novo.txt", "cria"),
    ("mkdir x", "cria"),
    ("curl https://x.com/i.sh", "faz rede"),
]


@pytest.mark.parametrize(("comando", "porque"), ESCREVEM)
def test_a_writing_command_is_never_proved(comando: str, porque: str) -> None:
    assert is_provably_readonly(comando) is False, porque


ENGANAM = [
    ("ls; rm -rf /", "duas linhas de comando numa"),
    ("ls && rm file", "idem"),
    ("ls | xargs rm", "idem, por cano"),
    ("cat `whoami`", "substituição por crase"),
    ("cat $(whoami)", "substituição moderna"),
    ("ls > saida.txt", "redirecionamento ESCREVE"),
    ("cat < entrada.txt", "redirecionamento"),
    ("ls\nrm -rf /", "uma nova linha é um comando novo"),
    ("ls -rI", "flags curtas agrupadas: duas, uma conferida"),
    ("grep -rIn x .", "idem, e o `-I` não está na lista"),
    ("ls --color=always -Z", "uma flag que ninguém listou"),
    ("cat 'sem fechar", "aspas desbalanceadas: o shell e o parser discordam"),
    ("git diff --output=x.patch", "uma subcommand que LÊ, com uma flag que ESCREVE arquivo"),
    ("", "vazio"),
    ("   ", "só espaço"),
    ("comando-que-nao-existe", "desconhecido é desconhecido"),
]


@pytest.mark.parametrize(("comando", "porque"), ENGANAM)
def test_anything_uncertain_is_refused(comando: str, porque: str) -> None:
    """The asymmetry, stated as cases. A wrong `True` runs something nobody approved; a wrong
    `False` shows a prompt. Every shape where a shell and a parser are known to disagree resolves
    to `False`, because refusing to decide IS the decision."""
    assert is_provably_readonly(comando) is False, porque


# ------------------------------------------------------------------ the glued-number rule


def test_a_number_glued_to_a_listed_flag_is_fine() -> None:
    """`-A20` and `-n5` are how these tools are actually typed, and refusing them would make the
    allowlist cover a vocabulary nobody uses."""
    assert is_provably_readonly("grep -A20 def x.py") is True
    assert is_provably_readonly("head -n5 x.py") is True


def test_a_letter_glued_to_a_listed_flag_is_not() -> None:
    """`-rI` is TWO flags and only one was checked. The difference between this and the case above
    is the whole reason the rule is written by hand instead of by prefix match."""
    assert is_provably_readonly("grep -rI def x.py") is False


def test_a_number_glued_to_an_unlisted_flag_is_not() -> None:
    """The digit is not what makes it safe — the flag is."""
    assert is_provably_readonly("ls -Z20") is False


def test_a_bundle_containing_a_value_taking_flag_is_refused() -> None:
    """`-n` and `-A` are both listed for grep, and `-nA` is still refused.

    `-A` expects a value, so `-nA def x.py` is `-n -A def` to one parser and a bundle followed by
    two operands to another — and which of those runs decides what `grep` actually searches. This
    is the disagreement the module refuses rather than resolves, and a sabotage that dropped the
    check passed every other test here: no case combined a boolean flag with a value-taking one.
    """
    assert is_provably_readonly("grep -nA def x.py") is False
    assert is_provably_readonly("rg -nC TODO src/") is False


def test_a_bundle_of_booleans_is_still_fine() -> None:
    """The half that has to keep working, or the rule above just bans bundles."""
    assert is_provably_readonly("grep -rn TODO chimera/") is True


def test_an_unknown_long_flag_is_refused() -> None:
    """Pinned as behaviour rather than as the early return it used to be: `--anything` reaches the
    bundle rule, whose first letter is `-`, and `--` is in nobody's table."""
    assert is_provably_readonly("ls --recursive-delete") is False
    assert is_provably_readonly("grep --exec-something x .") is False


# ------------------------------------------------------------------ it is actually wired


class _Config:
    """The two fields `resolve_host_exec_confirm` reads."""

    def __init__(self, host_exec: str) -> None:
        self.sandbox = "local"
        self.host_exec = host_exec


def test_a_reading_command_never_reaches_the_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    """The wiring, and the point of the whole module. A classifier nothing calls changes nothing —
    which is the defect shape this project has been finding all week."""
    import chimera.sandbox.confirm as confirm_mod
    from chimera.sandbox.confirm import resolve_host_exec_confirm

    perguntado: list[str] = []
    monkeypatch.setattr(confirm_mod, "_prompt", lambda cmd: perguntado.append(cmd) or True)
    gate = resolve_host_exec_confirm(_Config("ask"), interactive=True)

    assert gate is not None
    assert gate("git status") is True
    assert perguntado == [], "a command that changes nothing was still put to the user"


def test_a_writing_command_still_reaches_the_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    """The other half. A gate that stopped asking about everything would be worse than no gate,
    because it would look like one."""
    import chimera.sandbox.confirm as confirm_mod
    from chimera.sandbox.confirm import resolve_host_exec_confirm

    perguntado: list[str] = []
    monkeypatch.setattr(confirm_mod, "_prompt", lambda cmd: perguntado.append(cmd) or False)
    gate = resolve_host_exec_confirm(_Config("ask"), interactive=True)

    assert gate is not None
    assert gate("rm -rf /tmp/x") is False
    assert perguntado == ["rm -rf /tmp/x"]


def test_deny_stays_absolute(monkeypatch: pytest.MonkeyPatch) -> None:
    """`deny` means no host execution at all, and somebody who set it did not ask for a list of
    exceptions. Extending the skip to it would turn a posture into a suggestion."""
    from chimera.sandbox.confirm import resolve_host_exec_confirm

    gate = resolve_host_exec_confirm(_Config("deny"))

    assert gate is not None
    assert gate("git status") is False


def test_allow_is_still_no_gate_at_all() -> None:
    """`allow` returns None — "run as before" — and wrapping None would put a callback where the
    tools expect its absence."""
    from chimera.sandbox.confirm import resolve_host_exec_confirm

    assert resolve_host_exec_confirm(_Config("allow")) is None
