"""Run one solution against one task's hidden tests, in its own process. Not imported by anything.

    python grader_child.py <payload.json> <out.json>

The payload holds the code blocks of the final reply, the names they must define, the task's setup
and its tests. Every block is executed in order in one namespace (a later definition replaces an
earlier one); a block that fails is recorded and skipped, keeping whatever it defined before it
failed. Each test then runs on its own, with a 5-second limit where the platform has SIGALRM, so one
hanging or crashing test cannot take the others with it. Printing by the solution is swallowed.
"""

from __future__ import annotations

import contextlib
import io
import json
import signal
import sys
from datetime import date  # noqa: F401  (tests use it)
from decimal import Decimal  # noqa: F401  (tests use it)


def raises(exc, fn, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN201
    try:
        fn(*args, **kwargs)
    except exc:
        return True
    except Exception:  # noqa: BLE001
        return False
    return False


def raises_msg(exc, text, fn, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN201
    try:
        fn(*args, **kwargs)
    except exc as err:
        return text in str(err)
    except Exception:  # noqa: BLE001
        return False
    return False


_HAS_ALARM = hasattr(signal, "setitimer")


def _on_alarm(signum, frame):  # noqa: ANN001, ANN202, ARG001
    raise TimeoutError("h7 grader time limit")


def _limited(fn, seconds: float):  # noqa: ANN001, ANN202
    if _HAS_ALARM:
        signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        return fn()
    finally:
        if _HAS_ALARM:
            signal.setitimer(signal.ITIMER_REAL, 0)


def main() -> None:
    with open(sys.argv[1], encoding="utf-8") as fh:
        payload = json.load(fh)
    if _HAS_ALARM:
        signal.signal(signal.SIGALRM, _on_alarm)
    sink = io.StringIO()
    ns: dict = {"__name__": "h7_solution", "__builtins__": __builtins__}
    load_errors: list[str] = []
    for i, block in enumerate(payload["blocks"]):
        try:
            code = compile(block, f"<block {i}>", "exec")
        except SyntaxError:
            load_errors.append(f"block {i}: SyntaxError")
            continue
        try:
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                _limited(lambda code=code: exec(code, ns), 5)  # noqa: S102
        except BaseException as exc:  # noqa: BLE001  (SystemExit included)
            load_errors.append(f"block {i}: {type(exc).__name__}: {exc}"[:200])
    missing = [n for n in payload["names"] if not callable(ns.get(n))]
    helpers = {"raises": raises, "raises_msg": raises_msg, "date": date, "Decimal": Decimal}
    results: list[list] = []
    for src in payload["tests"]:
        env = dict(ns)
        env.update(helpers)
        source = (payload.get("setup") or "") + "\n" + src
        try:
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                _limited(lambda source=source, env=env: exec(source, env), 5)  # noqa: S102
            results.append([True, ""])
        except BaseException as exc:  # noqa: BLE001
            results.append([False, f"{type(exc).__name__}: {exc}"[:200]])
    with open(sys.argv[2], "w", encoding="utf-8") as fh:
        json.dump({"missing": missing, "load_errors": load_errors, "results": results}, fh)


if __name__ == "__main__":
    main()
