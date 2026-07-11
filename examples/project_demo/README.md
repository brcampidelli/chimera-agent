# Example: run a whole project against a spec (`chimera project`)

This is the flagship autonomous feature: instead of a single task, you hand Chimera a **Spec**
(a small YAML of requirements) and it works the *whole project* to completion — turning each
unmet requirement into a task, writing real files, checking its own work, and accepting the
result **only when the Spec is satisfied**. Not on the model's word — the drift gate is the
executable authority for "done".

## What this demo builds

A tiny `temperature.py` module, defined entirely by [`spec.yaml`](spec.yaml):

1. `c_to_f(c)` — Celsius → Fahrenheit
2. `f_to_c(f)` — Fahrenheit → Celsius (depends on step 1)
3. the two round-trip correctly (a `command` check that actually runs the code)

The `depends_on` links make it a real dependency chain: step 2 won't start until step 1 is
satisfied, and step 3 waits for step 2.

## Run it

```bash
# needs a model key (chimera doctor to check); --yes auto-approves the initial plan
chimera project start examples/project_demo/spec.yaml -w ./scratch --yes
```

Watch the status any time, or drive it one step at a time (cron-friendly):

```bash
chimera project status <id>     # the id is printed when it starts
chimera project step   <id>     # run exactly one iteration
```

## What to expect

- Chimera creates `./scratch/temperature.py` with the two functions.
- After each step it **re-checks the Spec** and stops the moment everything is aligned
  (`done`). Verify it yourself — the same gate, run by hand:
  ```bash
  chimera drift examples/project_demo/spec.yaml -w ./scratch
  ```
- If a step can't be satisfied, the project **escalates to you** instead of pretending success.

## Human approval for risky steps

Add `risk: high` to any requirement and Chimera **pauses before running that step** and waits
for your explicit go — the safety gate for deploys, migrations, or deletes:

```yaml
  - id: deploy
    text: "publish the package"
    check: command
    target: "your-deploy-command"
    risk: high
    depends_on: [round_trip]
```

It stops at `awaiting_approval`; you review, then:

```bash
chimera project approve <id> --card <card-id>   # run the risky step
chimera project deny    <id> --card <card-id>   # reject it and escalate
```

## Why this is different

The **Spec is the acceptance authority** — every step's success maps to a machine check
(`chimera drift --only <id>`), so the agent can't declare victory the artifacts don't back up.
And because it runs on the normal solve lane, the project **feeds Chimera's learning** (memory
and skills) as it goes. Full write-up: [Extending guide](../../docs/extending.md) ·
[Architecture](../../docs/architecture.md).
