"""Is this message a QUESTION about its attached sources, or a TASK done with them? No model call.

The verified-answers gate (`chimera/fusion/verified.py`) was measured on one shape: a question whose
answer is information the excerpts contain (study 26). A message that asks for work *with* a
document — summarize, critique, translate, rewrite, improve, extract into a new format, write
something from it, judge it — is a different shape: its right answer is not a fact in the sources,
and a verifier asking "is every fact in the excerpts?" would read a good summary or a fair critique
as unsupported and ship a decline in its place. The owner's rule: act only on questions; a task
passes straight through, with no check and no grounding note.

**When unsure, it is a task.** A false "question" risks declining legitimate work; a false "task"
only means one answer goes unchecked, which is where every answer was before this gate. So:

1. Only Portuguese and English are read (the owner writes PT-BR; the product is written in EN).
   Any other language is a task.
2. Any clause that asks for work — a task verb in the imperative, after a politeness frame ("can
   you…", "você pode…"), or as its infinitive — makes the whole message a task, even beside a
   question ("What does it say about pricing? Also summarize it.").
3. Asking for a judgement or advice is a task too ("is this well written?", "what do you think?",
   "are there mistakes?", "should we sign?", "is it fair to me?", "what are the risks?"): the answer
   is an opinion or an appraisal, not a fact the sources hold. ``should`` anywhere counts, so
   "should the key go in a header?" is read as a task too — the safe direction.
4. ``explain`` / ``explique`` is a task: an explanation invites background the sources do not carry.
5. What remains is a question when it is interrogative — a question mark, a question word, an
   auxiliary-verb opener — or asks to be told something ("tell me…", "me diga…", "quero saber…").
6. Everything else is a task.

`bench/grounded_question_classifier/` holds two labelled sets and the script that prints precision
and recall (README there). ``messages.jsonl`` (120) was written by this function's author before the
function; ``heldout.jsonl`` (80) by a separate session that never saw it. The first version read the
held-out set at 0.805 precision on "question"; the judgement and politeness rules above were widened
after reading its misses, so the held-out set is no longer held out. ``fresh.jsonl`` (160), written by
another model family, read 5 of 80 tasks as questions (inconclusive); four were Portuguese advice,
appraisal and "será que" frames whose English twins passed, and the rules were widened as classes
on them, so it is in-sample now too. How often a misread task is then declined was measured in
`bench/grounded_task_declines`: 1 real attempt withheld in 60 tasks forced through the check.
"""

from __future__ import annotations

import re
import unicodedata

#: Verbs that ask for WORK with the document. Stems, matched at a word start, so imperative,
#: infinitive and third-person forms of the same verb all count ("resuma", "resumir", "resume").
_TASK_STEMS_EN = (
    "summar", "translat", "critiqu", "criticiz", "criticis", "review", "rewrit", "reword", "rephras",
    "paraphras", "improv", "extract", "writ", "draft", "compos", "creat", "generat", "convert", "turn ",
    "make ", "fix", "correct", "proofread", "edit", "format", "reformat", "shorten", "condens",
    "expand", "simplif", "compar", "analy", "evaluat", "assess", "outlin", "produc", "build",
    "restructur", "implement", "explain", "polish", "recommend", "suggest", "give me a",
    "justify", "justifi",
    "prepare", "organi", "sort", "tabulat", "check ", "grade", "score", "rate ", "refactor",
)
_TASK_STEMS_PT = (
    "resum", "traduz", "critiqu", "critic", "revis", "reescrev", "reformul", "parafrase", "melhor",
    "extrai", "extraia", "escrev", "redij", "redig", "cri", "ger", "convert", "transform", "faç",
    "faz", "corrij", "corrig", "format", "encurt", "condens", "expand", "simplifi", "compar",
    "analis", "avali", "esboc", "esboç", "produz", "constru", "reestrutur", "implement", "expliq",
    "explic", "poli", "recomend", "suger", "sugir", "me dá um", "me da um", "me de um", "prepar",
    "organiz", "orden", "tabel", "verifiqu", "verific", "monte", "montar", "elabor", "deix",
    "list", "classifiq", "classific", "refator", "ajust", "adapt", "dá um", "da um", "dê um",
    "justifiq", "justific",
)
#: Asking for a judgement or advice: an opinion, not a fact the sources hold.
_JUDGEMENT = (
    "what do you think", "your opinion", "is this well", "is it well", "is this good", "is it good",
    "any mistakes", "any errors", "mistakes in", "errors in", "is this correct", "is it correct",
    "should we", "should i", "would you", "how can i improve", "how could i improve", "is it clear",
    "o que você acha", "o que voce acha", "sua opinião", "sua opiniao", "está bem escrito",
    "esta bem escrito", "está bom", "esta bom", "algum erro", "há erros", "ha erros", "tem erro",
    "está correto", "esta correto", "devemos", "devo ", "você acha", "voce acha", "como posso melhorar",
    "como eu melhoro", "está claro", "esta claro", "vale a pena",
    # the answer is about the reader or the assistant's view, not a fact in the document
    "for me", "to you", "for us", "pra mim", "para mim", "pra nós", "para nós", "pra gente",
    # advice: what someone ought to do
    "should", "ought to", "deveria", "deveríamos", "deveriamos", "recommend", "recomenda",
    # quality, fairness, value
    " fair", "good deal", "bad deal", "a good ", "worth it", "justo", "bom negócio", "bom negocio",
    "é bom", "e bom", "é boa", "é ruim", "boa ideia", "good idea",
    # appraisal: strengths, weaknesses, risks, gaps
    "weak point", "weakness", "strength", "strong point", "risks", "risk of", "pontos fracos",
    "pontos fortes", "ponto fraco", "ponto forte", "riscos", "lacunas", "gaps in", "flaws", "falhas",
    "ajuda a decidir", "ajude a decidir", "help me decide", "decidir se", "decide whether",
    "adequad", "apropriad", "appropriate", "suitable", "risky", "arriscad",
)
_POLITE_EN = (
    "please ", "can you ", "could you ", "would you ", "will you ", "pls ", "kindly ",
    "any chance you could ", "any chance you can ", "is it possible to ", "would it be possible to ",
)
_POLITE_PT = (
    "será que ", "sera que ", "por favor ", "por favor, ", "você pode ", "voce pode ",
    "vc pode ", "pode ", "poderia ",
    "consegue ", "você consegue ", "voce consegue ", "dá pra ", "da pra ", "tem como ",
    "você poderia ", "voce poderia ", "vc poderia ", "daria pra ", "daria para ", "dá para ",
    "seria possível ", "seria possivel ",
)
#: A second-person conditional asks for the assistant's own view ("o que você mudaria", "você faria").
#: Not the courtesy modals, which frame a plain question ("você poderia me dizer…", "saberia dizer…").
_YOUR_VIEW = re.compile(
    r"\b(?:você|voce|vc)\s+(?!(?:poderia|conseguiria|gostaria|saberia|teria)m?\b)\w+ria(?:m)?\b"
)
#: "…you could <verb>", "…você poderia <verb>" anywhere in a clause: the verb after it is the request.
_FRAME = re.compile(
    r"\b(?:you|u|você|voce|vc)\s+(?:could|can|would|will|poderia|pode|podia|consegue|conseguiria)"
    r"\s+(?:please\s+|por favor\s+|me\s+)?(\w.*)$"
)
#: Asking to be told something that is in the sources.
_TELL = (
    "tell me", "let me know", "i want to know", "i'd like to know", "i would like to know",
    "me diga", "me diz", "me fala", "me fale", "me informe", "diga-me", "quero saber",
    "gostaria de saber", "preciso saber", "dizer o que", "dizer quem", "dizer qual", "dizer quando",
    "dizer onde", "dizer como", "dizer quanto", "me dizer", "me lembra", "me lembre", "remind me",
)
_QWORDS_EN = (
    "what", "what's", "whats", "which", "who", "whom", "whose", "when", "where", "why", "how",
    "is", "are", "was", "were", "do", "does", "did", "can", "could", "will", "would", "has", "have",
    "had", "according", "quick question",
)
_QWORDS_PT = (
    "o que", "que", "qual", "quais", "quem", "quando", "onde", "por que", "porque", "por quê",
    "como", "quanto", "quantos", "quanta", "quantas", "existe", "existem", "há", "tem", "é", "são",
    "está", "estão", "segundo", "de acordo", "pergunta rápida", "em qual", "em que", "consta",
    "constam",
)
_PT_MARKERS = (
    " o ", " a ", " os ", " as ", " de ", " do ", " da ", " que ", " não ", " é ", " em ", " um ",
    " uma ", " para ", " com ", " por ", " se ", " qual", " quem", " você", " voce", " isso", " esse",
    " este", " esta", " está", "ção", "ções", " são ", " há ", " me ",
)
_EN_MARKERS = (
    " the ", " a ", " of ", " to ", " is ", " are ", " in ", " and ", " what", " how", " does",
    " do ", " this ", " it ", " for ", " with ", " you ", " me ", " which", " who ", " can ",
)

#: Clause breaks: sentence ends, commas and colons, and a coordinating "and"/"e" — so the task in
#: "…? Also summarize it." or "resuma e me diga…" is read as a clause of its own.
_CLAUSE = re.compile(r"[.!?;:,\n]+|\s+(?:and|e)\s+(?=\w)")


def language(text: str) -> str:
    """``"pt"``, ``"en"`` or ``""`` for anything else — by function-word counts, no model."""
    low = f" {_norm(text)} "
    if not low.strip() or re.search(r"[Ѐ-ӿ぀-ヿ一-鿿¿¡]", low):
        return ""
    # Another Latin-script language wins over the PT/EN counts below, which share accents with it
    # ("¿Cuál es…" has an "á" and no Portuguese word): unsure → not read → a task.
    from chimera.fusion.decline_language import from_text

    if from_text(low) not in ("", "pt", "en"):
        return ""
    pt = sum(low.count(m) for m in _PT_MARKERS) + 2 * len(re.findall(r"[ãõçáéíóúâêô]", low))
    en = sum(low.count(m) for m in _EN_MARKERS)
    if pt == en:
        # A tie is decided by the opening word when it is a question word of exactly one of the two.
        first = low.split()[0] if low.split() else ""
        in_pt, in_en = first in _QWORDS_PT, first in _QWORDS_EN
        if in_pt != in_en:
            return "pt" if in_pt else "en"
        return ""
    return "pt" if pt > en else "en"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text or "").casefold()).strip()


def _strip_polite(clause: str, polite: tuple[str, ...]) -> str:
    changed = True
    while changed:
        changed = False
        for p in polite:
            if clause.startswith(p):
                clause = clause[len(p):].lstrip(" ,")
                changed = True
    return clause


def _asks_for_work(clause: str, lang: str) -> bool:
    polite = _POLITE_PT if lang == "pt" else _POLITE_EN
    stems = _TASK_STEMS_PT if lang == "pt" else _TASK_STEMS_EN
    head = _strip_polite(clause, polite)
    # "me ajude a resumir", "help me summarize" — the work verb one step in.
    head = re.sub(r"^(?:me ajud[ae] a |ajude-me a |help me (?:to )?|i need you to |i want you to |quero que você |preciso que você )", "", head)
    head = re.sub(r"^(?:me |also |também |tambem |then |depois |agora |now |just |só |so )+", "", head)
    if any(head.startswith(s) for s in stems):
        return True
    framed = _FRAME.search(clause)
    if framed is None:
        return False
    return any(framed.group(1).startswith(s) for s in stems)


def is_question(message: str) -> bool:
    """True only when ``message`` asks for information its sources should hold (see the module)."""
    text = _norm(message)
    lang = language(text)
    if lang not in ("pt", "en") or not text:
        return False
    if any(j in text for j in _JUDGEMENT) or _YOUR_VIEW.search(text):
        return False
    clauses = [c.strip(" ,:-") for c in _CLAUSE.split(text) if c and c.strip(" ,:-")]
    if any(_asks_for_work(c, lang) for c in clauses):
        return False
    if any(t in text for t in _TELL):
        return True
    if "?" in text:
        return True
    qwords = _QWORDS_PT if lang == "pt" else _QWORDS_EN
    first = clauses[0] if clauses else text
    return any(first == q or first.startswith(q + " ") for q in qwords)
