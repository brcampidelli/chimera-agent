"""Core agent loop: ReAct, planner, persistable state machine, verify-or-revert.

State is kept *outside* the LLM context (git + DB) to resist continuous-evolution
degradation. Implemented from milestone M1 onward.
"""
