// src/pages/Upload.tsx — File upload + dataset status polling

import { useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { useDatasetStore } from "../state/datasetStore";

export default function Upload() {
  const navigate = useNavigate();
  const { setDataset } = useDatasetStore();
  const [uploadedId, setUploadedId] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const uploadMutation = useMutation({
    mutationFn: (file: File) => api.uploadDataset(file),
    onSuccess: (data) => {
      setUploadedId(data.dataset_id);
      setDataset(data.dataset_id, data.table_names[0] ?? "Dataset");
    },
  });

  // Poll status until golden_ready
  const { data: status } = useQuery({
    queryKey: ["dataset-status", uploadedId],
    queryFn: () => api.getDatasetStatus(uploadedId!),
    enabled: !!uploadedId,
    refetchInterval: (query) =>
      query.state.data?.golden_ready ? false : 2000,
  });

  const handleFile = (file: File) => uploadMutation.mutate(file);

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", padding: "40px", background: "var(--bg-base)" }}>
      <div style={{ marginBottom: "40px" }}>
        <h1 style={{ fontSize: "48px", fontWeight: 700, letterSpacing: "-0.02em", color: "var(--text-main)", textTransform: "uppercase", lineHeight: 1 }}>
          INITIALIZE_
        </h1>
        <p style={{ color: "var(--text-muted)", fontSize: "16px", fontFamily: "var(--font-mono)", marginTop: "8px" }}>
          SYSTEM REQUIRES CSV OR XLSX INPUT TO PROCEED.
        </p>
      </div>

      <div
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          const f = e.dataTransfer.files[0];
          if (f) handleFile(f);
        }}
        onClick={() => fileRef.current?.click()}
        style={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          border: `2px solid ${dragOver ? "var(--accent-base)" : "var(--border-strong)"}`,
          background: dragOver ? "var(--accent-base)" : "var(--bg-surface)",
          color: dragOver ? "var(--bg-base)" : "var(--text-main)",
          cursor: "pointer",
          transition: "none", // Brutalist snap
        }}
      >
        <h2 style={{ fontSize: dragOver ? "64px" : "32px", fontWeight: 700, letterSpacing: "-0.02em", transition: "font-size 0.1s" }}>
          {dragOver ? "DROP_NOW" : "SELECT_FILE"}
        </h2>
        
        <input
          ref={fileRef}
          type="file"
          accept=".csv,.xlsx,.xls"
          style={{ display: "none" }}
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) handleFile(f);
          }}
        />
      </div>

      {uploadMutation.isPending && (
        <div style={{ marginTop: "24px", padding: "16px", border: "2px solid var(--border-strong)", background: "var(--bg-elevated)" }}>
          <p style={{ fontWeight: 600, fontFamily: "var(--font-mono)", fontSize: "14px" }}>&gt; UPLOADING_DATA...</p>
        </div>
      )}
      
      {uploadMutation.isError && (
        <div style={{ marginTop: "24px", padding: "16px", border: "2px solid var(--warning-border)", background: "var(--warning-bg)", color: "var(--warning-text)" }}>
          <p style={{ fontWeight: 700, fontSize: "16px" }}>ERROR_DETECTED</p>
          <p style={{ fontSize: "14px", fontFamily: "var(--font-mono)", marginTop: "4px" }}>{(uploadMutation.error as Error).message}</p>
        </div>
      )}

      {uploadedId && status && (
        <div style={{ marginTop: "24px", border: "2px solid var(--success-border)", background: "var(--success-bg)", padding: "24px" }}>
          <h3 style={{ margin: 0, fontWeight: 700, color: "var(--success-text)", fontSize: "20px" }}>
            SUCCESS: {status.n_tables} TABLE{status.n_tables === 1 ? "" : "S"} MOUNTED
          </h3>
          
          <p style={{ margin: "8px 0 16px", fontSize: "14px", fontFamily: "var(--font-mono)", color: "var(--success-text)" }}>
            {status.golden_ready ? "CACHE: ACTIVE. SYSTEM READY." : "CACHE: GENERATING_GOLDEN_QUERIES..."}
          </p>

          {status.golden_ready && (
            <button
              onClick={() => navigate("/chat")}
              style={{
                padding: "16px 32px",
                background: "var(--accent-base)",
                color: "var(--bg-base)",
                border: "none",
                cursor: "pointer",
                fontWeight: 700,
                fontSize: "18px",
                textTransform: "uppercase",
                boxShadow: "var(--shadow-md)",
              }}
            >
              ACCESS_CONSOLE
            </button>
          )}
        </div>
      )}
    </div>
  );
}
