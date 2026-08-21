"""Regenerate ``chimera/skills/catalog.json`` from the upstream skills repository.

The catalogue is *derived*, not typed. Every field in it — the description, the licence, the
operating systems, the commands a skill needs before its first line does anything — is read out of
the skill's own YAML frontmatter and written to a file that is committed and reviewable in a diff.
A hand-written catalogue would be seventy claims nobody could check; this one can be re-derived by
anyone with a network connection, and when it disagrees with the source the source wins.

Run it when you want to pick up new skills upstream:

    gh auth status && python scripts/refresh_skill_catalog.py

``gh`` is used deliberately rather than an anonymous fetch: the anonymous GitHub API allows sixty
requests an hour and this makes about ninety.

**Pinned to one tree, on purpose.** ``skills/**`` in ``NousResearch/hermes-agent``, which is MIT
throughout, and nothing else. The upstream repository also carries an ``index-cache/`` that indexes
other publishers' catalogues — including ``anthropics/skills``, whose README says in as many words
that its ``docx``, ``pdf``, ``pptx`` and ``xlsx`` skills are *source-available, not open source*.
Following the index would quietly walk material under those terms into an Apache-2.0 project, and
the names collide with the MIT ones, so it would not even look wrong. ``optional-skills/`` is out
for the same reason: its licences have not been read.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml

# Run from the repository root, so the package next to this script is importable: the classifier
# asks the real alias table which names we answer to, rather than keeping a second copy of it here.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REPO = "NousResearch/hermes-agent"
REF = "main"
ROOT = "skills/"
OUT = Path("chimera/skills/catalog.json")

#: Directories under `skills/` that hold something other than installable skills.
_EXCLUDE = ("skills/index-cache/",)

#: A skill that needs a server or a desktop application running beside it. The frontmatter cannot
#: say this — `prerequisites.commands` names binaries to install, and "ComfyUI must already be
#: running on :8188" is not a binary. Curated, and short: every name here was decided by reading
#: that skill's own instructions, and the reason travels with it into the catalogue.
NEEDS_SERVICE: dict[str, str] = {
    "comfyui": "a ComfyUI server",
    "touchdesigner-mcp": "TouchDesigner with its MCP server",
    "computer-use": "the cua-driver desktop driver",
    "dogfood": "a browser toolset the agent provides",
    "openhue": "a Philips Hue bridge on the network",
    "serving-llms-vllm": "a GPU and the model weights",
    "llama-cpp": "the model weights and enough RAM",
}

#: Skills that need real hardware or a multi-gigabyte install before their first line does anything
#: — a GPU, model weights, a full LaTeX distribution. Separate from NEEDS_SETUP because "pip install
#: this" and "have 24GB of VRAM" are not the same sentence to a person deciding whether to click.
NEEDS_HEAVY: dict[str, str] = {
    "manim-video": "a full LaTeX distribution + ffmpeg (several GB)",
    "comfyui": "a GPU and multi-GB checkpoints",
    "llama-cpp": "compiled binaries and GGUF weights",
    "blogwatcher": "its compiled Go binary",
    "evaluating-llms-harness": "CUDA and ~16GB VRAM for a 7B model",
    "serving-llms-vllm": "a datacentre GPU (24-80GB VRAM)",
    "research-paper-writing": "full LaTeX, 8 Python packages and an Exa MCP server",
}

#: Skills whose frontmatter under-declares what they need. `ascii-art` ships `dependencies: []` and
#: its body asks for six binaries. This is why the catalogue says requirements are DECLARED ones:
#: the metadata is the author's summary, not an inventory, and a catalogue that presented it as an
#: inventory would be passing on a claim it had not checked as though it had.
UNDERSTATED: dict[str, list[str]] = {
    "ascii-art": ["pyfiglet", "cowsay", "boxes", "toilet", "ascii-image-converter", "jp2a", "curl"],
    # Its text says only "requires an API key"; what it pulls is google-genai, so the key is a
    # Gemini one, and it also wants Poppler and Tesseract.
    "nano-pdf": ["nano-pdf", "a Gemini API key", "poppler", "tesseract"],
    "ocr-and-documents": ["pymupdf", "marker-pdf (optional, ~2.5GB of models)"],
    "google-workspace": ["google-api-python-client", "an OAuth client from Google Cloud Console"],
    "box": ["@box/cli (node)", "interactive browser OAuth"],
    "notion": ["a NOTION_API_KEY"],
    "airtable": ["an AIRTABLE_API_KEY scoped to the base"],
    "huggingface-hub": ["the hf CLI", "HF_TOKEN"],
    "weights-and-biases": ["wandb", "WANDB_API_KEY"],
    "gif-search": ["curl", "jq", "a free TENOR_API_KEY"],
    "xurl": ["the xurl binary", "an X developer account"],
    "ascii-video": ["ffmpeg", "numpy", "scipy", "pillow"],
    "blogwatcher": ["blogwatcher-cli"],
    "songsee": ["songsee (go install)"],
    "youtube-content": ["youtube-transcript-api"],
    "excalidraw": ["cryptography (only to upload)"],
    "design-md": ["node", "npx"],
    "p5js": ["node + puppeteer + ffmpeg (only to export video)"],
}

#: Skills whose instructions are written against the upstream agent's own runtime, so the procedure
#: reads correctly but the interface it talks to is not ours. Recorded per skill with what it wants.
NEEDS_ADAPTATION: dict[str, str] = {
    # CURATED, from reading the bodies — and it stays curated after an attempt to derive it from
    # a scan of tool calls, which is worth writing down because the attempt failed instructively.
    #
    # The scan moved this from 20 skills to 34, in the wrong direction and for a bad reason: a
    # MENTION is not a DEPENDENCY. `test-driven-development` calls `delegate_task` once, inside a
    # paragraph beginning "When dispatching subagents for implementation" — an optional escalation
    # in a skill that is otherwise pure methodology. Counting that as a blocker marks a perfectly
    # usable skill unusable. Nor could the authors' own frontmatter settle it: exactly three of the
    # eighty-two declare `requires_toolsets`, and two of those declare `terminal`.
    #
    # Any threshold that separates "load-bearing" from "optional" by counting occurrences is a
    # guess wearing a number. So the rating stays a judgement made by reading, and is labelled as
    # one; the measured vocabulary rides alongside as `uses` and `missing`, labelled as measured.
    "sdlc-review": "the upstream Kanban toolset",
    "merge-reconciler": "the upstream Kanban CLI",
    "simplify-code": "four parallel subagents",
    "requesting-code-review": "a separate subagent for the reviewer",
    "inspecting-hermes-desktop-dom": "the upstream project's own desktop app",
    "hermes-agent": "the upstream agent itself",
    "hermes-agent-skill-authoring": "the upstream repo's own conventions",
    "session-librarian": "the upstream agent's session store",
    "teams-meeting-pipeline": "the upstream agent's own CLI",
    "product-price-monitor": "the upstream agent's cron tool",
    "competitor-news-monitor": "the upstream agent's cron tool",
    "ocr-and-documents": "the upstream page-extraction and vision tools",
    "grounded-citations": "the upstream agent's home directory",
    "llm-wiki": "the upstream agent's home directory",
    "architecture-diagram": "the upstream agent's own preview tool",
    "popular-web-designs": "the upstream agent's browser and screenshot tools",
    "sketch": "the upstream agent's browser and screenshot tools",
    "baoyu-infographic": "an image-generation tool the agent must provide",
    "findmy": "a vision tool the agent must provide",
}


def _token() -> str:
    """A GitHub token, from the environment or from ``gh`` if it is installed and logged in.

    Not ``gh api`` directly: ``gh`` keeps its token in the OS keyring, and a Python that cannot
    reach the keyring — the Windows Store build runs in an app container that cannot — is told it
    is not logged in by a CLI that plainly is. Asking ``gh`` for the token once and then speaking
    HTTP ourselves works everywhere, and drops the dependency for anyone who would rather export
    a token instead.
    """
    for var in ("GH_TOKEN", "GITHUB_TOKEN"):
        value = os.environ.get(var, "").strip()
        if value:
            return value
    try:
        out = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, check=False, encoding="utf-8"
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except FileNotFoundError:
        pass
    return ""


_TOKEN = ""


def gh(path: str) -> Any:
    """One GitHub API call, authenticated when a token is available.

    This makes about ninety calls and the anonymous ceiling is sixty an hour, so running it
    without a token gets you a partial catalogue and a confusing error rather than a refusal.
    """
    request = urllib.request.Request(  # noqa: S310 -- a fixed https api.github.com URL
        f"https://api.github.com/{path.lstrip('/')}",
        headers={
            "User-Agent": "chimera-catalog-refresh",
            "Accept": "application/vnd.github+json",
            **({"Authorization": f"Bearer {_TOKEN}"} if _TOKEN else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 -- as above
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403, 429):
            raise SystemExit(
                "GitHub refused the request"
                + ("" if _TOKEN else " and no token was found — run `gh auth login` or export GH_TOKEN")
            ) from exc
        raise SystemExit(f"GET {path} answered {exc.code}") from exc


def skill_paths() -> list[str]:
    """Every SKILL.md under `skills/`, found by looking rather than by assuming a depth.

    The tree is not uniform: `skills/mlops/` nests one level deeper than the rest
    (`skills/mlops/inference/llama-cpp/`), so anything that walks a fixed two levels silently drops
    four skills and picks up a directory that holds none.
    """
    tree = gh(f"repos/{REPO}/git/trees/{REF}?recursive=1")
    if tree.get("truncated"):
        raise SystemExit("the tree came back truncated — this needs paging before it can be trusted")
    return sorted(
        item["path"]
        for item in tree["tree"]
        if item["path"].startswith(ROOT)
        and item["path"].endswith("/SKILL.md")
        and not item["path"].startswith(_EXCLUDE)
    )


def skill_md(path: str) -> tuple[dict[str, Any], str]:
    """One SKILL.md as (frontmatter, whole text), fetched from raw rather than from the API.

    Eighty-two contents-API calls trip GitHub's secondary limit even well inside the hourly one —
    it is the rate, not the count. The raw host is not metered the same way, so the whole run costs
    a single API call for the tree and eighty-two ordinary file fetches.
    """
    url = f"https://raw.githubusercontent.com/{REPO}/{REF}/{path}"
    request = urllib.request.Request(url, headers={"User-Agent": "chimera-catalog-refresh"})  # noqa: S310 -- fixed https host
    # Eighty-two fetches in a row trips a rate limit that is about the RATE, not the count, and a
    # maintenance script that hammers somebody else's host and then reports "the source is broken"
    # is blaming the wrong party. Pace, and back off when asked to.
    text = ""
    for attempt in range(5):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 -- as above
                text = response.read().decode("utf-8", errors="replace")
            break
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 403) or attempt == 4:
                raise SystemExit(f"GET {path} answered {exc.code}") from exc
            wait = 5 * (attempt + 1)
            print(f"  {exc.code} on {path} — waiting {wait}s", file=sys.stderr)
            time.sleep(wait)
    time.sleep(0.15)
    if not text.startswith("---"):
        return {}, text
    _, _, rest = text.partition("---")
    block, _, _ = rest.partition("\n---")
    try:
        parsed = yaml.safe_load(block)
    except yaml.YAMLError:
        return {}, text
    return (parsed if isinstance(parsed, dict) else {}), text


def classify(name: str, meta: dict[str, Any], body: str) -> tuple[str, list[str], str, list[str], list[str]]:
    """(portability, requires, note, uses, missing) — derived, with two curated corrections.

    The verdict comes from the skill's own text: which tool names it calls, and which of those
    this agent can answer to. That replaced a hand-written list, and the difference is not tidiness
    — a hand list is a set of claims nobody can re-check, and it was wrong in both directions here
    (it marked skills unusable whose only problem was that `web_extract` is called `scrape`, and it
    missed ones that quietly need a capability we do not have).

    Order matters: the rating shown is whatever stops a person first. A missing capability beats
    needing a server, which beats needing an operating system we might be on, which beats a package.
    """
    from chimera.skills.aliases import foreign_names, missing_names, translated_names

    platforms = meta.get("platforms") or []
    commands = ((meta.get("prerequisites") or {}).get("commands")) or []
    requires = [str(c) for c in commands if c]
    for extra in UNDERSTATED.get(name, []):
        if extra not in requires:
            requires.append(extra)

    # Measured, and kept apart from the verdict: `uses` is what we can answer to under another
    # name, `missing` is what nothing here provides. Both are facts about the text. Whether a
    # mention blocks the skill is not, and that is the line this function refuses to blur.
    vocabulary = foreign_names(body)
    uses, missing = translated_names(vocabulary), missing_names(vocabulary)

    if name in NEEDS_ADAPTATION:
        return "needs_adaptation", requires, NEEDS_ADAPTATION[name], uses, missing
    if name in NEEDS_HEAVY:
        return "needs_heavy", requires, NEEDS_HEAVY[name], uses, missing
    if name in NEEDS_SERVICE:
        return "needs_service", requires, NEEDS_SERVICE[name], uses, missing
    if platforms and set(platforms) != {"linux", "macos", "windows"}:
        # The frontmatter's tokens are lowercase identifiers; these go into a sentence a person
        # reads, and "macos" in one is a typo they have no way to know we did not make.
        pretty = {"macos": "macOS", "linux": "Linux", "windows": "Windows"}
        return "os_locked", requires, ", ".join(pretty.get(p, p) for p in sorted(platforms)), uses, missing
    if requires:
        return "needs_setup", requires, "", uses, missing
    return "native", requires, "", uses, missing


def main() -> None:
    global _TOKEN
    _TOKEN = _token()
    print('token:', 'yes' if _TOKEN else 'NO (anonymous, 60/hour)', file=sys.stderr)
    paths = skill_paths()
    print(f"{len(paths)} SKILL.md under {ROOT} in {REPO}", file=sys.stderr)

    entries = []
    licences: dict[str, int] = {}
    for i, path in enumerate(paths, 1):
        meta, body = skill_md(path)
        name = str(meta.get("name") or Path(path).parent.name)
        directory = str(Path(path).parent).replace("\\", "/")
        hermes = (meta.get("metadata") or {}).get("hermes") or {}
        licence = str(meta.get("license") or "")
        licences[licence or "(none)"] = licences.get(licence or "(none)", 0) + 1
        portability, requires, note, uses, missing = classify(name, meta, body)
        entries.append(
            {
                "name": name,
                "description": str(meta.get("description") or "").strip(),
                "repo": REPO,
                "path": directory,
                "license": licence,
                "portability": portability,
                "ref": REF,
                "requires": requires,
                # The category directory, which is the grouping the upstream authors chose and the
                # one a person browsing will recognise.
                "topic": directory.split("/")[1] if directory.count("/") >= 2 else "",
                "note": note,
                "author": str(meta.get("author") or ""),
                "tags": [str(t) for t in (hermes.get("tags") or [])],
                # What it calls that we answer to under another name, and what it calls that
                # nothing here provides. Shown before the install, not discovered after it.
                "uses": uses,
                "missing": missing,
            }
        )
        if i % 20 == 0:
            print(f"  {i}/{len(paths)}", file=sys.stderr)

    OUT.write_text(
        json.dumps(
            {
                "source": {"repo": REPO, "ref": REF, "root": ROOT},
                "generated_by": "scripts/refresh_skill_catalog.py",
                "skills": entries,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"\nwrote {OUT} — {len(entries)} skills", file=sys.stderr)
    print(f"licences: {licences}", file=sys.stderr)
    by_port: dict[str, int] = {}
    for entry in entries:
        by_port[entry["portability"]] = by_port.get(entry["portability"], 0) + 1
    print(f"portability: {by_port}", file=sys.stderr)


if __name__ == "__main__":
    main()
