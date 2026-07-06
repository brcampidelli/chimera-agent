"""Terminal-Bench agent entrypoint for the Chimera treatment arm.

Harbor imports an agent class; this exposes ``ChimeraAgent`` bound to the model + the self-built
wheel (installed inside each task container before `chimera solve` runs). Configure via env:

  CHIMERA_TB_MODEL   the model slug (default: openrouter/deepseek/deepseek-chat-v3.1)
  CHIMERA_TB_WHEEL   container-side path to the chimera wheel the harness mounts/copies in

The import path for `tb run` is then ``chimera_agent:ChimeraAgent`` (run from this directory).
"""

from __future__ import annotations

import os

from chimera.eval.terminal_bench import make_chimera_tb_agent

_MODEL = os.environ.get("CHIMERA_TB_MODEL", "openrouter/deepseek/deepseek-chat-v3.1")
# Container-side path to the wheel. The run harness is responsible for placing the wheel here (via
# Terminal-Bench's agent-dir mount or a copy step) — see run_ab.sh.
_WHEEL = os.environ.get("CHIMERA_TB_WHEEL", "/agent/chimera_agent.whl")

# Built at import time so Harbor gets a ready class. Raises a friendly ImportError if the
# terminal-bench extra isn't installed (i.e. you're not on the benchmark box).
ChimeraAgent = make_chimera_tb_agent(_MODEL, wheel=_WHEEL)
