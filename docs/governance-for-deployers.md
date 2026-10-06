# Governance for deployers

Chimera provides mechanisms that can support your record-keeping and human-oversight processes. They do not make a deployment compliant: the controller remains responsible for the system, its configuration, records, people, and applicable law. The descriptions below are deliberately scoped to what the mechanisms establish; **they provide toward** the capabilities discussed in Articles 12 and 14 of the EU AI Act, not a determination of compliance. [EU AI Act, Articles 12 and 14](https://eur-lex.europa.eu/eli/reg/2024/1689/oj)

## Traceable governance events

The governance audit log records events as JSONL entries in a hash chain; verification can identify changed or reordered chained entries. It can also compare the chain with a saved head anchor, which helps reveal truncation or rewriting since that anchor. This provides toward keeping automatically generated logs under Article 12. [Implementation: `chimera/governance/audit.py`](../chimera/governance/audit.py) · [Study results: audit and right-hand governance](../bench/right_hand_governance/RESULTS.md)

**Limits:** the chain detects some alterations; it does not prevent them. Deleting newest entries is not detectable from the remaining chain alone, and a person able to rewrite the whole log can recompute it. An anchor is useful only if the saved copy is protected independently; the implementation's ordinary run receipts are in the same home directory, not an independent trusted host. Legacy unchained entries cannot be validated as chained. [Implementation and documented threat model](../chimera/governance/audit.py)

## Human approval and durable questions

For configured approval flows, Chimera can pause for a person to answer a durable, identified question; silence times out as refusal, mismatched answers are discarded, and resolved outcomes are retained in history. This provides toward human oversight of covered decisions under Article 14, and toward records of those decisions under Article 12. [Implementation: `chimera/governance/pending.py`](../chimera/governance/pending.py) · [Study results: stale-consent probes](../bench/stale_consent/RESULTS.md) · [Study results: right-hand governance surfaces](../bench/right_hand_governance/RESULTS.md)

**Limits:** this is a mechanism, not proof that a human is competent, available, independent, or meaningfully exercising oversight. The deployer must configure the approval points, deliver questions to reachable people, and define escalation and review. On the synchronous durable path the effect runs within one poll interval of the answer, so a consent cannot age before it is used there (measured; no revalidation was built for it). That measurement does not cover a plan approved for a turn that then runs for minutes, or an editor's standing grant. [Implementation and scope](../chimera/governance/pending.py) · [Study findings and limits](../bench/stale_consent/RESULTS.md)

## Who decided, and whether review has become a rubber stamp

Each resolved approval is also written into the hash-chained audit log, with the whole action hashed (not the 200-character excerpt a card shows) and an `approver_kind`: `person` when someone answered, `system` when the timeout refused, `agent` when a connected editor granted on the user's behalf. The weekly review reports, for questions a person answered, how many were approved in under ten seconds — the habituation signal reviewers show as approval volume grows. [Implementation: `chimera/governance/pending.py`](../chimera/governance/pending.py) · [Weekly review: `chimera/scheduler/weekly_review.py`](../chimera/scheduler/weekly_review.py)

**Limits:** a fast approval is a signal to look at, not evidence of a careless one; the threshold is a fixed ten seconds. `approver_kind` says what kind of approver answered, not who the person was.

## The agent's own tools and the audit log

The agent's shell and code tools refuse commands that name the audit log, its route, its code or the CLI command that reads it. [Implementation: `chimera/core/audit_fence.py`](../chimera/core/audit_fence.py)

**Limits:** this narrows the ways in; it does not prevent them. A path the command builds at run time is not seen. Putting the data folder where the agent's tools cannot write (or running the agent in a sandbox without it mounted) is the structural answer, and it is the deployer's to make.

## Use in a deployment process

Treat these features as inputs to a broader governance process: decide which actions require review, test delivery and timeout behavior in your environment, protect and independently retain records and anchors, verify logs regularly, and document who reviews exceptions. The implementation and studies describe bounded technical behavior, not an assessment of your particular system or legal duties. [Audit implementation and limits](../chimera/governance/audit.py) · [Approval implementation and limits](../chimera/governance/pending.py) · [EU AI Act, Articles 12 and 14](https://eur-lex.europa.eu/eli/reg/2024/1689/oj)

This page is general information, not legal advice. Consult qualified counsel for an assessment of your obligations and deployment.
