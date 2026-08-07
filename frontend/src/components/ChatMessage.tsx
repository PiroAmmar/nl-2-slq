// src/components/ChatMessage.tsx
import CacheHitBadge from "./CacheHitBadge";
import ChartRenderer from "./ChartRenderer";
import SqlBlock from "./SqlBlock";
import SourceButton from "./SourceButton";
import type { QueryResponse } from "../api/client";
import ResponseRenderer from "./ResponseRenderer";

interface UserMessage {
  role: "user";
  content: string;
}

interface AssistantMessage {
  role: "assistant";
  response: QueryResponse;
}

type Props = UserMessage | AssistantMessage;

export default function ChatMessage(props: Props) {
  if (props.role === "user") {
    return (
      <div style={{ display: "flex", width: "100%", borderBottom: "1px solid var(--border-subtle)", padding: "32px" }}>
        <div style={{ flex: 1 }}>
          <div style={{ color: "var(--accent-base)", fontSize: "14px", fontWeight: 700, fontFamily: "var(--font-mono)", marginBottom: "8px", textTransform: "uppercase" }}>
            [USER_DIRECTIVE]
          </div>
          <div style={{ fontSize: "18px", color: "var(--text-main)", fontWeight: 500, fontFamily: "var(--font-mono)", textTransform: "uppercase" }}>
            {props.content}
          </div>
        </div>
      </div>
    );
  }

  const { response } = props;
  const isError = response.answer?.startsWith("❌ Error:");

  return (
    <div style={{ 
      display: "flex", 
      width: "100%", 
      borderBottom: isError ? "2px solid var(--accent-magenta)" : "1px solid var(--border-subtle)", 
      borderLeft: isError ? "4px solid var(--accent-magenta)" : "none",
      padding: "32px", 
      background: isError ? "var(--bg-base)" : "var(--bg-surface)" 
    }}>
      <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: "24px" }}>
        <div style={{ color: "var(--text-muted)", fontSize: "14px", fontWeight: 700, fontFamily: "var(--font-mono)", textTransform: "uppercase" }}>
          [SYSTEM_RESPONSE]
        </div>

        {response.cache_hit && (
          <CacheHitBadge
            cacheHit={response.cache_hit}
            similarity={response.similarity}
            sourceType={response.source_type}
          />
        )}

        <ResponseRenderer answer={response.answer} />

        {response.chart_json && <ChartRenderer chartJson={response.chart_json} />}

        {response.sql && <SqlBlock sql={response.sql} />}

        <SourceButton sourcePdf={response.source_pdf} sourcePage={response.source_page} />

        {response.row_count !== undefined && (
          <div style={{ fontSize: "12px", color: "var(--text-muted)", marginTop: "8px", display: "flex", gap: "16px", fontFamily: "var(--font-mono)", textTransform: "uppercase" }}>
            <span>ROWS_FETCHED: {response.row_count}</span>
            <span>LLM_CALLS: {response.llm_calls_used ?? "?"}</span>
          </div>
        )}
      </div>
    </div>
  );
}
