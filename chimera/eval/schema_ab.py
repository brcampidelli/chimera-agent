"""Measure the token cost of tool schemas, full vs compacted (Improvement #5a).

Deterministic and model-free: it counts the tokens of the ``tools=`` payload before
and after :func:`~chimera.tools.schema_compact.compact_schemas`, per tool and in total.
Token counting uses LiteLLM's local tokenizer (no network); if unavailable it falls back
to a ~4-chars-per-token heuristic. The win is largest on verbose MCP/OpenAPI toolsets;
native tools are already terse, so their reduction is small — which the report shows
honestly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from chimera.tools.schema_compact import compact_schemas


@dataclass
class SchemaABRow:
    tool: str
    full_tokens: int
    compact_tokens: int


@dataclass
class SchemaABReport:
    rows: list[SchemaABRow] = field(default_factory=list)

    def summary(self) -> dict[str, float]:
        full = sum(r.full_tokens for r in self.rows)
        compact = sum(r.compact_tokens for r in self.rows)
        return {
            "tools": float(len(self.rows)),
            "full_tokens": float(full),
            "compact_tokens": float(compact),
            "reduction_pct": round((1 - compact / full) * 100, 1) if full else 0.0,
        }


def _count_tokens(text: str, model: str) -> int:
    try:
        import litellm

        return int(litellm.token_counter(model=model, text=text))
    except Exception:  # tokenizer unavailable — a rough but monotonic fallback
        return max(1, len(text) // 4)


def run_schema_ab(schemas: list[dict[str, Any]], *, model: str) -> SchemaABReport:
    """Count tokens for each tool schema, full vs compacted, against ``model``'s tokenizer."""
    compact = compact_schemas(schemas)
    report = SchemaABReport()
    for full_schema, compact_schema in zip(schemas, compact, strict=True):
        name = str(full_schema.get("function", {}).get("name", "?"))
        report.rows.append(
            SchemaABRow(
                tool=name,
                full_tokens=_count_tokens(json.dumps(full_schema), model),
                compact_tokens=_count_tokens(json.dumps(compact_schema), model),
            )
        )
    return report


def demo_bloated_schemas() -> list[dict[str, Any]]:
    """A couple of deliberately-verbose tool schemas (the kind MCP/OpenAPI imports produce)."""
    return [
        {
            "type": "function",
            "function": {
                "name": "create_invoice",
                "description": "Create an invoice.",
                "parameters": {
                    "type": "object",
                    "title": "CreateInvoiceRequest",
                    "properties": {
                        "customer_id": {
                            "type": "string",
                            "title": "Customer Id",
                            "description": "The unique identifier of the customer to bill. "
                            "This must be an existing customer in the system. If the customer "
                            "does not exist, create it first via the customers endpoint.",
                            "examples": ["cus_123", "cus_456"],
                        },
                        "amount": {
                            "type": "integer",
                            "title": "Amount",
                            "description": "Amount in cents. Positive integer.",
                            "default": 0,
                            "examples": [1000, 2500],
                        },
                        "line_items": {
                            "type": "array",
                            "title": "Line Items",
                            "items": {
                                "type": "object",
                                "title": "LineItem",
                                "properties": {
                                    "sku": {"type": "string", "title": "Sku", "example": "ABC"},
                                    "qty": {"type": "integer", "title": "Qty", "default": 1},
                                },
                            },
                        },
                    },
                    "required": ["customer_id", "amount"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_products",
                "description": "Search the product catalogue.",
                "parameters": {
                    "type": "object",
                    "title": "SearchProductsRequest",
                    "properties": {
                        "query": {
                            "type": "string",
                            "title": "Query",
                            "description": "The full-text search query string. Supports boolean "
                            "operators AND, OR, NOT and quoted phrases for exact matching.",
                            "examples": ["red shoes", '"running shoes"'],
                        },
                        "sort": {
                            "type": "string",
                            "enum": ["relevance", "price_asc", "price_desc"],
                            "title": "Sort",
                            "default": "relevance",
                            "description": "How to sort the results.",
                        },
                    },
                    "required": ["query"],
                },
            },
        },
    ]
