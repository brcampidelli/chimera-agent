import type { DesignOperation } from "@/lib/design/model";

/**
 * The two Chimera Design frames on the Code turn's stream (docs/design.md, "Frames on the Code
 * turn's stream"), read defensively. Kept apart from `lib/api.ts` so the conversation can parse a
 * live frame without reaching into the network module, which tests replace wholesale.
 */

/** `design_suggestion`: how plainly the message asked for Chimera Design. `explicit` named it or
 *  asked to use it; `suggest` paired "design" with a visual word. `reason` is a stable code. */
export interface CodeDesignSuggestion {
  level: "explicit" | "suggest";
  reason: string;
}

/** `design`: the tool saved a change to a design (`ops_applied: 0` when it only proposed). */
export interface CodeDesignFrame {
  document_id: string;
  title: string;
  revision: number;
  ops_applied: number;
  base_revision?: number;
  operations?: DesignOperation[];
  batch_index?: number;
  batch_count?: number;
  action: "create" | "apply" | "propose" | "export_html" | string;
}

/** The suggestion frame, read defensively: an unknown level is the weaker one, never the stronger. */
export function designSuggestionOf(payload: Record<string, unknown>): CodeDesignSuggestion {
  return { level: payload.level === "explicit" ? "explicit" : "suggest", reason: String(payload.reason ?? "") };
}

export function designFrameOf(payload: Record<string, unknown>): CodeDesignFrame {
  return {
    document_id: String(payload.document_id ?? ""),
    title: String(payload.title ?? ""),
    revision: Number(payload.revision ?? 0),
    ops_applied: Number(payload.ops_applied ?? 0),
    ...(Number.isInteger(payload.base_revision) ? { base_revision: Number(payload.base_revision) } : {}),
    ...(Array.isArray(payload.operations) ? { operations: payload.operations as DesignOperation[] } : {}),
    ...(Number.isInteger(payload.batch_index) ? { batch_index: Number(payload.batch_index) } : {}),
    ...(Number.isInteger(payload.batch_count) ? { batch_count: Number(payload.batch_count) } : {}),
    action: String(payload.action ?? ""),
  };
}
