import type { Config } from "tailwindcss";

export default {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        bg: "#0b1220",
        panel: "#121a2e",
        accent: "#22c55e",
        warn: "#f59e0b",
        bad: "#ef4444",
        muted: "#64748b",
      },
    },
  },
  plugins: [],
} satisfies Config;
