#!/bin/sh
# The image runs as an unprivileged user (uid 10001). A /data volume created by an older image is
# owned by root, and the first write would fail somewhere deep inside a run with "permission denied".
# Say so here, once, with the fix — and still refuse to run as root to dodge it.
set -eu

state="${CHIMERA_HOME:-/data}"
if [ ! -w "$state" ]; then
  {
    echo "chimera: the state directory $state is not writable by uid $(id -u)."
    echo "chimera: this image no longer runs as root. Fix the owner once, then start again:"
    echo "chimera:   docker compose run --rm --no-deps --user 0 --entrypoint chown chimera -R 10001:10001 /data"
    echo "chimera: Compose names the volume <project>_chimera-data, so a bare 'chimera-data' may be an empty"
    echo "chimera: volume; that command goes through Compose and reaches the real one. For a bind mount,"
    echo "chimera: chown the host path to 10001:10001. To keep root, stay on the previous image."
    echo "chimera: Details: docs/deploy.md"
  } >&2
  exit 78
fi

exec chimera "$@"
