import json
from pipeline.pdf_writer import write_qa_pdf

# Mock success entry bypassing the LLM entirely
successes = [
    {
        "question": "What brand has the highest price?",
        "sql": "SELECT brand, max(price) FROM products;",
        "answer": json.dumps({
            "query_type": "lookup",
            "answer": {"title": "Highest Price Brand", "value": "Apple"},
            "evidence": [{"label": "Price", "value": "$1999"}],
            "limitations": "Only covers current active products",
            "confidence": "SUPPORTED"
        }),
        "role": "Sales Director",
        "section": "Product Performance"
    }
]

page_meta = write_qa_pdf(successes, "output_docs/test_pdf.pdf")
print("Wrote test PDF to output_docs/test_pdf.pdf")
