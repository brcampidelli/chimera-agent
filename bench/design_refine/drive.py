"""Run every paid phase for both models in order. Resumable: each phase skips what is on disk."""
import run

for key in ("ds", "glm"):
    run.cmd_gen(key)
    run.cmd_corpus(key)
    run.cmd_refine(key)
print("DRIVE-DONE", run.spent())
