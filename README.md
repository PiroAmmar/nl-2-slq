# NL-to-SQL Assistant

Natural-language query interface over user-uploaded data.  
**Stack**: FastAPI backend · React (Vite) frontend · ChromaDB semantic cache · Groq LLM · Google Gemini embeddings.

---

## Architecture

```
┌─────────────────────┐       HTTP (CORS)      ┌──────────────────────────────┐
│   React Frontend    │ ─────────────────────▶ │   FastAPI Backend            │
│   Vite + TypeScript │                         │   uvicorn backend.main:app   │
│   TanStack Query    │ ◀────────────────────── │                              │
│   Zustand store     │                         │  ┌──────────────────────┐   │
└─────────────────────┘                         │  │ pipeline/ (pure Py)  │   │
                                                │  │  triage → selector   │   │
                                                │  │  generator → exec    │   │
                                                │  │  responder           │   │
                                                │  └──────────┬───────────┘   │
                                                │             │               │
                                                │  ┌──────────▼───────────┐   │
                                                │  │  ChromaDB cache      │   │
                                                │  │  (golden + doc_qa)   │   │
                                                └──┴──────────────────────┴───┘
```

---

## Environment variables

Create a `.env` file in the project root:

```env
GROQ_API_KEY=gsk_...
GROQ_MODEL_NAME=llama-3.3-70b-versatile
GOOGLE_API_KEY=AIza...
SQL_GUIDE_PATH=path/to/sql_guide.md      # optional: path to SQL rules reference
```

---

## Setup

```powershell
# 1. Create and activate venv
python -m venv venv
venv\Scripts\Activate

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Install frontend dependencies
cd frontend
npm install
cd ..
```

---

## Running

**Backend** (port 8000):
```powershell
venv\Scripts\uvicorn backend.main:app --reload
```

**Frontend** (port 5173):
```powershell
cd frontend
npm run dev
```

Open http://localhost:5173 in your browser.

---

## Features

### Chat interface
1. Upload a CSV or Excel file — the dataset is parsed into SQLite, a `dataset_id` (SHA-256 of file content) is returned.
2. Ask questions in plain English.
3. The pipeline (triage → table selection → SQL generation → execution → NL response) runs server-side.
4. Dynamic Plotly charts are returned as JSON and rendered in the browser.

### Semantic cache
- On upload, 10 golden queries are auto-generated and embedded into ChromaDB.
- Subsequent questions with cosine similarity ≥ threshold are served from cache — zero new LLM calls.
- Cache is keyed per `dataset_id` so different datasets never cross-contaminate.

### Doc-QA PDF cache + "Source" button
1. Place a structured `.docx` question file in `input_docs/` (auto-created).
2. Trigger ingestion via `POST /doc_qa/ingest` with `dataset_id` + `docx_filename`.
3. The pipeline runs every question, writes a PDF to `output_docs/` with **exact page numbers** (recorded via `fpdf2`'s `page_no()` before each block is written).
4. Results are embedded and stored in ChromaDB with `source_type=doc_qa`, `source_pdf`, and `source_page` metadata.
5. When a chat question hits a doc-QA cache entry, the response includes a **"Source"** button that opens the PDF at the exact page.

---

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/datasets/upload` | Upload CSV or Excel, returns `dataset_id` |
| `GET`  | `/datasets/{id}` | Schema + golden_ready status |
| `POST` | `/query` | Ask a question, returns answer + SQL + chart JSON |
| `GET`  | `/dashboard/{id}` | List all cached entries (golden + doc_qa) |
| `POST` | `/doc_qa/ingest` | Trigger doc-QA ingestion job (background) |
| `GET`  | `/doc_qa/status/{job_id}` | Poll ingestion job status |
| `GET`  | `/output_docs/{filename}` | Serve generated PDFs (static) |
| `GET`  | `/health` | Health check |

Interactive docs at http://localhost:8000/docs

---

## Project structure

```
NLP2SQL/
├── backend/                  # FastAPI application
│   ├── main.py               # App entry point, CORS, static mount
│   ├── schemas.py            # Pydantic request/response models
│   ├── session_store.py      # In-memory dataset_id → session map
│   ├── deps.py               # Dependency injection
│   └── routers/
│       ├── datasets.py       # Upload + status
│       ├── query.py          # NL-to-SQL pipeline
│       ├── dashboard.py      # Cache entry listing
│       └── doc_qa.py         # Doc ingestion + status polling
├── pipeline/                 # Framework-agnostic pipeline steps
│   ├── triage.py             # Query classification
│   ├── selector.py           # Table + field selection
│   ├── generator.py          # SQL generation (with rules)
│   ├── executor.py           # SQL execution + verification
│   ├── responder.py          # NL answer + chart
│   ├── cache.py              # ChromaDB golden + doc_qa store/lookup
│   ├── embedding.py          # Google Gemini batch embedding
│   ├── llm.py                # Groq LLM wrapper + CallBudget
│   ├── doc_ingestor.py       # Parse .docx, batch-run pipeline
│   ├── pdf_writer.py         # fpdf2 PDF generation (exact page numbers)
│   └── doc_qa_runner.py      # Orchestrator: parse→run→PDF→embed→store
├── frontend/                 # React (Vite + TypeScript)
│   └── src/
│       ├── api/client.ts     # Typed fetch wrappers
│       ├── state/            # Zustand dataset_id store
│       ├── pages/            # Upload, Chat, Dashboard
│       └── components/       # ChatMessage, ChartRenderer, SourceButton, …
├── input_docs/               # Drop .docx question files here (auto-created)
├── output_docs/              # Generated PDFs served at /output_docs/ (auto-created)
├── chroma_db/                # ChromaDB persistent store
├── requirements.txt
└── .env
```

---

## Cache management

```powershell
# Clear ChromaDB (wipe all golden + doc_qa cache)
.\clear_cache.ps1
```

---

## mistakes.md

Ongoing log of bugs found and fixed during development. See [mistakes.md](mistakes.md).
