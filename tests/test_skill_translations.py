"""The sidecar is checked here, because nothing else checks it and it went stale twice.

`skills/i18n/<locale>.json` carries two things the site shows in the reader's language: the card
`description`, and the five card `sections`. Neither is the payload — `SKILL.md` stays byte-identical
so the published SHA-256 keeps attesting to what the agent actually reads — and each entry declares
the `source_sha256` of the English it was made from, so a translation of a sentence that no longer
exists falls back to English instead of being shown as current.

That mechanism works. What did not exist is anything that notices when it is *incomplete*. Thirteen
cards shipped with no translation in any language and nobody was told; `ru.json` did not exist at
all while the site served ten locales, so a Russian reader had been getting English since the locale
was added. Both were found by reading files, not by a failing build.

**Three states, and conflating them would either lie or kill the contribution surface.**

A STALE entry is a lie: the sidecar claims to translate text that has changed. The site already
falls back, so nothing renders wrong — but the file now contains a translation nobody will ever see,
and the next person to read it cannot tell that from a live one. Always fails.

An INCOMPLETE section block is the same lie in a different shape. The site demands all five or none,
so four sections plus a gap renders as English with four orphan translations sitting in the file.
Always fails.

A MISSING entry is honest debt. The reader gets English, the page says so, and nothing is wrong
except that the work has not happened. `skills/README.md` calls this library "the one place you can
contribute something useful without touching a line of Python" — a gate that demanded nine
translations before a card could merge would end that. So missing is counted and capped, the way
`i18n-pending.json` caps the site's UI debt: the cap may fall, and raising it is an edit somebody
has to justify in a diff.

Cards we publish ourselves (`chimera-*`) are held to a stricter rule: we wrote them, we translate
them. The cap exists for what other people contribute.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest
import yaml

LIBRARY = Path(__file__).resolve().parent.parent / "skills"
I18N = LIBRARY / "i18n"
CARDS = sorted(LIBRARY.glob("*/SKILL.md"))
LOCALES = sorted(p.stem for p in I18N.glob("*.json"))

#: Mirrors `_CARD_SECTIONS` in the product and `SECTIONS` in the site.
SECTIONS = ("Trigger", "Do", "Avoid", "Check", "Risk")

#: The ceiling on missing card-locale blocks, counting descriptions and sections separately.
#: A ratchet: lower it as translations land. Raising it is a decision somebody makes in a diff,
#: which is the only reason a number in a file beats a good intention.
MAX_MISSING = 0


def _front_and_body(card: Path) -> tuple[str, str]:
    raw = card.read_text(encoding="utf-8")
    stripped = raw.lstrip()
    end = stripped.find("\n---", 3)
    if not stripped.startswith("---") or end == -1:
        return "", raw
    return stripped[3:end], stripped[end + 4 :]


def _description(card: Path) -> str:
    front, _ = _front_and_body(card)
    match = re.search(r"^description: (.+)$", front, re.M)
    return match.group(1).strip() if match else ""


def _triggers(card: Path) -> list[str]:
    """The `triggers:` list from the frontmatter, in order.

    Parsed with YAML rather than by hand. The first draft of this read the block with a regex and
    agreed with YAML on all 23 cards — which is the argument against it, not for it: it bought
    nothing (`yaml` is already a dependency, `chimera/skills/skill_md.py` imports it) and it added a
    way for the check to disagree with the site over a quoted string or a folded line. A hash whose
    two sides parse differently fails silently, and this file exists to stop exactly that.
    """
    front, _ = _front_and_body(card)
    loaded = yaml.safe_load(front) or {}
    triggers = loaded.get("triggers") if isinstance(loaded, dict) else None
    return [str(t) for t in triggers] if isinstance(triggers, list) else []


def _sections(card: Path) -> dict[str, str]:
    """A port of `splitSections` in the site's `src/content/skills.ts`.

    Ported rather than approximated: the hash below is computed from this result, and a parser that
    differs by one character makes every translation fall back to English with nothing to show for
    it. Validated against an entry the site already accepts.
    """
    _, body = _front_and_body(card)
    out: dict[str, str] = {}
    for section in SECTIONS:
        marker = f"## {section}"
        start = body.find(marker)
        if start == -1:
            out[section] = ""
            continue
        after = body[start + len(marker) :]
        nexts = sorted(i for i in (after.find(f"## {s}") for s in SECTIONS) if i > 0)
        out[section] = after[: nexts[0]].strip() if nexts else after.strip()
    return out


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _body_hash(card: Path) -> str:
    """The hash the site checks section translations against: all five, joined, in order.

    One hash for the whole body rather than five, so a change to any section retires all five
    together — a page that mixed a current Trigger with a stale Avoid would give the reader no way
    to tell which was which.
    """
    parts = _sections(card)
    return _sha("\n\n".join(parts[s] for s in SECTIONS))


@pytest.fixture(scope="module")
def sidecars() -> dict[str, dict]:
    return {loc: json.loads((I18N / f"{loc}.json").read_text(encoding="utf-8")) for loc in LOCALES}


# --- the file itself --------------------------------------------------------------------------


def test_there_is_a_sidecar_for_every_locale_the_site_serves() -> None:
    """`ru.json` was absent while the site served Russian, and the only symptom was English on the
    page. The locale list lives in the site, so this asserts the count the product knows about and
    names the real fix in the message rather than pretending to read the other repo."""
    assert LOCALES, "no sidecars at all"
    assert len(LOCALES) >= 9, (
        f"only {len(LOCALES)} sidecars ({LOCALES}) — the site serves ten locales including English, "
        "so a missing file here is a locale silently reading English"
    )


@pytest.mark.parametrize("locale", LOCALES)
def test_a_sidecar_is_readable_json_with_the_two_blocks(locale: str, sidecars: dict) -> None:
    data = sidecars[locale]
    assert isinstance(data.get("descriptions"), dict), f"{locale}: no descriptions block"
    assert isinstance(data.get("sections", {}), dict), f"{locale}: sections is not an object"


# --- a translation must not claim to be current when it is not ---------------------------------


def test_no_description_translation_is_stale(sidecars: dict) -> None:
    """Stale means the English moved and the sidecar still points at the old text. The site falls
    back, so nothing renders wrong — but the file now holds a translation nobody will ever see, and
    the next reader cannot tell it from a live one."""
    stale = []
    for locale, data in sidecars.items():
        for name, entry in data.get("descriptions", {}).items():
            card = LIBRARY / name / "SKILL.md"
            if not card.exists():
                stale.append(f"{locale}:{name} (card no longer exists)")
            elif entry.get("source_sha256") != _sha(_description(card)):
                stale.append(f"{locale}:{name}")

    assert not stale, (
        "these description translations claim a source that has changed — retranslate them or "
        f"delete the entry, but do not leave a hash that will never match again: {stale}"
    )


def test_no_section_translation_is_stale(sidecars: dict) -> None:
    stale = []
    for locale, data in sidecars.items():
        for name, entry in data.get("sections", {}).items():
            card = LIBRARY / name / "SKILL.md"
            if not card.exists():
                stale.append(f"{locale}:{name} (card no longer exists)")
            elif entry.get("source_sha256") != _body_hash(card):
                stale.append(f"{locale}:{name}")

    assert not stale, f"these section translations point at a body that has changed: {stale}"


def test_no_trigger_translation_is_stale_or_the_wrong_length(sidecars: dict) -> None:
    """Two failures in one check, because they produce the same wrong page.

    The chips are hashed as one list, so a card that gains a trigger retires the whole set rather
    than showing four translated chips beside one English one with nothing to tell them apart. And a
    list of the wrong length cannot be zipped back onto the English — rendering the short one would
    silently drop a trigger the card declares.
    """
    broken = []
    for locale, data in sidecars.items():
        for name, entry in data.get("triggers", {}).items():
            card = LIBRARY / name / "SKILL.md"
            if not card.exists():
                broken.append(f"{locale}:{name} (card no longer exists)")
                continue
            english = _triggers(card)
            text = entry.get("text")
            if not isinstance(text, list) or len(text) != len(english):
                broken.append(f"{locale}:{name} ({len(text or [])} chips for {len(english)})")
            elif entry.get("source_sha256") != _sha("\n".join(english)):
                broken.append(f"{locale}:{name} (stale)")
            elif any(not str(t).strip() for t in text):
                broken.append(f"{locale}:{name} (blank chip)")

    assert not broken, f"trigger translations the site will discard: {broken}"


def test_a_section_block_is_all_five_or_none(sidecars: dict) -> None:
    """The site rejects the whole block when one section is missing, so four translations plus a
    gap renders as English with four orphans sitting in the file — work done and invisible."""
    broken = []
    for locale, data in sidecars.items():
        for name, entry in data.get("sections", {}).items():
            missing = [s for s in SECTIONS if not str(entry.get(s, "")).strip()]
            if missing:
                broken.append(f"{locale}:{name} missing {missing}")

    assert not broken, f"incomplete section blocks (the site shows English for these): {broken}"


def test_no_translation_is_just_the_english(sidecars: dict) -> None:
    """A copied English string passes every hash check and reads, to a reader of that language, as
    a translation that was never made."""
    copied = []
    for locale, data in sidecars.items():
        if locale == "en":
            continue
        for name, entry in data.get("descriptions", {}).items():
            card = LIBRARY / name / "SKILL.md"
            if card.exists() and entry.get("text", "").strip() == _description(card):
                copied.append(f"{locale}:{name} (description)")
        for name, entry in data.get("sections", {}).items():
            card = LIBRARY / name / "SKILL.md"
            if not card.exists():
                continue
            english = _sections(card)
            for section in SECTIONS:
                if (
                    str(entry.get(section, "")).strip() == english[section].strip()
                    and english[section].strip()
                ):
                    copied.append(f"{locale}:{name}/{section}")

    assert not copied, f"these are the English text, not a translation: {copied[:20]}"


# --- missing is debt, counted and capped -------------------------------------------------------


def _missing() -> list[str]:
    gaps: list[str] = []
    for locale in LOCALES:
        data = json.loads((I18N / f"{locale}.json").read_text(encoding="utf-8"))
        blocks = {
            "description": data.get("descriptions", {}),
            "sections": data.get("sections", {}),
            "triggers": data.get("triggers", {}),
        }
        for card in CARDS:
            name = card.parent.name
            for block, entries in blocks.items():
                # A card with no triggers has nothing to translate; counting it as debt would put a
                # number in the ledger that no amount of work could ever bring down.
                if block == "triggers" and not _triggers(card):
                    continue
                if name not in entries:
                    gaps.append(f"{locale}:{name}:{block}")
    return sorted(gaps)


def test_the_cards_we_publish_ourselves_are_fully_translated() -> None:
    """Official cards carry the `chimera-` prefix. We wrote them, so we translate them — the cap
    below exists for what other people contribute, not as a place for our own work to accumulate.
    """
    ours = {f"{c.parent.name}" for c in CARDS if c.parent.name.startswith("chimera-")}
    gaps = [g for g in _missing() if g.split(":")[1] in ours]

    assert not gaps, f"{len(gaps)} missing translations for cards we publish: {gaps[:12]}" + (
        "" if len(gaps) <= 12 else f" … and {len(gaps) - 12} more"
    )


def test_missing_translations_stay_under_the_cap() -> None:
    """A ratchet, not a wall. `skills/README.md` calls this "the one place you can contribute
    something useful without touching a line of Python" — a gate demanding nine translations before
    a card could merge would end that. So the debt is a number that may fall and that somebody has
    to justify raising."""
    gaps = _missing()

    assert len(gaps) <= MAX_MISSING, (
        f"{len(gaps)} missing card-locale translations, cap is {MAX_MISSING}. Translate them, or "
        f"raise MAX_MISSING in this file and say why in the commit. First few: {gaps[:8]}"
    )


def test_the_cap_is_not_slack_nobody_is_using() -> None:
    """A cap far above the real debt stops being a ratchet and becomes decoration — it would let a
    whole locale go missing without a word. Kept within a card's worth of the truth."""
    gaps = len(_missing())

    assert MAX_MISSING - gaps <= 2 * len(LOCALES), (
        f"cap {MAX_MISSING} is {MAX_MISSING - gaps} above the actual debt of {gaps} — lower it"
    )


# --- proof the checks discriminate --------------------------------------------------------------


def test_the_staleness_check_would_catch_a_drifted_source(tmp_path: Path) -> None:
    """Without this, a bug in the hash port makes every check above pass forever and the guarantee
    quietly stops existing. Runs against a fabricated card so it asserts the arithmetic, not the
    current contents of the library."""
    card = tmp_path / "demo" / "SKILL.md"
    card.parent.mkdir()
    card.write_text(
        "---\nname: demo\ndescription: the original sentence\n---\n\n"
        "## Trigger\na\n\n## Do\nb\n\n## Avoid\nc\n\n## Check\nd\n\n## Risk\ne\n",
        encoding="utf-8",
    )
    before_desc, before_body = _sha(_description(card)), _body_hash(card)

    card.write_text(
        card.read_text(encoding="utf-8").replace("the original sentence", "reworded"),
        encoding="utf-8",
    )
    assert _sha(_description(card)) != before_desc, "a changed description did not change its hash"

    card.write_text(
        card.read_text(encoding="utf-8").replace("## Risk\ne", "## Risk\ne, plus more"),
        encoding="utf-8",
    )
    assert _body_hash(card) != before_body, "a changed section did not change the body hash"


def test_the_body_hash_matches_what_the_site_computes() -> None:
    """The port is only worth anything if it agrees with the consumer. Any card with a live section
    translation is a fixture the site itself has already accepted."""
    checked = 0
    for locale in LOCALES:
        data = json.loads((I18N / f"{locale}.json").read_text(encoding="utf-8"))
        for name, entry in data.get("sections", {}).items():
            card = LIBRARY / name / "SKILL.md"
            if card.exists() and entry.get("source_sha256") == _body_hash(card):
                checked += 1

    assert checked, (
        "no section translation agrees with the ported hash — either the sidecar is empty or the "
        "port has drifted from `splitSections` in the site's skills.ts"
    )
