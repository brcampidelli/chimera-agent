"""Bring a stored design document up to the schema this build writes.

Each entry in :data:`UPGRADES` takes a document at version ``N`` and returns it at ``N + 1``; ``migrate``
chains them. Version 1 is the first, so the registry is empty today and migrating a version-1 document
is the identity — the point of having it now is that the second version arrives with a place to go,
and that a document from a **newer** Chimera is refused with a reason instead of being read by rules
that do not know its fields (and, saved back, losing them).
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

from chimera.design.model import SCHEMA_VERSION, DesignDocument, DesignError

Upgrade = Callable[[dict[str, Any]], dict[str, Any]]

#: ``{from_version: upgrade}``. Every version below :data:`SCHEMA_VERSION` must have one (tested).
def _v1_to_v2(raw: dict[str, Any]) -> dict[str, Any]:
    upgraded = copy.deepcopy(raw)
    upgraded["schemaVersion"] = 2
    return upgraded


UPGRADES: dict[int, Upgrade] = {1: _v1_to_v2}


class DesignMigrationError(DesignError):
    """A stored document cannot be brought to this build's schema."""


def migrate(raw: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``raw`` at :data:`SCHEMA_VERSION`. The input is never modified."""
    if not isinstance(raw, dict):
        raise DesignMigrationError("a design document is a JSON object")
    version = raw.get("schemaVersion")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise DesignMigrationError(f"schemaVersion must be a positive integer, got {version!r}")
    if version > SCHEMA_VERSION:
        raise DesignMigrationError(
            f"this document was written by a newer Chimera (schema {version}); this build reads up "
            f"to schema {SCHEMA_VERSION}. Update Chimera to open it — saving it here would drop "
            "whatever the newer version added."
        )
    doc = copy.deepcopy(raw)
    while version < SCHEMA_VERSION:
        step = UPGRADES.get(version)
        if step is None:  # pragma: no cover - guarded by a test over the registry
            raise DesignMigrationError(f"no upgrade from schema {version}")
        doc = step(doc)
        new_version = doc.get("schemaVersion")
        if new_version != version + 1:  # pragma: no cover - guarded by a test over the registry
            raise DesignMigrationError(f"upgrade from schema {version} produced {new_version!r}")
        version = new_version
    return doc


def load_document(raw: dict[str, Any]) -> DesignDocument:
    """Migrate, then validate. The one door a stored document comes in through."""
    return DesignDocument.model_validate(migrate(raw))
