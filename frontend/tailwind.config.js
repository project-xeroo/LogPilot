/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ["class", '[data-theme="dark"]'],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"Schibsted Grotesk Variable"', "ui-sans-serif", "system-ui", "sans-serif"],
        serif: ['"Source Serif 4 Variable"', "ui-serif", "Georgia", "serif"], // the agent's voice
      },
      colors: {
        paper: "hsl(var(--paper))",
        surface: "hsl(var(--surface))",
        raised: "hsl(var(--raised))",
        ink: "hsl(var(--ink))",
        muted: "hsl(var(--muted))",
        line: "hsl(var(--line))",
        teal: { DEFAULT: "hsl(var(--teal))", soft: "hsl(var(--teal-soft))", ink: "hsl(var(--teal-ink))" },
        amber: { DEFAULT: "hsl(var(--amber))", soft: "hsl(var(--amber-soft))", ink: "hsl(var(--amber-ink))" },
        signal: { DEFAULT: "hsl(var(--signal))", soft: "hsl(var(--signal-soft))", ink: "hsl(var(--signal-ink))" },
        go: { DEFAULT: "hsl(var(--go))", soft: "hsl(var(--go-soft))" },
        chart: "hsl(var(--chart))",
      },
      borderRadius: { control: "6px", panel: "10px" },
      fontSize: {
        xs: ["12px", "16px"],
        sm: ["13.5px", "20px"],
        base: ["15px", "22px"],
        lg: ["17px", "26px"],
        xl: ["21px", "28px"],
        "2xl": ["28px", "34px"],
        "3xl": ["38px", "42px"],
      },
      keyframes: {
        drop: { from: { transform: "translateY(-100%)", opacity: "0" }, to: { transform: "translateY(0)", opacity: "1" } },
        pulseRing: { "0%": { boxShadow: "0 0 0 0 hsl(var(--signal) / .45)" }, "100%": { boxShadow: "0 0 0 10px hsl(var(--signal) / 0)" } },
        breathe: { "0%, 100%": { opacity: "0.45" }, "50%": { opacity: "1" } },
      },
      animation: { drop: "drop .28s cubic-bezier(.2,.8,.2,1)", ring: "pulseRing 1.6s ease-out infinite", breathe: "breathe 1.8s ease-in-out infinite" },
    },
  },
  plugins: [],
};
