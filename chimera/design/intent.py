"""Does this message ask for Chimera Design? A deterministic answer, no model, in pt, en and es.

The owner's requirement (2026-10-17): a person who asks to run or use Chimera Design, who names it,
who is working on the design of a project, or who wants a task or a plan presented through design,
gets the design tool. Read literally that fires on every "design" — and "design" is also the word
for code design, system design and database design, which are most of what this product is asked
about. So the answer has three levels, decided in ``study/chimera-design/PLANO.md`` ("Acionamento"):

- ``explicit`` — the person named the product or asked to use, open or present through it. The turn
  gets the tool and the screen may open the canvas directly.
- ``suggest`` — the word "design" (or ``diseño``) together with a second, visual signal: landing,
  screen, slide, layout, post, banner, logo, mockup, wireframe, presentation, poster, flyer, UI,
  interface, prototype… The turn gets the tool and the screen shows a suggestion card.
- ``none`` — everything else, including "design" with only technical company ("design pattern",
  "system design", "o design do banco de dados"). Nothing changes and the tool is not in the turn.

**How it reads.** The message is lower-cased and accent-folded (``apresentação`` → ``apresentacao``,
``diseño`` → ``diseno``) so one pattern covers the spellings people type. Every phrase matches on
word boundaries, never as a substring: ``postgres`` is not a ``post``, ``uiterst`` is not ``ui``.
Technical uses of the word are cut out of the text BEFORE the second signal is looked for, so
"the design pattern of the landing page" is read as a landing page with no design word left in it.

**Naming the product inside engineering work on it.** ``named`` ("Chimera Design" written out) is a
request for the tool only when the message is not work on the product's own code. Working on the
product is the most common reason the name appears: "corrija o bug do Chimera Design em
``chimera/design/ops.py``" or "rode pytest no Chimera Design" name the product and ask for code. So
when ``named`` matches AND the message carries a code-work signal (a path with a code extension or a
``chimera/`` / ``apps/`` segment, a backtick span, a test or lint command, or an engineering word such
as fix, teste, endpoint, commit, PR, branch), the answer is ``none`` with reason
``product_engineering``, and the two matched phrases are the name and the first signal. The other
explicit reasons (``asked_for_the_tool``, ``present_as_design``, ``plan_as_design``) are not gated:
they need the design word in a request, not the product name alone. The same gate applies one level
down: a ``suggest`` (design word plus a visual signal) that comes with code-work signals is ``none``
with reason ``product_engineering``, because a code task that says "design" and "tela" is still code.
A visual request with no code signal ("faz um design de landing page") keeps its suggestion.

Pure function over a bounded prefix of the message: it is called on every turn, before the agent is
built, and must cost nothing a person could notice.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Literal

Level = Literal["explicit", "suggest", "none"]

#: Only the start of a message is read. A pasted log of a megabyte is not a design request, and the
#: patterns below are linear, but there is no reason to pay for the whole paste on every turn.
MAX_CHARS = 4_000

# ---- folding ------------------------------------------------------------------------------------


def fold(text: str) -> str:
    """Lower-case, accents removed, every run of whitespace one space."""
    decomposed = unicodedata.normalize("NFKD", text[:MAX_CHARS].lower())
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(stripped.split())


def _words(*alternatives: str) -> str:
    """A non-capturing alternation, longest first so a phrase wins over its own first word."""
    return "(?:" + "|".join(sorted(alternatives, key=len, reverse=True)) + ")"


def _bounded(body: str) -> re.Pattern[str]:
    """``body`` on word boundaries. ``\\b`` is not enough: it treats ``-`` as a boundary."""
    return re.compile(rf"(?<![a-z0-9]){body}(?![a-z0-9])")


# ---- the design word ----------------------------------------------------------------------------

_DESIGN_WORD = _words("design", "designs", "designer", "designers", "diseno", "disenos",
                      "disenar", "disenador", "disenadora")

# ---- explicit requests --------------------------------------------------------------------------

# The verbs a person uses to ask for the tool itself: run, use, open, start, switch on.
_USE_VERBS = _words(
    # pt
    "usa", "usar", "use", "usem", "utiliza", "utilizar", "utilize", "roda", "rodar", "rode",
    "abre", "abrir", "abra", "executa", "executar", "execute", "ativa", "ativar", "ative",
    "liga", "ligar", "ligue", "inicia", "iniciar", "inicie", "chama", "chamar", "chame",
    # en
    "run", "open", "launch", "start", "fire up", "bring up", "switch to", "go to",
    # es
    "usa", "usar", "usad", "utiliza", "ejecuta", "ejecutar", "ejecute", "corre", "correr",
    "abre", "abrir", "lanza", "lanzar", "activa", "activar", "inicia", "iniciar",
)
_ARTICLE = _words("o", "a", "the", "el", "la", "seu", "su", "your", "teu", "tu")
# What may follow "design" in an explicit request: the tool's own nouns, or nothing.
_TOOL_NOUN = _words("tool", "editor", "canvas", "app", "mode", "ferramenta", "modo", "herramienta",
                    "lienzo", "aplicativo")
# Words that, right after "design", make it a technical design: "use the design pattern…".
_TECH_TAIL = _words(
    "pattern", "patterns", "principle", "principles", "doc", "docs", "document", "decision",
    "decisions", "review", "system", "systems", "of", "for the api", "do", "da", "dos", "das", "de",
    "del", "dela", "padrao", "padroes", "patron", "patrones", "pattern", "by contract",
)
_TOOL_NAMES = _words(
    "design tool", "design editor", "design canvas", "design app", "design mode",
    "ferramenta de design", "editor de design", "canvas de design", "modo design", "modo de design",
    "herramienta de design", "herramienta de diseno", "editor de diseno", "lienzo de diseno",
)
# The verbs of presenting or showing something.
_SHOW_VERBS = _words(
    # pt
    "apresenta", "apresentar", "apresente", "apresentem", "mostra", "mostrar", "mostre", "exibe",
    "exibir", "exiba", "coloca", "colocar", "coloque", "passa", "passar", "passe", "transforma",
    "transformar", "transforme", "monta", "montar", "monte", "visualiza", "visualizar", "visualize",
    "faz", "fazer", "faca", "faca isso",
    # en
    "present", "show", "display", "put", "turn", "lay out", "render", "visualize", "visualise",
    "make",
    # es
    "presenta", "presentar", "presente", "muestra", "mostrar", "muestre", "pon", "poner", "ponga",
    "convierte", "convertir", "convierta", "visualiza", "haz", "hacer", "haga",
)
_THROUGH = _words("em", "como", "no", "num", "numa", "via", "pelo", "atraves do", "atraves de",
                  "in", "as", "as a", "into", "via", "through", "with", "en", "como un", "con el",
                  "en el", "mediante")
_PLAN = _words("plano", "planos", "plan", "plans", "planejamento", "roadmap", "proposta",
               "propuesta", "proposal", "estrategia", "strategy", "pitch")

# ---- code work on the product, which turns the product name into code talk ----------------------

# A path with a code extension: ``chimera/design/intent.py``, ``apps/desktop/package.json``.
_CODE_PATH = re.compile(
    r"(?<![a-z0-9_])[a-z0-9_./-]*\.(?:py|tsx?|js|json|md|css|rs|toml|yml)(?![a-z0-9_])"
)
# A path segment that names this repository's code tree, with or without a leading directory.
_CODE_TREE = re.compile(r"(?<![a-z0-9_.-])(?:chimera|apps)/[a-z0-9_]")
# A backtick span with something inside it: ``pytest -q``, `tests/x.py`.
_CODE_SPAN = re.compile(r"`[^`]*[a-z0-9][^`]*`")
_CODE_COMMANDS = _bounded(_words(
    "pytest", "vitest", "ruff", "mypy", "npm", "uv", "git",
))
_CODE_WORDS = _bounded(_words(
    # pt and en: the work itself
    "implementar", "implemente", "implementa", "implement", "corrigir", "corrija", "fix", "bugs?",
    "refatorar", "refactor", "testes?", "tests?", "endpoints?", "rotas?", "routes?",
    "commit", "pull requests?", "prs?", "worktree", "branch", "diff", "merge",
    "regress(?:ao|oes|ion|ions)",
))
_CODE_SIGNALS: tuple[re.Pattern[str], ...] = (
    _CODE_PATH, _CODE_TREE, _CODE_SPAN, _CODE_COMMANDS, _CODE_WORDS,
)


def _first_code_signal(text: str) -> str | None:
    """The earliest code-work signal in ``text``, or None. Earliest, so the reason reads in order."""
    best: re.Match[str] | None = None
    for pattern in _CODE_SIGNALS:
        found = pattern.search(text)
        if found and (best is None or found.start() < best.start()):
            best = found
    return best.group(0) if best else None


#: (reason, pattern) — the first that matches decides. Each reason is a stable code the screen and
#: the tests read; the sentence a person sees is the desktop's to write (D6).
_EXPLICIT: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("named", _bounded(r"chimera[\s_-]*design")),
    ("asked_for_the_tool", _bounded(rf"{_USE_VERBS}\s+(?:{_ARTICLE}\s+)?{_TOOL_NAMES}")),
    ("asked_for_the_tool", _bounded(
        rf"{_USE_VERBS}\s+(?:{_ARTICLE}\s+)?design(?:\s+{_TOOL_NOUN})?"
        rf"(?!\s+{_TECH_TAIL}(?![a-z0-9]))(?=\s*(?:$|[.,;:!?)]|\s(?:e|and|y|para|pra|to|for|now|"
        rf"agora|ahora|aqui|here|please|por favor|pf|pls)(?![a-z0-9])))"
    )),
    ("present_as_design", _bounded(
        rf"{_SHOW_VERBS}\s+(?:[a-z0-9-]+\s+){{0,6}}?{_THROUGH}\s+(?:{_ARTICLE}\s+)?design"
        rf"(?!\s+{_TECH_TAIL}(?![a-z0-9]))"
    )),
    ("plan_as_design", _bounded(
        rf"{_PLAN}\s+(?:[a-z0-9-]+\s+){{0,3}}?{_THROUGH}\s+(?:{_ARTICLE}\s+)?design"
        rf"(?!\s+{_TECH_TAIL}(?![a-z0-9]))"
    )),
)

# ---- technical uses, removed before the visual signal is looked for -----------------------------

_TECHNICAL = _bounded(_words(
    # en
    r"design patterns?", r"system design", r"systems design", r"software design", r"api design",
    r"database design", r"schema design", r"data model design", r"architecture design",
    r"architectural design", r"code design", r"class design", r"object design", r"design docs?",
    r"design document", r"design decisions?", r"design principles?", r"design review",
    r"domain[- ]driven design", r"design by contract", r"low[- ]level design", r"high[- ]level design",
    r"protocol design", r"algorithm design", r"backend design", r"service design", r"network design",
    # pt — "o design do código", "design de sistemas", "design da API"
    r"padroes? de design", r"padrao de projeto", r"design d[aoe]s? (?:banco|bancos|codigo|api|apis|"
    r"sistema|sistemas|software|arquitetura|classe|classes|modulo|modulos|backend|servico|servicos|"
    r"dados|schema|esquema|tabela|tabelas|funcao|funcoes|algoritmo|protocolo|rede|projeto de "
    r"software|banco de dados|endpoint|endpoints|microsservicos?|dominio|interface de programacao)",
    # es — "diseño del sistema", "patrones de diseño"
    r"patron(?:es)? de diseno", r"diseno de (?:sistemas?|software|base de datos|la base de datos|"
    r"api|apis|codigo|clases?|algoritmos?|backend|servicios?|arquitectura)",
    r"diseno del (?:sistema|codigo|software|backend|servicio|algoritmo|esquema|modelo de datos)",
    r"design del (?:sistema|codigo|software|backend)",
))

# ---- the second, visual signal ------------------------------------------------------------------

_VISUAL = _bounded(_words(
    # surfaces and artefacts
    "landing", "landing page", "landing pages", "pagina de destino", "home page", "homepage",
    "tela", "telas", "screen", "screens", "pantalla", "pantallas",
    "slide", "slides", "deck", "decks", "diapositiva", "diapositivas", "carrossel", "carousel",
    "carrusel", "layout", "layouts", "post", "posts", "banner", "banners", "logo", "logos",
    "logotipo", "logomarca", "mockup", "mockups", "mock-up", "mock-ups", "maqueta", "wireframe",
    "wireframes", "apresentacao", "apresentacoes", "presentation", "presentations", "presentacion",
    "presentaciones", "poster", "posters", "cartaz", "cartazes", "cartel", "flyer", "flyers",
    "folheto", "panfleto", "volante", "ui", "ux", "ui/ux", "interface", "interfaces", "interfaz",
    "prototipo", "prototipos", "prototype", "prototypes", "prototipagem", "prototyping",
    "thumbnail", "miniatura", "capa", "cover", "portada", "infografico", "infographic",
    "infografia", "instagram", "story do instagram", "stories", "convite", "invitation",
    "invitacion", "cardapio", "menu visual", "business card", "cartao de visita", "tarjeta",
    "identidade visual", "visual identity", "identidad visual", "brand", "branding", "marca visual",
    "paleta", "palette", "paleta de cores", "color palette", "tipografia", "typography",
    "dashboard visual", "pitch deck", "one-pager", "one pager", "pagina", "website", "site",
    "sitio web", "visual", "visualmente", "grafico", "graphic", "grafica",
    # the design-system sense of the word, which is visual: tokens, colours, type
    "design system", "sistema de design", "sistema de diseno", "design tokens", "tokens de design",
))
_DESIGN_ANYWHERE = _bounded(_DESIGN_WORD)


@dataclass(frozen=True)
class DesignIntent:
    """What :func:`detect` read. ``matched`` holds the phrases that decided, folded, in order."""

    level: Level
    reason: str
    matched: tuple[str, ...] = field(default_factory=tuple)

    @property
    def fires(self) -> bool:
        return self.level != "none"

    def to_dict(self) -> dict[str, object]:
        return {"level": self.level, "reason": self.reason, "matched": list(self.matched)}


NONE = DesignIntent("none", "no_design_word")


def detect(message: str) -> DesignIntent:
    """Read ``message`` and say whether it asks for Chimera Design, and how firmly."""
    text = fold(message or "")
    if not text:
        return NONE
    for reason, pattern in _EXPLICIT:
        found = pattern.search(text)
        if not found:
            continue
        if reason == "named":
            code = _first_code_signal(text)
            if code is not None:
                return DesignIntent("none", "product_engineering", (found.group(0), code))
        return DesignIntent("explicit", reason, (found.group(0),))
    if not _DESIGN_ANYWHERE.search(text):
        return NONE
    technical = [m.group(0) for m in _TECHNICAL.finditer(text)]
    rest = _TECHNICAL.sub(" ", text) if technical else text
    design_words = [m.group(0) for m in _DESIGN_ANYWHERE.finditer(rest)]
    if not design_words:
        return DesignIntent("none", "technical_design", tuple(technical))
    signals = [m.group(0) for m in _VISUAL.finditer(rest)]
    if not signals:
        return DesignIntent("none", "design_without_visual_signal", (design_words[0],))
    code = _first_code_signal(text)
    if code is not None:
        return DesignIntent("none", "product_engineering", (design_words[0], code))
    return DesignIntent("suggest", "design_with_visual_signal", (design_words[0], *signals[:3]))
