"""Bundle the built desktop SPA and the curated skill library into the wheel.

`apps/desktop/dist` is a build artifact: gitignored, and absent from every fresh clone. A static
`[tool.hatch.build.targets.wheel.force-include]` pointing at it therefore made the build backend
raise `FileNotFoundError: Forced include not found` for anyone building from source — which meant
`pip install -e .`, `uv sync`, and every CI job died before a single test ran.

Declaring the include here instead makes it conditional: present when the SPA has been built (the
release path — `publish.yml` runs `npm ci && npm run build` and asserts `dist/index.html` exists
before `uv build`), skipped when it has not (contributor checkouts and CI). A wheel built without
it is still correct: `chimera app`/`serve --ui` falls back to a source-checkout dist and then to
API-only when neither is there.

`skills/` rides along for a different reason. It is tracked, so it is never missing and a static
table would have been safe — but `packages = ["chimera"]` means only `chimera/` reaches the wheel,
and the library sat one level above it. An installed user therefore had no curated cards at all:
`chimera skills-import skills/<name>`, the one line the README gives for using them, names a path
that exists only in a checkout.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):  # type: ignore[type-arg]
    """Map `apps/desktop/dist` and `skills/` into the package as `chimera/_*`."""

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        dist = Path(self.root) / "apps" / "desktop" / "dist"
        # `index.html` is the entrypoint the CLI probes for, so an empty or half-written directory
        # must not count as a build — otherwise we would ship a UI that cannot load.
        if (dist / "index.html").is_file():
            build_data.setdefault("force_include", {})[str(dist)] = "chimera/_desktop_dist"
        skills = Path(self.root) / "skills"
        if skills.is_dir():
            build_data.setdefault("force_include", {})[str(skills)] = "chimera/_skill_library"
