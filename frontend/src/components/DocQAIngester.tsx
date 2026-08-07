// src/components/DocQAIngester.tsx
import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { useDatasetStore } from "../state/datasetStore";

export default function DocQAIngester() {
  const { datasetId } = useDatasetStore();
  const [jobId, setJobId] = useState<string | null>(null);
  const [docxFilename, setDocxFilename] = useState("Demo Objectives Questions.docx");

  const startIngest = useMutation({
    mutationFn: () => api.startDocQAIngest(datasetId!, docxFilename),
    onSuccess: (data) => {
      setJobId(data.job_id);
    },
    onError: (error) => {
      alert(`Failed to start ingestion: ${(error as Error).message}`);
    }
  });

  const { data: status } = useQuery({
    queryKey: ["doc_qa_status", jobId],
    queryFn: () => api.getDocQAStatus(jobId!),
    enabled: !!jobId,
    refetchInterval: (query) => {
      const state = query.state.data?.status;
      return state === "done" || state === "failed" ? false : 2000;
    },
  });

  if (!datasetId) return null;

  return (
    <div style={{ padding: "16px", border: "1px solid var(--border-strong)", background: "var(--bg-elevated)", marginTop: "16px" }}>
      <h3 style={{ fontSize: "14px", fontWeight: 700, color: "var(--accent-magenta)", marginBottom: "8px", textTransform: "uppercase" }}>
        DOC_QA INGESTION
      </h3>
      <input
        type="text"
        value={docxFilename}
        onChange={(e) => setDocxFilename(e.target.value)}
        style={{
          width: "100%",
          padding: "8px",
          marginBottom: "8px",
          background: "var(--bg-base)",
          border: "1px solid var(--border-subtle)",
          color: "var(--text-main)",
          fontFamily: "var(--font-mono)",
          fontSize: "12px"
        }}
        placeholder="Enter .docx filename"
      />
      <button
        onClick={() => startIngest.mutate()}
        disabled={startIngest.isPending || (status && status.status === "running")}
        style={{
          width: "100%",
          padding: "8px",
          background: "var(--accent-base)",
          color: "var(--bg-base)",
          border: "none",
          fontWeight: 700,
          cursor: "pointer",
          textTransform: "uppercase",
          fontFamily: "var(--font-mono)",
          fontSize: "12px",
          transition: "all 0.2s ease-out",
          opacity: startIngest.isPending || status?.status === "running" ? 0.5 : 1
        }}
      >
        {startIngest.isPending ? "STARTING..." : status?.status === "running" ? "INGESTING..." : "PARSE INPUT_DOCS"}
      </button>

      {status && (
        <div style={{ marginTop: "12px", fontSize: "12px", fontFamily: "var(--font-mono)" }}>
          <div style={{ color: status.status === "failed" ? "var(--warning-text)" : status.status === "done" ? "var(--success-text)" : "var(--text-main)" }}>
            STATUS: {status.status.toUpperCase()}
          </div>
          {status.status === "done" && (
            <div style={{ color: "var(--text-muted)", marginTop: "4px" }}>
              SUCCESS: {status.success_count} | FAILED: {status.failure_count}
              {status.pdf_path && <div>PDF Generated in output_docs/</div>}
            </div>
          )}
          {status.error && (
            <div style={{ color: "var(--warning-text)", marginTop: "4px" }}>
              ERROR: {status.error}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
