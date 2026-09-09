#!/usr/bin/env bash
# Offline smoke test of pty_drive.py: does it spawn a pty, type into it, and time the answer?
# Spends nothing. Deleted after Part 2 if it has served its purpose.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
PY=/home/brcamp/hb-venv/bin/python
OUT="$(mktemp -d)"

echo "--- 1. does the child see a tty, and does typing reach it? ---"
"$PY" "$HERE/pty_drive.py" --label smoke-tty \
  --cmd 'python3 -c "import sys;print(\"stdin-isatty\",sys.stdin.isatty());print(\"typed:\",input())"' \
  --send "hello-from-the-driver" --send-after 2 --timeout 20 \
  --watch "stdin-isatty True" --watch "typed: hello-from-the-driver" \
  --out "$OUT/smoke-tty.txt"
echo
sed -n '/## screen text/,$p' "$OUT/smoke-tty.txt" | head -12

echo
echo "--- 2. does --reply fire on a pattern? ---"
"$PY" "$HERE/pty_drive.py" --label smoke-reply \
  --cmd 'python3 -c "print(\"Run it?\", flush=True); import sys; print(\"answer:\", sys.stdin.readline().strip())"' \
  --send "" --timeout 20 --watch "answer: y" --reply "Run it?::y" \
  --out "$OUT/smoke-reply.txt"
echo
sed -n '/## screen text/,$p' "$OUT/smoke-reply.txt" | head -12
rm -rf "$OUT"
