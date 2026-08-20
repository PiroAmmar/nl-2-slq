// src/pages/Chat.tsx
import { useRef, useState, useEffect } from "react";
import { useMutation } from "@tanstack/react-query";
import { api, type QueryResponse } from "../api/client";
import { useDatasetStore } from "../state/datasetStore";
import { useChatStore } from "../state/chatStore";
import ChatMessage from "../components/ChatMessage";

import SchemaSidebar from "../components/SchemaSidebar";

export default function Chat() {
  const { datasetId } = useDatasetStore();
  const { messages, addMessage } = useChatStore();
  const [input, setInput] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);
  
  // Auto-scroll when messages change or while pending
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length]);

  const queryMutation = useMutation({
    mutationFn: (question: string) =>
      api.askQuestion({ dataset_id: datasetId!, question }),
    onSuccess: (response) => {
      addMessage({ role: "assistant", response });
    },
    onError: (err) => {
      const errResponse: QueryResponse = {
        answer: `❌ Error: ${(err as Error).message}`,
        cache_hit: false,
      };
      addMessage({ role: "assistant", response: errResponse });
    },
  });

  const handleSend = () => {
    const q = input.trim();
    if (!q || queryMutation.isPending) return;
    addMessage({ role: "user", content: q });
    setInput("");
    queryMutation.mutate(q);
    setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: "smooth" }), 50);
  };

  return (
    <div style={{ display: "flex", flexDirection: "row", height: "100%", background: "var(--bg-base)" }}>
      {/* Main Console Content */}
      <div style={{ display: "flex", flexDirection: "column", flex: 1, minWidth: 0, borderRight: "2px solid var(--border-strong)" }}>
        
        {/* Top Command Bar */}
        <div style={{ padding: "32px", borderBottom: "2px solid var(--border-strong)", background: "var(--bg-surface)" }}>
          <div style={{ display: "flex", gap: "16px" }}>
            <span style={{ fontSize: "24px", fontWeight: 700, color: "var(--accent-base)", alignSelf: "center", fontFamily: "var(--font-mono)" }}>&gt;</span>
            <textarea
              value={input}
              onChange={(e) => {
                setInput(e.target.value);
                e.target.style.height = 'auto';
                e.target.style.height = Math.min(e.target.scrollHeight, 300) + 'px';
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  handleSend();
                }
              }}
              placeholder="ENTER QUERY DIRECTIVE..."
              rows={1}
              style={{
                flex: 1,
                padding: "20px 24px",
                border: queryMutation.isError ? "2px solid var(--accent-magenta)" : "2px solid var(--border-strong)",
                background: "var(--bg-base)",
                color: "var(--text-main)",
                fontSize: "18px",
                fontFamily: "var(--font-mono)",
                outline: "none",
                borderRadius: 0,
                textTransform: "uppercase",
                resize: "none",
                minHeight: "68px",
                overflowY: "auto",
              }}
            />
            <button
              className="exec-btn"
              onClick={handleSend}
              disabled={queryMutation.isPending || !input.trim()}
              style={{
                padding: "0 40px",
                background: queryMutation.isPending ? "var(--border-subtle)" : "var(--accent-base)",
                color: queryMutation.isPending ? "var(--text-muted)" : "var(--bg-base)",
                border: "2px solid var(--border-strong)",
                cursor: queryMutation.isPending ? "not-allowed" : "pointer",
                fontWeight: 700,
                fontSize: "18px",
                fontFamily: "var(--font-mono)",
                textTransform: "uppercase",
                boxShadow: queryMutation.isPending ? "none" : "var(--shadow-md)",
                borderRadius: 0,
              }}
            >
              {queryMutation.isPending ? "EXEC" : "EXEC"}
            </button>
          </div>
        </div>

        {/* Data Log Area */}
        <div style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column" }}>
          {messages.length === 0 && (
            <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", padding: "40px" }}>
              <div style={{ fontSize: "5vw", fontWeight: 700, fontFamily: "var(--font-mono)", color: "var(--border-subtle)", textTransform: "uppercase", letterSpacing: "-0.05em", lineHeight: 0.9, textAlign: "center" }}>
                AWAITING<br/>COMMAND_
              </div>
            </div>
          )}
          
          <div style={{ width: "100%", display: "flex", flexDirection: "column" }}>
            {messages.map((msg, i) => (
              <ChatMessage key={i} {...(msg as any)} />
            ))}
            
            {queryMutation.isPending && (
              <div style={{ padding: "32px", borderBottom: "1px solid var(--border-subtle)", background: "var(--bg-surface)" }}>
                <span style={{ fontSize: "16px", color: "var(--accent-base)", fontWeight: 700, fontFamily: "var(--font-mono)", textTransform: "uppercase" }}>[SYSTEM: COMPILING_QUERY...]</span>
              </div>
            )}
            <div ref={bottomRef} style={{ height: 1 }} />
          </div>
        </div>
      </div>
      
      {/* Sidebar Area */}
      <SchemaSidebar />
    </div>
  );
}
