"""Re-pin the three public catalogues: the one deliberate way `chimera/catalogs/pins.json` changes.

The app reads the plugin, skill and connector catalogues only at the commits this file names, and
refuses any byte whose sha256 is not the one recorded here (`chimera/catalogs/loader.py`). That is
what keeps a catalogue from changing under the owner: a push to one of the repositories changes
nothing in an installed Chimera until a release ships a new pin. Nothing at runtime writes this file.

So bumping is a maintainer's act, done in a pull request that a reviewer reads:

    chimera catalog pins --check                 # which repositories moved since the pin
    python scripts/bump_catalog_pins.py          # pin every catalogue at its current main
    python scripts/bump_catalog_pins.py --sha skills=<40-hex>   # or one catalogue at one commit
    python scripts/bump_catalog_pins.py --keep-shas             # same commits, dates refilled
    git diff chimera/catalogs/pins.json          # the commits and the files that changed

The script resolves each commit, lists the catalogue files at that commit (the same selection rule
for every bump, below), downloads each one from `raw.githubusercontent.com/<owner>/<repo>/<sha>/`
and records its sha256 over the exact bytes served. It goes through the same bounded, allowlisted
fetch the plugin installer uses (`chimera.skills.bundles._get`), so `GITHUB_TOKEN` helps here too.

It also records the dates the "New" and "Updated" sections read (plan M4), so the app never asks git
at runtime: the pinned commit's own date (`committed_at`), and per item the first and last commit
that touched the item's OWN content at the pinned commit (`history`: `skills/<id>`, a plugin's source
folder, `connectors/<id>.yaml`). Not the generated card: every metadata refresh in the catalogue
repository rewrites all the cards, and dating them would call every item "updated" each time. That
is one commits listing per item, so it goes through `gh api` (your own `gh auth login`) when `gh` is
installed, and through the anonymous fetch otherwise.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import urllib.parse
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PINS = ROOT / "chimera" / "catalogs" / "pins.json"
sys.path.insert(0, str(ROOT))

from chimera.catalogs.pins import MAX_CATALOG_FILE_BYTES  # noqa: E402
from chimera.skills import bundles  # noqa: E402

OWNER = "brcampidelli"
#: kind -> repository, and which files of it the app reads. The rule is the selection, not a list,
#: so a new skill or plugin in the repository is picked up by the next bump without editing this.
CATALOGS: dict[str, tuple[str, re.Pattern[str]]] = {
    "connectors": ("chimera-connectors", re.compile(r"^dist/catalog\.json$")),
    "skills": ("chimera-skills", re.compile(r"^catalog/[a-z0-9][a-z0-9-]*\.json$")),
    "plugins": (
        "chimera-plugins",
        re.compile(r"^(\.claude-plugin/marketplace\.json|catalog/[a-z0-9][a-z0-9-]*\.json)$"),
    ),
}
#: kind -> how an item's own content is named in its repository tree. Plugins are the exception:
#: their folder is the marketplace entry's `source`, read in `_content_paths`.
CONTENT: dict[str, re.Pattern[str]] = {
    "connectors": re.compile(r"^connectors/([a-z0-9][a-z0-9-]*)\.yaml$"),
    "skills": re.compile(r"^skills/([a-z0-9][a-z0-9-]*)$"),
}
MARKETPLACE = ".claude-plugin/marketplace.json"
_SHA = re.compile(r"^[0-9a-f]{40}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

#: `api.github.com` path (no host) -> its pages, parsed. Swapped in the tests.
ApiGet = Callable[[str], list[object]]
#: (repo, sha, path) -> the raw bytes of one file at that commit.
BodyGet = Callable[[str, str, str], bytes]
Tree = list[dict[str, object]]


def _json(url: str) -> object:
    return json.loads(bundles._get(url).decode("utf-8"))


def _api_pages(path: str) -> list[object]:
    """Every page of an `api.github.com` read, parsed (one element per page)."""
    if shutil.which("gh"):
        out = subprocess.run(  # noqa: S603 -- fixed argv; `path` is built in this file
            ["gh", "api", "--paginate", "--slurp", path],
            check=True, capture_output=True, text=True, encoding="utf-8",
        ).stdout
        pages = json.loads(out)
        return pages if isinstance(pages, list) else [pages]
    pages = []
    for page in range(1, 50):
        sep = "&" if "?" in path else "?"
        data = _json(f"https://api.github.com/{path}{sep}page={page}")
        pages.append(data)
        if not isinstance(data, list) or len(data) < 100:
            break
    return pages


def _date_of(row: object) -> str | None:
    if not isinstance(row, dict):
        return None
    commit = row.get("commit")
    committer = commit.get("committer") if isinstance(commit, dict) else None
    date = committer.get("date") if isinstance(committer, dict) else None
    return date if isinstance(date, str) and _DATE.match(date) else None


def _commit_dates(api: ApiGet, repo: str, sha: str, path: str) -> tuple[str, str] | None:
    """(first, last) committer date of the commits touching `path` at `sha`, or None if none do."""
    quoted = urllib.parse.quote(path)
    dates: list[str] = []
    for page in api(f"repos/{OWNER}/{repo}/commits?sha={sha}&path={quoted}&per_page=100"):
        for row in page if isinstance(page, list) else []:
            date = _date_of(row)
            if date:
                dates.append(date)
    if not dates:
        return None
    # The listing is newest first; min/max makes that an assumption this does not need.
    return min(dates), max(dates)


def _committed_at(api: ApiGet, repo: str, sha: str) -> str:
    pages = api(f"repos/{OWNER}/{repo}/commits/{sha}")
    date = _date_of(pages[0] if pages else None)
    if date is None:
        raise SystemExit(f"{repo}@{sha[:12]}: could not read the commit date")
    return date


def _content_paths(kind: str, tree: Tree, marketplace: bytes | None) -> dict[str, str]:
    """item id -> the path of the item's own content at the pinned commit."""
    out: dict[str, str] = {}
    if kind == "plugins":
        data = json.loads(marketplace.decode("utf-8-sig")) if marketplace else {}
        entries = data.get("plugins", []) if isinstance(data, dict) else []
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            name, source = entry.get("name"), entry.get("source")
            if isinstance(name, str) and isinstance(source, str) and source.startswith("./"):
                out[name] = source[2:].rstrip("/")
        return out
    rule = CONTENT[kind]
    for item in tree:
        match = rule.match(str(item.get("path")))
        if match:
            out[match.group(1)] = str(item.get("path"))
    return out


def _history(api: ApiGet, kind: str, repo: str, sha: str, tree: Tree,
             marketplace: bytes | None) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item, path in sorted(_content_paths(kind, tree, marketplace).items()):
        dates = _commit_dates(api, repo, sha, path)
        if dates is None:
            print(f"  {kind}/{item}: no commit touches {path} at {sha[:12]}; no dates recorded")
            continue
        rows.append({"item": item, "path": path, "added_at": dates[0], "updated_at": dates[1]})
    return rows


def _head(repo: str) -> str:
    data = _json(f"https://api.github.com/repos/{OWNER}/{repo}/commits/main")
    sha = str(data.get("sha", "")) if isinstance(data, dict) else ""
    if not _SHA.match(sha):
        raise SystemExit(f"{repo}: could not read the commit of main")
    return sha


def _tree(repo: str, sha: str) -> Tree:
    data = _json(f"https://api.github.com/repos/{OWNER}/{repo}/git/trees/{sha}?recursive=1")
    if not isinstance(data, dict) or data.get("truncated"):
        raise SystemExit(f"{repo}@{sha[:12]}: the tree listing is missing or truncated")
    return [item for item in data.get("tree", []) if isinstance(item, dict)]


def _files(repo: str, sha: str, rule: re.Pattern[str], tree: Tree) -> list[str]:
    paths = [
        str(item.get("path"))
        for item in tree
        if item.get("type") == "blob" and rule.match(str(item.get("path")))
    ]
    if not paths:
        raise SystemExit(f"{repo}@{sha[:12]}: no catalogue file matches the rule")
    return sorted(paths)


def _body(repo: str, sha: str, path: str) -> bytes:
    quoted = urllib.parse.quote(path)
    return bundles._get(
        f"https://raw.githubusercontent.com/{OWNER}/{repo}/{sha}/{quoted}",
        accept="*/*",
        limit=MAX_CATALOG_FILE_BYTES,
    )


def pin_one(kind: str, repo: str, rule: re.Pattern[str], sha: str, *, api: ApiGet,
            body: BodyGet, tree: Tree | None = None) -> dict[str, object]:
    """One catalogue's pin entry at `sha`: the files and their sha256, the commit date, the history.

    Every value sits on a line of its own when written, so nothing shares a line with a file name.
    """
    listing = tree if tree is not None else _tree(repo, sha)
    bodies = {path: body(repo, sha, path) for path in _files(repo, sha, rule, listing)}
    history = _history(api, kind, repo, sha, listing, bodies.get(MARKETPLACE))
    print(f"{kind}: {OWNER}/{repo}@{sha[:12]}: {len(bodies)} file(s), {len(history)} dated item(s)")
    return {
        "owner": OWNER,
        "repo": repo,
        "sha": sha,
        "committed_at": _committed_at(api, repo, sha),
        "files": [{"path": p, "sha256": hashlib.sha256(b).hexdigest()} for p, b in bodies.items()],
        "history": history,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--sha", action="append", default=[], metavar="KIND=SHA",
                        help="pin this catalogue at this commit instead of main (repeatable)")
    parser.add_argument("--only", action="append", default=[], choices=sorted(CATALOGS),
                        help="re-pin only this catalogue and keep the others as they are")
    parser.add_argument("--keep-shas", action="store_true",
                        help="keep every pinned commit; re-verify its files and refill the dates")
    args = parser.parse_args(argv)

    chosen: dict[str, str] = {}
    for item in args.sha:
        kind, _, sha = item.partition("=")
        if kind not in CATALOGS or not _SHA.match(sha):
            parser.error(f"--sha takes KIND=<40 lowercase hex>, KIND in {sorted(CATALOGS)}")
        chosen[kind] = sha

    current = json.loads(PINS.read_text(encoding="utf-8")) if PINS.exists() else {}
    catalogs = dict(current.get("catalogs", {}))
    for kind, (repo, rule) in CATALOGS.items():
        if args.only and kind not in args.only:
            continue
        kept = catalogs.get(kind, {}).get("sha") if args.keep_shas else None
        sha = chosen.get(kind) or kept or _head(repo)
        catalogs[kind] = pin_one(kind, repo, rule, sha, api=_api_pages, body=_body)

    out = {"format": 1, "catalogs": {k: catalogs[k] for k in CATALOGS if k in catalogs}}
    PINS.write_text(json.dumps(out, indent=2, sort_keys=False) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {PINS.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
