"""
NL-to-SQL RAG Pipeline — Streamlit UI (app.py)

Flow per user question:
  0. Semantic cache lookup (ChromaDB + gemini-embedding-001)
  1. Query triage  (Groq)
  2. Table + field selection  (Groq)
  3. SQL generation  (Groq, grounded by sql-guide skill)
  4. Execution + retry + verification  (SQLite + Groq)
  5. NL answer + Plotly chart  (Groq + Plotly)
"""

from __future__ import annotations

import logging
import os
import sqlite3
import tempfile

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from pipeline.llm import CallBudget
from pipeline.embedding import call_embedding
from pipeline import cache as golden_cache
from pipeline.triage import classify_query
from pipeline.selector import select_tables_and_fields
from pipeline.generator import generate_sql
from pipeline.executor import execute_and_verify, ExecutionResult
from pipeline.responder import generate_response

# ── logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── constants ─────────────────────────────────────────────────────────────────
DEFAULT_DB = os.path.join(os.path.dirname(__file__), "students.db")
GOLDEN_QUERY_COUNT = 10

GOLDEN_SYSTEM = """You are a SQL expert. Generate {n} diverse golden SQL queries
for the following database schema. Cover: simple lookups, aggregations, filters,
group-bys, ordering, and edge cases (empty results, max/min, counts).

CRITICAL SQL RULES:
- ALWAYS wrap text column comparisons in LOWER(): LOWER(col) = LOWER('value')
- NEVER use bare equality for string filters: Channel = 'retail' -> LOWER(Channel) = 'retail'
- For LIKE patterns, use: LOWER(col) LIKE LOWER('%pattern%')
- Always add IS NOT NULL filters for aggregation columns.

Schema:
{schema}

Reply with ONLY valid JSON (no markdown):
{{"golden_queries": [{{"question": "...", "sql": "...", "answer": "..."}}]}}"""


# ── page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="NL-to-SQL Assistant",
    page_icon="🗄️",
    layout="wide",
)

st.title("🗄️ NL-to-SQL RAG Assistant")
st.caption("Ask questions about your data in plain English.")


# ── helpers ───────────────────────────────────────────────────────────────────

def _get_schema(db_path: str) -> dict[str, list[str]]:
    """Inspect SQLite schema → {table: [col, ...]}"""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [row[0] for row in cursor.fetchall()]
        schema: dict[str, list[str]] = {}
        for table in tables:
            cursor.execute(f"PRAGMA table_info('{table}');")
            schema[table] = [row[1] for row in cursor.fetchall()]
        conn.close()
        return schema
    except Exception as exc:
        logger.error("Schema inspection failed: %s", exc)
        return {}


def _schema_to_text(schema: dict[str, list[str]]) -> str:
    return "\n".join(f"{t}({', '.join(cols)})" for t, cols in schema.items())


# ── session state ─────────────────────────────────────────────────────────────
if "messages" not in st.session_state:
    st.session_state.messages = []
if "db_path" not in st.session_state:
    st.session_state.db_path = DEFAULT_DB
if "schema" not in st.session_state:
    st.session_state.schema = _get_schema(DEFAULT_DB)
if "dataset_hash" not in st.session_state:
    st.session_state.dataset_hash = "default"
if "golden_ready" not in st.session_state:
    st.session_state.golden_ready = False


def _generate_and_store_golden(db_path: str, schema: dict, dataset_hash: str) -> None:
    """Generate golden queries and store them in ChromaDB (called on file upload)."""
    from pipeline.llm import call_llm
    budget = CallBudget(max_calls=2)
    schema_text = _schema_to_text(schema)

    with st.spinner("Generating golden queries for semantic cache…"):
        try:
            raw = call_llm(
                messages=[
                    {
                        "role": "user",
                        "content": GOLDEN_SYSTEM.format(
                            n=GOLDEN_QUERY_COUNT, schema=schema_text
                        ),
                    }
                ],
                step="golden-gen",
                budget=budget,
                max_tokens=2048,
                temperature=0.3,
            )
            import json
            gqs = json.loads(raw).get("golden_queries", [])
        except Exception as exc:
            st.warning(f"Golden query generation failed: {exc}. Cache disabled for this session.")
            return

    questions = [g["question"] for g in gqs]
    sqls = [g["sql"] for g in gqs]
    answers = [g["answer"] for g in gqs]

    # Batch-embed all golden questions
    embeddings = call_embedding(questions)
    if embeddings is None:
        st.warning("Embedding API unavailable — semantic cache disabled.")
        return

    golden_cache.store_golden(dataset_hash, questions, sqls, answers, embeddings)
    st.success(f"✅ {len(gqs)} golden queries cached for fast lookups.")
    st.session_state.golden_ready = True


def _run_pipeline(question: str) -> None:
    """Full pipeline execution for one user question."""
    # 8 was too tight: 2-pass selection (2) + triage (1) + initial gen (1)
    # + up to 2 regen cycles (4) already = 8, leaving nothing for Step 5.
    budget = CallBudget(max_calls=12)
    db_path: str = st.session_state.db_path
    schema: dict = st.session_state.schema
    dataset_hash: str = st.session_state.dataset_hash

    # ── Step 0: Semantic cache lookup ─────────────────────────────────────────
    question_embedding = call_embedding([question])
    if question_embedding is not None:
        hit = golden_cache.lookup(dataset_hash, question_embedding[0])
        if hit:
            with st.chat_message("assistant"):
                st.success(f"⚡ Cache hit (similarity {hit['similarity']:.2f})")
                st.markdown(hit["answer"])
                st.code(hit["sql"], language="sql")
            st.session_state.messages.append(
                {"role": "assistant", "content": hit["answer"]}
            )
            return

    # ── Step 1: Triage ────────────────────────────────────────────────────────
    try:
        category = classify_query(question, budget)
    except RuntimeError as exc:
        _show_error(str(exc))
        return

    if category == "general":
        _answer_general(question, budget)
        return
    if category == "out_of_scope":
        _show_message("❌ This question is out of scope for the current dataset.")
        return

    # ── Step 2: Table + field selection ──────────────────────────────────────
    try:
        selected_schema = select_tables_and_fields(question, schema, budget)
    except RuntimeError as exc:
        _show_error(str(exc))
        return

    # ── Step 3: Initial SQL generation ───────────────────────────────────────
    try:
        sql = generate_sql(question, selected_schema, budget)
    except RuntimeError as exc:
        _show_error(str(exc))
        return

    # ── Step 4: Execution + verification ─────────────────────────────────────
    def _gen_fn(**kwargs):
        return generate_sql(**kwargs)

    try:
        result: ExecutionResult = execute_and_verify(
            question=question,
            initial_sql=sql,
            selected_schema=selected_schema,
            db_path=db_path,
            budget=budget,
            generate_sql_fn=_gen_fn,
        )
    except RuntimeError as exc:
        _show_error(str(exc))
        return

    if not result.success:
        _show_message(
            f"⚠️ {result.error or 'Query could not be completed. Please rephrase your question.'}"
        )
        return

    # ── Step 5: Response + chart ──────────────────────────────────────────────
    try:
        nl_answer, fig, is_real_answer = generate_response(question, result, budget)
    except RuntimeError as exc:
        _show_error(str(exc))
        return

    with st.chat_message("assistant"):
        st.markdown(nl_answer)

        if result.df is not None and not result.df.empty:
            # Single scalar → metric card
            if result.row_count == 1 and len(result.df.columns) == 1:
                col_name = result.df.columns[0]
                val = result.df.iloc[0, 0]
                st.metric(label=col_name, value=val)
            else:
                if fig is not None:
                    st.plotly_chart(fig, width="stretch")
                # Always show full table below chart
                with st.expander("📋 Full data table", expanded=fig is None):
                    st.dataframe(result.df, width="stretch")
        else:
            st.info("No data returned.")

        with st.expander("🔍 SQL used"):
            st.code(result.sql, language="sql")
        st.caption(
            f"Rows: {result.row_count} | "
            f"LLM calls: {budget.used}/{budget.max_calls} | "
            f"Execution attempts: {result.attempts}"
        )

    # ── handle schema errors — optional re-select ─────────────────────────────
    if result.error_type == "schema":
        st.warning(
            "⚠️ A schema error occurred. The table/field selection may have been incorrect. "
            "Try rephrasing your question or check that the column names match your data."
        )

    st.session_state.messages.append({"role": "assistant", "content": nl_answer})

    # Cache new question+result for future lookups — but never cache a
    # fallback placeholder answer, or it poisons the semantic cache for
    # every future similarly-worded question.
    if question_embedding is not None and result.success and is_real_answer:
        golden_cache.store_golden(
            dataset_hash,
            [question],
            [result.sql],
            [nl_answer],
            [question_embedding[0]],
        )
    elif not is_real_answer:
        logger.warning("Not caching fallback answer for question: %s", question[:80])


def _answer_general(question: str, budget: CallBudget) -> None:
    from pipeline.llm import call_llm
    try:
        answer = call_llm(
            messages=[{"role": "user", "content": question}],
            step="general-answer",
            budget=budget,
            max_tokens=512,
            temperature=0.5,
        )
    except Exception:
        answer = "I'm here to help with data questions. Ask me anything about your dataset!"
    _show_message(answer)


def _show_message(text: str) -> None:
    with st.chat_message("assistant"):
        st.write(text)
    st.session_state.messages.append({"role": "assistant", "content": text})


def _show_error(text: str) -> None:
    with st.chat_message("assistant"):
        st.error(text)
    st.session_state.messages.append({"role": "assistant", "content": text})


# ── sidebar: file upload ──────────────────────────────────────────────────────
with st.sidebar:
    st.header("📂 Dataset")
    uploaded = st.file_uploader(
        "Upload CSV or Excel",
        type=["csv", "xlsx", "xls"],
        help="Upload your data file. A temporary SQLite DB will be created.",
    )

    if uploaded:
        # Hash first (cheap) — Streamlit reruns this block on every interaction
        # (chat message, widget change, etc.) as long as a file sits in the
        # uploader. Only do the expensive work (DB rebuild + golden-query
        # generation) when this is genuinely a *new* file.
        file_bytes = uploaded.getvalue()
        dataset_hash = golden_cache.file_hash(file_bytes)
        already_loaded = (
            dataset_hash == st.session_state.get("dataset_hash")
            and st.session_state.get("golden_ready")
        )

        if already_loaded:
            # Same file already processed this session — just re-show the
            # last-known status, do nothing else.
            n_tables = len(st.session_state.schema)
            st.success(f"Loaded **{uploaded.name}** — {n_tables} table(s)")
        else:
            try:
                tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
                conn = sqlite3.connect(tmp.name)
                total_rows = 0

                if uploaded.name.endswith(".csv"):
                    df_upload = pd.read_csv(uploaded)
                    table_name = os.path.splitext(uploaded.name)[0].replace(" ", "_").lower()
                    df_upload.to_sql(table_name, conn, if_exists="replace", index=False)
                    total_rows = len(df_upload)
                    preview_df = df_upload.head(5)
                else:
                    # Excel: read ALL sheets — returns dict[sheet_name → DataFrame]
                    sheets: dict = pd.read_excel(uploaded, sheet_name=None)
                    preview_df = None
                    for sheet_name, df_sheet in sheets.items():
                        tbl = sheet_name.strip().replace(" ", "_").lower()
                        # Normalise datetime cols → ISO string so SQLite strftime() works
                        for col in df_sheet.select_dtypes(include=["datetime64[ns]", "datetimetz"]).columns:
                            df_sheet[col] = df_sheet[col].dt.strftime("%Y-%m-%d %H:%M:%S")
                        df_sheet.to_sql(tbl, conn, if_exists="replace", index=False)
                        total_rows += len(df_sheet)
                        if preview_df is None:
                            preview_df = df_sheet.head(5)

                conn.close()

                st.session_state.db_path = tmp.name
                st.session_state.schema = _get_schema(tmp.name)
                st.session_state.dataset_hash = dataset_hash
                st.session_state.golden_ready = False
                st.session_state.messages = []

                n_tables = len(st.session_state.schema)
                st.success(
                    f"Loaded **{uploaded.name}** — "
                    f"{n_tables} table(s), {total_rows:,} total rows"
                )
                if preview_df is not None:
                    st.dataframe(preview_df, width="stretch")

                # Generate golden queries — only reached for a genuinely new file
                _generate_and_store_golden(tmp.name, st.session_state.schema, dataset_hash)

            except Exception as exc:
                st.error(f"Upload failed: {exc}")

    st.divider()
    st.subheader("Active schema")
    for table, cols in st.session_state.schema.items():
        st.write(f"**{table}**: {', '.join(cols)}")

    if st.button("🗑️ Clear chat"):
        st.session_state.messages = []
        st.rerun()


# ── chat history ──────────────────────────────────────────────────────────────
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.write(msg["content"])

# ── chat input ────────────────────────────────────────────────────────────────
if question := st.chat_input("Ask a question about your data…"):
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.write(question)
    _run_pipeline(question)