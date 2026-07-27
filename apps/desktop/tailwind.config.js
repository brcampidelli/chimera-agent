/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ["class", '[data-theme="dark"]'],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        muted: { DEFAULT: "hsl(var(--muted))", foreground: "hsl(var(--muted-foreground))" },
        card: { DEFAULT: "hsl(var(--card))", foreground: "hsl(var(--card-foreground))" },
        accent: { DEFAULT: "hsl(var(--accent))", foreground: "hsl(var(--accent-foreground))" },
        accent2: "hsl(var(--accent2))",
        primary: { DEFAULT: "hsl(var(--primary))", foreground: "hsl(var(--primary-foreground))" },
        ok: "hsl(var(--ok))",
        bad: "hsl(var(--bad))",
        warn: { DEFAULT: "hsl(var(--warn))", foreground: "hsl(var(--warn-foreground))" },
        // The 1px separator between surfaces. Was written as `border-white/5` everywhere, which is
        // invisible on the light theme's white cards — this is a bug fix wearing a token's clothes.
        hairline: "hsl(var(--hairline))",
        // A panel raised INSIDE another panel. Replaces the ad-hoc `bg-white/[0.05]`.
        "surface-2": "hsl(var(--surface-2))",
        // The fill a row takes under the pointer. Replaces `hover:bg-white/5`.
        "surface-hover": "hsl(var(--surface-hover))",
        // The dim behind a modal — a real design concept, and lighter on the light theme.
        scrim: "hsl(var(--scrim))",
      },
      // Neumorphic / soft-UI shadows, defined as CSS vars so they adapt per theme (index.css).
      boxShadow: {
        elev: "var(--elev)",
        "elev-lg": "var(--elev-lg)",
        inset: "var(--inset)",
        glow: "var(--glow)",
        btn: "var(--btn-shadow)",
        "btn-hover": "var(--btn-shadow-hover)",
        "rail-active": "var(--rail-active)",
        "status-dot": "var(--status-dot)",
        "toggle-on": "var(--toggle-on)",
      },
      borderRadius: { chip: "1.5rem", xl2: "1.15rem" },
      fontFamily: {
        sans: ["ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
      // Five sizes, no more. These are not new values — they codify the scale the app already used
      // via arbitrary utilities (`text-[11px]`, `text-[13px]`, `text-[15px]` recur because they are
      // right). Note this DELIBERATELY overrides Tailwind's defaults: `sm` moves 14px -> 13px, so
      // existing `text-sm` sites shift by 1px once, and every `text-[13px]` becomes `text-sm` with
      // no visual change at all. That is what makes the cleanup a mechanical find-replace.
      fontSize: {
        xs: ["0.6875rem", { lineHeight: "1rem", letterSpacing: "0.01em" }], // 11 — meta, badges
        sm: ["0.8125rem", { lineHeight: "1.25rem" }], //                       13 — UI default
        base: ["0.9375rem", { lineHeight: "1.6" }], //                         15 — prose, chat body
        lg: ["1.125rem", { lineHeight: "1.5rem", letterSpacing: "-0.01em" }], // 18 — screen titles
        xl: ["1.375rem", { lineHeight: "1.75rem", letterSpacing: "-0.02em" }], // 22 — hero
      },
      // Motion vocabulary. Durations climb in perceptual steps, not linear ones.
      transitionDuration: {
        1: "var(--dur-1)", // 120ms — hover, press, anything under the pointer
        2: "var(--dur-2)", // 200ms — overlays entering, list items arriving
        3: "var(--dur-3)", // 320ms — a column sliding into place
        4: "var(--dur-4)", // 520ms — the ambient wash on first paint
      },
      transitionTimingFunction: {
        out: "var(--ease-out)", // the house easing: fast start, long settle
        "in-out": "var(--ease-in-out)",
        spring: "var(--ease-spring)", // overshoots — brand mark and send button only
      },
      backgroundImage: {
        // The signature button/progress gradient: brand blue → brand cyan.
        "accent-grad": "linear-gradient(135deg, hsl(var(--accent)) 0%, hsl(var(--accent2)) 100%)",
      },
    },
  },
  plugins: [],
};
