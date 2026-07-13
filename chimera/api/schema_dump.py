"""Dump the desktop API's OpenAPI schema as JSON to stdout.

This is the single source of truth the frontend generates its TypeScript types from:

    python -m chimera.api.schema_dump > apps/desktop/openapi.json
    npx openapi-typescript apps/desktop/openapi.json -o apps/desktop/src/lib/api-schema.ts

(wrapped as ``npm run gen:api`` in apps/desktop). Output is sorted + indented so regeneration
produces a deterministic diff — a CI job can regenerate and fail if it isn't committed, catching any
drift between the backend response models and the UI's types.
"""

from __future__ import annotations

import json
from typing import cast

from chimera.api import build_api_app
from chimera.interface import ChatSession
from chimera.interface.session import SupportsRun


def main() -> None:
    # The factory is never invoked while only building the schema, so a null agent is fine here.
    app = build_api_app(lambda: ChatSession(cast(SupportsRun, None)))
    print(json.dumps(app.openapi(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
