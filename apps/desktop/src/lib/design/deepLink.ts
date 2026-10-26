/**
 * The design a link names: `?design=<id>` on the app's own address. "Copy link" writes it, and the
 * Code screen reads it on start and on navigation, so the link opens the design in the viewer.
 *
 * Local only: the link names a design on this computer. Nothing here sends it anywhere, and an id that
 * is not a plain token is ignored rather than passed on.
 */
export const DESIGN_PARAM = "design";

const DESIGN_ID = /^[A-Za-z0-9_-]{1,96}$/;

/** The design id in a query string (`?design=doc_1`), or `null` when there is none or it is malformed. */
export function designIdFromSearch(search: string): string | null {
  const id = new URLSearchParams(search).get(DESIGN_PARAM);
  return id !== null && DESIGN_ID.test(id) ? id : null;
}

/** The link to `id` on the address `href`: the same app, with the design named in its query. */
export function shareLinkFor(href: string, id: string): string {
  const link = new URL(href);
  link.searchParams.set(DESIGN_PARAM, id);
  return link.toString();
}

/** The address without the design parameter, so a link is acted on once and a refresh does not reopen it. */
export function withoutDesignParam(href: string): string {
  const url = new URL(href);
  url.searchParams.delete(DESIGN_PARAM);
  return url.toString();
}
