"""Write the offline site the M7 task suite browses. See PREREGISTRATION.md.

    python -m bench.browser_viewport_tasks.build_site          # (re)writes pages/ and pages/MANIFEST.json

Eight pages, one per kind of page step 1 measured (a long encyclopedic article, library docs with a
big module sidebar, a news front page, a package index page, a forum thread, a shop listing, a code
repository page, a public-service portal). They are authored, not saved: no page from step 1 was
kept on disk, and a page written here has no licence to track and no live site to drift from. What
that costs is realism, and the PREREGISTRATION says so.

Deterministic: no clock, no randomness — every choice goes through `common.pick`, so the same
source writes the same bytes, and `MANIFEST.json` pins them (the dry-run refuses a site whose bytes
differ). Every link leads somewhere: `/go/<site>/<slug>` is answered by the server with a page that
shows that slug's own code, so a wrong click lands on a plausible page with a WRONG code rather than
on an error that would tell the model it was wrong.
"""

from __future__ import annotations

import hashlib
import html
import json
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bench.browser_viewport_tasks.common import FNV_JS, pick, slugify  # noqa: E402

SITE = Path(__file__).resolve().parent / "pages"

CSS = """
body{margin:0;font:16px/1.5 Arial,Helvetica,sans-serif;color:#202122;background:#fff}
a{color:#0645ad;text-decoration:none}
header.top{display:flex;gap:14px;align-items:center;padding:10px 16px;border-bottom:1px solid #ccc;flex-wrap:wrap;background:#f8f9fa}
header.sticky{position:sticky;top:0;z-index:5}
.wrap{display:flex;align-items:flex-start}
nav.side{width:220px;flex:none;padding:12px 16px;font-size:14px}
nav.side a{display:block;padding:1px 0}
nav.side h3{font-size:13px;margin:14px 0 4px;color:#54595d}
main{flex:1;padding:12px 24px;min-width:0}
aside.right{width:270px;flex:none;padding:12px 16px;font-size:14px}
aside.right a{display:block}
.infobox{float:right;width:280px;margin:0 0 12px 16px;border:1px solid #a2a9b1;background:#f8f9fa;font-size:14px;padding:8px}
.infobox a{display:block}
footer{border-top:1px solid #ccc;padding:16px;font-size:14px;clear:both}
footer a{display:inline-block;margin:2px 10px 2px 0}
ol.refs{font-size:13px;columns:2}
.navbox{border:1px solid #a2a9b1;margin:12px 0;padding:6px;font-size:13px}
.navbox a{margin-right:8px}
.card{border:1px solid #ddd;padding:12px;height:300px;box-sizing:border-box}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}
.row{display:flex;gap:16px;border-bottom:1px solid #eee;padding:8px 4px;height:24px;overflow:hidden}
.row .name{width:260px;flex:none}
.comment{border-top:1px solid #eee;padding:10px 0}
.meta{color:#54595d;font-size:13px}
pre{background:#f6f8fa;padding:10px;overflow:auto}
"""


def esc(text: str) -> str:
    return html.escape(text, quote=True)


def go(site: str, slug: str) -> str:
    return f"/go/{site}/{slug}"


def a(site: str, label: str, slug: str | None = None, **attrs: str) -> str:
    extra = "".join(f' {k.replace("_", "-")}="{esc(v)}"' for k, v in attrs.items())
    return f'<a href="{go(site, slug or slugify(label))}"{extra}>{esc(label)}</a>'


def page(title: str, body: str, *, script: str = "") -> str:
    js = f"<script>{FNV_JS}{script}</script>" if script else ""
    return (
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>{esc(title)}</title><style>{CSS}</style></head>\n<body>\n{body}\n{js}\n</body></html>\n"
    )


def rotate(items: list[str], key: str, count: int) -> list[str]:
    """`count` items from `items`, starting at a deterministic offset and wrapping."""
    start = pick(key, len(items))
    return [items[(start + i) % len(items)] for i in range(count)]


# --- 1. wiki: a long encyclopedic article (step 1's Wikipedia, 2,112 elements) --------------------

WIKI_ADJ = ["tidal", "marine", "coastal", "offshore", "hydraulic", "electrical", "ocean", "estuarine",
            "rotor", "sluice", "subsea", "harbour"]
WIKI_NOUN = ["barrage", "lagoon", "turbine", "generator", "current", "range", "stream", "basin", "gate",
             "fence", "engineering", "sediment", "ecology", "cable", "foundation", "array", "blade",
             "survey", "model", "channel"]
WIKI_TERMS = [f"{adj} {noun}" for adj in WIKI_ADJ for noun in WIKI_NOUN]  # 240 link terms
WIKI_SENTENCES = [
    "Early studies of the {0} treated it as a special case of the {1}.",
    "Engineers still disagree about how far the {0} limits the {1}.",
    "In most designs the {0} is sized before the {1} is chosen.",
    "A survey of existing sites found the {0} to be the main cost driver after the {1}.",
    "The {0} and the {1} are usually modelled together.",
    "Critics argue that the {0} was underestimated in favour of the {1}.",
    "Maintenance of the {0} is scheduled around the {1}.",
    "Later work separated the {0} from the {1} for the first time.",
]
WIKI_SECTIONS = ["History", "Physical principles", "Tidal range generation", "Tidal stream generation",
                 "Barrage design", "Lagoon design", "Turbine technology", "Grid integration",
                 "Economics", "Environmental effects", "Sediment and ecology", "Public opinion",
                 "Research programmes", "Outlook"]
WIKI_SEE_ALSO = ["Dynamic tidal power", "Ocean thermal energy conversion", "Wave power",
                 "Osmotic power", "Pumped-storage hydroelectricity", "Marine current power",
                 "List of tidal power stations", "Tidal mill", "Run-of-the-river hydroelectricity",
                 "Offshore wind power", "Blue energy", "Tide gauge", "Tidal prediction",
                 "Renewable energy by country", "Energy storage", "Coastal engineering"]
WIKI_BOOKS = [
    ("Marlow, T.", 1998, "Moon Engines: A History of Tidal Mills", "Bristol", "Severn House"),
    ("Okafor, L.", 2004, "Power from the Estuary", "Cardiff", "Western Press"),
    ("Brandt, H.", 2011, "Barrages and Their Discontents", "Hamburg", "Nordsee Verlag"),
    ("Quint, A.", 2013, "The Lagoon Question", "London", "Ashgrove"),
    ("Siddiqui, R.", 2015, "Tidal Stream Arrays in Practice", "Glasgow", "Clyde Technical"),
    ("Lefevre, M.", 2017, "Rance at Fifty", "Rennes", "Editions du Littoral"),
    ("Havelock, P.", 2019, "Sediment, Fish and Turbines", "Plymouth", "Sound Books"),
    ("Tanaka, Y.", 2020, "Harnessing the Kuroshio", "Kobe", "Seto Academic"),
    ("Ferreira, J.", 2022, "Ocean Power Economics", "Lisbon", "Atlantico"),
    ("Njoroge, W.", 2024, "Tides and the Grid", "Mombasa", "Coastline Press"),
]
WIKI_JOURNALS = ["Journal of Ocean Engineering", "Renewable Energy Review", "Coastal Studies",
                 "Proceedings of the Marine Power Society", "Energy Policy Letters",
                 "Estuarine Research", "Applied Hydraulics"]
WIKI_SURNAMES = ["Abbott", "Baptiste", "Castell", "Dunmore", "Eriksen", "Fairley", "Gorman", "Hollis",
                 "Iwata", "Jansen", "Kowalski", "Lindqvist", "Moreau", "Nakamura", "Oyelaran", "Pryce",
                 "Quayle", "Rourke", "Szabo", "Thorne", "Ueda", "Varga", "Whitlock", "Yilmaz"]
WIKI_LANGS = ["العربية", "Català", "Čeština", "Dansk", "Deutsch", "Eesti", "Español", "Esperanto",
              "Euskara", "فارسی", "Français", "Galego", "한국어", "Italiano", "Nederlands", "日本語",
              "Norsk bokmål", "Polski", "Português", "Română", "Русский", "Suomi", "Svenska", "中文"]


def wiki_paragraph(key: str) -> str:
    out = []
    for i in range(6):
        k = f"{key}.{i}"
        sentence = WIKI_SENTENCES[pick(k + "s", len(WIKI_SENTENCES))]
        t0 = WIKI_TERMS[pick(k + "a", len(WIKI_TERMS))]
        t1 = WIKI_TERMS[pick(k + "b", len(WIKI_TERMS))]
        out.append(sentence.format(a("wiki", t0), a("wiki", t1)))
    return "<p>" + " ".join(out) + "</p>"


def build_wiki() -> str:
    side = ["Help", "Learn to edit", "Community portal", "Recent changes", "Upload file",
            "What links here", "Related changes", "Special pages", "Permanent link",
            "Page information", "Cite this page", "Get shortened URL", "Download as PDF",
            "Printable version"]
    header = (
        '<header class="top">' + a("wiki", "Openpedia", "main-page") +
        '<input type="search" placeholder="Search Openpedia" aria-label="Search Openpedia">'
        "<button>Search</button>" +
        "".join(a("wiki", x) for x in ["Main page", "Contents", "Current events", "Random article",
                                        "About Openpedia", "Contact us", "Donate", "Create account",
                                        "Log in"]) + "</header>"
    )
    nav = ('<nav class="side"><h3>Tools</h3>' + "".join(a("wiki", x) for x in side) +
           "<h3>Languages</h3>" + "".join(a("wiki", x, f"lang-{i}") for i, x in enumerate(WIKI_LANGS)) +
           "</nav>")
    infobox = ('<div class="infobox"><b>Tidal power</b><br>Type: ' + a("wiki", "Renewable energy") +
               "Leading sites: " + a("wiki", "Sihwa Lake station") + a("wiki", "La Rance barrage", "rance-barrage-infobox") +
               a("wiki", "Swansea Bay proposal") + "Related: " + a("wiki", "Hydropower") +
               a("wiki", "Ocean energy") + a("wiki", "Tidal force") + a("wiki", "Tidal range") +
               a("wiki", "Lunar cycle") + a("wiki", "Capacity factor") + a("wiki", "Levelised cost") +
               a("wiki", "Grid frequency") + "</div>")
    lead = (
        "<p><b>Tidal power</b> is the generation of electricity from the regular rise and fall of the "
        f"sea. The first large plant, the {a('wiki', 'Rance Tidal Power Station')}, opened in 1966 in "
        "Brittany and ran for decades as the largest of its kind. Unlike "
        f"{a('wiki', 'wind power')} or {a('wiki', 'solar power')}, tides can be predicted years in "
        "advance, which makes the output easy to plan but hard to move to the hours of peak demand. "
        f"Two families of scheme exist: {a('wiki', 'tidal range')} schemes, which hold water behind a "
        f"wall, and {a('wiki', 'tidal stream')} schemes, which place turbines in fast currents.</p>"
    )
    sections = []
    for s, title in enumerate(WIKI_SECTIONS):
        paras = "".join(wiki_paragraph(f"w{s}.{p}") for p in range(5))
        sections.append(f'<h2 id="s{s}">{esc(title)} <small>[{a("wiki", "edit", f"edit-section-{s}")}]</small></h2>{paras}')
    see_also = "<h2>See also</h2><ul>" + "".join(f"<li>{a('wiki', x)}</li>" for x in WIKI_SEE_ALSO) + "</ul>"
    refs = []
    for n in range(1, 381):
        k = f"ref{n}"
        author = WIKI_SURNAMES[pick(k + "a", len(WIKI_SURNAMES))]
        year = 1960 + pick(k + "y", 65)
        journal = WIKI_JOURNALS[pick(k + "j", len(WIKI_JOURNALS))]
        topic = WIKI_TERMS[pick(k + "t", len(WIKI_TERMS))]
        title = f"Measurements of the {topic} at site {n}"
        archived = f" {a('wiki', 'Archived', f'archive-{n}')}" if n % 3 == 0 else ""
        refs.append(
            f'<li id="cite-{n}"><a href="#cite-{n}">^</a> {author}, {chr(65 + n % 26)}. ({year}). '
            f'"{a("wiki", title, f"ref-{n}")}". <i>{journal}</i>. {n % 40 + 1}({n % 4 + 1}): '
            f"{n * 3}-{n * 3 + 9}.{archived}</li>"
        )
    references = '<h2>References</h2><ol class="refs">' + "".join(refs) + "</ol>"
    further = "<h2>Further reading</h2><ul>" + "".join(
        f"<li>{author} ({year}). <i>{a('wiki', title, 'book-' + slugify(title))}</i>. {city}: {pub}. "
        f"ISBN 978-0-{1000 + i * 7:04d}-{2000 + i * 13:04d}-{i}.</li>"
        for i, (author, year, title, city, pub) in enumerate(WIKI_BOOKS)
    ) + "</ul>"
    external = "<h2>External links</h2><ul>" + "".join(
        f"<li>{a('wiki', x)}</li>" for x in ["Tidal energy atlas", "Marine Power Society",
                                                 "Tidal data portal", "Estuary observatory",
                                                 "Ocean energy statistics", "Open tide tables",
                                                 "Turbine test centre", "Lagoon consultation archive"]
    ) + "</ul>"
    navboxes = ""
    for b, name in enumerate(["Renewable energy", "Ocean engineering", "Power stations"]):
        items = rotate(WIKI_TERMS, f"nav{b}", 55)
        vte = "".join(a("wiki", x, f"template-{b}-{x}") for x in ("v", "t", "e"))
        navboxes += (f'<div class="navbox">{vte} <b>{esc(name)}</b>: ' +
                     "".join(a("wiki", x, f"nav-{b}-{slugify(x)}") for x in items) + "</div>")
    cats = "<p>Categories: " + "".join(
        a("wiki", x) for x in ["Tidal power", "Energy conversion", "Renewable energy technology",
                               "Coastal construction", "Hydropower", "Ocean energy",
                               "Electric power generation", "Marine engineering"]
    ) + "</p>"
    footer = "<footer>" + "".join(
        a("wiki", x) for x in ["Privacy policy", "About Openpedia", "Disclaimers", "Code of Conduct",
                               "Developers", "Statistics", "Cookie statement", "Mobile view",
                               "Terms of Use", "Contact Openpedia"]
    ) + "</footer>"
    tabs = "<div>" + "".join(a("wiki", x, f"tab-{slugify(x)}") for x in ["Article", "Talk", "Read", "Edit", "View history"]) + "</div>"
    body = (header + '<div class="wrap">' + nav + "<main>" + tabs + "<h1>Tidal power</h1>" + infobox +
            lead + "".join(sections) + see_also + references + further + external + navboxes + cats +
            "</main></div>" + footer)
    return page("Tidal power - Openpedia", body)


# --- 2. docs: library documentation with a long module sidebar (step 1's MDN / python docs) --------

DOC_PKGS = ["actuators", "adapters", "analytics", "audio", "auth", "cache", "calibration", "cli",
            "codecs", "config", "diagnostics", "display", "drivers", "events", "export", "firmware",
            "geometry", "io", "logging", "net", "plugins", "power", "scheduling", "sensors", "serial",
            "storage", "testing", "threads", "ui", "units"]
DOC_MODS = ["base", "compat", "constants", "errors", "factory", "helpers", "models", "registry",
            "schemas", "thermal", "types", "utils", "validators", "worker"]
DOC_FUNCS = [
    ("Widget.__init__", None), ("Widget.open", None), ("Widget.close", None),
    ("Widget.ping", "30 seconds"), ("Widget.reset", "12 seconds"), ("Widget.configure", None),
    ("Widget.subscribe", None), ("Widget.unsubscribe", None), ("Widget.status", None),
    ("Widget.calibrate", None), ("Widget.connect", "45 seconds"), ("Widget.read", None),
    ("Widget.write", None), ("Widget.fetch", "37.5 seconds"), ("Widget.flush", None),
    ("Widget.export", None), ("Widget.snapshot", None), ("Widget.restore", None),
    ("Widget.describe", None), ("Widget.__repr__", None),
]
DOC_XREFS = ["Device", "Channel", "Frame", "Reading", "Profile", "Session", "Transport", "Clock",
             "Unit", "Error", "Event", "Buffer"]


def build_docs() -> str:
    header = (
        '<header class="top">' + a("docs", "Acme Widgets", "home") +
        "".join(a("docs", x) for x in ["Docs", "Tutorial", "API", "Changelog", "Blog", "Community",
                                        "GitHub", "Download"]) +
        '<input type="search" placeholder="Search docs" aria-label="Search docs"><button>Go</button>'
        '<select aria-label="Version"><option>4.2</option><option>4.1</option><option>3.9</option></select>'
        "</header>"
    )
    side = ['<nav class="side"><h3>Modules</h3>']
    for p in DOC_PKGS:
        side.append(f"<h3>acme.widgets.{p}</h3>")
        side.extend(a("docs", f"acme.widgets.{p}.{m}", f"module-{p}-{m}") for m in DOC_MODS)
    side.append("</nav>")
    toc = ('<aside class="right"><b>On this page</b>' +
           "".join(f'<a href="#f{i}">{esc(name)}</a>' for i, (name, _) in enumerate(DOC_FUNCS)) +
           '<a href="#see-also">See also</a><a href="#top">Back to top</a></aside>')
    intro = (
        '<h1 id="top">acme.widgets.core — Widget objects</h1>'
        f"<p>A <code>Widget</code> is the handle this library gives you for one physical device. It "
        f"wraps a {a('docs', 'Transport', 'xref-transport-intro')}, keeps a "
        f"{a('docs', 'Session', 'xref-session-intro')} open while you work, and reports each "
        f"measurement as a {a('docs', 'Reading', 'xref-reading-intro')}. Read the "
        f"{a('docs', 'Tutorial', 'tutorial-intro')} first if this is your first device.</p>"
    )
    sections = []
    for i, (name, timeout) in enumerate(DOC_FUNCS):
        refs = rotate(DOC_XREFS, f"doc{i}", 6)
        params = "".join(
            f"<li><b>{esc(r.lower())}</b> ({a('docs', r, f'xref-{slugify(r)}-{i}')}) — passed through "
            f"to the underlying {esc(r.lower())}.</li>" for r in refs
        )
        if timeout:
            params += (f"<li><b>timeout</b> (float) — How long {esc(name)}() waits for the device, in "
                       f"seconds. Default: {timeout}.</li>")
        sig = f"{name}({', '.join(r.lower() for r in refs[:3])}{', timeout=...' if timeout else ''})"
        sections.append(
            f'<h2 id="f{i}">{esc(name)} <a href="#f{i}" aria-label="Permalink to {esc(name)}">¶</a></h2>'
            f"<pre>{esc(sig)}</pre><button>Copy</button>"
            f"<p>{esc(name)} is part of the stable API since version {1 + i % 4}.{i % 10}. It never "
            "blocks the event loop and it raises only the errors listed below.</p>"
            f"<ul>{params}</ul><p>See also: "
            + ", ".join(a("docs", x, f"see-{i}-{slugify(x)}") for x in rotate(DOC_XREFS, f"see{i}", 3))
            + "</p>"
        )
    bottom = (
        '<h2 id="see-also">See also</h2><p>' +
        a("docs", "Previous topic: acme.widgets.config", "acme-widgets-config") + " · " +
        a("docs", "Next topic: acme.widgets.events", "acme-widgets-events") + "</p><p>" +
        a("docs", "Report a bug") + " · " + a("docs", "Show source") + " · " + a("docs", "Edit this page") +
        "</p>"
    )
    footer = "<footer>" + "".join(
        a("docs", x) for x in ["About", "Code of conduct", "Security", "Governance", "Sponsors",
                               "Release notes", "Roadmap", "Contributing", "Privacy", "License",
                               "Status", "Contact"]
    ) + "</footer>"
    body = (header + '<div class="wrap">' + "".join(side) + "<main>" + intro + "".join(sections) +
            bottom + "</main>" + toc + "</div>" + footer)
    return page("acme.widgets.core — Acme Widgets 4.2 documentation", body)


# --- 3. news: a portal front page (step 1's BBC / g1) -------------------------------------------

NEWS_SECTIONS = ["World", "Business", "Politics", "Technology", "Health", "Sport", "Culture",
                 "Opinion", "Travel", "Climate", "Science", "Weather", "Local", "Education",
                 "Economy", "Arts", "Books", "Food", "Style", "Obituaries"]
NEWS_SUBJECTS = ["Port authority", "City council", "Researchers", "Regulators", "Union leaders",
                 "Farmers", "Local schools", "The central bank", "Hospital staff", "Ferry operators",
                 "Museum curators", "Fishermen", "Tenants", "Start-up founders", "Coastguard crews"]
NEWS_VERBS = ["approve", "reject", "delay", "question", "celebrate", "review", "challenge", "fund",
              "pause", "expand"]
NEWS_OBJECTS = ["new harbour levy", "river clean-up plan", "night ferry service", "school meal budget",
                "flood barrier upgrade", "housing quota", "bridge repairs", "museum extension",
                "fishing permits", "tram timetable", "hospital car park", "wind farm lease"]
NEWS_PLACES = ["Eastport", "Millbrook", "Saltmarsh", "Northgate", "Kellow Bay", "Dunmere", "Ashcombe",
               "Rivermouth", "Pellham", "Stoneford", "Greywater", "Harlow Point"]


def build_news() -> str:
    header = (
        '<header class="top sticky">' + a("news", "The Harbor Ledger", "home") +
        "".join(a("news", x, f"section-{slugify(x)}") for x in NEWS_SECTIONS[:12]) +
        a("news", "Subscribe") + a("news", "Sign in") +
        '<input type="search" placeholder="Search news" aria-label="Search news"></header>'
    )
    top = (
        '<section><h1 style="font-size:32px;margin:8px 0">' +
        a("news", "Harbor council approves tidal lagoon after decade-long fight", "top-story") +
        "</h1><p>The vote ends a dispute that split the town and its fishing fleet.</p><p>Related: " +
        a("news", "What the lagoon will cost you") + " · " + a("news", "Timeline: ten years of hearings") +
        " · " + a("news", "Fishermen react to the vote") + "</p><p>Live: " +
        a("news", "Storm warning for the northern coast") + " · " + a("news", "Rail strike talks resume") +
        " · " + a("news", "Election count updates") + "</p></section>"
    )
    # Headlines do not carry their section's name, as on a real front page: which section a headline
    # sits in is known only from where it sits. Unique by construction (a repeat is skipped).
    blocks = []
    seen: set[str] = set()
    n = 0
    for s, name in enumerate(NEWS_SECTIONS):
        items = []
        while len(items) < 10:
            k = f"news{n}"
            n += 1
            subj = NEWS_SUBJECTS[pick(k + "s", len(NEWS_SUBJECTS))]
            verb = NEWS_VERBS[pick(k + "v", len(NEWS_VERBS))]
            obj = NEWS_OBJECTS[pick(k + "o", len(NEWS_OBJECTS))]
            place = NEWS_PLACES[pick(k + "p", len(NEWS_PLACES))]
            headline = f"{subj} {verb} {obj} in {place}"
            if headline in seen:
                continue
            seen.add(headline)
            items.append(f"<li>{a('news', headline, f'story-{s}-{len(items)}')}</li>")
        blocks.append(f"<h2>{a('news', name, f'section-page-{slugify(name)}')}</h2><ul>{''.join(items)}</ul>")
    most_read = ('<aside class="right"><b>Most read</b>' + "".join(
        a("news", f"Most read {i}: {NEWS_OBJECTS[i]} explained", f"most-read-{i}") for i in range(10)
    ) + "</aside>")
    footer = "<footer>" + "".join(
        a("news", x) for x in ["About us", "Contact", "Careers", "Advertise", "Terms of use",
                               "Privacy policy", "Cookie settings", "Accessibility", "Corrections",
                               "Newsletters", "Archive", "RSS feeds", "Help centre", "Editorial standards",
                               "Complaints", "Syndication", "Photo sales", "Events", "Podcasts",
                               "Puzzles", "Weather maps", "Obituary notices", "Letters",
                               "Staff directory", "Tip line", "Apps", "Gift subscriptions",
                               "Print edition", "Sitemap", "Modern slavery statement"]
    ) + "</footer>"
    body = header + '<div class="wrap"><main>' + top + "".join(blocks) + "</main>" + most_read + "</div>" + footer
    return page("The Harbor Ledger — front page", body)


# --- 4. pkg: a package index page (step 1's PyPI) -------------------------------------------------

def pkg_versions() -> list[str]:
    out: list[str] = []
    for major, minor_count in ((4, 3), (3, 10), (2, 6), (1, 4)):
        for minor in reversed(range(minor_count)):
            for patch in reversed(range(3 if (major, minor) != (4, 2) else 1)):
                out.append(f"{major}.{minor}.{patch}")
    return out[:45]


def pkg_hash(label: str) -> str:
    return hashlib.sha256(f"acme-widgets|{label}".encode()).hexdigest()


def build_pkg() -> str:
    header = (
        '<header class="top">' + a("pkg", "PackageIndex", "home") +
        '<input type="search" placeholder="Search projects" aria-label="Search projects"><button>Search</button>' +
        "".join(a("pkg", x) for x in ["Help", "Docs", "Sponsors", "Log in", "Register"]) + "</header>"
    )
    side = (
        '<nav class="side"><h3>Navigation</h3><a href="#description">Project description</a>'
        '<a href="#history">Release history</a><a href="#files">Download files</a>'
        "<h3>Project links</h3>" +
        "".join(a("pkg", x, f"project-{slugify(x)}") for x in ["Homepage", "Documentation", "Source", "Issues", "Changelog", "Funding"]) +
        "<h3>Meta</h3>" + a("pkg", "License: MIT", "license-mit") + a("pkg", "Author: Acme Devices Ltd", "author") +
        "<h3>Maintainers</h3>" + "".join(a("pkg", x, f"maintainer-{x}") for x in ["jkarlsson", "mbello", "t_osei"]) +
        "<h3>Classifiers</h3>" + "".join(
            a("pkg", x, f"classifier-{i}") for i, x in enumerate([
                "Development Status :: 5 - Production/Stable", "Framework :: AsyncIO",
                "Intended Audience :: Developers", "Intended Audience :: Science/Research",
                "License :: OSI Approved :: MIT License", "Operating System :: OS Independent",
                "Programming Language :: Python :: 3", "Programming Language :: Python :: 3.10",
                "Programming Language :: Python :: 3.11", "Programming Language :: Python :: 3.12",
                "Programming Language :: Python :: 3.13", "Topic :: Scientific/Engineering",
                "Topic :: Software Development :: Libraries", "Topic :: System :: Hardware",
                "Topic :: System :: Hardware :: Hardware Drivers", "Topic :: Home Automation",
                "Topic :: Scientific/Engineering :: Physics", "Typing :: Typed",
                "Natural Language :: English", "Environment :: Console"])
        ) + "</nav>"
    )
    readme_links = rotate(["Documentation", "Tutorial", "Examples", "API reference", "FAQ",
                           "Changelog", "Contributing guide", "Code of conduct", "Security policy",
                           "Discussions", "Issue tracker", "Roadmap", "Benchmarks", "Plugins",
                           "Firmware notes", "Supported devices", "Migration guide",
                           "Async guide", "Testing guide", "Packaging notes"], "readme", 20)
    readme = (
        '<h2 id="description">Project description</h2>'
        "<p>acme-widgets talks to Acme measurement devices over serial, USB and TCP. It gives you one "
        "object per device, typed readings, and an async API that never blocks your loop.</p>" +
        "".join(f"<p>{a('pkg', x, f'readme-{slugify(x)}')} — {esc(x)} for acme-widgets. "
                f"See also {a('pkg', x + ' (mirror)', f'readme-mirror-{slugify(x)}')}.</p>" for x in readme_links)
    )
    versions = pkg_versions()
    history = '<h2 id="history">Release history</h2><ul>' + "".join(
        f"<li>{a('pkg', v, f'release-{v}')} <span class=\"meta\">released {2014 + (len(versions) - i) // 5}"
        f"-{(i % 12) + 1:02d}-{(i * 7) % 28 + 1:02d}</span></li>"
        for i, v in enumerate(versions)
    ) + "</ul>"
    files = '<h2 id="files">Download files</h2>'
    for label, fname, size in (("sdist", "acme_widgets-4.2.0.tar.gz", "88.4 kB"),
                               ("wheel", "acme_widgets-4.2.0-py3-none-any.whl", "41.2 kB")):
        files += (
            f"<h3>{a('pkg', fname, f'file-{label}')} ({size})</h3>"
            f"<p>SHA256: <code>{pkg_hash(label + '-sha256')}</code><br>"
            f"MD5: <code>{pkg_hash(label + '-md5')[:32]}</code><br>"
            f"BLAKE2b-256: <code>{pkg_hash(label + '-blake2b')}</code></p>"
            f"<button>View hashes for {esc(fname)}</button>"
        )
    footer = "<footer>" + "".join(
        a("pkg", x) for x in ["Help", "Installing packages", "Uploading packages", "User guide",
                              "Project name retention", "FAQs", "About", "Blog", "Infrastructure",
                              "Security", "Accessibility", "Report a bug", "Code of conduct",
                              "Status", "Donate", "Sponsors", "Privacy notice", "Terms of use",
                              "Acceptable use", "Trademarks"]
    ) + "</footer>"
    title_block = (
        "<h1>acme-widgets 4.2.0</h1><p><code>pip install acme-widgets</code> "
        "<button>Copy to clipboard</button> " + a("pkg", "Latest version", "latest") + "</p>"
    )
    body = (header + '<div class="wrap">' + side + "<main>" + title_block + readme + history + files +
            "</main></div>" + footer)
    return page("acme-widgets · PackageIndex", body)


# --- 5. forum: a Q&A thread (step 1's Hacker News / Stack Overflow) ------------------------------

FORUM_USERS = ["ines_r", "tobiasw", "k_mbeki", "rsaxena", "olga.v", "dmitri_p", "sunita88", "fjord_dev",
               "a_nakashima", "petra_l", "cwen", "b_oduya", "lucca_m", "haru_t", "zofia_k",
               "emeka_o", "yusuf.a", "marek_n", "marlow_k", "priya_s", "jonas_h", "aline_c",
               "wen_li", "tomas_r", "greta_v", "kofi_a", "mira_d", "sven_b", "lea_m", "raj_p",
               "nadia_f", "oscar_t"]
FORUM_REPLIES = [
    "I hit the same thing on 3.12; pinning the driver fixed it for me.",
    "Have you checked whether the serial port is opened twice?",
    "This looks like the buffer size default changed between releases.",
    "Same here. The workaround below works but it is ugly.",
    "Can you post the full traceback? The second frame matters.",
    "It is a race in the reconnect path, there is an open issue about it.",
    "Setting the timeout explicitly made the error go away on my board.",
    "Does it still happen with the async API?",
]


def build_forum() -> str:
    header = (
        '<header class="top">' + a("forum", "DevAnswers", "home") +
        "".join(a("forum", x) for x in ["Questions", "Tags", "Users", "Companies", "Jobs", "Unanswered"]) +
        '<input type="search" placeholder="Search questions" aria-label="Search questions">' +
        a("forum", "Log in") + a("forum", "Sign up") + "</header>"
    )
    question = (
        "<h1>Widget.fetch() hangs forever after a reconnect — how do I make it time out?</h1>"
        '<p class="meta">Asked by ' + a("forum", "halvard_n", "user-halvard_n") +
        " · viewed 4,412 times</p>"
        "<p>After my device drops off USB and comes back, the next call to fetch() never returns. I read "
        f"the {a('forum', 'library docs', 'docs-link')} and the {a('forum', 'changelog', 'changelog-link')}, "
        f"tried the {a('forum', 'reconnect example', 'example-link')} and searched the "
        f"{a('forum', 'issue tracker', 'issues-link')} without luck. Any ideas?</p><p>Tags: " +
        "".join(a("forum", t, f"tag-{t}") for t in ["python", "asyncio", "serial", "acme-widgets", "timeout"]) +
        "</p><p>" + "".join(a("forum", x, f"question-{slugify(x)}") for x in ["Share", "Edit", "Follow", "Flag"]) + "</p>"
    )
    comments = []
    for c, user in enumerate(FORUM_USERS):
        text = FORUM_REPLIES[pick(f"fc{c}", len(FORUM_REPLIES))]
        comments.append(
            '<div class="comment"><p class="meta">' + a("forum", user, f"user-{user}") +
            f" · {c + 2} hours ago</p><p>{esc(text)}</p><p>"
            "<button>upvote</button> <button>downvote</button> " +
            a("forum", "reply", f"reply-c{c + 101}") + " " + a("forum", "share", f"share-c{c + 101}") + " " +
            a("forum", "flag", f"flag-c{c + 101}") + "</p></div>"
        )
    more = "<p>" + a("forum", "More comments", "thread-4412-page-2") + "</p>"
    related = ('<aside class="right"><b>Related questions</b>' + "".join(
        a("forum", f"Related: {x}", f"related-{i}") for i, x in enumerate([
            "fetch() returns stale readings", "USB device disappears on Windows",
            "asyncio timeout does not cancel serial read", "How to reset a widget remotely",
            "Reconnect loop eats CPU", "Reading frames in bulk", "Calibrate before or after connect?",
            "Why does ping() succeed but fetch() fail?", "Serial port permission denied",
            "Widget.close() leaves a thread running", "Docker and USB passthrough",
            "Profiling slow reads", "Mocking a widget in tests", "Thread safety of Widget",
            "Firmware update over TCP"])
    ) + "</aside>")
    footer = "<footer>" + "".join(
        a("forum", x) for x in ["About", "Press", "Work here", "Legal", "Privacy policy",
                                "Terms of service", "Contact us", "Cookie settings", "Cookie policy",
                                "Blog", "Help", "Chat", "Meta", "Teams", "Advertising"]
    ) + "</footer>"
    body = (header + '<div class="wrap"><main>' + question + "<h2>32 comments</h2>" +
            "".join(comments) + more + "</main>" + related + "</div>" + footer)
    return page("Widget.fetch() hangs forever after a reconnect - DevAnswers", body)


# --- 6. shop: a product listing with a search box, filters and pagination ------------------------

SHOP_MATERIALS = ["Brass", "Oak", "Linen", "Steel", "Glass", "Ceramic", "Walnut", "Rattan", "Copper",
                  "Marble", "Concrete", "Paper"]
SHOP_SHAPES = ["Tulip", "Dome", "Globe", "Arc", "Column", "Lantern", "Cone", "Drum", "Bell", "Orb"]
SHOP_KINDS = ["Desk Lamp", "Floor Lamp", "Pendant", "Wall Light", "Table Lamp", "Reading Light"]


def shop_catalog() -> list[dict[str, Any]]:
    """120 products; the listing page shows the first 48. Names are unique; prices are exact cents."""
    products: list[dict[str, Any]] = []
    seen: set[str] = set()
    i = 0
    while len(products) < 120:
        k = f"p{i}"
        name = (f"{SHOP_MATERIALS[pick(k + 'm', len(SHOP_MATERIALS))]} "
                f"{SHOP_SHAPES[pick(k + 's', len(SHOP_SHAPES))]} {SHOP_KINDS[pick(k + 'k', len(SHOP_KINDS))]}")
        i += 1
        if name in seen:
            continue
        seen.add(name)
        price = 29.0 + pick(k + "p", 400) + pick(k + "c", 10) * 0.1
        products.append({"id": f"p{len(products) + 1}", "name": name, "price": round(price, 2)})
    # The two the tasks name, placed where the tasks need them: one far down the listing, one the
    # cheapest brass product anywhere in the catalogue.
    products[36] = {"id": "p37", "name": "Harbor Brass Floor Lamp", "price": 189.0}
    products[90] = {"id": "p91", "name": "Petite Brass Candle Lamp", "price": 24.9}
    return products


SHOP_HEADER = (
    '<header class="top sticky"><a href="/shop/lamps.html">Lumen &amp; Co.</a>'
    '<form action="/shop/search.html" method="get"><input name="q" placeholder="Search the shop">'
    '<button type="submit">Search</button></form>'
    '<a href="/go/shop/account">Account</a><a id="cart-link" href="/go/shop/cart">Cart (0)</a>'
    '<span id="cart-status" role="status"></span>'
    + "".join(f'<a href="/go/shop/category-{slugify(x)}">{x}</a>' for x in
              ["Lamps", "Pendants", "Wall lights", "Outdoor", "Bulbs", "Shades", "Smart lighting",
               "Kids", "Sale", "New in"])
    + "</header>"
)
SHOP_JS = """
const CATALOG = __CATALOG__;
function addToCart(id) {
  const p = CATALOG.find(x => x.id === id);
  document.getElementById('cart-link').textContent = 'Cart (1)';
  document.getElementById('cart-status').textContent =
    'Added ' + p.name + ' to your cart. Cart code: ' + m7code('cart|' + id);
}
"""


def shop_card(p: dict[str, Any]) -> str:
    return (
        f'<div class="card"><div style="height:170px;background:#eee"></div>'
        f'<a href="/shop/product.html?id={p["id"]}">{esc(str(p["name"]))}</a>'
        f'<p>${float(p["price"]):.2f}</p><button onclick="addToCart(\'{p["id"]}\')">Add to cart</button></div>'
    )


def build_shop(catalog: list[dict[str, Any]]) -> dict[str, str]:
    script = SHOP_JS.replace("__CATALOG__", json.dumps(catalog))
    filters = '<nav class="side"><h3>Filter</h3>' + "".join(
        f'<label><input type="checkbox" name="f{i}" aria-label="{esc(x)}"> {esc(x)}</label><br>'
        for i, x in enumerate(SHOP_MATERIALS + ["Under $50", "$50-$100", "$100-$200", "Over $200",
                                                "In stock", "Dimmable", "Smart", "LED included",
                                                "Plug-in", "Hardwired", "Black", "White", "Gold",
                                                "Green", "Blue", "Red", "Grey", "Natural"])
    ) + "<button>Apply filters</button></nav>"
    grid = '<div class="grid">' + "".join(shop_card(p) for p in catalog[:48]) + "</div>"
    pager = '<nav aria-label="Pagination"><p>' + a("shop", "Previous page", "lamps-page-0") + " " + " ".join(
        f'<a href="/go/shop/lamps-page-{n}" aria-label="Page {n}">{n}</a>' for n in range(1, 9)
    ) + " " + a("shop", "Next page", "lamps-page-2") + "</p></nav>"
    footer = "<footer>" + "".join(
        a("shop", x) for x in ["About us", "Stores", "Careers", "Press", "Sustainability", "Delivery",
                               "Returns", "Warranty", "Track your order", "Gift cards", "Trade programme",
                               "Contact", "FAQ", "Size guide", "Bulb guide", "Installation",
                               "Recycling", "Privacy", "Cookies", "Terms", "Accessibility", "Sitemap",
                               "Affiliates", "Reviews", "Newsletter"]
    ) + "</footer>"
    listing = page(
        "Lamps — Lumen & Co.",
        SHOP_HEADER + '<div class="wrap">' + filters + "<main><h1>Lamps</h1><p>120 products</p>" + grid +
        pager + "</main></div>" + footer,
        script=script,
    )
    search = page(
        "Search — Lumen & Co.",
        SHOP_HEADER + '<main><h1 id="h">Search</h1><div id="results"></div></main>',
        script=script + """
const q = (new URLSearchParams(location.search).get('q') || '').trim().toLowerCase();
const hits = q ? CATALOG.filter(p => p.name.toLowerCase().includes(q)) : [];
document.getElementById('h').textContent = hits.length + ' results for "' + q + '"';
document.getElementById('results').innerHTML = hits.map(p =>
  '<div class="row"><a class="name" href="/shop/product.html?id=' + p.id + '">' + p.name + '</a>' +
  '<span>$' + p.price.toFixed(2) + '</span>' +
  '<button onclick="addToCart(\\'' + p.id + '\\')">Add to cart</button></div>').join('');
""",
    )
    product = page(
        "Product — Lumen & Co.",
        SHOP_HEADER + '<main><h1 id="name"></h1><p id="price"></p><p>Ships in 3-5 working days.</p>'
        '<button id="add">Add to cart</button></main>',
        script=script + """
const id = new URLSearchParams(location.search).get('id');
const p = CATALOG.find(x => x.id === id);
if (p) {
  document.getElementById('name').textContent = p.name;
  document.getElementById('price').textContent = '$' + p.price.toFixed(2);
  document.getElementById('add').onclick = () => addToCart(p.id);
} else { document.getElementById('name').textContent = 'Product not found'; }
""",
    )
    return {"shop/lamps.html": listing, "shop/search.html": search, "shop/product.html": product}


# --- 7. repo: a code repository page (step 1's GitHub) -------------------------------------------

REPO_DIRS = [".devcontainer", ".github", "benchmarks", "docs", "examples", "firmware", "scripts", "src",
             "tests", "tools"]
REPO_FILES = [".editorconfig", ".gitattributes", ".gitignore", ".pre-commit-config.yaml", "CHANGELOG.md",
              "CITATION.cff", "CODE_OF_CONDUCT.md", "CONTRIBUTING.md", "LICENSE", "MANIFEST.in",
              "Makefile", "README.md", "SECURITY.md", "noxfile.py", "pyproject.toml", "tox.ini"]
REPO_COMMITS = ["fix: retry the handshake once", "docs: clarify the timeout default", "ci: pin the runner",
                "feat: async reconnect", "chore: bump dev dependencies", "test: cover the USB path",
                "refactor: split the transport", "build: drop 3.9"]


def build_repo() -> str:
    header = (
        '<header class="top">' + a("repo", "CodeHub", "home") +
        "".join(a("repo", x) for x in ["Product", "Solutions", "Resources", "Open Source", "Enterprise", "Pricing"]) +
        '<input type="search" placeholder="Search or jump to" aria-label="Search or jump to">' +
        a("repo", "Sign in") + a("repo", "Sign up") + "</header>"
    )
    repo_head = (
        "<div style=\"padding:8px 16px\">" + a("repo", "acme-org", "org") + " / " +
        a("repo", "acme-widgets", "repo-home") + " <span>Public</span> " +
        "".join(f"<button>{x}</button>" for x in ["Notifications", "Fork 212", "Star 3.1k"]) + "</div>"
        '<div style="padding:0 16px;border-bottom:1px solid #ccc">' +
        "".join(a("repo", x, f"tab-{slugify(x.split(' ')[0])}") + " " for x in
                ["Code", "Issues 12", "Pull requests 3", "Discussions", "Actions", "Projects", "Wiki",
                 "Security", "Insights"]) + "</div>"
    )
    toolbar = (
        "<p><button>main</button> " + a("repo", "Branches") + " " + a("repo", "Tags") +
        ' <input placeholder="Go to file" aria-label="Go to file"> <button>Code</button></p>'
        '<p class="meta">' + a("repo", "jkarlsson", "commit-author") + " " +
        a("repo", "fix: retry the handshake once", "commit-latest") + " " + a("repo", "a41c9e2", "commit-hash") +
        " " + a("repo", "History", "commits") + "</p>"
    )
    rows = []
    for i, name in enumerate(REPO_DIRS + REPO_FILES):
        msg = REPO_COMMITS[pick(f"rc{i}", len(REPO_COMMITS))]
        rows.append(
            f'<div class="row"><span class="name">{a("repo", name, f"file-{slugify(name)}")}</span>'
            f'<span>{a("repo", msg, f"commit-{i}")}</span><span class="meta">{(i % 11) + 1} days ago</span></div>'
        )
    readme_sections = ["Installation", "Quick start", "Supported devices", "Configuration", "Async usage",
                       "Testing", "Benchmarks", "Firmware", "Troubleshooting", "Roadmap"]
    readme = "<h2>README.md</h2>" + "".join(
        a("repo", b, f"badge-{slugify(b)}") + " " for b in ["build passing", "coverage 94%", "pypi 4.2.0",
                                                          "python 3.10+", "license MIT", "downloads 2M"]
    )
    for s, title in enumerate(readme_sections):
        links = rotate(["the docs", "an example", "the API reference", "the changelog", "the FAQ",
                        "the migration guide", "the test suite", "the benchmark notes",
                        "the firmware table", "the issue template"], f"readme{s}", 6)
        readme += (f"<h3>{esc(title)}</h3><p>" + " ".join(
            f"For {esc(title.lower())}, see {a('repo', x, f'readme-{s}-{slugify(x)}')}." for x in links
        ) + "</p>")
    readme += (
        "<h3>Community</h3><p>Questions are welcome in the " + a("repo", "discussion forum", "discussion-forum") +
        ", quick ones in the " + a("repo", "chat room", "chat-room") + ", and release news goes out on the " +
        a("repo", "mailing list", "mailing-list") + ".</p><h3>License</h3><p>MIT — see " +
        a("repo", "LICENSE", "license-readme") + ".</p>"
    )
    about = (
        '<aside class="right"><b>About</b><p>Talk to Acme measurement devices from Python.</p>' +
        a("repo", "acme-widgets.example.org", "website") + "<p>" +
        " ".join(a("repo", t, f"topic-{t}") for t in ["python", "hardware", "serial", "usb", "asyncio",
                                                       "sensors", "iot", "instrumentation"]) + "</p>" +
        "".join(a("repo", x, f"about-{slugify(x)}") for x in ["Readme", "MIT license", "Code of conduct",
                                                               "Security policy", "Activity",
                                                               "Custom properties", "3.1k stars",
                                                               "40 watching", "212 forks",
                                                               "Report repository"]) +
        "<p><b>" + a("repo", "Releases 18", "releases") + "</b></p>" + a("repo", "v4.2.0 Latest", "release-v4-2-0") +
        a("repo", "+ 17 releases", "releases-more") +
        "<p><b>Contributors</b></p>" + "".join(a("repo", u, f"user-{u}") for u in FORUM_USERS[:20]) +
        a("repo", "+ 35 contributors", "contributors") +
        "<p><b>Languages</b></p>" + "".join(a("repo", x, f"lang-{slugify(x)}") for x in ["Python 91.2%", "C 6.1%", "Shell 1.9%", "Other 0.8%"]) +
        "</aside>"
    )
    footer = "<footer>" + "".join(
        a("repo", x) for x in ["Terms", "Privacy", "Security", "Status", "Docs", "Contact", "Manage cookies",
                               "Do not share my personal information", "Blog", "About", "Pricing",
                               "Training", "API", "Shop", "Careers"]
    ) + "</footer>"
    body = (header + repo_head + '<div class="wrap"><main>' + toolbar + "".join(rows) + readme + "</main>" +
            about + "</div>" + footer)
    return page("acme-org/acme-widgets: Talk to Acme measurement devices from Python", body)


# --- 8. gov: a public-service portal with a contact form at the bottom (step 1's gov.br) ----------

GOV_TOPICS = ["Documents", "Health", "Education", "Work", "Taxes", "Housing", "Transport",
              "Family", "Justice", "Environment", "Business", "Travel"]
GOV_ACTIONS = ["Apply for", "Renew", "Update", "Cancel", "Check the status of", "Book an appointment for"]
GOV_JS = """
document.getElementById('contact').addEventListener('submit', (ev) => {
  ev.preventDefault();
  const email = document.getElementById('email').value.trim().toLowerCase();
  const subject = document.getElementById('subject').value.trim().toLowerCase();
  document.getElementById('receipt').textContent = (email && subject)
    ? 'Message received. Protocol number: ' + m7code('gov-contact|' + email + '|' + subject)
    : 'Please fill in your email address and a subject.';
});
"""


def build_gov() -> str:
    header = (
        '<header class="top">' + a("gov", "gov.example", "home") +
        "".join(a("gov", x) for x in ["Ministries", "Services", "Participate", "Access to information",
                                       "Legislation", "Channels"]) +
        "<button>High contrast</button>"
        '<input type="search" placeholder="What are you looking for?" aria-label="What are you looking for?">'
        "<button>Search</button>" + a("gov", "Sign in with gov.example ID", "sign-in") + "</header>"
        '<div style="padding:4px 16px;font-size:13px">' +
        "".join(a("gov", x, f"skip-{slugify(x)}") + " " for x in ["Skip to content", "Skip to menu", "Skip to search", "Skip to footer"]) +
        "<button>A+</button> <button>A-</button></div>"
    )
    most = ["Renew your passport", "Get a criminal record certificate", "Check your tax refund",
            "Book a vaccination", "Replace an identity card", "Register a birth",
            "Apply for unemployment benefit", "Pay a traffic fine", "Get a driving licence",
            "Enrol in public school", "Request a pension statement", "Report a lost document"]
    grid = ('<h1>Services</h1><h2>Most searched services</h2><div class="grid">' +
            "".join(f'<div style="border:1px solid #ddd;padding:10px">{a("gov", x)}</div>' for x in most) + "</div>")
    topics = "<h2>All services by topic</h2>"
    for t, topic in enumerate(GOV_TOPICS):
        items = [f"{GOV_ACTIONS[(t + j) % len(GOV_ACTIONS)]} {topic.lower()} service {j + 1}" for j in range(6)]
        topics += f"<h3>{esc(topic)}</h3><ul>" + "".join(f"<li>{a('gov', x)}</li>" for x in items) + "</ul>"
    news = "<h2>News</h2><ul>" + "".join(
        f"<li>{a('gov', f'Notice {i + 1}: changes to {GOV_TOPICS[i % 12].lower()} services', f'news-{i}')}</li>"
        for i in range(15)
    ) + "</ul>"
    form = (
        "<h2>Contact us</h2><p>Send us a message and we will reply within ten working days.</p>"
        '<form id="contact"><p><input id="name" placeholder="Your name"></p>'
        '<p><input id="email" placeholder="Your email address"></p>'
        '<p><input id="subject" placeholder="Subject"></p>'
        '<p><textarea id="message" placeholder="Your message" rows="4" cols="60"></textarea></p>'
        '<p><button type="submit">Send message</button></p></form><p id="receipt" role="status"></p>'
    )
    footer = "<footer>" + "".join(
        a("gov", x) for x in ["About gov.example", "Ombudsman", "Transparency", "Open data", "Accessibility",
                              "Privacy", "Terms of use", "Cookies", "Press", "Careers in public service",
                              "Public tenders", "Budget", "Audits", "Complaints", "Freedom of information",
                              "Regional offices", "Embassies", "Emergency numbers", "Elections",
                              "Statistics", "Maps", "Weather service", "Civil defence", "Archives",
                              "National library", "Museums", "Parks", "Culture", "Sport", "Youth",
                              "Seniors", "Veterans", "Consumers", "Competition", "Patents",
                              "Standards", "Research", "Innovation", "Digital services", "Sitemap"]
    ) + "</footer>"
    body = header + "<main>" + grid + topics + news + form + "</main>" + footer
    return page("Services — gov.example", body, script=GOV_JS)


# --- write ------------------------------------------------------------------------------------------


def build() -> dict[str, str]:
    catalog = shop_catalog()
    pages = {
        "wiki/tidal-power.html": build_wiki(),
        "docs/widgets-core.html": build_docs(),
        "news/index.html": build_news(),
        "pkg/acme-widgets.html": build_pkg(),
        "forum/thread-4412.html": build_forum(),
        "repo/acme-widgets.html": build_repo(),
        "gov/services.html": build_gov(),
    }
    pages.update(build_shop(catalog))
    return pages


def manifest(pages: Iterable[tuple[str, str]]) -> dict[str, str]:
    return {path: hashlib.sha256(text.encode("utf-8")).hexdigest() for path, text in sorted(pages)}


def main() -> None:
    pages = build()
    for rel, text in pages.items():
        target = SITE / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))  # bytes: no newline translation on Windows
    (SITE / "MANIFEST.json").write_bytes((json.dumps(manifest(pages.items()), indent=2) + "\n").encode())
    for rel, text in sorted(pages.items()):
        print(f"{len(text):>8,} bytes  {rel}")


if __name__ == "__main__":
    main()
