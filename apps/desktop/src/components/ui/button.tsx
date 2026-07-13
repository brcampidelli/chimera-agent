import { forwardRef, type ButtonHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

type Variant = "primary" | "ghost" | "outline";
type Size = "sm" | "md" | "icon";

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
}

const variants: Record<Variant, string> = {
  // Gradient pill with a soft accent glow; presses "in" with an inset shadow; disabled loses the glow.
  primary: cn(
    "bg-accent-grad text-accent-foreground shadow-btn",
    "hover:shadow-btn-hover hover:brightness-[1.06]",
    "active:shadow-inset active:brightness-95 active:translate-y-px",
    "disabled:bg-none disabled:bg-muted disabled:text-muted-foreground disabled:shadow-none",
  ),
  outline: cn(
    "border border-white/10 bg-card text-foreground shadow-elev",
    "hover:shadow-glow hover:border-accent/40",
    "active:shadow-inset active:translate-y-px",
    "disabled:opacity-50 disabled:shadow-none",
  ),
  ghost: "text-foreground hover:bg-muted/70 active:bg-muted",
};

const sizes: Record<Size, string> = {
  sm: "h-8 px-3.5 text-sm",
  md: "h-9 px-4 text-sm",
  icon: "h-9 w-9 shrink-0",
};

export const Button = forwardRef<HTMLButtonElement, Props>(
  ({ className, variant = "primary", size = "md", ...props }, ref) => (
    <button
      ref={ref}
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-chip font-medium transition-all duration-150",
        "focus-visible:outline-none focus-visible:shadow-glow",
        "disabled:cursor-not-allowed",
        variants[variant],
        sizes[size],
        className,
      )}
      {...props}
    />
  ),
);
Button.displayName = "Button";
