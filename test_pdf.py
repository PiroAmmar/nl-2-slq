"""
test_pdf.py — Smoke test for write_qa_pdf (one-page-per-question layout).

Run with:   python test_pdf.py
Asserts:
  1. page_meta has exactly one entry per success item.
  2. Every consecutive pair of entries has strictly increasing source_page
     (guarantees one-page-per-question placement is working).
"""

import json
import sys
from pipeline.pdf_writer import write_qa_pdf

# ── Fixture ───────────────────────────────────────────────────────────────────
# Three entries with distinct roles/sections and varying SQL lengths so we
# exercise role-header, section-header, and multi-line SQL code-block paths.
successes = [
    {
        "question": "What brand has the highest unit price?",
        "sql": "SELECT brand, MAX(unit_price) AS max_price FROM products LIMIT 500;",
        "answer": json.dumps({
            "query_type": "lookup",
            "answer": {"title": "Highest Unit Price", "value": "BrightWash"},
            "evidence": [{"label": "Unit Price", "value": "$1,999"}],
            "limitations": "Covers current active SKUs only.",
            "confidence": "SUPPORTED",
        }),
        "role": "Sales Director",
        "section": "Product Performance",
    },
    {
        "question": "Which region had the highest total revenue last quarter?",
        "sql": (
            "SELECT region, SUM(revenue) AS total_revenue\n"
            "FROM sales\n"
            "WHERE strftime('%Y-%m', sale_date) >= '2025-01'\n"
            "  AND strftime('%Y-%m', sale_date) <= '2025-03'\n"
            "GROUP BY region\n"
            "ORDER BY total_revenue DESC\n"
            "LIMIT 500;"
        ),
        "answer": json.dumps({
            "query_type": "ranking",
            "answer": {"title": "Top Revenue Region", "value": "North"},
            "evidence": [{"label": "Revenue", "value": "$4,200,000"}],
            "confidence": "SUPPORTED",
        }),
        "role": "Regional Manager",
        "section": "Revenue Analysis",
    },
    {
        "question": "How many unique customers placed orders this year?",
        "sql": (
            "SELECT COUNT(DISTINCT customer_id) AS unique_customers\n"
            "FROM orders\n"
            "WHERE strftime('%Y', order_date) = '2025'\n"
            "LIMIT 500;"
        ),
        "answer": json.dumps({
            "query_type": "lookup",
            "answer": {"title": "Unique Customers", "value": "12,483"},
            "evidence": [],
            "confidence": "SUPPORTED",
        }),
        "role": "Regional Manager",
        "section": "Customer Insights",
    },
]

# ── Run ───────────────────────────────────────────────────────────────────────
output_path = "output_docs/test_pdf.pdf"
page_meta = write_qa_pdf(successes, output_path)
print(f"Wrote test PDF to {output_path}")
print(f"page_meta: {json.dumps(page_meta, indent=2)}")

# ── Assertions ────────────────────────────────────────────────────────────────
# 1. One page_meta entry per success item.
assert len(page_meta) == len(successes), (
    f"Expected {len(successes)} page_meta entries, got {len(page_meta)}"
)

# 2. Strictly increasing source_page across consecutive entries.
# With one-page-per-question layout each block must start on a later page
# than the previous one.
for i in range(len(page_meta) - 1):
    cur = page_meta[i]["source_page"]
    nxt = page_meta[i + 1]["source_page"]
    assert cur < nxt, (
        f"source_page not strictly increasing at index {i}: "
        f"page_meta[{i}].source_page={cur} >= page_meta[{i+1}].source_page={nxt}"
    )

print("All assertions passed.")
sys.exit(0)
