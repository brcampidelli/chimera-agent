"""One solve: one SWE-bench instance, one arm, one fresh sanitized checkout. Run by `run.py` as a
subprocess so a wall-clock timeout can kill it; never run by hand in a paid session.

    python solve_one.py <instance.json> <arm> <out.json>

Writes one JSON object to <out.json>: the patch (`git diff base_commit`, tracked files only — the
same extraction as `bench/swe_bench/run_swe.py`), the loop's own counters, every model call's usage
and served provider, the shell commands the agent ran, and the hash of the system message it sent.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))

from arms import ARMS, MODEL, PROVIDER, assert_frozen  # noqa: E402

REF = Path(os.environ["H45_DJANGO_REF"])
WORK = Path(os.environ["H45_WORK"])
# `rm -rf` runs under WORK: an empty or shallow path must never reach it (an empty variable in a
# delete path once removed a real project).
if not WORK.is_absolute() or len(WORK.parts) < 4 or "h45" not in WORK.name:
    raise SystemExit(f"refusing H45_WORK={WORK!s}")
MAX_STEPS = 30  # the budget the closed SWE-bench phase settled on (run 1's 8 starved the agent)

#: The task text of `bench/swe_bench/run_swe.py`, byte for byte, so the instrument is the one whose
#: resolve band is on record. Its "Do NOT" is in the user turn and identical in every arm.
INSTRUCTION = (
    "You are working in the checked-out django repository at a specific commit. Resolve this issue by "
    "editing the code so the project's tests pass. Do NOT edit any test files.\n\nIssue:\n{problem}"
)

#: The coding tools only. The web tools (http_get, scrape, crawl, web_search, ...) are left out so
#: the one route to the network that remains is run_shell, which the reader scans (PROTOCOL §1).
CODING_TOOLS = (
    "read_file", "write_file", "edit_file", "apply_patch", "list_dir", "grep", "glob",
    "run_shell", "job_status", "job_cancel", "todo_write",
)


def _run(cmd: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout, check=False)


def template_path(inst: dict[str, Any]) -> Path:
    return WORK / "templates" / inst["instance_id"]


def assert_no_future(repo: Path, base_commit: str) -> None:
    """The git half of the wall (PROTOCOL §1), probed on every workspace: no ref may reach a commit
    that is not an ancestor of base_commit."""
    future = _run(["git", "-C", str(repo), "rev-list", "--all", "--count", f"^{base_commit}"]).stdout.strip()
    if future != "0":
        raise RuntimeError(f"SANITIZE FAILED: {future!r} commits after base_commit reachable in {repo}")


def build_template(inst: dict[str, Any]) -> Path:
    """The anti-leakage recipe of `bench/swe_bench/run_swe.py`, once per item: clone without tags
    from the local reference, reset to base_commit, drop the remote, delete tags, expire and gc.
    Every arm of the item then starts from a byte copy of it, so the gc runs once, not three times."""
    tpl = template_path(inst)
    if (tpl / ".git").exists():
        return tpl
    tmp = tpl.with_name(tpl.name + ".building")
    if tmp.exists():
        _run(["rm", "-rf", str(tmp)])
    tmp.parent.mkdir(parents=True, exist_ok=True)
    for cmd in (
        ["git", "clone", "--no-hardlinks", "--single-branch", "--no-tags", "-q", str(REF), str(tmp)],
        ["git", "-C", str(tmp), "reset", "-q", "--hard", inst["base_commit"]],
        ["git", "-C", str(tmp), "remote", "remove", "origin"],
    ):
        r = _run(cmd)
        if r.returncode != 0:
            raise RuntimeError(f"template step {cmd[1:4]} failed: {r.stderr[-300:]}")
    tags = _run(["git", "-C", str(tmp), "tag"]).stdout.split()
    if tags:
        _run(["git", "-C", str(tmp), "tag", "-d", *tags])
    _run(["git", "-C", str(tmp), "reflog", "expire", "--expire=now", "--all"])
    _run(["git", "-C", str(tmp), "gc", "-q", "--prune=now"], timeout=1200)
    assert_no_future(tmp, inst["base_commit"])
    tmp.rename(tpl)
    return tpl


def prepare_workspace(inst: dict[str, Any], arm: str) -> Path:
    """A fresh byte copy of the item's sanitized template, checked again for future commits."""
    tpl = build_template(inst)
    ws = WORK / f"{arm}__{inst['instance_id']}"
    if ws.exists():
        _run(["rm", "-rf", str(ws)])
    r = _run(["cp", "-a", str(tpl), str(ws)])
    if r.returncode != 0:
        raise RuntimeError(f"workspace copy failed: {r.stderr[-300:]}")
    status = _run(["git", "-C", str(ws), "status", "--porcelain"]).stdout.strip()
    if status:
        raise RuntimeError(f"template not clean: {status[:200]}")
    assert_no_future(ws, inst["base_commit"])
    return ws


class Recorder:
    """The gateway pinned to one provider with no fallbacks, recording every call's usage.

    The pin is the pattern of `bench/directive_boundary`. `top_p` is sent only for an arm that
    registers one (C); A and B send none, as production does."""

    def __init__(self, top_p: float | None) -> None:
        from chimera.providers import LLMGateway

        self.gateway = LLMGateway()
        if self.gateway.settings.fallback_models:
            raise SystemExit(f"fallback models configured: {self.gateway.settings.fallback_models}")
        self.top_p = top_p
        self.calls: list[dict[str, Any]] = []

    def complete(self, messages: Any, **kwargs: Any) -> Any:
        kwargs["extra_body"] = {"provider": {"order": [PROVIDER], "allow_fallbacks": False}}
        if self.top_p is not None:
            kwargs["top_p"] = self.top_p
        started = time.monotonic()
        result = self.gateway.complete(messages, **kwargs)
        self.calls.append({
            "prompt": result.prompt_tokens, "completion": result.completion_tokens,
            "cache_read": result.cache_read_tokens, "provider": result.provider,
            "finish": result.finish_reason, "temperature": kwargs.get("temperature"),
            "top_p": kwargs.get("top_p"), "seconds": round(time.monotonic() - started, 2),
        })
        return result


def shell_commands(transcript: list[Any]) -> list[str]:
    """Every run_shell command the agent issued, from its own transcript."""
    out: list[str] = []
    for msg in transcript:
        if not isinstance(msg, dict):
            msg = getattr(msg, "as_dict", lambda: {})()
        for call in msg.get("tool_calls") or []:
            fn = call.get("function") or {}
            if fn.get("name") != "run_shell":
                continue
            try:
                out.append(str(json.loads(fn.get("arguments") or "{}").get("command", ""))[:500])
            except (ValueError, TypeError):
                out.append(str(fn.get("arguments"))[:500])
    return out


def main() -> None:
    inst_path, arm_name, out_path = sys.argv[1], sys.argv[2], Path(sys.argv[3])
    assert_frozen()
    inst = json.loads(Path(inst_path).read_text(encoding="utf-8"))
    arm = ARMS[arm_name]
    row: dict[str, Any] = {"instance_id": inst["instance_id"], "arm": arm_name, "halted": None}
    started = time.monotonic()
    ws: Path | None = None
    recorder: Recorder | None = None
    try:
        ws = prepare_workspace(inst, arm_name)
        from chimera.core.agent import Agent, AgentConfig
        from chimera.governance.allowlist import restrict_registry
        from chimera.tools.builtin import default_registry

        recorder = Recorder(arm.top_p)
        tools = restrict_registry(default_registry(ws, host_exec_confirm=None), allow=CODING_TOOLS)
        config = AgentConfig(
            model=MODEL, system_prompt=arm.system, temperature=arm.temperature, max_steps=MAX_STEPS,
            insist_on_action=True, project_root=ws, turn_context=True, prefix_nonce="",
        )
        agent = Agent(recorder, tools, config)  # type: ignore[arg-type]
        task = INSTRUCTION.format(problem=inst["problem_statement"])
        system = agent.compose_system_prompt(task)
        row["system_sha256"] = hashlib.sha256(system.encode("utf-8")).hexdigest()
        row["system_starts_with_arm"] = system.startswith(arm.system)
        row["tools"] = sorted(t.name for t in tools.tools())
        result = agent.run(task)
        row.update({
            "steps": result.steps, "stopped": result.stopped_reason, "tool_calls": result.tool_calls_made,
            "tool_names": result.tool_names, "answer": (result.answer or "")[:600],
            "agent_prompt_tokens": result.prompt_tokens, "agent_completion_tokens": result.completion_tokens,
            "agent_cache_read_tokens": result.cache_read_tokens, "agent_usd": result.usd,
            "shell": shell_commands(result.transcript),
        })
    except Exception as exc:  # noqa: BLE001 — a provider or harness failure is a halt (PROTOCOL §2)
        row["halted"] = f"{type(exc).__name__}: {exc}"[:400]
        row["traceback"] = traceback.format_exc()[-1500:]
    finally:
        if ws is not None and ws.exists():
            diff = _run(["git", "-C", str(ws), "diff", inst["base_commit"]])
            row["patch"] = diff.stdout if diff.returncode == 0 else ""
        else:
            row.setdefault("patch", "")
        row["calls"] = recorder.calls if recorder else []
        row["seconds"] = round(time.monotonic() - started, 1)
        out_path.write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
