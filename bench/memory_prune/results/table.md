# memory_prune — 288 cells, deterministic

## budget 20%

| position | kind | key | source | kept (over 2 N × 3 seeds) |
|---|---|---|---|---:|
| first | episodic | none | chimera | 0/6 |
| first | episodic | none | user | 0/6 |
| first | episodic | keyed | chimera | 0/6 |
| first | episodic | keyed | user | 0/6 |
| first | semantic | none | chimera | 0/6 |
| first | semantic | none | user | 0/6 |
| first | semantic | keyed | chimera | 0/6 |
| first | semantic | keyed | user | 0/6 |
| middle | episodic | none | chimera | 0/6 |
| middle | episodic | none | user | 0/6 |
| middle | episodic | keyed | chimera | 0/6 |
| middle | episodic | keyed | user | 0/6 |
| middle | semantic | none | chimera | 0/6 |
| middle | semantic | none | user | 0/6 |
| middle | semantic | keyed | chimera | 0/6 |
| middle | semantic | keyed | user | 0/6 |
| last | episodic | none | chimera | 0/6 |
| last | episodic | none | user | 0/6 |
| last | episodic | keyed | chimera | 0/6 |
| last | episodic | keyed | user | 0/6 |
| last | semantic | none | chimera | 0/6 |
| last | semantic | none | user | 0/6 |
| last | semantic | keyed | chimera | 1/6 |
| last | semantic | keyed | user | 6/6 |

- the paper's profile (first, episodic, no key, agent-written): **0/6**
- its opposite (last, semantic, keyed, user-written): **6/6**
- survival by axis (everything else pooled): position: first 0/48, middle 0/48, last 7/48; kind: episodic 0/72, semantic 7/72; key: none 0/72, keyed 7/72; source: chimera 1/72, user 6/72

## budget 50%

| position | kind | key | source | kept (over 2 N × 3 seeds) |
|---|---|---|---|---:|
| first | episodic | none | chimera | 0/6 |
| first | episodic | none | user | 0/6 |
| first | episodic | keyed | chimera | 0/6 |
| first | episodic | keyed | user | 0/6 |
| first | semantic | none | chimera | 0/6 |
| first | semantic | none | user | 0/6 |
| first | semantic | keyed | chimera | 0/6 |
| first | semantic | keyed | user | 0/6 |
| middle | episodic | none | chimera | 0/6 |
| middle | episodic | none | user | 0/6 |
| middle | episodic | keyed | chimera | 0/6 |
| middle | episodic | keyed | user | 0/6 |
| middle | semantic | none | chimera | 0/6 |
| middle | semantic | none | user | 0/6 |
| middle | semantic | keyed | chimera | 0/6 |
| middle | semantic | keyed | user | 6/6 |
| last | episodic | none | chimera | 0/6 |
| last | episodic | none | user | 0/6 |
| last | episodic | keyed | chimera | 6/6 |
| last | episodic | keyed | user | 6/6 |
| last | semantic | none | chimera | 0/6 |
| last | semantic | none | user | 6/6 |
| last | semantic | keyed | chimera | 6/6 |
| last | semantic | keyed | user | 6/6 |

- the paper's profile (first, episodic, no key, agent-written): **0/6**
- its opposite (last, semantic, keyed, user-written): **6/6**
- survival by axis (everything else pooled): position: first 0/48, middle 6/48, last 30/48; kind: episodic 12/72, semantic 24/72; key: none 6/72, keyed 30/72; source: chimera 12/72, user 24/72

- `persona` at a 20% budget: kept 6/6 (never pruned by construction)
