// src/components/ThemeToggle.tsx
import { useTheme } from "./ThemeProvider";

export function ThemeToggle() {
  const { theme, setTheme } = useTheme();

  return (
    <div
      style={{
        display: "flex",
        background: "var(--bg-surface)",
        border: "1px solid var(--border-subtle)",
        borderRadius: "8px",
        padding: "4px",
        gap: "4px",
      }}
    >
      {(["light", "system", "dark"] as const).map((t) => (
        <button
          key={t}
          onClick={() => setTheme(t)}
          style={{
            background: theme === t ? "var(--bg-elevated)" : "transparent",
            color: theme === t ? "var(--text-main)" : "var(--text-muted)",
            border: theme === t ? "1px solid var(--border-subtle)" : "1px solid transparent",
            boxShadow: theme === t ? "var(--shadow-sm)" : "none",
            borderRadius: "4px",
            padding: "4px 8px",
            fontSize: "12px",
            fontWeight: 500,
            cursor: "pointer",
            textTransform: "capitalize",
          }}
        >
          {t === "system" ? "💻" : t === "light" ? "☀️" : "🌙"}
        </button>
      ))}
    </div>
  );
}
