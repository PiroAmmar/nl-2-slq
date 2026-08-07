// src/components/CacheHitBadge.tsx

interface Props {
  cacheHit: boolean;
  similarity?: number;
  sourceType?: string | null;
}

export default function CacheHitBadge({ cacheHit, similarity, sourceType }: Props) {
  if (!cacheHit) return null;
  const isDocQA = sourceType === "doc_qa";
  const label = isDocQA ? "DOC_CACHE_HIT" : "CACHE_HIT";
  
  return (
    <div style={{ display: "flex" }}>
      <span
        aria-label={`Cache hit badge. Type: ${label}. Similarity: ${similarity !== undefined ? (similarity * 100).toFixed(0) + '%' : 'N/A'}`}
        style={{
          display: "inline-block",
          background: isDocQA ? "var(--info-bg)" : "var(--warning-bg)",
          color: isDocQA ? "var(--info-text)" : "var(--warning-text)",
          border: `2px solid ${isDocQA ? "var(--info-border)" : "var(--warning-border)"}`,
          borderRadius: 0,
          padding: "6px 12px",
          fontSize: "13px",
          fontWeight: 700,
          fontFamily: "var(--font-mono)",
          textTransform: "uppercase",
        }}
      >
        {label} {similarity !== undefined ? `[${(similarity * 100).toFixed(0)}%]` : ""}
      </span>
    </div>
  );
}
