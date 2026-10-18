"""The three public catalogues (plugins, skills, connectors), read from pinned commits only.

* :mod:`chimera.catalogs.pins` — the shipped pin file: repository, commit, sha256 of each file.
* :mod:`chimera.catalogs.loader` — fetch a pinned file, verify it, cache it, work offline from cache.
* :mod:`chimera.catalogs.parse` — the repositories' formats, read into one neutral item shape.
* :mod:`chimera.catalogs.categories` — the 27 shared category ids, and ``other``.

Reading only: nothing here installs anything. Installing stays where it was (the plugin installer,
the skill bundle path, the MCP screen), each with its own pending state and the owner's switch.
"""
