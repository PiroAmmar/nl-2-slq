# Guideline for the workflow of the NL-to-SQL RAG pipeline

## Table of Contents
1. [Overview](#1-overview)
2. [Tech Stack Summary](#2-tech-stack-summary)
3. [Pipeline Steps](#3-pipeline-steps)
   - 3.1 [Query Triage](#31-query-triage)
   - 3.2 [Table and Field Selection](#32-table-and-field-selection)
   - 3.3 [SQL Generation](#33-sql-generation)
   - 3.4 [Execution and Verification](#34-execution-and-verification-detailed)
   - 3.5 [Final Response Generation](#35-final-response-generation)
4. [Phase 2: Dynamic Data Visualization](#4-phase-2-dynamic-data-visualization)
5. [LLM Provider: Groq](#5-llm-provider-groq)
6. [Golden Queries & Semantic Caching](#6-golden-queries--semantic-caching-when-an-excel-sheetcsv-is-uploaded)
7. [Rate Limiting & Reliability](#7-rate-limiting--reliability)

---

## 1. Overview

This guideline describes a modular architecture for a Natural-Language-to-SQL RAG system. The workflow is broken into discrete, single-purpose steps to prevent the AI model from becoming overwhelmed and to keep each stage independently controllable, testable, and retry-able.

**High-level flow:**

```
User Query
   │
   ▼
1. Query Triage (general / data / out-of-scope)
   │
   ▼  (data-related)
2. Table & Field Selection
   │
   ▼
3. SQL Generation (grounded by sql-guide skill)
   │
   ▼
4. Execution & Verification (retry + regeneration loops)
   │
   ▼
5. Final Response Generation
   (natural language answer + Phase 2 dynamic chart)
```

---

## 2. Tech Stack Summary

| Component | Choice | Notes |
|---|---|---|
| LLM provider | Groq (`openai/gpt-oss-120b`) | Used for triage, table/field selection, SQL generation, verification, and response generation |
| SQL generation grounding | `sql-guide` skill (local) | Injected into SQL-generation and retry-loop prompts |
| Chart/visualization library | Plotly (`plotly.express`) | Rendered via `st.plotly_chart()` in Streamlit |
| Vector database | ChromaDB (local, persistent, embedded) | One collection per uploaded dataset/schema |
| Embedding model | `gemini-embedding-001` (Google API) | Embeds golden queries + incoming user questions |
| Orchestration | Hand-rolled (no LangChain/LlamaIndex) | Each pipeline stage is its own explicit function/module |
| UI | Streamlit | File upload, chat interface, chart + table rendering |

---

## 3. Pipeline Steps

### 3.1 Query Triage
Every request begins with a user query, which is first categorized as either a **general question**, a **data-related question**, or an **out-of-scope inquiry**.

### 3.2 Table and Field Selection
For data-related questions, the system identifies the relevant database tables and specific fields. For large schemas, this is split into separate steps: table selection first, followed by field selection, to provide the necessary context for the query generator.

### 3.3 SQL Generation
Once the tables and fields are identified, the AI generates a SQL query based on the specific schema provided. This step is grounded using the locally loaded **`sql-guide` skill** — its instructions/examples are injected into the SQL-generation prompt (alongside the selected table/field schema) so the agent follows established query conventions, dialect-specific syntax, and best practices rather than generating SQL from general knowledge alone. The same `sql-guide` skill should also be referenced during the Step 3.4 retry/regeneration loop, so corrected queries stay consistent with the same conventions.

### 3.4 Execution and Verification (detailed)
This step is the core control loop of the pipeline and should be implemented with explicit state tracking rather than a single pass, since both execution and semantic correctness can fail independently.

#### 3.4.1 SQL Execution
- Execute the generated SQL against the database inside a try/except (or equivalent) block. Never execute against a connection with write privileges — use a read-only role/connection for the RAG pipeline.
- Enforce a query timeout (e.g., 10–15s) and a row-limit cap (e.g., `LIMIT 500` injected if not already present) to protect against runaway or unbounded queries.
- Capture and classify execution failures separately, e.g.:
  - **Syntax error** (malformed SQL)
  - **Schema error** (unknown table/column — usually means Step 3.2 selected the wrong fields)
  - **Timeout**
  - **Permission error**

#### 3.4.2 Retry Loop (on execution failure)
- If execution fails, pass the **original question, the generated SQL, and the exact database error message** back to the AI model with an instruction to correct the query.
- Cap retries at a fixed number (e.g., 2–3 attempts) to avoid infinite loops and runaway token usage.
- If a schema-related error occurs, consider routing back to Step 3.2 (Table/Field Selection) rather than only Step 3.3, since the failure may indicate the wrong table/field was chosen — not just a syntax mistake.
- If retries are exhausted, fail gracefully: return a natural-language message to the user explaining the query could not be completed, rather than surfacing a raw stack trace or SQL error.

#### 3.4.3 Result Verification (on execution success)
Execution succeeding does not guarantee the result actually answers the user's question. Add an explicit verification pass:
- Pass the original question, the SQL used, and a preview of the returned rows (e.g., first 5–10 rows + row count) back to the AI model, asking it to judge whether the result plausibly answers the question (e.g., empty result set when a non-empty one was expected, wrong aggregation level, mismatched date range, wrong entity returned).
- If verification fails, trigger a **regeneration** of the SQL query (back to Step 3.3), providing the verifier's reasoning for why the prior result was insufficient, so the regenerated query is more targeted than a blind retry.
- Cap regeneration attempts as well (separate counter from the execution-retry counter above) to bound total pipeline latency/cost.
- Log every execution attempt, error, and verification verdict for observability and later debugging of failure patterns.

#### 3.4.4 State to carry forward
By the end of Step 3.4, the pipeline should have: the final validated SQL, the resulting dataframe/rows, the row count, and column metadata (names + inferred types). This state is required by both Final Response Generation (3.5) and the Phase 2 visualization layer (Section 4).

### 3.5 Final Response Generation
Once valid data is retrieved and verified:
1. Generate a natural language response summarizing/answering the user's question based on the query results.
2. **(Phase 2)** Generate an accompanying chart/graph visualizing the result set, chosen dynamically based on the shape and type of the data (see Section 4).
3. Present the natural language answer and the visualization together in the Streamlit UI — text first, chart alongside/below it.

---

## 4. Phase 2: Dynamic Data Visualization

### 4.1 Framework
**Plotly** (`plotly.express` primarily, with `plotly.graph_objects` for custom cases) is used for chart generation, rendered in Streamlit via `st.plotly_chart(fig, use_container_width=True)`. Plotly was selected over alternatives (Altair, Matplotlib/Seaborn, native `st.*_chart`) because:
- It renders natively and cleanly inside Streamlit.
- Charts are interactive out of the box (hover tooltips, zoom, pan) — useful for inspecting exact values returned by SQL without cluttering the text response.
- `plotly.express` has a concise, predictable API that maps well to programmatic/dynamic chart-type selection driven by an AI model or rule-based logic.

### 4.2 Goal
After Step 3.4 produces a validated result set, automatically determine the most appropriate chart type for that specific result — without requiring the user to manually pick a chart type — and render it alongside the natural language answer.

### 4.3 Chart-Type Selection Logic
Selection can be done either via a deterministic rule-based function (faster, cheaper, recommended as the default) or by asking the LLM to choose (more flexible, higher latency/cost). A hybrid approach is recommended: use rules first, fall back to the LLM only for ambiguous cases.

**Rule-based heuristics** (inspect the verified dataframe's shape and column dtypes):

| Data shape | Recommended chart |
|---|---|
| Single row, single numeric value | KPI / metric card (`st.metric`), not a chart |
| One categorical column + one numeric column, ≤ 15 categories | Bar chart |
| One categorical column + one numeric column, > 15 categories | Horizontal bar chart (or top-N + "other") |
| One datetime/date column + one numeric column | Line chart (time series) |
| One datetime column + one numeric column + one categorical column | Multi-line chart, grouped by category |
| Two numeric columns, many rows | Scatter plot |
| One categorical column + numeric column representing proportions/parts-of-a-whole, ≤ 6–8 categories | Pie / donut chart |
| Multiple numeric columns across shared categories | Grouped or stacked bar chart |
| Wide numeric table (many numeric columns, few rows) | Heatmap |
| No clear pattern / purely tabular / free-text columns | Data table only (`st.dataframe`) — skip chart generation |

**LLM fallback (optional):** For cases the rules don't confidently classify, pass the column names, dtypes, row count, and a small data sample to the model and ask it to return a structured decision, e.g.:

```json
{
  "chart_type": "bar" | "line" | "pie" | "scatter" | "grouped_bar" | "heatmap" | "table_only",
  "x": "column_name",
  "y": "column_name",
  "color": "column_name_or_null",
  "reasoning": "short justification"
}
```

This keeps chart selection structured and machine-actionable rather than free text, so it can be validated before rendering. Note: an LLM fallback call consumes Groq quota just like any other pipeline step — see Section 7 for how this is budgeted.

### 4.4 Rendering
- Implement one small `build_chart(df, decision)` function per chart type using `plotly.express` (e.g., `px.bar`, `px.line`, `px.pie`, `px.scatter`, `px.imshow` for heatmaps).
- Always validate that the chosen `x`/`y`/`color` columns actually exist in the dataframe and have compatible dtypes before calling Plotly — fall back to `table_only` if the decision is invalid, rather than raising an error to the user.
- Apply consistent styling (title generated from the user's question, axis labels from column names, a shared color theme) so charts feel cohesive across different queries.
- If the row count is very large (e.g., > 5,000 rows) for a scatter/line chart, consider down-sampling or aggregating before rendering to keep the UI responsive.

### 4.5 Integration point in the pipeline
Insert this as a sub-step at the end of Step 3.4 / start of Step 3.5:

```
Step 3.4 (Execution + Verification)
        │  validated dataframe + column metadata
        ▼
Step 3.5a — Chart-type selection (rules → LLM fallback if ambiguous)
        │
        ▼
Step 3.5b — Chart rendering (Plotly) + natural language answer generation (parallel or sequential)
        │
        ▼
Streamlit UI: text answer + st.plotly_chart(fig)
```

---

## 5. LLM Provider: Groq

Substitute `genai.Client` with the Groq API client (you can determine your own values for reasoning effort, max completion tokens, top_p, and stop):

```python
from groq import Groq

client = Groq()
completion = client.chat.completions.create(
    model="openai/gpt-oss-120b",
    messages=[
        {
            "role": "user",
            "content": ""
        }
    ],
    temperature=0.1,
    max_completion_tokens=,
    top_p=,
    reasoning_effort="low",
    stream=True,
    stop=None
)

for chunk in completion:
    print(chunk.choices[0].delta.content or "", end="")
```

Note: every pipeline step (triage, table selection, field selection, SQL generation, retries, verification, response generation, and the optional chart-selection fallback) is a separate Groq call. A single user question can trigger 4–8+ calls — this multiplies quickly and is the primary driver for the rate-limiting design in Section 7.

---

## 6. Golden Queries & Semantic Caching (when an Excel sheet/.csv is uploaded)

Generate 8–10 golden queries when the user uploads an Excel sheet/CSV file on the Streamlit interface, and use these to train/ground the model's understanding of the schema. The golden queries should be diverse and cover various scenarios, edge cases, and complexities (e.g., simple lookups, aggregations, filters, joins across sheets/tables if applicable, date-range queries, and empty-result edge cases).

### 6.1 Frameworks
- **Vector database:** ChromaDB — used as a lightweight, embedded (no separate server) local vector store. A persistent `chromadb.PersistentClient` should be used so golden queries survive across app restarts, with one collection per uploaded dataset/schema (e.g., keyed by file name or a schema hash) to avoid cross-dataset contamination.
- **Embedding model:** `gemini-embedding-001` (Google's embedding API) — used to embed the golden questions at storage time and the incoming user question at query time. Since this is a hosted API rather than a local model, wrap the embedding call in the same retry/rate-limit handling described in Section 7, and cache/reuse embeddings for stored golden queries so they aren't re-embedded on every run — only new/unmatched questions need a fresh API call.
- **Orchestration:** Hand-rolled — no LangChain/LlamaIndex. Each pipeline stage (triage → table/field selection → SQL generation → execution/verification → response + chart) remains its own explicit function/module, consistent with the modular step design used throughout this architecture, and the vector store lookup is simply an extra function call inserted before Step 3.1 (see flow below).

### 6.2 Storage & Retrieval flow
Store these SQL queries, along with their questions and retrieved answers, embedded (via `gemini-embedding-001`) in the ChromaDB collection for that dataset. When a new user question comes in:
1. Embed the question with `gemini-embedding-001`.
2. Query ChromaDB for the nearest golden-query embedding(s) and check the similarity score against a threshold (e.g., cosine similarity ≥ 0.85–0.9, tune empirically).
3. **If above threshold:** treat it as a match — return the stored SQL/answer directly (optionally re-executing the stored SQL if the underlying data may have changed, rather than blindly reusing a cached answer).
4. **If below threshold / no match:** fall back to the standard SQL pipeline (Steps 3.1–3.5), and optionally add the new question/query/answer triple back into the ChromaDB collection for future reuse.

This cache lookup also reduces load on both Groq and the embedding API — a cache hit skips the entire Groq pipeline for that question.

---

## 7. Rate Limiting & Reliability

Two external APIs are in the critical path of every user question — **Groq** (multiple calls per question) and **`gemini-embedding-001`** (one call per question, unless served by the semantic cache). Both need explicit rate-limit handling; ChromaDB is local/embedded and not subject to external rate limits.

### 7.1 General pattern (applies to both APIs)
- Wrap every external call in a **retry with exponential backoff + jitter** (e.g., start at 1s, double up to a max of ~16–30s, cap total retries at 3–5) specifically on 429 (rate limit) and 5xx responses. Do not retry on 4xx errors that indicate a bad request (e.g., malformed payload) — fail fast on those instead.
- Respect `Retry-After` response headers when present, rather than relying purely on a fixed backoff schedule.
- Use a **client-side token bucket / semaphore** to cap concurrent in-flight requests per API, so the app never fires more parallel requests than the provider's rate limit allows — this matters especially if multiple Streamlit sessions/users hit the app at once.
- Centralize both API clients behind a single wrapper function (e.g., `call_groq()`, `call_embedding()`) so backoff, concurrency limits, and logging are implemented once and reused by every pipeline step, rather than duplicated at each call site.
- Log every 429/5xx event (endpoint, wait time, retry count) so real-world rate-limit pressure is visible and the limits below can be tuned from actual usage.

### 7.2 Groq-specific
- Since a single question can trigger several sequential Groq calls (triage → selection → generation → verification → response), treat the **per-question call budget** as a first-class constraint, not just per-call limits: cap total Groq calls per question (e.g., ≤ 8, accounting for the capped retry/regeneration loops in 3.4.2/3.4.3) so a pathological retry storm can't multiply usage unexpectedly.
- Where steps don't depend on each other's output, avoid unnecessary sequential calls — but note most steps in this pipeline are intentionally sequential/dependent, so the main lever here is capping retries, not parallelizing.
- Set `max_completion_tokens` conservatively per step (triage and table/field selection need far fewer tokens than SQL generation or the final response) — this reduces both cost and the chance of hitting token-per-minute limits.
- Check current Groq rate limits (requests/min and tokens/min, which vary by model and account tier) via the Groq console and size the concurrency semaphore accordingly, since these limits can change.

### 7.3 Embedding-specific (`gemini-embedding-001`)
- Batch embedding calls where possible — e.g., embed all 8–10 golden queries for a newly uploaded dataset in a single batched request (if the API supports batching) rather than one call per query.
- Cache embeddings for golden queries (Section 6.1) so re-running the app or re-querying the same dataset never re-embeds unchanged golden queries.
- Since the embedding call sits in front of every user question (to check the semantic cache), a failure or rate-limit hit here should **not** block the pipeline entirely — on repeated embedding failure after retries, skip the cache-lookup step and fall through directly to the standard SQL pipeline (Section 3), rather than failing the user's request outright.

### 7.4 Failure UX
- If a rate limit is hit and all retries are exhausted, surface a clear, non-technical message to the user in the Streamlit UI (e.g., "The system is a bit busy right now, please try again in a moment") rather than a raw exception — consistent with the graceful-failure approach already used in Section 3.4.2.
