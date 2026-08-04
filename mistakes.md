# Mistakes & Fixes Log — NLP2SQL Project

## 1. Function defined after it was called (`app.py`)
**Bug:** `_get_schema()` called at module level (session state init) before defined.
**Error:** `NameError: name '_get_schema' is not defined`
**Fix:** Moved `_get_schema` and `_schema_to_text` above the session state block.

---

## 2. Duplicate sql-guide skill file created in project
**Bug:** Created `sql_guide_skill.md` locally instead of reading from global skill path.
**Fix:** Deleted local copy. `generator.py` now reads directly from
`C:\Users\Syed Ammar Ali\.gemini\config\skills\sql-guide\SKILL.md`.

---

## 3. Stale `groq_llm.py` left after architecture change
**Bug:** Separate `pipeline/groq_llm.py` created during model confusion, superseded by unified `pipeline/llm.py`.
**Fix:** Deleted `groq_llm.py`.

---

## 4. `uv sync` created unwanted `.venv` alongside existing `venv`
**Bug:** `uv sync` bootstrapped a new `.venv` — user already had `venv`.
**Fix:** Deleted `.venv`. All subsequent commands use `.\venv\Scripts\...`.

---

## 5. `pyproject.toml` had `uv_build` backend + `[project.scripts]` with no `src/` package
**Bug:** `uv add` failed: `Expected a Python module at: src\nlp2sql\__init__.py`.
**Fix:** Removed `[build-system]` and `[project.scripts]` sections. Plain `[project]` only.

---

## 6. Excel upload only loaded first sheet
**Bug:** `pd.read_excel(uploaded)` defaults to `sheet_name=0` — rest of sheets ignored.
**Impact:** Multi-sheet FMCG file — only one table in schema.
**Fix:** `pd.read_excel(uploaded, sheet_name=None)` returns all sheets as dict; each written as separate SQLite table.

---

## 7. Excel datetime columns stored as opaque objects in SQLite
**Bug:** Pandas datetime64 columns written raw — SQLite `strftime()` returned NULL — date-based queries returned empty.
**Fix:** Normalise before write:
```python
df[col] = df[col].dt.strftime("%Y-%m-%d %H:%M:%S")
```

---

## 8. String comparisons case-sensitive — silent empty results
**Bug:** `WHERE Channel = 'retail'` fails if data has `'Retail'` or `'RETAIL'`. SQLite `=` is case-sensitive.
**Fix:** Generator system prompt now mandates `LOWER(col) = LOWER('value')` for all string equality filters.

---

## 9. SQL generator could emit `YEAR()` / `MONTH()` — invalid in SQLite
**Bug:** Generator could produce MySQL/Postgres date functions not available in SQLite.
**Fix:** Prompt rule added: always use `strftime('%Y', col)` etc., never `YEAR()`/`MONTH()`.

---

## 10. Missing `IS NOT NULL` on aggregation columns
**Bug:** `AVG(col)` over NULLs silently skews results; no guard in generator.
**Fix:** Prompt rule: always add `IS NOT NULL` filter on aggregated columns.

---

## 11. LLM answer prompt too terse — plain text output
**Bug:** `_ANSWER_SYSTEM` prompted for "2-4 sentences" — no markdown, no bold, no structure.
**Impact:** Output plain, not matching rich report style.
**Fix:** Prompt now requests bold key figures, paragraph structure, bullet breakdown.

---

## 12. `st.write()` used instead of `st.markdown()`
**Bug:** `st.write(nl_answer)` does not render `**bold**` or bullet markdown from LLM.
**Fix:** Changed to `st.markdown(nl_answer)`.

---

## 13. Stray `GOLDE_ANSWER_SYSTEM` constant inserted into `app.py`
**Bug:** Multi-file edit sent answer prompt to wrong file (`app.py` instead of `responder.py`), with typo in name.
**Fix:** Removed stray constant from `app.py`; correct edit applied to `pipeline/responder.py`.

---

## 14. Mixed LLM strategy confusion (Gemini vs Groq)
**Bug:** Multiple back-and-forth changes — Gemini Flash in `llm.py`, then split Groq/Gemini, then unified to Groq-only.
**Impact:** Created `groq_llm.py` as dead code; `pipeline/llm.py` rewritten twice.
**Final state:** All LLM calls — Groq (`pipeline/llm.py`). Embeddings only — `gemini-embedding-001` (`pipeline/embedding.py`).

---

## 16. Fallback placeholder answer cached as if it were real
**Bug:** `_generate_answer()` in `responder.py` caught any `call_llm()` exception (including
`CallBudget` exhaustion) and silently returned `"Retrieved {n} row(s). See the table below for
details."` — `app.py` then wrote this generic string into ChromaDB via `golden_cache.store_golden()`
with no check that it was a real LLM answer.
**Impact:** Once cached, every future question with cosine similarity ≥ 0.88 replayed the same
broken placeholder forever, even after the underlying pipeline bug was fixed. Root trigger was
usually a case-sensitive string filter (`Channel = 'retail'` vs stored `'Retail'`) causing a 0-row
result → verifier rejection → regeneration loop → `CallBudget` exhausted before Step 5.
**Fix:** `_generate_answer()` / `generate_response()` now return `(text, is_real_answer)`.
`app.py` only calls `golden_cache.store_golden()` when `is_real_answer is True`.

---

## 17. `CallBudget(max_calls=8)` too tight for large schemas
**Bug:** 10-table schema → 2-pass table/field selection (2 calls) + triage (1) + initial SQL gen (1)
= 4 calls before execution even starts. Two regeneration cycles (verifier rejection) cost 2 calls
each, pushing total usage to 8+ before Step 5 (responder) ever runs — see #16.
**Fix:** Raised `CallBudget(max_calls=8)` → `12` in `app.py::_run_pipeline`.

---

## 18. Golden queries regenerated on every chat message, not just on upload
**Bug:** `if uploaded:` block in `app.py` ran on every Streamlit script rerun — which happens on
every chat message, not just on file upload — because `st.file_uploader` keeps returning the same
`UploadedFile` across reruns as long as it's still shown in the widget. `st.session_state.golden_ready`
existed but was never checked as a guard; it was unconditionally reset to `False` and the whole
upload-processing block (DB rebuild, schema reload, `st.session_state.messages = []`, golden-query
generation) re-ran every turn. Confirmed in production logs: `[golden-gen]` fired twice for the same
`dataset_hash` a minute apart with no re-upload in between.
**Impact:** Wasted Groq + embedding calls every question, extra latency, and silently wiped chat
history (`messages = []`) on every turn.
**Fix:** Compute `dataset_hash` first (cheap), skip the entire upload-processing block when
`dataset_hash == st.session_state.dataset_hash and st.session_state.golden_ready` is already true.

---

## 19. Cache-hit answers rendered with `st.write` instead of `st.markdown`
**Bug:** Mistake #12 fixed `st.write` → `st.markdown` for the main pipeline response, but missed
the semantic-cache-hit render path — cached answers showed raw `**bold**`/bullet markdown as plain
text instead of rendering it.
**Fix:** `st.write(hit["answer"])` → `st.markdown(hit["answer"])` in the cache-hit branch.

---

## 20. `use_container_width` deprecated in Streamlit
**Bug:** `use_container_width=True` on `st.plotly_chart` / `st.dataframe` throws a deprecation
warning every run (removed after 2025-12-31).
**Fix:** Replaced with `width="stretch"` at all three call sites.

---

## 21. Hardcoded absolute Windows path for sql-guide skill
**Known issue (not yet fixed):** `generator.py` loads
`C:\Users\Syed Ammar Ali\.gemini\config\skills\sql-guide\SKILL.md` — works locally but silently
falls back to `_SQL_GUIDE = ""` (with only a log warning) on any other machine, CI runner, or
deployment target. Should be replaced with an env var (e.g. `SQL_GUIDE_PATH`) with the current
path as a default, or a path relative to the repo.

