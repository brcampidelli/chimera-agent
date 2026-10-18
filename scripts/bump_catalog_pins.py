"""Re-pin the three public catalogues: the one deliberate way `chimera/catalogs/pins.json` changes.

The app reads the plugin, skill and connector catalogues only at the commits this file names, and
refuses any byte whose sha256 is not the one recorded here (`chimera/catalogs/loader.py`). That is
what keeps a catalogue from changing under the owner: a push to one of the repositories changes
nothing in an installed Chimera until a release ships a new pin. Nothing at runtime writes this file.

So bumping is a maintainer's act, done in a pull request that a reviewer reads:

    chimera catalog pins --check                 # which repositories moved since the pin
    python scripts/bump_catalog_pins.py          # pin every catalogue at its current main
    python scripts/bump_catalog_pins.py --sha skills=<40-hex>   # or one catalogue at one commit
    git diff chimera/catalogs/pins.json          # the commits and the files that changed

The script resolves each commit, lists the catalogue files at that commit (the same selection rule
for every bump, below), downloads each one from `raw.githubusercontent.com/<owner>/<repo>/<sha>/`
and records its sha256 over the exact bytes served. It goes through the same bounded, allowlisted
fetch the plugin installer uses (`chimera.skills.bundles._get`), so `GITHUB_TOKEN` helps here too.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.parse
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
_SHA = re.compile(r"^[0-9a-f]{40}$")


def _json(url: str) -> object:
    return json.loads(bundles._get(url).decode("utf-8"))


def _head(repo: str) -> str:
    data = _json(f"https://api.github.com/repos/{OWNER}/{repo}/commits/main")
    sha = str(data.get("sha", "")) if isinstance(data, dict) else ""
    if not _SHA.match(sha):
        raise SystemExit(f"{repo}: could not read the commit of main")
    return sha


def _files(repo: str, sha: str, rule: re.Pattern[str]) -> list[str]:
    data = _json(f"https://api.github.com/repos/{OWNER}/{repo}/git/trees/{sha}?recursive=1")
    if not isinstance(data, dict) or data.get("truncated"):
        raise SystemExit(f"{repo}@{sha[:12]}: the tree listing is missing or truncated")
    paths = [
        str(item.get("path"))
        for item in data.get("tree", [])
        if isinstance(item, dict) and item.get("type") == "blob" and rule.match(str(item.get("path")))
    ]
    if not paths:
        raise SystemExit(f"{repo}@{sha[:12]}: no catalogue file matches the rule")
    return sorted(paths)


def _digest(repo: str, sha: str, path: str) -> str:
    quoted = urllib.parse.quote(path)
    body = bundles._get(
        f"https://raw.githubusercontent.com/{OWNER}/{repo}/{sha}/{quoted}",
        accept="*/*",
        limit=MAX_CATALOG_FILE_BYTES,
    )
    return hashlib.sha256(body).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--sha", action="append", default=[], metavar="KIND=SHA",
                        help="pin this catalogue at this commit instead of main (repeatable)")
    parser.add_argument("--only", action="append", default=[], choices=sorted(CATALOGS),
                        help="re-pin only this catalogue and keep the others as they are")
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
        sha = chosen.get(kind) or _head(repo)
        files = {path: _digest(repo, sha, path) for path in _files(repo, sha, rule)}
        catalogs[kind] = {"owner": OWNER, "repo": repo, "sha": sha, "files": files}
        print(f"{kind}: {OWNER}/{repo}@{sha[:12]} — {len(files)} file(s)")

    out = {"format": 1, "catalogs": {k: catalogs[k] for k in CATALOGS if k in catalogs}}
    PINS.write_text(json.dumps(out, indent=2, sort_keys=False) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {PINS.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
