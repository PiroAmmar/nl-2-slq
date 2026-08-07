// src/components/ResponseRenderer.tsx
import ReactMarkdown from 'react-markdown';

interface Props {
  answer: string;
}

interface StructuredResponse {
  query_type: "lookup" | "ranking" | "comparison" | "trend" | "distribution" | "time_series" | "exploratory";
  answer: {
    title: string;
    value: string | number;
  };
  evidence: Array<{ label: string; value: string | number }>;
  confidence: "SUPPORTED" | "REJECTED";
  limitations?: string;
}

export default function ResponseRenderer({ answer }: Props) {
  let parsed: StructuredResponse | null = null;
  
  // Try to parse the answer as JSON
  try {
    parsed = JSON.parse(answer);
  } catch (e) {
    // Legacy markdown fallback
    return (
      <div className="wrap" style={{ fontSize: "16px", lineHeight: 1.6 }}>
        <ReactMarkdown>{answer}</ReactMarkdown>
      </div>
    );
  }

  // If parsed doesn't have the expected schema, fallback
  if (!parsed || !parsed.query_type || !parsed.answer) {
    return (
      <div className="wrap" style={{ fontSize: "16px", lineHeight: 1.6 }}>
        <ReactMarkdown>{answer}</ReactMarkdown>
      </div>
    );
  }

  const { query_type, answer: ans, evidence, limitations, confidence } = parsed;

  const renderLookup = () => (
    <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
      <div style={{ border: "2px solid var(--border-strong)", padding: "24px", background: "var(--bg-base)" }}>
        <div style={{ fontSize: "14px", fontWeight: 700, color: "var(--text-muted)", textTransform: "uppercase", marginBottom: "8px" }}>
          {ans.title}
        </div>
        <div style={{ fontSize: "48px", fontWeight: 700, color: "var(--accent-base)", lineHeight: 1, textTransform: "uppercase" }}>
          {ans.value}
        </div>
      </div>
      
      {evidence && evidence.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: "8px" }}>
          {evidence.map((item, idx) => (
            <div key={idx} style={{ padding: "8px 12px", border: "1px solid var(--border-subtle)", background: "var(--bg-base)", fontSize: "14px", fontFamily: "var(--font-mono)" }}>
              <span style={{ color: "var(--text-muted)", marginRight: "8px" }}>{item.label}:</span>
              <span style={{ fontWeight: 600, color: "var(--text-main)" }}>{item.value}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );

  const renderTable = () => (
    <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
      <div style={{ borderLeft: "4px solid var(--accent-base)", paddingLeft: "16px" }}>
        <div style={{ fontSize: "20px", fontWeight: 700, color: "var(--text-main)", textTransform: "uppercase", marginBottom: "4px" }}>
          {ans.value}
        </div>
        <div style={{ fontSize: "14px", color: "var(--text-muted)" }}>
          {ans.title}
        </div>
      </div>

      {evidence && evidence.length > 0 && (
        <div style={{ width: "100%", overflowX: "auto", border: "1px solid var(--border-strong)", marginTop: "16px" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", textAlign: "left", fontFamily: "var(--font-mono)", fontSize: "14px" }}>
            <thead>
              <tr style={{ background: "var(--bg-base)", borderBottom: "2px solid var(--border-strong)" }}>
                <th style={{ padding: "12px 16px", textTransform: "uppercase", color: "var(--text-muted)" }}>Metric</th>
                <th style={{ padding: "12px 16px", textTransform: "uppercase", color: "var(--text-muted)" }}>Value</th>
              </tr>
            </thead>
            <tbody>
              {evidence.map((item, idx) => (
                <tr key={idx} style={{ borderBottom: "1px solid var(--border-subtle)", background: idx % 2 === 0 ? "transparent" : "var(--bg-base)" }}>
                  <td style={{ padding: "12px 16px", fontWeight: 600, color: "var(--text-main)" }}>{item.label}</td>
                  <td style={{ padding: "12px 16px", color: "var(--text-main)" }}>{item.value}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );

  const renderDefault = () => (
    <div style={{ display: "flex", flexDirection: "column", gap: "24px" }}>
      <div>
        <h3 style={{ fontSize: "24px", fontWeight: 700, margin: "0 0 8px 0", color: "var(--accent-base)", textTransform: "uppercase" }}>{ans.title}</h3>
        <p style={{ fontSize: "16px", margin: 0, color: "var(--text-main)", lineHeight: 1.6 }}>{ans.value}</p>
      </div>
      
      {evidence && evidence.length > 0 && (
        <div>
          <h4 style={{ fontSize: "14px", fontWeight: 700, color: "var(--text-muted)", textTransform: "uppercase", marginBottom: "12px", borderBottom: "1px solid var(--border-subtle)", paddingBottom: "8px" }}>Evidence</h4>
          <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: "8px" }}>
            {evidence.map((item, idx) => (
              <li key={idx} style={{ display: "flex", alignItems: "flex-start", gap: "12px" }}>
                <span style={{ color: "var(--accent-base)", fontWeight: 700, fontFamily: "var(--font-mono)" }}>&gt;</span>
                <div>
                  <span style={{ fontWeight: 600, color: "var(--text-main)" }}>{item.label}: </span>
                  <span style={{ color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>{item.value}</span>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "16px", width: "100%" }}>
      {query_type === "lookup" 
        ? renderLookup() 
        : (query_type === "ranking" || query_type === "comparison") 
          ? renderTable() 
          : renderDefault()
      }

      {limitations && (
        <details style={{ marginTop: "16px" }}>
          <summary style={{ cursor: "pointer", fontSize: "12px", color: "var(--text-muted)", fontWeight: 700, fontFamily: "var(--font-mono)", outline: "none", textTransform: "uppercase" }}>
            [VIEW_LIMITATIONS]
          </summary>
          <div style={{ 
            marginTop: "8px", 
            padding: "12px", 
            border: "1px dashed var(--warning-border)", 
            background: "var(--bg-base)",
            color: "var(--warning-text)",
            fontSize: "13px",
            lineHeight: 1.5 
          }}>
            {limitations}
          </div>
        </details>
      )}

      {confidence === "REJECTED" && (
        <div style={{ marginTop: "16px", padding: "12px", background: "var(--warning-bg)", color: "var(--warning-text)", border: "2px solid var(--warning-border)", fontWeight: 700, textTransform: "uppercase", fontSize: "14px", fontFamily: "var(--font-mono)" }}>
          WARNING: This analysis was rejected by the internal fact-checker due to insufficient evidence in the SQL result.
        </div>
      )}
    </div>
  );
}
