"""The 24 tasks, their checkers, and a scripted solution for each. See PREREGISTRATION.md.

A task is a start page, a goal in plain words, a deterministic checker, and where its target sits
against the first viewport (``in_view`` or ``below``) — a label the dry-run MEASURES on the real
browser rather than trusts. ``hurt_prone`` is registered here, before any solve: the target is below
the fold AND the goal gives no name that ``find`` could match directly (a position in a list, an
element among identically named ones, a form field known only by its placeholder). Those are where a
viewport-first listing could plausibly make things worse.

Checkers (no model, no judgement):
* ``code`` — the answer's set of codes (``AB1234C``) must be exactly {expected}: the right code
  alone passes; the right code next to a wrong one fails, so listing every code seen does not pay.
* ``number`` — the set of numbers in the answer must be exactly {expected}.
* ``phrase`` — the expected phrase appears (case- and space-insensitive) and no decoy phrase does.
* ``substring`` — the expected lowercase substring appears and no decoy substring does.

The scripted solutions drive the product's ``BrowserTool`` — navigate, find, scroll, click, type,
read — in BOTH arms. They are the reachability proof: a target the script reaches in the
viewport-first arm is reachable by a model through the same tool. They are not a model of how a
model behaves.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from bench.browser_viewport_tasks.build_site import pkg_hash, shop_catalog
from bench.browser_viewport_tasks.common import CODE_RE, code_for, go_key

PAGES = {
    "wiki": "/wiki/tidal-power.html",
    "docs": "/docs/widgets-core.html",
    "news": "/news/index.html",
    "pkg": "/pkg/acme-widgets.html",
    "forum": "/forum/thread-4412.html",
    "shop": "/shop/lamps.html",
    "repo": "/repo/acme-widgets.html",
    "gov": "/gov/services.html",
}
EMAIL, SUBJECT = "maria.silva@example.org", "Address change"


@dataclass(frozen=True)
class Check:
    kind: str  # code | number | phrase | substring
    expected: str
    decoys: tuple[str, ...] = ()

    def passes(self, answer: str) -> bool:
        text = answer or ""
        if self.kind == "code":
            return set(CODE_RE.findall(text.upper())) == {self.expected}
        if self.kind == "number":
            return set(re.findall(r"\d+(?:\.\d+)?", text)) == {self.expected}
        if self.kind == "phrase":
            norm = _norm(text)
            return _norm(self.expected) in norm and not any(_norm(d) in norm for d in self.decoys)
        if self.kind == "substring":
            low = text.lower()
            return self.expected in low and not any(d in low for d in self.decoys)
        raise ValueError(f"unknown checker kind {self.kind!r}")


def _norm(text: str) -> str:
    return " " + " ".join(re.findall(r"[a-z0-9]+", text.lower())) + " "


@dataclass(frozen=True)
class Task:
    id: str
    site: str
    stratum: str  # in_view | below
    hurt_prone: bool
    goal: str  # "{url}" is the start page
    check: Check
    wrong: tuple[str, ...]  # answers the checker must reject (dry-run)
    target: tuple[str, int]  # (element name, occurrence; -1 = last) whose place the dry-run measures
    solve: Callable[[Script], str]

    def prompt(self, origin: str) -> str:
        return (f"{self.goal.format(url=origin + PAGES[self.site])}\n"
                "Use the browser tool. Reply with the answer only.")

    def right(self) -> tuple[str, ...]:
        """Answers the checker must accept (dry-run)."""
        e = self.check.expected
        return (e, f"The answer is: {e}.", e.lower() if self.check.kind == "code" else e)


def code_check(site: str, slug: str, decoy_slug: str) -> tuple[Check, tuple[str, ...]]:
    expected, decoy = code_for(go_key(site, slug)), code_for(go_key(site, decoy_slug))
    return Check("code", expected), (decoy, f"{expected} or {decoy}", "I could not find it.")


# --- the scripted driver -------------------------------------------------------------------------

LINE = re.compile(r"^\[(e(\d+))\] ([^:]+): (.*)$")
PLACE = re.compile(r" \((?:in view|below|above|beside)\)$")


class Script:
    """Drives the tool the way a careful agent could: by name, through `find` when the listing does
    not show the element, and by scrolling when only position identifies it."""

    def __init__(self, tool: Any, arm: str, origin: str, driver: Any) -> None:
        self.tool, self.arm, self.origin, self.driver = tool, arm, origin, driver
        self.listing = ""

    def call(self, **kwargs: Any) -> str:
        out = str(self.tool.run(**kwargs))
        if out.startswith("error:"):
            raise RuntimeError(f"{kwargs}: {out}")
        return out

    @staticmethod
    def parse(out: str) -> list[tuple[str, int, str, str]]:
        rows = []
        for line in out.splitlines():
            m = LINE.match(line.strip())
            if m:
                rows.append((m[1], int(m[2]), m[3], PLACE.sub("", m[4])))
        return rows

    def open(self, site: str) -> None:
        self.listing = self.call(action="navigate", url=self.origin + PAGES[site])

    def resolve(self, name: str, occurrence: int = 0) -> str:
        """The ref of the `occurrence`-th element named exactly `name`, in document order. Today the
        listing is the whole page; viewport-first, a first occurrence already in view is taken from
        the listing, and anything else is asked of `find`, which answers for the whole page."""
        listed = [r for r in self.parse(self.listing) if r[3] == name]
        if self.arm == "today" or (occurrence == 0 and listed):
            pool = listed
        else:
            found = self.call(action="find", query=name)
            pool = [r for r in self.parse(found.split("element(s) named like", 1)[-1]) if r[3] == name]
        if not pool:
            raise LookupError(f"no element named {name!r}")
        return pool[occurrence][0]

    def click(self, name: str, occurrence: int = 0) -> None:
        self.listing = self.call(action="click", ref=self.resolve(name, occurrence))

    def click_ref(self, ref: str) -> None:
        self.listing = self.call(action="click", ref=ref)

    def type(self, name: str, text: str) -> None:
        self.listing = self.call(action="type", ref=self.resolve(name), text=text)

    def after(self, anchor: str, occurrence: int, want: Callable[[str, str], bool], nth: int = 1) -> str:
        """The `nth` element after the anchor (in document order) for which `want(role, name)` holds.
        Refs are stamped in document order, so "after" is a larger ref number; in the viewport arm the
        page is scrolled until enough of what follows the anchor has been listed."""
        anchor_n = int(self.resolve(anchor, occurrence)[1:])
        seen: dict[int, tuple[str, str, str]] = {}
        for _ in range(150):
            for ref, n, role, name in self.parse(self.listing):
                seen[n] = (ref, role, name)
            # Only the unbroken run of refs right after the anchor counts: an element listed because
            # it sits in view elsewhere (a right-hand column late in the document) is "after" by
            # number and unrelated by position. Refs are consecutive, so a gap means "not seen yet".
            hits, n = [], anchor_n + 1
            while n in seen and len(hits) < nth:
                if want(seen[n][1], seen[n][2]):
                    hits.append(seen[n][0])
                n += 1
            if len(hits) >= nth:
                return hits[nth - 1]
            if self.arm == "today":
                break
            before = set(seen)
            self.listing = self.call(action="scroll", direction="down")
            if {n for _, n, _, _ in self.parse(self.listing)} <= before:
                break  # the bottom: nothing new came into view
        raise LookupError(f"fewer than {nth} matches after {anchor!r}")

    def find_lines(self, query: str) -> list[str]:
        out = self.call(action="find", query=query)
        text = out.split("element(s) named like", 1)[0]
        return [ln.strip() for ln in text.splitlines()[2:] if ln.strip() and query.lower() in ln.lower()]

    def code(self) -> str:
        m = CODE_RE.search(self.call(action="read_text"))
        if not m:
            raise LookupError("no code on the page")
        return m.group(0)


def _brass_results() -> list[dict[str, Any]]:
    return [p for p in shop_catalog() if "brass" in str(p["name"]).lower()]


def _solve_s1(s: Script) -> str:
    s.open("shop")
    s.type("Search the shop", "brass")
    s.click("Search")
    lines = [ln.strip() for ln in s.driver.page_text().splitlines() if ln.strip()]
    priced = [(float(lines[i + 1][1:]), lines[i]) for i in range(len(lines) - 1)
              if re.fullmatch(r"\$\d+\.\d\d", lines[i + 1])]
    return min(priced)[1]


def _solve_g2(s: Script) -> str:
    s.open("gov")
    s.type("Your name", "Maria Silva")
    s.type("Your email address", EMAIL)
    s.type("Subject", SUBJECT)
    s.click("Send message")
    return CODE_RE.findall(" ".join(s.find_lines("Protocol number")))[0]


def _solve_w3(s: Script) -> str:
    s.open("wiki")
    third = s.find_lines("ISBN")[2]
    title = re.search(r"\(\d{4}\)\. (.+?)\. [A-Z][a-z]+: ", third)
    assert title, third
    s.click(title.group(1))
    return s.code()


def _solve_p3(s: Script) -> str:
    s.open("pkg")
    wheel = s.find_lines("SHA256")[1]  # the sdist is listed first, the wheel second
    m = re.search(r"[0-9a-f]{64}", wheel)
    assert m, wheel
    return m.group(0)[:12]


def _solve_d2(s: Script) -> str:
    s.open("docs")
    line = s.find_lines("Widget.fetch() waits")[0]
    m = re.search(r"Default: ([\d.]+ seconds)", line)
    assert m, line
    return m.group(1)


def _link(site: str, name: str, occurrence: int = 0) -> Callable[[Script], str]:
    def solve(s: Script) -> str:
        s.open(site)
        s.click(name, occurrence)
        return s.code()
    return solve


def _after(site: str, anchor: str, occurrence: int, want: Callable[[str, str], bool], nth: int,
           then: Callable[[Script], str]) -> Callable[[Script], str]:
    def solve(s: Script) -> str:
        s.open(site)
        s.click_ref(s.after(anchor, occurrence, want, nth))
        return then(s)
    return solve


def _cart_code(s: Script) -> str:
    return CODE_RE.findall(" ".join(s.find_lines("Cart code")))[0]


def _build() -> list[Task]:
    tasks: list[Task] = []

    def add(tid: str, site: str, stratum: str, hurt: bool, goal: str, check: Check,
            wrong: tuple[str, ...], target: tuple[str, int], solve: Callable[[Script], str]) -> None:
        tasks.append(Task(tid, site, stratum, hurt, goal, check, wrong, target, solve))

    def link(tid: str, site: str, stratum: str, goal: str, name: str, slug: str, decoy: str,
             occurrence: int = 0) -> None:
        check, wrong = code_check(site, slug, decoy)
        add(tid, site, stratum, False, goal, check, wrong, (name, occurrence), _link(site, name, occurrence))

    wiki = "On the Openpedia article about tidal power ({url}), "
    link("W1", "wiki", "in_view", wiki + "open the link to the Rance Tidal Power Station in the article's "
         "opening paragraph and tell me the page code shown on the page it opens.",
         "Rance Tidal Power Station", "rance-tidal-power-station", "wind-power")
    link("W2", "wiki", "below", wiki + "open 'Dynamic tidal power' from the article's 'See also' list and "
         "tell me the page code shown there.", "Dynamic tidal power", "dynamic-tidal-power", "wave-power")
    check, wrong = code_check("wiki", "book-barrages-and-their-discontents", "book-power-from-the-estuary")
    add("W3", "wiki", "below", True, wiki + "open the third book listed under 'Further reading' and tell me "
        "the page code shown on its page.", check, wrong, ("Barrages and Their Discontents", 0), _solve_w3)

    docs = "On the Acme Widgets documentation page ({url}), "
    link("D1", "docs", "in_view", docs + "open 'Changelog' from the top navigation and tell me the page code "
         "shown there.", "Changelog", "changelog", "blog")
    add("D2", "docs", "below", False, docs + "what is the default timeout of Widget.fetch()? Give the value "
        "as the page writes it.", Check("number", "37.5"), ("30 seconds", "37.5 or 30 seconds", "12"),
        ("Permalink to Widget.fetch", 0), _solve_d2)
    link("D3", "docs", "below", docs + "open the module acme.widgets.sensors.thermal from the module list in "
         "the left sidebar and tell me the page code shown there.", "acme.widgets.sensors.thermal",
         "module-sensors-thermal", "module-sensors-types")

    news = "On The Harbor Ledger's front page ({url}), "
    link("N1", "news", "in_view", news + "open today's top story and tell me the page code shown on it.",
         "Harbor council approves tidal lagoon after decade-long fight", "top-story",
         "what-the-lagoon-will-cost-you")
    check, wrong = code_check("news", "story-10-2", "story-10-1")
    add("N2", "news", "below", True, news + "open the third headline in the Science section and tell me the "
        "page code shown on it.", check, wrong, ("Science", -1),
        _after("news", "Science", -1, lambda role, name: role == "link", 3, lambda s: s.code()))
    link("N3", "news", "below", news + "open the 'Corrections' page linked in the site footer and tell me the "
         "page code shown there.", "Corrections", "corrections", "complaints")

    pkg = "On the acme-widgets package page ({url}), "
    link("P1", "pkg", "in_view", pkg + "open the project's 'Issues' link under 'Project links' and tell me the "
         "page code shown there.", "Issues", "project-issues", "project-changelog")
    link("P2", "pkg", "below", pkg + "open the release history entry for version 3.8.2 and tell me the page "
         "code shown there.", "3.8.2", "release-3.8.2", "release-3.8.1")
    wheel = pkg_hash("wheel-sha256")[:12]
    decoys = (pkg_hash("sdist-sha256")[:12], pkg_hash("wheel-md5")[:12], pkg_hash("wheel-blake2b")[:12])
    add("P3", "pkg", "below", False, pkg + "what are the first 12 characters of the SHA256 hash listed for "
        "the wheel file (.whl) of version 4.2.0?", Check("substring", wheel, decoys),
        (decoys[0], f"{wheel} or {decoys[0]}", "I could not find it."),
        ("acme_widgets-4.2.0-py3-none-any.whl", 0), _solve_p3)

    forum = "On this DevAnswers thread ({url}), "
    link("F1", "forum", "in_view", forum + "open the profile of the user who asked the question and tell me "
         "the page code shown on it.", "halvard_n", "user-halvard_n", "user-ines_r")
    check, wrong = code_check("forum", "reply-c119", "reply-c118")
    add("F2", "forum", "below", True, forum + "open the 'reply' link of the comment written by marlow_k and "
        "tell me the page code shown on the page it opens.", check, wrong, ("marlow_k", 0),
        _after("forum", "marlow_k", 0, lambda role, name: name == "reply", 1, lambda s: s.code()))
    link("F3", "forum", "below", forum + "load the next page of comments with the 'More comments' link at the "
         "end of the thread and tell me the page code shown there.", "More comments",
         "thread-4412-page-2", "related-0")

    shop = "On the Lumen & Co. lamp shop ({url}), "
    brass = _brass_results()
    cheapest = min(brass, key=lambda p: float(p["price"]))
    others = tuple(str(p["name"]) for p in brass if p is not cheapest)
    add("S1", "shop", "in_view", False, shop + "search the shop for 'brass' using the search box and tell me "
        "the name of the cheapest product in the results.", Check("phrase", str(cheapest["name"]), others),
        (others[0], f"{cheapest['name']} or {others[0]}", "I could not find it."),
        ("Search the shop", 0), _solve_s1)
    link("S2", "shop", "below", shop + "go to page 3 of the lamp listing with the pagination at the bottom and "
         "tell me the page code shown there.", "Page 3", "lamps-page-3", "lamps-page-2")
    cart, near = code_for("cart|p37"), code_for("cart|p36")
    add("S3", "shop", "below", True, shop + "add the 'Harbor Brass Floor Lamp' to the cart and tell me the cart "
        "code the shop shows afterwards.", Check("code", cart), (near, f"{cart} or {near}", "Done."),
        ("Harbor Brass Floor Lamp", 0),
        _after("shop", "Harbor Brass Floor Lamp", 0, lambda role, name: name == "Add to cart", 1, _cart_code))

    repo = "On the CodeHub page of the acme-widgets repository ({url}), "
    link("R1", "repo", "in_view", repo + "open the repository's Issues tab and tell me the page code shown "
         "there.", "Issues 12", "tab-issues", "tab-pull")
    link("R2", "repo", "below", repo + "open the file CONTRIBUTING.md from the repository's file list and tell "
         "me the page code shown there.", "CONTRIBUTING.md", "file-contributing-md", "file-code-of-conduct-md")
    link("R3", "repo", "below", repo + "follow the link to the discussion forum in the README's Community "
         "section and tell me the page code shown there.", "discussion forum", "discussion-forum", "chat-room")

    gov = "On the gov.example services portal ({url}), "
    link("G1", "gov", "in_view", gov + "open the 'Renew your passport' service from the most searched services "
         "and tell me the page code shown there.", "Renew your passport", "renew-your-passport",
         "get-a-driving-licence")
    protocol = code_for(f"gov-contact|{EMAIL}|{SUBJECT.lower()}")
    near_protocol = code_for(f"gov-contact|{EMAIL}|address")
    add("G2", "gov", "below", True, gov + f"use the contact form at the bottom of the page to send a message "
        f"from the email address {EMAIL} with the subject '{SUBJECT}' (any name and message will do), and "
        "tell me the protocol number the page shows after sending.", Check("code", protocol),
        (near_protocol, f"{protocol} or {near_protocol}", "Sent."), ("Your email address", 0), _solve_g2)
    link("G3", "gov", "below", gov + "open the 'Ombudsman' link in the page footer and tell me the page code "
         "shown there.", "Ombudsman", "ombudsman", "transparency")
    return tasks


TASKS: list[Task] = _build()
BY_ID = {t.id: t for t in TASKS}
