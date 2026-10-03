---
name: check-the-provider-before-you-catalog
description: Verify the provider's current documentation before recording a model identifier, price, limit, or capability.
version: 0.1.0
kind: pattern
stage: verify
topic: ai-agents
triggers:
- adding a model to a provider catalogue
- updating model prices or context windows
- a provider renamed a model
- a catalogue entry looks stale
provenance: clean
status: active
license: Apache-2.0
---

## Trigger

You are changing a model catalogue. Identifiers, prices, limits, modalities and tool support can change independently.

## Do

Read official model and pricing documentation; record its URL and check date. Use the exact identifier. Keep unknown prices or limits as `None`. Check for duplicates and run invariant tests. If the source is ambiguous, leave the entry unchanged and ask.

## Avoid

Do not substitute marketing names, third-party prices or another model's capabilities for verified provider facts.

## Check

Run catalogue tests and inspect the entry through the CLI. Non-empty fields alone do not prove correctness.

## Risk

Provider documentation can change after merge. Source URLs and check dates make later updates traceable; they do not guarantee future accuracy.
