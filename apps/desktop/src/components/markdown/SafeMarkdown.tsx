import { useEffect, useMemo, useState, type ReactNode } from "react";
import Markdown, { defaultUrlTransform, type Components, type Options } from "react-markdown";
import { ImageOff, Play } from "lucide-react";

import { Button } from "@/components/ui/button";
import { getFsImage, getFsMedia } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { focusRing } from "@/components/ui/focus";

/** Where a Markdown image would be fetched from, decided before anything is fetched. */
export type ImageSource =
  | { kind: "inline" }
  | { kind: "local" }
  | { kind: "external"; label: string };

/** A file in the workspace an answer names: the arguments the workspace endpoints take. */
export type WorkspaceRef = { workspace: string | null; path: string };

/** The workspace image endpoint (`/api/fs/image` in `chimera/api/app.py`): serves `image/*` only. */
const LOCAL_IMAGE_PATH = "/api/fs/image";
/** The workspace file route, which an answer may link to for a file it wants the person to open. */
const LOCAL_FILE_PATH = "/api/fs/file";
/** The workspace media endpoint (`/api/fs/media`): serves `audio/*` and `video/*` only. */
const LOCAL_MEDIA_PATH = "/api/fs/media";
const LOCAL_ENDPOINTS = new Set([LOCAL_IMAGE_PATH, LOCAL_FILE_PATH, LOCAL_MEDIA_PATH]);
/** The model writes this app's own host as a bare `api` (`https://api/fs/file?path=...`). It is this app. */
const APP_HOST_NAME = "api";
const ABSOLUTE_SRC = /^[a-z][a-z0-9+.-]*:|^\/\//i;
/** A path that starts at a root of its own: a backslash, a protocol-relative `//` (a UNC share is two
 *  backslashes), or a drive letter. Such a path names a machine's disk or a network share, never a
 *  file in the workspace, and it must not be handed to the operating system to resolve. A single
 *  leading slash is this app's own origin, which the endpoint routes below use, so it is not here. */
const ROOTED = /^\\|^\/\/|^[a-zA-Z]:/;
/** The same roots written percent-encoded, which a model does to get past a plain check. */
const ENCODED_ROOT = /^%5C|^%2F%2F/i;
const AUDIO_EXTENSIONS = new Set(["mp3", "wav", "ogg", "m4a"]);
const VIDEO_EXTENSIONS = new Set(["mp4", "webm"]);

function safeDecode(text: string): string {
  try {
    return decodeURI(text);
  } catch {
    // A literal `%` that is not an escape: the text as written.
    return text;
  }
}

/** Whether `text`, raw or decoded, starts at a root of its own. Both are checked: the decode is what
 *  turns `%5C%5Cserver` into the two backslashes that name a share. */
function startsAtRoot(text: string): boolean {
  const decoded = safeDecode(text);
  return ROOTED.test(text) || ROOTED.test(decoded) || ENCODED_ROOT.test(text) || ENCODED_ROOT.test(decoded);
}

/** The workspace path a relative `src` names, or null when it is not one: an absolute URL, a
 *  fragment, or a path that starts at a root of its own. */
function relativeWorkspacePath(src: string): string | null {
  if (src === "" || ABSOLUTE_SRC.test(src) || src.startsWith("#") || src.startsWith("/")) return null;
  const bare = src.split(/[?#]/)[0].replace(/^\.\//, "");
  if (startsAtRoot(bare)) return null;
  const path = safeDecode(bare);
  if (startsAtRoot(path)) return null;
  return path || null;
}

/** This app's own file endpoint named by `src`, or null. The host must be this origin, or the bare
 *  `api` the model writes; the path must be one of the workspace endpoints. Anything else is another
 *  server, and another server is never fetched. */
function ownEndpoint(src: string, origin: string): URL | null {
  let url: URL;
  try {
    url = new URL(src, origin);
  } catch {
    return null;
  }
  const ownHost = url.origin === origin;
  const modelHost = url.hostname === APP_HOST_NAME && /^https?:$/.test(url.protocol);
  if (!ownHost && !modelHost) return null;
  // `https://api/fs/file` is the route `/api/fs/file` with the host the model wrote: the `/api`
  // prefix is the part it left out, so it is put back before the route is matched.
  const route = modelHost ? `/api${url.pathname}` : url.pathname;
  return LOCAL_ENDPOINTS.has(route) ? url : null;
}

/** Whether `src` is one of this app's workspace endpoints, whether or not it may be read. A guest page
 *  uses it to withhold the link instead of letting the owner's workspace open in the guest's browser. */
export function isWorkspaceEndpoint(src: string, origin: string = window.location.origin): boolean {
  return ownEndpoint(src, origin) !== null;
}

/** Two folder spellings that name the same place: separators and case differ between what a model
 *  writes and what the app holds, and a trailing separator is noise. */
function sameFolder(a: string, b: string): boolean {
  const norm = (s: string) => s.replace(/[\\/]+$/, "").replace(/\//g, "\\").toLowerCase();
  return norm(a) === norm(b);
}

/** The workspace file an `src` or `href` from an answer refers to, or null.
 *
 * The workspace is the conversation's, and only that. A plain relative path names a file in it. An
 * endpoint URL that names a DIFFERENT workspace in its own `workspace=` is refused: the model wrote
 * that address, and following it would read a folder the conversation never opened.
 */
export function workspaceRef(
  src: string,
  origin: string = window.location.origin,
  workspace: string | null = null,
): WorkspaceRef | null {
  const relative = relativeWorkspacePath(src);
  if (relative !== null) return { workspace, path: relative };
  const url = ownEndpoint(src, origin);
  const path = url?.searchParams.get("path");
  if (!url || !path) return null;
  const named = url.searchParams.get("workspace");
  if (named !== null && (workspace === null || !sameFolder(named, workspace))) return null;
  return { workspace, path };
}

/** `audio` or `video` for a file the inline player can play, by extension; null for anything else. */
export function mediaKind(path: string): "audio" | "video" | null {
  const ext = /\.([a-z0-9]+)$/i.exec(path)?.[1]?.toLowerCase() ?? "";
  if (AUDIO_EXTENSIONS.has(ext)) return "audio";
  if (VIDEO_EXTENSIONS.has(ext)) return "video";
  return null;
}

/** Classify an image `src` from an answer.
 *
 * Inline (`data:image/…`, `blob:`) carries its bytes with it, and the workspace endpoint is this
 * app's own origin — neither is a request to anybody else. A relative path is a file in the
 * workspace, read through the authenticated client. A path that starts at a root of its own is not
 * a file here at all. Everything else is: an answer that says `![x](https://host/?d=…)` is a GET to
 * `host` with whatever the query holds, and the model wrote that query. That is the classic
 * exfiltration channel of a Markdown renderer, so it is shown as a named link instead of fetched.
 */
export function classifyImageSrc(
  src: string,
  origin: string = window.location.origin,
): ImageSource {
  if (/^data:image\//i.test(src) || /^blob:/i.test(src)) return { kind: "inline" };
  if (relativeWorkspacePath(src) !== null || ownEndpoint(src, origin)) return { kind: "local" };
  let url: URL;
  try {
    url = new URL(src, origin);
  } catch {
    return { kind: "external", label: src };
  }
  // A relative path is said as written: "external image from 127.0.0.1:8765" would name the app's
  // own address for a file the answer merely mentioned.
  const absolute = ABSOLUTE_SRC.test(src);
  return { kind: "external", label: absolute ? url.host || src : src };
}

/** `react-markdown`'s default transform drops `data:` and `blob:` — right for links, where they can
 *  carry a document, and wrong for an image, which is the one place they are safe and useful. */
function urlTransform(url: string, key: string, node: { tagName: string }): string {
  if (key === "src" && node.tagName === "img" && /^(data:image\/|blob:)/i.test(url)) return url;
  return defaultUrlTransform(url);
}

/** Whether opening `src` would send more than a location: a query string or a fragment.
 *
 * The chip names only the host, which is what decides WHERE a request goes. It does not say WHAT
 * goes with it, and `?d=<secret>` is the payload of the exfiltration this whole component exists
 * to stop — one click on a chip that reads "external image from evil.example" would deliver it.
 * A path can carry data too, which is why the full address is also in the tooltip; the query is
 * flagged out loud because it is the shape a model writes when it is smuggling something.
 */
export function addressCarriesData(src: string, origin: string = window.location.origin): boolean {
  try {
    const url = new URL(src, origin);
    return url.search !== "" || url.hash !== "";
  } catch {
    return false;
  }
}

const CHIP = cn(
  "inline-flex items-center gap-1.5 rounded-chip border border-hairline px-2 py-1 text-xs",
  "text-muted-foreground no-underline transition hover:text-foreground",
);

/** Where a workspace image reference points, as the arguments `getFsImage` takes. */
export function localImageQuery(
  src: string,
  origin: string = window.location.origin,
  workspace: string | null = null,
): { workspace: string | null; path: string } | null {
  return workspaceRef(src, origin, workspace);
}

/** A workspace image, fetched with the bearer token and shown through an object URL.
 *
 * Mounting `<img src="/api/fs/image?…">` straight from the answer was how this used to work, and it
 * stopped working the day the desktop backend began requiring a token on every `/api` request: an
 * `<img>` cannot send an `Authorization` header, so every local image in a chat answer became a
 * 401 and a broken icon. Putting the token in the query string instead would write it into access
 * logs and history. So the bytes come through `getFsImage` (the same path the file viewer in
 * `Code.tsx` uses) and the element gets a `blob:` URL, revoked when the source changes or the
 * answer unmounts — an object URL pins its blob in memory until it is released.
 */
function LocalImage({ query, alt }: { query: WorkspaceRef | null; alt: string }) {
  const t = useT();
  const [url, setUrl] = useState("");
  const [failed, setFailed] = useState(false);
  const workspace = query?.workspace ?? null;
  const path = query?.path ?? "";

  useEffect(() => {
    setUrl("");
    setFailed(false);
    if (!path) {
      setFailed(true);
      return;
    }
    let cancelled = false;
    let objectUrl = "";
    getFsImage(workspace, path).then(
      (blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
      },
      () => {
        if (!cancelled) setFailed(true);
      },
    );
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [workspace, path]);

  if (failed) {
    // Said, not a broken icon: a 401 or a missing file is a fact the person can act on.
    return (
      <span data-testid="markdown-local-image-error" className={CHIP} title={alt || undefined}>
        <ImageOff className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        <span>{t("code.imageError")}</span>
      </span>
    );
  }
  if (!url) return null;
  // Capped by height, not width: a tall SVG logo filled the whole column. The width stays automatic,
  // so the proportion holds, and the preflight already caps it at the column.
  return <img src={url} alt={alt} className="max-h-media" />;
}

/** A workspace audio or video file. The card is shown at once; the bytes are fetched only when the
 *  person presses play. A file the answer merely mentions is then not read, and a long answer full of
 *  media does not pull every file in on arrival. The bytes come through the authenticated client
 *  because an `<audio>` element cannot send the bearer token. */
function WorkspaceMedia({ query, kind }: { query: WorkspaceRef; kind: "audio" | "video" }) {
  const t = useT();
  const [url, setUrl] = useState("");
  const [state, setState] = useState<"idle" | "loading" | "failed">("idle");
  const { workspace, path } = query;
  const name = path.split(/[\\/]/).pop() || path;

  // Released when it is replaced and when the answer goes away: an object URL pins its blob.
  useEffect(() => {
    return () => {
      if (url) URL.revokeObjectURL(url);
    };
  }, [url]);

  function play() {
    setState("loading");
    getFsMedia(workspace, path).then(
      (blob) => {
        setUrl(URL.createObjectURL(blob));
        setState("idle");
      },
      () => setState("failed"),
    );
  }

  return (
    <figure
      data-testid="markdown-media"
      className="my-2 flex flex-col gap-1.5 rounded-card border border-hairline bg-surface-2 p-2"
    >
      <figcaption className="flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
        <span className="min-w-0 flex-1 truncate" title={path}>
          {name}
        </span>
        {url ? (
          <a
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            className={cn(
              "shrink-0 rounded-chip px-2 py-0.5 text-foreground transition duration-1 ease-out hover:text-accent",
              focusRing,
            )}
          >
            {t("markdown.openMedia")}
          </a>
        ) : null}
      </figcaption>
      {!url ? (
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="self-start"
          disabled={state === "loading"}
          onClick={play}
          data-testid="markdown-media-play"
        >
          <Play className="h-3.5 w-3.5" aria-hidden="true" />
          {t("markdown.playMedia")}
        </Button>
      ) : null}
      {state === "failed" ? (
        <span data-testid="markdown-media-error" className={CHIP}>
          <ImageOff className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          <span>{t("markdown.mediaError")}</span>
        </span>
      ) : null}
      {url && kind === "audio" ? (
        <audio controls autoPlay preload="metadata" src={url} aria-label={name} className="w-full" />
      ) : null}
      {url && kind === "video" ? (
        <video
          controls
          autoPlay
          preload="metadata"
          src={url}
          aria-label={name}
          className="max-h-media w-full rounded-card"
        />
      ) : null}
    </figure>
  );
}

/** A workspace file on the guest page, said rather than played: this origin is the owner's machine
 *  there, so showing it would fetch the owner's file for a guest. */
function WithheldWorkspaceFile({ kind }: { kind: "image" | "media" }) {
  const t = useT();
  return (
    <span
      data-testid={kind === "image" ? "markdown-withheld-image" : "markdown-withheld-media"}
      className={CHIP}
    >
      <ImageOff className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
      <span>{t(kind === "image" ? "markdown.localImageWithheld" : "markdown.localMediaWithheld")}</span>
    </span>
  );
}

function MarkdownImage({
  src,
  alt,
  allowLocal,
  workspace,
}: {
  src?: unknown;
  alt?: string;
  allowLocal: boolean;
  workspace: string | null;
}) {
  const t = useT();
  if (typeof src !== "string" || !src) return null;
  const source = classifyImageSrc(src);
  if (source.kind === "inline") {
    return <img src={src} alt={alt ?? ""} className="max-h-media" />;
  }
  if (source.kind === "local") {
    const query = workspaceRef(src, window.location.origin, workspace);
    const kind = query ? mediaKind(query.path) : null;
    if (query && kind) {
      return allowLocal ? (
        <WorkspaceMedia query={query} kind={kind} />
      ) : (
        <WithheldWorkspaceFile kind="media" />
      );
    }
    // Not a link either: on the guest page this origin's image endpoint is the OWNER's workspace,
    // and a link would open it in the guest's browser — the thing being withheld.
    return allowLocal ? (
      <LocalImage query={query} alt={alt ?? ""} />
    ) : (
      <WithheldWorkspaceFile kind="image" />
    );
  }
  // A path that starts at a root of its own is shown as text. Any link to it would hand the
  // operating system a network share the moment the person clicked, so it is never a link.
  if (startsAtRoot(src)) {
    return (
      <span data-testid="markdown-blocked-path" className={CHIP}>
        <ImageOff className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        <span>{t("markdown.externalImage", { host: src })}</span>
      </span>
    );
  }
  // A link, not a "load here" button. The page's own policy (`page_csp.py`) refuses remote images
  // anyway, so loading in place would only ever show a broken icon. Opening it is a request the
  // person chose to make, in their own browser, after reading which host it goes to — and, in the
  // tooltip, the whole address, because the host alone does not say what the request carries.
  const carries = addressCarriesData(src);
  return (
    <a
      href={src}
      target="_blank"
      rel="noopener noreferrer"
      title={alt ? `${alt}\n${src}` : src}
      data-testid="markdown-external-image"
      className={cn(CHIP, focusRing)}
    >
      <ImageOff className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
      <span>{t("markdown.externalImage", { host: source.label })}</span>
      {carries ? (
        <>
          <span aria-hidden="true">·</span>
          <span data-testid="markdown-external-image-data">{t("markdown.externalImageCarriesData")}</span>
        </>
      ) : null}
      <span aria-hidden="true">·</span>
      <span>{t("markdown.openExternalImage")}</span>
    </a>
  );
}

/** A link to a workspace audio or video file becomes its player. A link that names a root of its own,
 *  or one of this app's endpoints that was refused, is text: neither may be opened from here. */
function MarkdownLink({
  href,
  title,
  children,
  allowLocal,
  workspace,
}: {
  href?: string;
  title?: string;
  children?: ReactNode;
  allowLocal: boolean;
  workspace: string | null;
}) {
  if (href && startsAtRoot(href)) return <span>{children}</span>;
  const query = href ? workspaceRef(href, window.location.origin, workspace) : null;
  const kind = query ? mediaKind(query.path) : null;
  if (query && kind) {
    return allowLocal ? (
      <WorkspaceMedia query={query} kind={kind} />
    ) : (
      <WithheldWorkspaceFile kind="media" />
    );
  }
  if (href && !query && isWorkspaceEndpoint(href)) return <span>{children}</span>;
  return (
    <a href={href} title={title}>
      {children}
    </a>
  );
}

function buildComponents(allowLocal: boolean, workspace: string | null): Components {
  return {
    img: ({ src, alt }) => (
      <MarkdownImage src={src} alt={alt} allowLocal={allowLocal} workspace={workspace} />
    ),
    a: ({ href, title, children }) => (
      <MarkdownLink href={href} title={title} allowLocal={allowLocal} workspace={workspace}>
        {children}
      </MarkdownLink>
    ),
  };
}

/**
 * Markdown for anything the agent wrote, with images that fetch only from here.
 *
 * Used by every place an answer is rendered — the Code conversation, a hierarchy run's answer and
 * the guest page — so a new image rule lands in all of them at once. It is the second layer: the
 * page policy already blocks a remote image; this is what makes the block visible, so the person
 * sees "an image from host X was not loaded" rather than a broken icon or nothing at all.
 *
 * `allowLocalImages` is false on the guest page. The workspace image endpoint is safe to show the
 * OWNER — it is their files, on their machine — but a guest sees the page on the owner's server,
 * and an answer naming `/api/fs/image?path=…` would show the guest the owner's workspace images.
 * The guest renderer has no legitimate use for that endpoint, so it does not get one.
 *
 * `workspace` is the conversation's workspace: a relative path in the answer is a file there, and
 * an endpoint URL that names another workspace is refused.
 */
export function SafeMarkdown({
  children,
  rehypePlugins,
  allowLocalImages = true,
  workspace = null,
}: {
  children: string;
  rehypePlugins?: Options["rehypePlugins"];
  allowLocalImages?: boolean;
  workspace?: string | null;
}) {
  const components = useMemo(
    () => buildComponents(allowLocalImages, workspace),
    [allowLocalImages, workspace],
  );
  return (
    <Markdown components={components} rehypePlugins={rehypePlugins} urlTransform={urlTransform}>
      {children}
    </Markdown>
  );
}
