// src/components/SourceButton.tsx
// "Source" button — only rendered on doc_qa cache hits (per spec §1.6)

const BASE_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

interface Props {
  sourcePdf?: string | null;
  sourcePage?: number | null;
}

export default function SourceButton({ sourcePdf, sourcePage }: Props) {
  if (!sourcePdf || sourcePage === undefined || sourcePage === null) return null;

  const url = `${BASE_URL}/output_docs/${encodeURIComponent(sourcePdf)}#page=${sourcePage}`;

  return (
    <div style={{ display: "flex", marginTop: "8px" }}>
      <a
        href={url}
        target="_blank"
        rel="noopener noreferrer"
        aria-label="View source document"
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: "6px",
          padding: "8px 16px",
          background: "transparent",
          color: "var(--accent-magenta)",
          border: "2px solid var(--accent-magenta)",
          borderRadius: 0,
          fontSize: "14px",
          fontWeight: 700,
          fontFamily: "var(--font-mono)",
          textTransform: "uppercase",
          textDecoration: "none",
          cursor: "pointer",
          transition: "all 0.2s ease-out",
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.background = "var(--accent-magenta)";
          e.currentTarget.style.color = "var(--bg-base)";
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.background = "transparent";
          e.currentTarget.style.color = "var(--accent-magenta)";
        }}
      >
        [VIEW_SOURCE_DOCUMENT]
      </a>
    </div>
  );
}
