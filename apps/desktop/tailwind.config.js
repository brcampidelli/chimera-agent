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
      },
      // Neumorphic / soft-UI shadows, defined as CSS vars so they adapt per theme (index.css).
      boxShadow: {
        elev: "var(--elev)",
        "elev-lg": "var(--elev-lg)",
        inset: "var(--inset)",
        glow: "var(--glow)",
        btn: "var(--btn-shadow)",
        "btn-hover": "var(--btn-shadow-hover)",
      },
      borderRadius: { chip: "1.5rem", xl2: "1.15rem" },
      fontFamily: {
        sans: ["ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
      backgroundImage: {
        // The signature button/progress gradient: brand blue → brand cyan.
        "accent-grad": "linear-gradient(135deg, hsl(var(--accent)) 0%, hsl(var(--accent2)) 100%)",
      },
    },
  },
  plugins: [],
};
