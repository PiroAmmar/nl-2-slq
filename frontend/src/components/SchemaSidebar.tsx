import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { useDatasetStore } from "../state/datasetStore";
import DocQAIngester from "./DocQAIngester";

export default function SchemaSidebar() {
  const { datasetId } = useDatasetStore();

  const { data, isLoading, isError } = useQuery({
    queryKey: ["dataset_status", datasetId],
    queryFn: () => api.getDatasetStatus(datasetId!),
    enabled: !!datasetId,
  });

  if (isLoading) {
    return <div style={{ padding: "24px", color: "var(--text-muted)", width: "300px", borderLeft: "1px solid var(--border-subtle)" }}>Loading schema...</div>;
  }

  if (isError || !data) {
    return <div style={{ padding: "24px", color: "var(--warning-text)", width: "300px", borderLeft: "1px solid var(--border-subtle)" }}>Failed to load schema.</div>;
  }

  return (
    <div style={{ padding: "24px", height: "100%", width: "300px", overflowY: "auto", borderLeft: "1px solid var(--border-subtle)", background: "var(--bg-surface)" }}>
      <h3 style={{ fontSize: "16px", fontWeight: 600, color: "var(--text-main)", marginBottom: "16px", borderBottom: "1px solid var(--border-subtle)", paddingBottom: "8px" }}>
        Database Schema
      </h3>
      <div style={{ display: "flex", flexDirection: "column", gap: "20px" }}>
        {Object.entries(data.tables).map(([tableName, columns]) => (
          <div key={tableName}>
            <div style={{ fontWeight: 600, fontSize: "14px", color: "var(--accent-base)", marginBottom: "8px" }}>
              {tableName}
            </div>
            <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: "6px" }}>
              {columns.map((col) => (
                <li key={col} style={{ fontSize: "13px", color: "var(--text-muted)", display: "flex", alignItems: "center" }}>
                  <span style={{ display: "inline-block", width: "4px", height: "4px", borderRadius: "50%", background: "var(--text-muted)", marginRight: "8px" }} />
                  {col}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
      <DocQAIngester />
    </div>
  );
}
