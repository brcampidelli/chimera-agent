"""Drive one terminal surface inside a real pty and time what appears on screen.

    python3 pty_drive.py --label tui --cmd "<shell command>" --send "<what to type>" \
        --send-after 6 --timeout 300 --watch "run_shell" --watch "Run it?" --out <file>

The command runs under ``script -qfec ... /dev/null``, so the child gets a genuine pty: this is the
only way to ask the question Part 2 asks, because both the TUI's driver and ``typer.confirm`` behave
differently the moment stdin stops being a terminal, and the whole claim under test is about what
happens when it IS one.

The instrument is a clock and a substring, nothing more. Every chunk read off the pty is timestamped
relative to process start; ``--watch`` records the first second at which a string appears; ``--reply``
sends a line the first time its pattern is seen. The transcript is written twice, raw and
ANSI-stripped, because a full-screen TUI redraws and the stripped view is the only readable one.

Never prints or stores the environment: the parent's environment is inherited by ``script`` and is
not echoed anywhere in the output.
"""

from __future__ import annotations

import argparse
import os
import re
import select
import subprocess
import sys
import time

_ANSI = re.compile(rb"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[@-Z\\-_]|\x1b\][^\x07]*\x07")


def strip_ansi(data: bytes) -> str:
    return _ANSI.sub(b"", data).decode("utf-8", "replace")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--cmd", required=True, help="shell command run inside the pty")
    ap.add_argument("--send", default="", help="text typed after --send-after seconds")
    ap.add_argument("--send-after", type=float, default=6.0)
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--watch", action="append", default=[], help="substring to time-stamp")
    ap.add_argument(
        "--reply", action="append", default=[],
        help="PATTERN::TEXT -- type TEXT the first time PATTERN appears",
    )
    ap.add_argument("--stop-on", default="", help="stop once this substring appears")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    replies = []
    for rule in args.reply:
        pattern, _, text = rule.partition("::")
        replies.append([pattern, text, False])

    started = time.monotonic()
    proc = subprocess.Popen(
        ["script", "-qfec", args.cmd, "/dev/null"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
    )
    assert proc.stdin is not None and proc.stdout is not None
    fd = proc.stdout.fileno()

    raw = bytearray()
    timeline: list[tuple[float, str]] = []
    seen: dict[str, float] = {}
    sent_prompt = False

    def note(event: str) -> None:
        timeline.append((round(time.monotonic() - started, 2), event))

    def type_line(text: str) -> None:
        try:
            proc.stdin.write(text.encode() + b"\r")  # type: ignore[union-attr]
            proc.stdin.flush()  # type: ignore[union-attr]
        except (BrokenPipeError, ValueError):
            note("stdin closed; could not type")

    note(f"spawned: {args.cmd}")
    while True:
        elapsed = time.monotonic() - started
        if elapsed > args.timeout:
            note(f"HARD TIMEOUT at {args.timeout:.0f}s -- killing")
            break
        if not sent_prompt and args.send and elapsed >= args.send_after:
            sent_prompt = True
            note(f"typed: {args.send!r}")
            type_line(args.send)
        ready, _, _ = select.select([fd], [], [], 0.25)
        if ready:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                chunk = b""
            if not chunk:
                note("pty closed (child exited)")
                break
            raw.extend(chunk)
            text = strip_ansi(bytes(raw))
            for needle in args.watch:
                if needle not in seen and needle in text:
                    seen[needle] = round(time.monotonic() - started, 2)
                    note(f"FIRST APPEARANCE {needle!r}")
            for rule in replies:
                if not rule[2] and rule[0] in text:
                    rule[2] = True
                    note(f"saw {rule[0]!r} -> typed {rule[1]!r}")
                    type_line(rule[1])
            if args.stop_on and args.stop_on in text:
                note(f"stop-on {args.stop_on!r} seen")
                break

    if proc.poll() is None:
        proc.kill()
        proc.wait(timeout=10)
    total = round(time.monotonic() - started, 2)

    clean = strip_ansi(bytes(raw))
    report = [
        f"# arm: {args.label}",
        f"command      : {args.cmd}",
        f"typed        : {args.send!r} at t+{args.send_after:.0f}s",
        f"wall clock   : {total}s",
        f"exit code    : {proc.returncode}",
        f"raw bytes    : {len(raw)}",
        "",
        "## timeline (seconds from spawn)",
        *[f"  t+{t:>7.2f}s  {event}" for t, event in timeline],
        "",
        "## first appearance of each watched string",
        *[
            f"  {needle!r:<44} {'t+' + str(seen[needle]) + 's' if needle in seen else 'NEVER APPEARED'}"
            for needle in args.watch
        ],
        "",
        "## screen text, ANSI stripped",
        clean,
    ]
    with open(args.out, "w", encoding="utf-8", errors="replace") as fh:
        fh.write("\n".join(report))
    # The head only on stdout; the screen dump is large and full of redraws, so it stays in the file.
    print("\n".join(report[: report.index("## screen text, ANSI stripped")]))
    print(f"  full transcript: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
