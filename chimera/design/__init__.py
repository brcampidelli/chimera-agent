"""Chimera Design: AI-assisted visual artefacts as typed, validated, invertible documents.

The domain only — no routes, no agent tool, no screens. See ``docs/design.md``.

- :mod:`.model` — the document schema and its invariants.
- :mod:`.ops` — the operations and :func:`~.ops.apply_operations` (transactional, with inverses).
- :mod:`.ids` — server-generated ids and the remap of model-proposed ones.
- :mod:`.migrate` — schema versions.
- :mod:`.store` — documents and their version history on disk.
- :mod:`.tokens` — the default design system and colour contrast.
- :mod:`.assets` — admitting imported images.
- :mod:`.export_html` — a static, script-free HTML rendering and the token checks.
- :mod:`.propose` — asking a model for proposals, refinements and audits.
"""
