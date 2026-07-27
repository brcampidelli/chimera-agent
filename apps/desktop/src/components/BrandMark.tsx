import type { ImgHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

/**
 * The Chimera brand mark — the real icon (blue lion + cyan serpent on deep navy),
 * served from /chimera-icon.png, with an optional accent glow for the hero placement.
 */
export function BrandMark({
  className,
  glow = false,
  alt = "Chimera",
  ...rest
}: {
  className?: string;
  glow?: boolean;
  alt?: string;
  // Callers reach past the named props for `data-ignite` (the launch sequence) and for the
  // aria-hidden the mark takes when it sits next to its own name.
} & Omit<ImgHTMLAttributes<HTMLImageElement>, "src" | "alt" | "className">) {
  return (
    <img
      src="/chimera-icon.png"
      alt={alt}
      draggable={false}
      className={cn(
        "select-none rounded-lg object-cover",
        glow && "drop-shadow-[0_0_16px_hsl(var(--accent)/0.55)]",
        className,
      )}
      {...rest}
    />
  );
}
