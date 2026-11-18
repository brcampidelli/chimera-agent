# Offline edit grader

The runner requires the local image `chimera-proportionality-pytest:local`. Its Dockerfile fixes
Python 3.12.8 and pytest 8.3.4 and uses the base image's `/bin/sh`. Prepare the exact base image and
the wheels for pytest 8.3.4, iniconfig 2.0.0, packaging 24.2 and pluggy 1.5.0 in advance. Put the
wheels in `wheels/` inside a separate build context, then from the repository root run:

```sh
docker image inspect python:3.12.8-slim-bookworm > /dev/null || exit 1
docker build --pull=false --network=none -t chimera-proportionality-pytest:local -f bench/proportionality/Dockerfile <offline-context>
```

The image inspection must succeed before the build; do not run the build if the base is missing.
The build fails if a wheel is absent; `--network=none` disables networking in build steps. The runner checks pytest
inside the image before any model call. It mounts only a disposable copy of the fixture, disables
network and pulling, has a timeout, and refuses execution when Docker is unavailable. The local
image and wheelhouse must come from trusted sources; this container is not a virtual machine.
