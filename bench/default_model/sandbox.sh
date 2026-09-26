#!/bin/bash
# Amendment 3: the local-disk wall. Runs one solve in its own mount and PID namespaces, where the
# host's answers are not there to find.
#
#   unshare --user --map-root-user --mount --pid --fork --mount-proc \
#     bash sandbox.sh <uid> <gid> <real_home> <workspace> <scratch> <code_dir> -- <command...>
#
# Inside, as the namespace's root (which is only the invoking user outside):
#   - /mnt, /tmp, /var/tmp, /dev/shm and the real home are covered by empty tmpfs mounts. That hides
#     the django reference clones (at `main`, so every later fix), every other item's template and
#     workspace (a later base commit is a later django), the grading logs (`patch.diff`, `eval.sh`),
#     the Hugging Face cache of SWE-bench, and the Windows drives with every bench's results;
#   - the solve's own workspace and scratch are bound back at their real paths, and the code dir
#     (the /tmp copy with the venv) is bound back read-only;
#   - the Docker socket is covered, so no image of another instance can be started;
#   - the PID namespace hides every other process (and so their working directories).
# Then a nested user namespace maps the invoking user's own uid back, so the solve runs as that user
# again, with the same `id`, the same file ownership and no capabilities. The network wall of
# H4/H5 Amendment 2 is unchanged (the proxy variables come in through the environment).
set -eu
# The invoking user's real ids, passed in: inside this namespace their files read as owned by 0.
# PYROOT is where the venv's interpreter lives (uv keeps it under the real home); it is bound back
# read-only, and holds CPython builds only.
UID_OUT=$1; GID_OUT=$2; REAL_HOME=$3; WS=$4; SCR=$5; CODE=$6; PYROOT=$7; shift 7
[ "${1:-}" = "--" ] && shift
case "$PYROOT" in "$REAL_HOME"/.local/share/uv/python) ;; *) echo "sandbox: bad python root $PYROOT" >&2; exit 90;; esac
case "$UID_OUT$GID_OUT" in *[!0-9]*|"") echo "sandbox: bad ids" >&2; exit 90;; esac
case "$REAL_HOME" in /home/?*) ;; *) echo "sandbox: bad home $REAL_HOME" >&2; exit 90;; esac
case "$WS" in "$REAL_HOME"/?*/?*) ;; *) echo "sandbox: bad workspace $WS" >&2; exit 90;; esac
case "$SCR" in "$REAL_HOME"/?*/?*) ;; *) echo "sandbox: bad scratch $SCR" >&2; exit 90;; esac
case "$CODE" in /tmp/?*) ;; *) echo "sandbox: bad code dir $CODE" >&2; exit 90;; esac

mount -t tmpfs -o mode=755 none /mnt
mkdir -p /mnt/.keep/ws /mnt/.keep/scr /mnt/.keep/code /mnt/.keep/py
mount --bind "$WS" /mnt/.keep/ws
mount --bind "$SCR" /mnt/.keep/scr
mount --bind "$CODE" /mnt/.keep/code
mount --bind "$PYROOT" /mnt/.keep/py

mount -t tmpfs -o mode=755 none "$REAL_HOME"
mkdir -p "$WS" "$SCR" "$PYROOT"
mount --bind /mnt/.keep/ws "$WS"
mount --bind /mnt/.keep/scr "$SCR"
mount --bind /mnt/.keep/py "$PYROOT"
mount -o remount,bind,ro "$PYROOT"

mount -t tmpfs -o mode=1777 none /tmp
mkdir -p "$CODE"
mount --bind /mnt/.keep/code "$CODE"
mount -o remount,bind,ro "$CODE"

for d in /var/tmp /dev/shm; do
  [ -d "$d" ] && mount -t tmpfs -o mode=1777 none "$d"
done
for s in /var/run/docker.sock /run/docker.sock; do
  if [ -S "$s" ]; then mount --bind /dev/null "$s" 2>/dev/null || true; fi
done

umount /mnt/.keep/ws /mnt/.keep/scr /mnt/.keep/code /mnt/.keep/py
rmdir /mnt/.keep/ws /mnt/.keep/scr /mnt/.keep/code /mnt/.keep/py /mnt/.keep

# The directories the tmpfs mounts created belong to this namespace's root, which is the invoking
# user outside; the solve runs as that user again below.
exec unshare --user --map-user="$UID_OUT" --map-group="$GID_OUT" -- "$@"
