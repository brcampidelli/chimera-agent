"""Whether a request probably needs commands (tests, builds, checks), decided without a model.

Used at the start of a coding turn: if the folder has commands off and the request needs them, the
owner is asked before the agent edits anything (30/10: an hour of editing, then "commands are off
for this folder").

The rule, in order:

1. An action verb in imperative or infinitive form (rode, rodar, corrija, consertar, faça, altere,
   adicione, ...), or a command-line tool name (pytest, npm, mypy, ...), anywhere in the message,
   question or not. "Pode rodar os testes?" and "Qual o status? Faça o deploy." both count.
2. Otherwise, a plain question (ends with "?" or opens with a question word) does not need commands:
   "qual é o bug no login?", "explique o build", "o que executa esta função?".
3. Otherwise, a request that is not a question needs commands only when the project has a verify
   command.

Third-person and ambiguous forms are not in the verb list ("executa", "roda", "verifica", "corrige",
"faz"), so a description in a question does not count as an order. Nouns ("build", "bug") are not
action words. Known false positives: "teste" and "test" are listed as action words, so a question
about one test ("por que o teste falha?") asks; one question, answered "continue", is the cost.
"""

from __future__ import annotations

import re

#: Command-line tools: a request naming one means commands, whatever the grammar around it.
_TOOL_WORDS = re.compile(
    r"\b(pytest|vitest|jest|mypy|ruff|npm|pnpm|yarn|typecheck|type-check)\b",
    re.IGNORECASE,
)

#: Action verbs, imperative and infinitive only (the form the owner gives an order in), in pt and en.
#: Edits are here too: a long edit is what ran unverified on 30/10.
_ACTION_VERBS = re.compile(
    r"\b("
    # running, checking, fixing
    r"rodar|rode|rodem|run|lint|test|teste|testar|testem|execute|executar|executem|"
    r"verificar|verifique|verifiquem|verify|checar|valide|validar|"
    r"corrigir|corrija|corrijam|consertar|conserte|fix|debugar|debugue|"
    r"compilar|compile|instalar|instale|deploy|faça|faca|implementar|implemente|implementem|"
    r"implement|"
    # editing code
    r"altere|alterar|ajuste|ajustar|crie|criar|refatore|refatorar|edite|editar|"
    r"adicione|adicionar|remova|remover"
    r")\b",
    re.IGNORECASE,
)

#: A question opens with one of these, in pt or en.
_QUESTION_OPENER = re.compile(
    r"^\s*(o que|oque|qual|quais|quem|quando|onde|por que|porque|como|explique|explica|"
    r"what|which|who|when|where|why|how|explain)\b",
    re.IGNORECASE,
)


def has_action_verb(message: str) -> bool:
    """Whether the message contains an order: an action verb, or a command-line tool's name."""
    return bool(_TOOL_WORDS.search(message) or _ACTION_VERBS.search(message))


def is_plain_question(message: str) -> bool:
    """A question or explanation request: it ends with "?" or opens with a question word."""
    text = message.strip()
    return text.endswith("?") or bool(_QUESTION_OPENER.match(text))


def request_needs_commands(message: str, *, verify_configured: bool = False) -> bool:
    """Whether this request probably needs commands to be done or checked. See the module rule."""
    if has_action_verb(message):
        return True
    if is_plain_question(message):
        return False
    return verify_configured
