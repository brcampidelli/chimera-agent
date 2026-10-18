/**
 * One small line drawing per category (study 04, §4.2): a scene, not a 16px icon blown up.
 *
 * Every drawing shares one frame — 48×48, a 6-unit margin, stroke 1.5 with round ends — and names
 * no colour: the stroke is `currentColor`, so it follows the theme, a custom accent and high
 * contrast with no extra line, and the tile decides the ink (muted at rest, accent on hover and
 * focus). Depth is a second stroke at reduced opacity, which is not a colour literal and reads in
 * both themes. No large solid fills — on the light theme one becomes a blot. Nothing animates.
 *
 * Decorative: the tile's own text names the category, so the drawing is hidden from assistive
 * technology rather than read twice.
 */
import type { ReactNode } from "react";

import type { CategoryId } from "@/components/catalog/categories";
import { cn } from "@/lib/utils";

/** The second, quieter stroke. */
const FAINT = 0.45;

const DRAWINGS: Record<CategoryId, ReactNode> = {
  // Layers of a system, stacked.
  engineering: (
    <>
      <path d="M24 8l16 8-16 8-16-8z" />
      <path d="M8 24l16 8 16-8" />
      <path d="M8 32l16 8 16-8" opacity={FAINT} />
    </>
  ),
  // An editor window with braces and a cursor.
  code: (
    <>
      <rect x="6" y="10" width="36" height="28" rx="4" />
      <path d="M6 16h36" opacity={FAINT} />
      <path d="M18 22h-2v4l-2 2 2 2v4h2" />
      <path d="M30 22h2v4l2 2-2 2v4h-2" />
      <path d="M24 24v8" opacity={FAINT} />
    </>
  ),
  // A branch that leaves the main line and comes back.
  "pull-requests": (
    <>
      <circle cx="14" cy="10" r="4" />
      <circle cx="14" cy="38" r="4" />
      <circle cx="34" cy="18" r="4" />
      <path d="M14 14v20" />
      <path d="M34 22v6a6 6 0 0 1-6 6H18" opacity={FAINT} />
    </>
  ),
  // A test tube and a check.
  "testing-quality": (
    <>
      <path d="M10 6h14" />
      <path d="M12 6v28a5 5 0 0 0 10 0V6" />
      <path d="M12 22h10" opacity={FAINT} />
      <path d="M28 30l4 4 8-10" />
    </>
  ),
  // A sheet with a folded corner.
  "files-documents": (
    <>
      <path d="M12 6h16l10 10v26H12z" />
      <path d="M28 6v10h10" />
      <path d="M18 24h14M18 30h14M18 36h8" opacity={FAINT} />
    </>
  ),
  // An open book with a bookmark.
  "reference-docs": (
    <>
      <path d="M24 12c-4-3-10-4-16-4v28c6 0 12 1 16 4 4-3 10-4 16-4V8c-6 0-12 1-16 4z" />
      <path d="M24 12v28" />
      <path d="M12 16h8M12 22h8M28 22h8" opacity={FAINT} />
      <path d="M30 9v10l3-2 3 2V8" />
    </>
  ),
  // A picture: a frame, a sun and two hills.
  "design-media": (
    <>
      <rect x="6" y="10" width="36" height="28" rx="4" />
      <circle cx="16" cy="20" r="3" opacity={FAINT} />
      <path d="M6 34l10-10 8 8 6-6 12 12" />
    </>
  ),
  // A browser window with a globe.
  "sites-frontend": (
    <>
      <rect x="6" y="8" width="36" height="32" rx="4" />
      <path d="M6 16h36" />
      <path d="M11 12h0M15 12h0" />
      <circle cx="24" cy="28" r="8" />
      <path d="M16 28h16M24 20c-3 3-3 13 0 16M24 20c3 3 3 13 0 16" opacity={FAINT} />
    </>
  ),
  // Three steps joined in a line, and the loop back.
  "pipelines-jobs": (
    <>
      <rect x="6" y="18" width="8" height="10" rx="2" />
      <rect x="20" y="18" width="8" height="10" rx="2" />
      <rect x="34" y="18" width="8" height="10" rx="2" />
      <path d="M14 23h6M28 23h6" />
      <path d="M38 28v8H10v-8" opacity={FAINT} />
    </>
  ),
  // A cloud with an arrow going up.
  "servers-cloud": (
    <>
      <path d="M14 34a8 8 0 0 1-1-16 11 11 0 0 1 21-2 8 8 0 0 1 2 18" />
      <path d="M24 40V24M19 29l5-5 5 5" />
      <path d="M14 34h4M30 34h6" opacity={FAINT} />
    </>
  ),
  // A stacked cylinder.
  databases: (
    <>
      <ellipse cx="24" cy="12" rx="14" ry="4" />
      <path d="M10 12v24c0 2 6 4 14 4s14-2 14-4V12" />
      <path d="M10 24c0 2 6 4 14 4s14-2 14-4" opacity={FAINT} />
    </>
  ),
  // A plug meeting its socket.
  "apis-integrations": (
    <>
      <path d="M6 24h4" />
      <path d="M10 16h8a4 4 0 0 1 4 4v8a4 4 0 0 1-4 4h-8z" />
      <path d="M22 20h6M22 28h6" opacity={FAINT} />
      <path d="M30 16h4a4 4 0 0 1 4 4v8a4 4 0 0 1-4 4h-4z" />
      <path d="M38 24h4" />
    </>
  ),
  // Log lines and a warning sign.
  "logs-errors": (
    <>
      <path d="M8 10h20M8 16h16M8 22h12M8 28h8" opacity={FAINT} />
      <path d="M32 18l10 18H22z" />
      <path d="M32 25v5M32 33v0" />
    </>
  ),
  // A shield with a lock.
  security: (
    <>
      <path d="M24 6l14 5v11c0 9-6 16-14 20-8-4-14-11-14-20V11z" />
      <rect x="18" y="22" width="12" height="10" rx="2" />
      <path d="M20 22v-3a4 4 0 0 1 8 0v3" />
      <path d="M24 26v2" opacity={FAINT} />
    </>
  ),
  // A clipboard with a checklist.
  "tickets-tasks": (
    <>
      <path d="M14 10h-2a2 2 0 0 0-2 2v28a2 2 0 0 0 2 2h24a2 2 0 0 0 2-2V12a2 2 0 0 0-2-2h-2" />
      <rect x="16" y="6" width="16" height="6" rx="2" />
      <path d="M16 22l2 2 4-4M16 32l2 2 4-4" />
      <path d="M26 22h8M26 32h8" opacity={FAINT} />
    </>
  ),
  // A ring-bound notebook.
  "notes-memory": (
    <>
      <rect x="10" y="10" width="28" height="32" rx="3" />
      <path d="M16 6v8M24 6v8M32 6v8" />
      <path d="M16 22h16M16 28h16M16 34h10" opacity={FAINT} />
    </>
  ),
  // An envelope and a speech bubble behind it.
  "email-messaging": (
    <>
      <rect x="6" y="16" width="26" height="20" rx="2" />
      <path d="M6 18l13 9 13-9" />
      <path
        d="M28 6h12a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-6l-4 4v-4h-2a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2z"
        opacity={FAINT}
      />
    </>
  ),
  // A contact card.
  "contacts-leads": (
    <>
      <rect x="6" y="10" width="36" height="28" rx="4" />
      <circle cx="17" cy="21" r="4" />
      <path d="M11 32c1-4 4-5 6-5s5 1 6 5" />
      <path d="M28 20h8M28 26h8M28 32h6" opacity={FAINT} />
    </>
  ),
  // A funnel in three levels.
  sales: (
    <>
      <path d="M8 10h32L28 24v12l-8 4V24z" />
      <path d="M14 17h20M18 21h12" opacity={FAINT} />
    </>
  ),
  // A megaphone with sound waves.
  marketing: (
    <>
      <path d="M8 20v8h6l14 8V12l-14 8z" />
      <path d="M14 28l2 8h4l-2-8" />
      <path d="M34 18c2 2 2 10 0 12M38 14c4 4 4 16 0 20" opacity={FAINT} />
    </>
  ),
  // Candles and a trend line.
  "finance-payments": (
    <>
      <path d="M12 18v16M22 12v20M32 16v18" />
      <rect x="10" y="22" width="4" height="8" rx="1" />
      <rect x="20" y="16" width="4" height="10" rx="1" />
      <rect x="30" y="20" width="4" height="10" rx="1" />
      <path d="M8 40l12-6 8 4 14-10" opacity={FAINT} />
    </>
  ),
  // A box, the thing that ships.
  product: (
    <>
      <path d="M24 6l16 8v20l-16 8-16-8V14z" />
      <path d="M8 14l16 8 16-8M24 22v20" />
      <path d="M16 10l16 8" opacity={FAINT} />
    </>
  ),
  // A magnifying glass over a small chart.
  research: (
    <>
      <path d="M10 40V30M16 40V26M22 40V32" opacity={FAINT} />
      <circle cx="28" cy="20" r="9" />
      <path d="M34.5 26.5L42 34" />
    </>
  ),
  // A gear inside a turning arrow.
  operations: (
    <>
      <circle cx="24" cy="24" r="6" />
      <path d="M24 12v4M24 32v4M12 24h4M32 24h4M16 16l2 2M30 30l2 2M32 16l-2 2M18 30l-2 2" />
      <path d="M40 24A16 16 0 0 1 12 34.5M8 24A16 16 0 0 1 36 13.5" opacity={FAINT} />
    </>
  ),
  // A calendar page with a day checked off.
  productivity: (
    <>
      <rect x="8" y="10" width="32" height="30" rx="3" />
      <path d="M16 6v8M32 6v8" />
      <path d="M8 18h32" opacity={FAINT} />
      <path d="M18 29l4 4 8-8" />
      <path d="M14 24h0M34 24h0M14 36h0M34 36h0" opacity={FAINT} />
    </>
  ),
  // A mortarboard over a book.
  education: (
    <>
      <path d="M24 8l18 8-18 8-18-8z" />
      <path d="M14 20v6c0 2 4 4 10 4s10-2 10-4v-6" />
      <path d="M42 16v8" />
      <path d="M10 36c5-2 10-2 14 0 4-2 9-2 14 0v4c-5-2-10-2-14 0-4-2-9-2-14 0z" opacity={FAINT} />
    </>
  ),
  // Linked nodes around a larger one.
  "ai-models-agents": (
    <>
      <circle cx="24" cy="24" r="6" />
      <circle cx="10" cy="10" r="3" />
      <circle cx="38" cy="10" r="3" />
      <circle cx="10" cy="38" r="3" />
      <circle cx="38" cy="38" r="3" />
      <path d="M12 12l7.5 7.5M36 12l-7.5 7.5M12 36l7.5-7.5M36 36l-7.5-7.5" opacity={FAINT} />
    </>
  ),
};

export function CategoryIllustration({ id, className }: { id: CategoryId; className?: string }) {
  return (
    <svg
      viewBox="0 0 48 48"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      data-category={id}
      className={cn("h-12 w-12 shrink-0", className)}
    >
      {DRAWINGS[id]}
    </svg>
  );
}
