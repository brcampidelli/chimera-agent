import { cn } from "@/lib/utils";

/**
 * The Chimera brand mark — the real icon (blue lion + cyan serpent on deep navy),
 * served from /chimera-icon.png. Rounded to match the neumorphic surface language,
 * with an optional accent glow for the hero placement.
 */
export function BrandMark({
  className,
  glow = false,
  alt = "Chimera",
}: {
  className?: string;
  glow?: boolean;
  alt?: string;
}) {
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
    />
  );
}
