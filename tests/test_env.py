"""TaskEnv (#3): the reset/step/state episode and the diff-gate reward, tested without a network."""

from __future__ import annotations

import sys

from chimera.eval.env import EnvState, TaskEnv

TASK = {
    "id": "add_fix",
    "prompt": "add(a,b) must return a+b, not a-b. Fix mod.py.",
    "files": {"mod.py": "def add(a, b):\n    return a - b\n"},
    "test": "test_mod.py",
    "test_src": "from mod import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
}


def test_reset_lays_out_workspace(tmp_path):
    env = TaskEnv(TASK, root=tmp_path, verifier=lambda ws: True)
    state = env.reset()
    assert isinstance(state, EnvState)
    assert (state.workspace / "mod.py").exists()
    assert (state.workspace / "test_mod.py").read_text() == TASK["test_src"]
    assert state.files == {"mod.py": TASK["files"]["mod.py"]}
    assert not state.done


def test_correct_solution_earns_reward(tmp_path):
    # verifier: pass iff mod.py returns a+b
    env = TaskEnv(TASK, root=tmp_path, verifier=lambda ws: "a + b" in (ws / "mod.py").read_text())
    env.reset()
    s = env.step({"mod.py": "def add(a, b):\n    return a + b\n"})
    assert s.done and s.reward == 1.0 and s.info["verified"] is True


def test_wrong_solution_earns_nothing(tmp_path):
    env = TaskEnv(TASK, root=tmp_path, verifier=lambda ws: "a + b" in (ws / "mod.py").read_text())
    env.reset()
    s = env.step({"mod.py": "def add(a, b):\n    return a * b\n"})  # still wrong
    assert s.reward == 0.0 and s.info["verified"] is False


def test_hollow_no_op_earns_nothing_even_if_verifier_would_pass(tmp_path):
    # diff-gate: a step that changes nothing must not be rewarded, even with an always-true verifier.
    env = TaskEnv(TASK, root=tmp_path, verifier=lambda ws: True)
    env.reset()
    s = env.step({"mod.py": TASK["files"]["mod.py"]})  # identical to starter
    assert s.info["productive"] is False and s.reward == 0.0


def test_editing_the_test_cannot_make_it_pass(tmp_path):
    # A policy that rewrites the test to something trivial must not become its own judge:
    # the pristine test is restored before grading, so the real (still-buggy) code fails.
    env = TaskEnv(TASK, root=tmp_path)  # default pytest verifier
    env.reset()
    s = env.step({"test_mod.py": "def test_add():\n    assert True\n"})  # tamper, no real fix
    assert (env.state().workspace / "test_mod.py").read_text() == TASK["test_src"]  # restored
    assert s.reward == 0.0


def test_default_pytest_verifier_grades_a_real_fix(tmp_path):
    # End-to-end with the real subprocess verifier (no network, just pytest).
    env = TaskEnv({**TASK, "verify": f'"{sys.executable}" -m pytest -q test_mod.py'}, root=tmp_path)
    env.reset()
    s = env.step({"mod.py": "def add(a, b):\n    return a + b\n"})
    assert s.reward == 1.0
