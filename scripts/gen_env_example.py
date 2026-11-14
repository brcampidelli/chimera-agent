"""Give every ``config.py`` alias a line in ``.env.example``.

Run from the repo root: ``python scripts/gen_env_example.py``. It rewrites only the block between the
two markers at the end of the file. The hand-written part above is left alone, because its comments
say why each value is what it is, and a generator would erase them.

Every generated line is commented out. ``chimera init`` copies the example to ``.env`` verbatim, so
an active ``KEY=value`` here would pin that value for every install that follows the onboarding
(``tests/test_a_copied_example_config_does_not_pin_the_fusion_cast.py`` is the case that taught it).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXEMPLO = ROOT / ".env.example"
INICIO = "# >>> generated: one line per config.py alias (scripts/gen_env_example.py) >>>"
FIM = "# <<< generated <<<"
_PRESENCA = re.compile(r"^\s*#?\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", re.MULTILINE)


def aliases() -> dict[str, str]:
    """Every string ``validation_alias`` on ``Settings``, with its description's first line."""
    sys.path.insert(0, str(ROOT))
    from chimera.config import Settings

    found: dict[str, str] = {}
    for campo in Settings.model_fields.values():
        if isinstance(campo.validation_alias, str):
            linha = (campo.description or "").strip().splitlines()
            found[campo.validation_alias] = linha[0] if linha else ""
    return found


def nomes_presentes(texto: str) -> set[str]:
    """Names with a line in the file, active or commented out."""
    return set(_PRESENCA.findall(texto))


def gerar(texto: str, todos: dict[str, str]) -> str:
    """The file text with the generated block rebuilt to cover what the hand-written part lacks."""
    if INICIO in texto:
        texto = texto[: texto.index(INICIO)].rstrip() + "\n"
    faltam = sorted(n for n in todos if n not in nomes_presentes(texto))
    if not faltam:
        return texto
    linhas = [INICIO, "# Commented out on purpose: an active line here would be copied into .env."]
    for nome in faltam:
        descricao = todos[nome][:100]
        linhas.append(f"# {nome}=" + (f"   # {descricao}" if descricao else ""))
    linhas.append(FIM)
    return texto + "\n" + "\n".join(linhas) + "\n"


def main() -> int:
    texto = EXEMPLO.read_text(encoding="utf-8")
    novo = gerar(texto, aliases())
    if novo != texto:
        EXEMPLO.write_text(novo, encoding="utf-8", newline="\n")
        print(f"{EXEMPLO}: bloco gerado atualizado")
    else:
        print(f"{EXEMPLO}: nada a fazer")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
