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

## 15. Single-value results shown as bare dataframe
**Bug:** Single scalar result (e.g. "total sales") rendered in full dataframe widget — ugly.
**Fix:** `row_count == 1 and len(cols) == 1` — `st.metric()` card instead.
