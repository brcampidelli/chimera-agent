"""Migration: import config, memory and skills from other agents (Hermes/OpenClaw).

Long-term memory is *merged* with existing history (never overwritten), reusing the
Memory Manager. Implemented in milestone M1 (config+skills) and M4 (memory-merge).
"""
