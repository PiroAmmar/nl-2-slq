// src/components/SqlBlock.tsx

interface Props {
  sql: string;
}

export default function SqlBlock({ sql }: Props) {
  return (
    <details style={{ marginTop: "16px" }}>
      <summary aria-label="View executed SQL" style={{ cursor: "pointer", fontSize: "14px", color: "var(--accent-cyan)", fontWeight: 700, fontFamily: "var(--font-mono)", outline: "none", textTransform: "uppercase" }}>
        [VIEW_EXECUTED_SQL]
      </summary>
      <pre
        style={{
          background: "var(--bg-base)",
          border: "1px solid var(--border-subtle)",
          borderLeft: "4px solid var(--accent-cyan)",
          color: "var(--accent-cyan)",
          borderRadius: 0,
          padding: "16px",
          fontSize: "14px",
          overflowX: "auto",
          marginTop: "12px",
          fontFamily: "var(--font-mono)",
          whiteSpace: "pre-wrap",
          wordBreak: "break-all",
        }}
      >
        {sql}
      </pre>
    </details>
  );
}
