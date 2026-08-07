// src/pages/Dashboard.tsx — Lists golden queries + doc-QA entries

import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api, type DashboardEntry } from "../api/client";
import { useDatasetStore } from "../state/datasetStore";
import SourceButton from "../components/SourceButton";

export default function Dashboard() {
  const navigate = useNavigate();
  const { datasetId } = useDatasetStore();

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["dashboard", datasetId],
    queryFn: () => api.getDashboard(datasetId!),
    enabled: !!datasetId,
  });

  if (!datasetId) {
    return (
      <div style={{ textAlign: "center", marginTop: "80px" }}>
        <p style={{ color: "var(--text-muted)" }}>No dataset loaded.</p>
        <button 
          onClick={() => navigate("/")}
          style={{
            marginTop: "12px", padding: "8px 16px", background: "var(--bg-surface)",
            color: "var(--text-main)", border: "1px solid var(--border-subtle)",
            borderRadius: "6px", cursor: "pointer"
          }}
        >
          Upload a file
        </button>
      </div>
    );
  }

  const docQA = data?.entries.filter((e) => e.source_type === "doc_qa") ?? [];
  const golden = data?.entries.filter((e) => e.source_type !== "doc_qa") ?? [];

  return (
    <div style={{ maxWidth: "900px", margin: "0 auto", padding: "32px 24px", width: "100%", overflowY: "auto" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "32px" }}>
        <h1 style={{ fontSize: "24px", fontWeight: 700, margin: 0, color: "var(--text-main)" }}>Dashboard</h1>
      </div>

      {!data?.golden_ready && (
        <div style={{ background: "var(--warning-bg)", border: "1px solid var(--warning-border)", color: "var(--warning-text)", padding: "12px 16px", borderRadius: "8px", marginBottom: "24px", fontSize: "14px", fontWeight: 500 }}>
          ⏳ Golden queries are still generating...
        </div>
      )}

      {isLoading && <p style={{ color: "var(--text-muted)" }}>Loading...</p>}
      {isError && <p style={{ color: "var(--warning-text)" }}>Error: {(error as Error).message}</p>}

      <div style={{ display: "flex", flexDirection: "column", gap: "32px" }}>
        {docQA.length > 0 && (
          <section>
            <h2 style={{ fontSize: "16px", fontWeight: 600, marginBottom: "16px", color: "var(--info-text)", borderBottom: "1px solid var(--border-subtle)", paddingBottom: "8px" }}>
              Document Q&A ({docQA.length})
            </h2>
            <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
              {docQA.map((e, i) => <EntryCard key={i} entry={e} />)}
            </div>
          </section>
        )}

        {golden.length > 0 && (
          <section>
            <h2 style={{ fontSize: "16px", fontWeight: 600, marginBottom: "16px", color: "var(--text-main)", borderBottom: "1px solid var(--border-subtle)", paddingBottom: "8px" }}>
              Golden Queries ({golden.length})
            </h2>
            <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
              {golden.map((e, i) => <EntryCard key={i} entry={e} />)}
            </div>
          </section>
        )}

        {data && docQA.length === 0 && golden.length === 0 && (
          <div style={{ textAlign: "center", padding: "40px", background: "var(--bg-surface)", borderRadius: "12px", border: "1px dashed var(--border-strong)" }}>
            <p style={{ color: "var(--text-muted)", margin: 0 }}>No cached entries yet.</p>
          </div>
        )}
      </div>
    </div>
  );
}

function EntryCard({ entry }: { entry: DashboardEntry }) {
  return (
    <div
      style={{
        background: "var(--bg-surface)",
        border: "1px solid var(--border-subtle)",
        borderRadius: "12px",
        padding: "16px",
        transition: "box-shadow 0.2s, border-color 0.2s",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.borderColor = "var(--border-strong)";
        e.currentTarget.style.boxShadow = "var(--shadow-sm)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.borderColor = "var(--border-subtle)";
        e.currentTarget.style.boxShadow = "none";
      }}
    >
      {(entry.role || entry.section) && (
        <p style={{ fontSize: "12px", color: "var(--text-muted)", margin: "0 0 8px", fontWeight: 500 }}>
          {[entry.role, entry.section].filter(Boolean).join(" › ")}
        </p>
      )}
      <p style={{ fontWeight: 600, margin: "0 0 8px", fontSize: "15px", color: "var(--text-main)" }}>{entry.question}</p>
      <p style={{ color: "var(--text-muted)", fontSize: "14px", margin: "0 0 12px", whiteSpace: "pre-wrap", lineHeight: 1.5 }}>{entry.answer}</p>
      <SourceButton sourcePdf={entry.source_pdf} sourcePage={entry.source_page} />
    </div>
  );
}
