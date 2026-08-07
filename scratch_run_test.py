import sqlite3
import pandas as pd
from pipeline.doc_qa_runner import ingest

conn = sqlite3.connect("test.db")
df = pd.DataFrame({"id": [1, 2], "name": ["A", "B"]})
df.to_sql("test_table", conn, if_exists="replace", index=False)
conn.close()

schema = {"test_table": ["id", "name"]}
print("Running ingest...")
summary = ingest(
    docx_path="input_docs/Demo Objectives Questions.docx",
    db_path="test.db",
    schema=schema,
    dataset_hash="test_hash_123"
)
print(summary)
