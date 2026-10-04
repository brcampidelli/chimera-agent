/** A turn the provider refused on content policy (study 29 P5.7), as its `error` frame says it.
 *
 *  Present only for that reason: an outage, a bad key or a crash carries none of this, and offering
 *  "another model" for those would teach that a model swap is the answer to every failure. Nothing
 *  was retried when this arrives — CONTENT_POLICY stops the turn on purpose, and choosing a model
 *  that lets the text through is a choice about safeguards the person makes, not the app. */
export interface PolicyBlockInfo {
  /** The model that refused, as the gateway recorded it (past a fallback, not the one asked for). */
  model: string;
  /** The route a router named in its reply, or null. */
  provider: string | null;
  /** The provider's id for the refused call — minted by them, never a secret, and what a support
   *  desk asks for. Null when the reply carried none. */
  request_id: string | null;
}

/** The refusal an `error` frame describes, or undefined when it describes anything else. */
export function policyBlockOf(payload: Record<string, unknown>): PolicyBlockInfo | undefined {
  if (payload.reason !== "content_policy") return undefined;
  const text = (v: unknown) => (typeof v === "string" && v ? v : null);
  return {
    model: text(payload.model) ?? "",
    provider: text(payload.provider),
    request_id: text(payload.request_id),
  };
}
