"""Items for the useful-context bench: agent-shaped transcripts grown to a target length.

Every item is ONE conversation, rendered at every length of the ladder. What changes between the
renders of one item is only how much filler sits between the parts that matter; the task, the rule,
the five runbooks, their relative depths and the question are identical. That is what makes the
comparison against the 4k render paired.

The parts, in order:

1. The product's default system prompt and the schemas of the four read-only tools the transcript
   uses (``read_file``, ``grep``, ``list_dir``, ``glob``) -- straight from the product's registry.
2. The user's opening message: an audit task plus three session rules. One of the three is the
   TARGET rule, a naming format for "our services" (six formats, balanced over items); the other two
   are ordinary session rules that are never graded. The target's position in the list (1st, 2nd or
   3rd) varies.
3. Filler: assistant tool calls and their real results, produced by running the product's own
   ``ReadFileTool`` / ``GrepTool`` over this repository's ``chimera/`` sources.
4. Five runbook reads (``read_file ops/runbooks/<service>.md``) placed at 10/30/50/70/90% of the
   filler. Each says where one invented service's machines sit, by a LANDMARK, never by a country.
   The TARGET runbook is at 10%, 50% or 90% (balanced); the other four slots hold distractors.
5. A short assistant progress message, then the user's question: which of our services is hosted in
   <country>? The answer needs the landmark -> country association (no literal overlap between the
   question and the needle, NoLiMa-style) AND the naming rule from the opening message.

Graded deterministically in :func:`grade`: an exact, case-sensitive string plus "no other service
named". No model reads the answers.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import sys
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from chimera.core.agent import DEFAULT_SYSTEM_PROMPT  # noqa: E402
from chimera.tools.files import ListDirTool, ReadFileTool  # noqa: E402
from chimera.tools.search import GlobTool, GrepTool  # noqa: E402

#: The ladder, in provider-counted prompt tokens. Registered in PREREGISTRATION.md.
LADDER: tuple[int, ...] = (4_000, 16_000, 32_000, 64_000, 128_000)
CONTROL = 4_000

#: Where the five runbooks sit, as a fraction of the filler that precedes them.
SLOTS: tuple[float, ...] = (0.1, 0.3, 0.5, 0.7, 0.9)
#: Where the TARGET runbook may sit (a subset of SLOTS): early, middle, late.
TARGET_DEPTHS: tuple[float, ...] = (0.1, 0.5, 0.9)

#: Characters per provider token, used only to ASSEMBLE a render of roughly the target size. What is
#: reported is always the provider's own count. Registered value; recalibrated once from the pilot's
#: measured counts, in writing, before the main run (see PREREGISTRATION.md).
DEFAULT_CHARS_PER_TOKEN = 3.8

#: Typical size of one filler tool result at the long lengths, in characters; at short lengths the
#: filler is split into at least MIN_UNITS results so the five depths stay distinct.
UNIT_CHARS = 6_000
MIN_UNITS = 12

EXAMPLE = "night-auditor"

#: The target rules. Each is (id, sentence tail, formatter). The sentence is completed below with
#: an example on a name that is never one of the item's services.
RULES: dict[str, tuple[str, Any]] = {
    "snake": (
        f"write it in SCREAMING_SNAKE_CASE: {EXAMPLE} becomes NIGHT_AUDITOR.",
        lambda s: s.replace("-", "_").upper(),
    ),
    "prefix": (
        f"prefix it with svc: -- {EXAMPLE} becomes svc:{EXAMPLE}.",
        lambda s: f"svc:{s}",
    ),
    "suffix": (
        f"append the owning group @ops -- {EXAMPLE} becomes {EXAMPLE}@ops.",
        lambda s: f"{s}@ops",
    ),
    "camel": (
        f"write it in camelCase: {EXAMPLE} becomes nightAuditor.",
        lambda s: s.split("-")[0] + "".join(p.capitalize() for p in s.split("-")[1:]),
    ),
    "bracket": (
        f"wrap it in square brackets: {EXAMPLE} becomes [{EXAMPLE}].",
        lambda s: f"[{s}]",
    ),
    "dots": (
        f"write it with dots instead of hyphens: {EXAMPLE} becomes night.auditor.",
        lambda s: s.replace("-", "."),
    ),
}
RULE_IDS: tuple[str, ...] = tuple(RULES)

RULE_LEAD = (
    "Every time you write the name of one of our services -- in prose, in a list, or as a "
    "one-word answer -- "
)

#: Ordinary session rules. Never graded; they make the rule list look like a real one and let the
#: target's position in it vary.
OTHER_RULES: tuple[str, ...] = (
    "Do not modify anything under chimera/providers/; another team owns it.",
    "Run the test suite before you tell me that anything is finished.",
    "Do not add new dependencies to pyproject.toml.",
    "Keep every commit message under 72 characters.",
    "Never paste secrets or API keys into your answers.",
    "Ask me before you delete any file.",
)

TASK = (
    "I'm auditing this repository ahead of a hosting migration. Read whatever you need -- start "
    "with how configuration and model providers are loaded, then go through the ops runbooks for "
    "our services -- and keep going until I stop you; I'll ask you questions along the way.\n\n"
    "Rules for the whole session:\n1. {r1}\n2. {r2}\n3. {r3}"
)

PROGRESS = (
    "So far I have read through the configuration and provider modules and the runbooks for our "
    "services. Next I will trace how the gateway picks a backend for each call."
)

QUESTION = (
    "Quick question before you carry on: which of our services is hosted in {country}? Answer from "
    "what you have already read, without calling any tool, and reply with the service name only."
)

#: Invented service names. Checked against the filler corpus by :func:`corpus_collisions`.
SERVICES: tuple[str, ...] = (
    "ember-relay", "quill-sorter", "tallow-gate", "marrow-sync", "cobalt-drift", "fennel-queue",
    "lumen-forge", "osprey-ledger", "thistle-cache", "garnet-pipe", "velvet-scout", "basalt-hook",
    "juniper-mint", "harbor-vane", "ochre-spool", "lichen-post", "saffron-bell", "tundra-press",
    "cinder-loom", "bramble-wire", "pewter-dock", "meadow-flare", "sorrel-grid", "walnut-beacon",
    "flint-harvest", "quartz-shuttle", "nettle-stamp", "alder-signal", "copper-tide", "heron-batch",
    "orchid-rail", "pumice-vault", "raven-ticket", "tamarind-mesh", "willow-parcel",
    "yarrow-switch", "zinnia-tally",
)

#: (landmark phrase, country phrase as the question says it). No landmark contains its country's
#: name or adjective, so the question and the needle share no word.
LANDMARKS: tuple[tuple[str, str], ...] = (
    ("Shibuya Crossing", "Japan"),
    ("the Sagrada Familia", "Spain"),
    ("the Christ the Redeemer statue", "Brazil"),
    ("the Colosseum", "Italy"),
    ("the CN Tower", "Canada"),
    ("the Golden Gate Bridge", "the United States"),
    ("the Petronas Twin Towers", "Malaysia"),
    ("the Burj Khalifa", "the United Arab Emirates"),
    ("the Charles Bridge", "the Czech Republic"),
    ("the Atomium", "Belgium"),
    ("the Acropolis", "Greece"),
    ("the Hagia Sophia", "Turkey"),
    ("the Rijksmuseum", "the Netherlands"),
    ("the Hallgrimskirkja church", "Iceland"),
    ("the Sydney Opera House", "Australia"),
    ("Schonbrunn Palace", "Austria"),
    ("the Vasa Museum", "Sweden"),
    ("the Oriental Pearl Tower", "China"),
    ("Marina Bay Sands", "Singapore"),
    ("Big Ben", "the United Kingdom"),
    ("the Eiffel Tower", "France"),
    ("the Taj Mahal", "India"),
    ("the pyramids of Giza", "Egypt"),
    ("the Brandenburg Gate", "Germany"),
    ("Table Mountain", "South Africa"),
    ("the Belem Tower", "Portugal"),
    ("Wawel Castle", "Poland"),
    ("the Lotte World Tower", "South Korea"),
    ("the Sky Tower in Auckland", "New Zealand"),
    ("the Guinness Storehouse", "Ireland"),
    ("the Kapellbrucke in Lucerne", "Switzerland"),
    ("Wat Arun", "Thailand"),
)

#: Needle sentences. None contains "host", "run", "service" or a country: the question's words are
#: absent from the needle by construction.
WHERE = (
    "its machines sit in a colocation hall a short walk from {landmark}.",
    "the primary instance lives on racks in a building that overlooks {landmark}.",
    "a cage in the data hall across the street from {landmark}.",
    "the hardware sits in a facility within sight of {landmark}.",
    "its only cluster is racked a few blocks from {landmark}.",
)
TEAMS = ("payments", "growth", "platform", "billing", "search", "mobile", "data", "support")
PURPOSES = (
    "batches outgoing webhooks", "renders invoice PDFs", "resizes uploaded images",
    "sends the nightly digest email", "reconciles card settlements", "expires stale sessions",
    "rebuilds the search index", "archives audit logs", "scores fraud signals",
    "syncs the product catalogue",
)
DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday")


@dataclass(frozen=True)
class Item:
    id: str
    seed: int
    rule: str
    rule_pos: int  # 0, 1 or 2: where the target rule sits in the list of three
    other_rules: tuple[str, str]
    services: tuple[str, ...]  # five, in SLOTS order
    landmarks: tuple[tuple[str, str], ...]  # five, aligned with services
    target_slot: int  # index into SLOTS
    runbooks: tuple[str, ...]  # five rendered runbook bodies, aligned with services

    @property
    def target(self) -> str:
        return self.services[self.target_slot]

    @property
    def country(self) -> str:
        return self.landmarks[self.target_slot][1]

    @property
    def depth(self) -> float:
        return SLOTS[self.target_slot]

    @property
    def expected(self) -> str:
        return RULES[self.rule][1](self.target)


def _seed(text: str) -> int:
    return int(hashlib.sha256(text.encode()).hexdigest()[:12], 16)


def make_item(item_id: str, rule: str, depth: float, rule_pos: int) -> Item:
    rng = random.Random(_seed(item_id))
    services = tuple(rng.sample(SERVICES, len(SLOTS)))
    landmarks = tuple(rng.sample(LANDMARKS, len(SLOTS)))  # distinct countries by construction
    others = tuple(rng.sample(OTHER_RULES, 2))
    runbooks = []
    for service, (landmark, _country) in zip(services, landmarks, strict=True):
        runbooks.append(
            f"# {service}\n\n"
            f"Owner: {rng.choice(TEAMS)} team. Pager rotation: {rng.choice(('primary', 'secondary', 'follow-the-sun'))}.\n"
            f"What it does: {rng.choice(PURPOSES)}.\n"
            f"Where it lives: {rng.choice(WHERE).format(landmark=landmark)}\n"
            f"Deploys go out from main on {rng.choice(DAYS)}; roll back by redeploying the previous image tag.\n"
        )
    return Item(
        id=item_id, seed=_seed(item_id), rule=rule, rule_pos=rule_pos,
        other_rules=(others[0], others[1]), services=services, landmarks=landmarks,
        target_slot=SLOTS.index(depth), runbooks=tuple(runbooks),
    )


def items(prefix: str, n: int) -> list[Item]:
    """n items, balanced: the 18 cells rule x target depth in turn, the rule's list position cycling.

    Everything else (names, landmarks, which ordinary rules, which filler) is drawn per item from a
    seed derived from the item id, so the pilot's items (prefix "P") and the main run's ("M") are
    different items.
    """
    cells = list(product(RULE_IDS, TARGET_DEPTHS))
    out: list[Item] = []
    for i in range(n):
        rep, cell = divmod(i, len(cells))
        rule, depth = cells[cell]
        out.append(make_item(f"{prefix}{i:03d}", rule, depth, (rep + cell) % 3))
    return out


# --------------------------------------------------------------------------------------------------
# Rendering


def tool_schemas() -> list[dict[str, Any]]:
    return [
        ReadFileTool(REPO).to_openai_schema(),
        GrepTool(REPO).to_openai_schema(),
        ListDirTool(REPO).to_openai_schema(),
        GlobTool(REPO).to_openai_schema(),
    ]


def corpus() -> list[str]:
    """The filler's source files: this repository's product code, as posix paths, sorted."""
    root = REPO / "chimera"
    files = [p for p in root.rglob("*.py") if p.stat().st_size > 1_500 and "__pycache__" not in p.parts]
    return sorted(p.relative_to(REPO).as_posix() for p in files)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def corpus_collisions() -> list[str]:
    """Any service name, formatted variant or landmark that the filler could contain. Must be empty."""
    hits: list[str] = []
    probes: list[str] = []
    for service in SERVICES:
        probes += [service, service.replace("-", "_"), RULES["camel"][1](service), service.replace("-", ".")]
    probes += [landmark.removeprefix("the ") for landmark, _ in LANDMARKS]
    lowered = [p.lower() for p in probes]
    for rel in corpus():
        text = (REPO / rel).read_text(encoding="utf-8", errors="replace").lower()
        for probe, low in zip(probes, lowered, strict=True):
            if low in text:
                hits.append(f"{rel}: {probe}")
    return hits


def _call_id(item: Item, index: int) -> str:
    return "call_" + hashlib.sha256(f"{item.id}:{index}".encode()).hexdigest()[:22]


def _tool_turn(item: Item, index: int, name: str, args: dict[str, Any], observation: str, text: str = "") -> list[dict[str, Any]]:
    cid = _call_id(item, index)
    return [
        {"role": "assistant", "content": text,
         "tool_calls": [{"id": cid, "type": "function",
                         "function": {"name": name, "arguments": json.dumps(args)}}]},
        {"role": "tool", "tool_call_id": cid, "content": observation},
    ]


_DEF = re.compile(r"^\s*(?:def|class)\s+([A-Za-z_]\w{3,})", re.M)


def _unit(rng: random.Random, rel: str, size: int) -> tuple[str, dict[str, Any], str]:
    """One filler tool call on a real file, sized to roughly ``size`` characters of output."""
    reader, grepper = ReadFileTool(REPO), GrepTool(REPO)
    text = (REPO / rel).read_text(encoding="utf-8", errors="replace")
    names = _DEF.findall(text)
    if names and rng.random() < 0.25:
        name = rng.choice(names)
        args: dict[str, Any] = {"pattern": rf"\b{name}\b", "path": rel.rsplit("/", 1)[0]}
        out = grepper.run(**args)
        if len(out) > size:
            lines = out.splitlines()
            keep, total = 0, 0
            for line in lines:
                if total + len(line) + 1 > size and keep > 0:
                    break
                total += len(line) + 1
                keep += 1
            args["max_results"] = max(1, keep)
            out = grepper.run(**args)
        return "grep", args, out
    lines = text.splitlines(keepends=True)
    if len(text) <= min(20_000, int(size * 1.4)) and len(text) >= int(size * 0.6):
        args = {"path": rel}
        return "read_file", args, reader.run(**args)
    # A window of the file sized to the unit, starting where a model reading on would start.
    need, count = 0, 0
    start = 1 if rng.random() < 0.5 else rng.randint(1, max(1, len(lines) // 2))
    for line in lines[start - 1:]:
        if need + len(line) > size and count > 0:
            break
        need += len(line)
        count += 1
    args = {"path": rel, "start_line": start, "max_lines": max(1, count)}
    return "read_file", args, reader.run(**args)


def _chars(messages: list[dict[str, Any]]) -> int:
    return len(json.dumps(messages, ensure_ascii=False))


def render(item: Item, target_tokens: int, chars_per_token: float = DEFAULT_CHARS_PER_TOKEN) -> dict[str, Any]:
    """The full request for one item at one length: messages, tools, and where things landed."""
    rules = list(item.other_rules)
    rules.insert(item.rule_pos, RULE_LEAD + RULES[item.rule][0])
    head = [
        {"role": "system", "content": DEFAULT_SYSTEM_PROMPT},
        {"role": "user", "content": TASK.format(r1=rules[0], r2=rules[1], r3=rules[2])},
    ]
    notes = []
    for k, (service, body) in enumerate(zip(item.services, item.runbooks, strict=True)):
        notes.append(_tool_turn(item, 10_000 + k, "read_file", {"path": f"ops/runbooks/{service}.md"}, body))
    tail = [
        {"role": "assistant", "content": PROGRESS},
        {"role": "user", "content": QUESTION.format(country=item.country)},
    ]
    tools = tool_schemas()
    fixed = _chars(head) + sum(_chars(n) for n in notes) + _chars(tail) + len(json.dumps(tools))
    budget = max(2_000, int(target_tokens * chars_per_token) - fixed)

    # The filler: real tool calls on real files, in an order drawn from the item's seed, so the
    # same item at every length reads the same files in the same order, just further.
    rng = random.Random(item.seed ^ 0x5EED)
    files = corpus()
    rng.shuffle(files)
    units_wanted = max(MIN_UNITS, round(budget / UNIT_CHARS))
    mean = budget / units_wanted
    filler: list[list[dict[str, Any]]] = []
    used, index = 0, 0
    while used < budget:
        rel = files[index % len(files)]
        size = int(min(mean * rng.uniform(0.6, 1.4), budget - used))
        if size < 300:
            break
        name, args, observation = _unit(rng, rel, size)
        turn = _tool_turn(item, index, name, args, observation)
        filler.append(turn)
        used += _chars(turn)
        index += 1

    # Insert the runbooks at their depths, measured in filler characters.
    sizes = [_chars(t) for t in filler]
    total = sum(sizes)
    positions: list[int] = []
    for depth in SLOTS:
        cumulative, at = 0, len(filler)
        for k, size in enumerate(sizes):
            if cumulative >= depth * total:
                at = k
                break
            cumulative += size
        positions.append(at)
    body: list[dict[str, Any]] = []
    for k, turn in enumerate(filler):
        for slot, at in enumerate(positions):
            if at == k:
                body += notes[slot]
        body += turn
    for slot, at in enumerate(positions):
        if at == len(filler):
            body += notes[slot]
    messages = head + body + tail
    digest = hashlib.sha256(json.dumps([messages, tools], ensure_ascii=False).encode()).hexdigest()[:16]
    return {
        "messages": messages, "tools": tools, "est_chars": _chars(messages) + len(json.dumps(tools)),
        "filler_units": len(filler), "positions": positions, "sha": digest,
    }


# --------------------------------------------------------------------------------------------------
# Grading


def _exact(expected: str, text: str) -> bool:
    return re.search(r"(?<![A-Za-z0-9_\-])" + re.escape(expected) + r"(?![A-Za-z0-9_\-])", text) is not None


def grade(item: Item, content: str, tool_calls: int, finish_reason: str) -> dict[str, Any]:
    """Deterministic. ``ok`` is the primary outcome; ``kind`` says what went wrong when it is not.

    Format breakage (the model did not give an answer):  tool_call, truncated, empty, no_service.
    Forgetting (it answered, wrongly):                   rule_forgotten, wrong_service, several.
    """
    text = (content or "").strip()
    named = [s for s in item.services if _norm(s) in _norm(text)]
    out: dict[str, Any] = {"ok": False, "fact_ok": None, "rule_ok": None, "named": named, "kind": ""}
    if tool_calls:
        out["kind"] = "tool_call"
        return out
    if finish_reason == "length":
        out["kind"] = "truncated"
        return out
    if not text:
        out["kind"] = "empty"
        return out
    if not named:
        out["kind"] = "no_service"
        return out
    if len(named) > 1:
        out["kind"] = "several"
        out["fact_ok"] = False
        return out
    service = named[0]
    out["fact_ok"] = service == item.target
    out["rule_ok"] = _exact(RULES[item.rule][1](service), text)
    if out["fact_ok"] and out["rule_ok"]:
        out["ok"] = True
        out["kind"] = "ok"
    elif out["fact_ok"]:
        out["kind"] = "rule_forgotten"
    else:
        out["kind"] = "wrong_service"
    return out


BREAKAGE = ("tool_call", "truncated", "empty", "no_service")
FORGETTING = ("rule_forgotten", "wrong_service", "several")


def selftest() -> list[str]:
    """The grader, fed answers of every shape it must tell apart. Returns failures (empty = pass)."""
    failures: list[str] = []
    for rule in RULE_IDS:
        item = make_item(f"T-{rule}", rule, 0.5, 0)
        fmt = RULES[rule][1]
        other = item.services[0] if item.target_slot != 0 else item.services[1]
        cases = [
            (item.expected, 0, "stop", "ok"),
            (f"`{item.expected}`", 0, "stop", "ok"),
            (f"{item.expected}.", 0, "stop", "ok"),
            (f"The service is {item.expected}.", 0, "stop", "ok"),
            (item.target, 0, "stop", "rule_forgotten"),
            (fmt(other), 0, "stop", "wrong_service"),
            (f"{item.expected} or {fmt(other)}", 0, "stop", "several"),
            ("", 0, "stop", "empty"),
            ("I cannot tell from what I have read.", 0, "stop", "no_service"),
            (item.expected, 1, "tool_calls", "tool_call"),
            (item.expected[:3], 0, "length", "truncated"),
        ]
        if rule != "snake":
            cases.append((item.target.replace("-", "_").upper(), 0, "stop", "rule_forgotten"))
        if rule not in ("prefix", "suffix", "bracket"):
            # A lookalike: the target's letters in the wrong format must not pass.
            cases.append((f"svc:{item.target}", 0, "stop", "rule_forgotten"))
        for answer, calls, finish, want in cases:
            got = grade(item, answer, calls, finish)["kind"]
            if got != want:
                failures.append(f"{rule}: {answer!r} -> {got}, expected {want}")
    # Names must not contain one another once normalised, or `named` could double count.
    norms = [_norm(s) for s in SERVICES]
    for a in norms:
        for b in norms:
            if a != b and a in b:
                failures.append(f"name {a} is inside {b}")
    if len({c for _, c in LANDMARKS}) != len(LANDMARKS):
        failures.append("two landmarks share a country")
    return failures
