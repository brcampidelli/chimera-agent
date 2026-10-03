import Markdown, { defaultUrlTransform, type Components, type Options } from "react-markdown";
import { ImageOff } from "lucide-react";

import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { focusRing } from "@/components/ui/focus";

/** Where a Markdown image would be fetched from, decided before anything is fetched. */
export type ImageSource =
  | { kind: "inline" }
  | { kind: "local" }
  | { kind: "external"; label: string };

/** The workspace image endpoint (`/api/fs/image` in `chimera/api/app.py`): serves `image/*` only. */
const LOCAL_IMAGE_PATH = "/api/fs/image";

/** Classify an image `src` from an answer.
 *
 * Inline (`data:image/…`, `blob:`) carries its bytes with it, and the workspace endpoint is this
 * app's own origin — neither is a request to anybody else. Everything else is: an answer that says
 * `![x](https://host/?d=…)` is a GET to `host` with whatever the query holds, and the model wrote
 * that query. That is the classic exfiltration channel of a Markdown renderer, so it is shown as a
 * named link instead of fetched.
 */
export function classifyImageSrc(
  src: string,
  origin: string = window.location.origin,
): ImageSource {
  if (/^data:image\//i.test(src) || /^blob:/i.test(src)) return { kind: "inline" };
  let url: URL;
  try {
    url = new URL(src, origin);
  } catch {
    return { kind: "external", label: src };
  }
  if (url.origin === origin && url.pathname === LOCAL_IMAGE_PATH) return { kind: "local" };
  // A relative path is said as written: "external image from 127.0.0.1:8765" would name the app's
  // own address for a file the answer merely mentioned.
  const absolute = /^[a-z][a-z0-9+.-]*:|^\/\//i.test(src);
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

function MarkdownImage({ src, alt, allowLocal }: { src?: unknown; alt?: string; allowLocal: boolean }) {
  const t = useT();
  if (typeof src !== "string" || !src) return null;
  const source = classifyImageSrc(src);
  if (source.kind === "inline" || (source.kind === "local" && allowLocal)) {
    return <img src={src} alt={alt ?? ""} />;
  }
  if (source.kind === "local") {
    // Not a link either: on the guest page this origin's image endpoint is the OWNER's workspace,
    // and a link would open it in the guest's browser — the thing being withheld.
    return (
      <span data-testid="markdown-withheld-image" className={CHIP}>
        <ImageOff className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        <span>{t("markdown.localImageWithheld")}</span>
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

const WITH_LOCAL: Components = {
  img: ({ src, alt }) => <MarkdownImage src={src} alt={alt} allowLocal />,
};
const WITHOUT_LOCAL: Components = {
  img: ({ src, alt }) => <MarkdownImage src={src} alt={alt} allowLocal={false} />,
};

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
 */
export function SafeMarkdown({
  children,
  rehypePlugins,
  allowLocalImages = true,
}: {
  children: string;
  rehypePlugins?: Options["rehypePlugins"];
  allowLocalImages?: boolean;
}) {
  return (
    <Markdown
      components={allowLocalImages ? WITH_LOCAL : WITHOUT_LOCAL}
      rehypePlugins={rehypePlugins}
      urlTransform={urlTransform}
    >
      {children}
    </Markdown>
  );
}
