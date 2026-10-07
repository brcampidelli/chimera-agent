# Wire reconciliation — preregistration

**Study:** S30-61, second-writer wire log vs. harness steplog
**Status:** Registered before implementation or fault-injection run.
**Cost ceiling:** US$0. Synthetic only. No network provider is called.

## Question

Can an append-only provider-gateway record, written independently of the harness steplog, identify records that disagree with what the harness retained? The wire log is an independent observation point, not proof that a provider itself is honest. If both writers share a fault or the gateway is bypassed, reconciliation may not detect it.

## Registered outcomes and mutation classes

The benchmark generates deterministic synthetic exchanges through a fake backend. For each exchange, the fake backend supplies a request and response; the gateway tap records digests, and the harness creates the corresponding steplog. Mutations are applied **after** these pristine artifacts have been written and before audit:

1. **Omission:** remove one steplog exchange/step while leaving the wire record intact.
2. **Fabrication:** insert a plausible but nonexistent steplog exchange while leaving the wire log unchanged.
3. **Altered copy:** change one retained step's request or response representation while leaving the wire log unchanged.
4. **Clean controls:** leave both artifacts unchanged.

One independent trial is one generated run, and one run has exactly one registered fault for fault trials. The seeded generator, mutation selection and audit are deterministic. The benchmark reports each class separately and pooled, with raw `detected / trials` and two-sided 95% Wilson score intervals. A detection is a nonzero reconciliation discrepancy for the mutated run; a clean run is a false positive if reconciliation reports any discrepancy. Detection sensitivity is not inferred from a hash-chain check alone.

The implementation run uses 30 trials per fault class and 30 clean controls (120 synthetic runs total). No exclusion or replacement is allowed after outcomes are observed; generation/runner errors are reported separately and count as protocol failure rather than silently dropped data.

## Analysis

For each fault class, report sensitivity and its two-sided 95% Wilson interval. For controls, report false-positive rate and the same interval. Also report aggregate fault sensitivity over the three equally sized classes. No significance test or post-hoc subgroup is planned. These synthetic results establish only behavior against the registered mutations, not prevalence or real-provider generalization.

## Absolute rule for turning the option on

The option MUST remain **OFF by default**. A deployment may explicitly turn it on only when all of the following hold:

- the wire file is on storage with access controls and retention appropriate for run metadata;
- the operator has reviewed that the log contains only request/response digests and non-secret correlation metadata (never message bodies, credentials, or authorization headers);
- a validation run has at least 100 clean controls with zero false positives, and at least 100 trials in **each** registered fault class with the lower bound of that class's two-sided 95% Wilson interval at or above 0.90;
- the wire file is append-only for the process (no in-place edits or truncation in normal operation), and the operator understands that reconciliation cannot establish gateway/provider truth if both sources are compromised.

Failure to meet any condition, including insufficient sample size or inability to keep the log protected, means leave it OFF. The small initial synthetic run is a correctness exercise and cannot satisfy this enablement rule.

## Planned synthetic run and external work

Run the deterministic fake-backend fault injector; no credentials or network access are permitted. Publish `RESULTS.md` with the observed counts and intervals regardless of outcome. A real local-Ollama replication is owed separately and must be run only after the synthetic artifacts and command are available. Its cost is expected to be US$0, but it is not represented as completed here.
