"""Block fixable CVEs while still reporting advisories without a published fix."""

from __future__ import annotations

import json
import subprocess
import sys


def evaluate(report: object, status: int) -> int:
    """Scanner failures and incomplete reports must never become a clean audit."""
    if status not in (0, 1):
        raise ValueError(f"pip-audit failed with exit status {status}")
    if not isinstance(report, dict) or not isinstance(report.get("dependencies"), list):
        raise ValueError("pip-audit did not return a dependency report")
    dependencies = report["dependencies"]
    if not dependencies:
        raise ValueError("pip-audit audited no dependencies")

    found = 0
    fixable = 0
    for dependency in dependencies:
        if not isinstance(dependency, dict) or "skip_reason" in dependency:
            raise ValueError(f"pip-audit skipped or malformed a dependency: {dependency!r}")
        name, version, vulns = (
            dependency.get("name"), dependency.get("version"), dependency.get("vulns")
        )
        if not isinstance(name, str) or not isinstance(version, str) or not isinstance(vulns, list):
            raise ValueError(f"pip-audit returned an incomplete dependency: {dependency!r}")
        for vuln in vulns:
            if not isinstance(vuln, dict) or not isinstance(vuln.get("id"), str):
                raise ValueError(f"pip-audit returned an invalid vulnerability: {vuln!r}")
            fixes = vuln.get("fix_versions")
            if not isinstance(fixes, list) or not all(isinstance(fix, str) and fix for fix in fixes):
                raise ValueError(f"pip-audit returned invalid fix versions: {vuln!r}")
            found += 1
            if fixes:
                fixable += 1
                print(f"BLOCK: {name}=={version} {vuln['id']} — fixed in {', '.join(fixes)}")
            else:
                print(f"ADVISORY: {name}=={version} {vuln['id']} — no published fix")

    if bool(found) != (status == 1):
        raise ValueError("pip-audit exit status disagrees with its vulnerability report")
    print(f"Python audit: {fixable} fixable, {found - fixable} without a published fix")
    return int(fixable > 0)


def main(requirements: str) -> int:
    # The export is already fully pinned. Resolving it again would audit a different tree and
    # install GPU extras merely to inspect their metadata. pip-audit only needs the advisory API.
    result = subprocess.run(
        [
            "uvx", "pip-audit", "--requirement", requirements, "--strict",
            "--no-deps", "--disable-pip", "--format", "json", "--progress-spinner", "off",
        ],
        capture_output=True, text=True, check=False,
    )
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    try:
        return evaluate(json.loads(result.stdout), result.returncode)
    except ValueError as exc:
        print(f"Python audit failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
