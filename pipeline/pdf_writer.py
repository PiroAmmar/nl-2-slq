"""
pipeline/pdf_writer.py — Generate a Q&A PDF with exact page-number tracking.

Uses fpdf2 (FPDF2). Calls pdf.page_no() BEFORE writing each Q&A block so the
recorded page number is always the page the block *starts* on — never estimated
by character counting.

Usage:
    from pipeline.pdf_writer import write_qa_pdf

    page_meta = write_qa_pdf(successes, output_path)
    # page_meta: [{question, source_pdf, source_page, role, section}]
"""

from __future__ import annotations

import logging
import os
import json
from pathlib import Path

from fpdf import FPDF, XPos, YPos

logger = logging.getLogger(__name__)

# ── layout constants ──────────────────────────────────────────────────────────
_MARGIN = 15          # mm
_LINE_H = 6           # mm — normal line height
_SECTION_GAP = 4      # mm — gap before role/section headers
_FONT_FAMILY = "Helvetica"


class _QaPdf(FPDF):
    def header(self):
        self.set_font(_FONT_FAMILY, "B", 9)
        self.set_text_color(120, 120, 120)
        self.cell(0, 8, "NL-to-SQL Q&A Report", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(2)

    def footer(self):
        self.set_y(-13)
        self.set_font(_FONT_FAMILY, "I", 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 8, f"Page {self.page_no()}", align="C")


def write_qa_pdf(
    successes: list[dict],
    output_path: str,
) -> list[dict]:
    """
    Write Q&A pairs to a PDF at `output_path`.

    Each entry in `successes` must have keys:
        question, sql, answer, role, section

    Returns list of page_meta dicts:
        [{question, source_pdf, source_page, role, section}]
    """
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    pdf = _QaPdf(orientation="P", unit="mm", format="A4")
    pdf.set_margins(_MARGIN, _MARGIN, _MARGIN)
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    source_filename = Path(output_path).name
    page_meta: list[dict] = []

    current_role = None
    current_section = None
    is_first = True  # first item uses the page already added by pdf.add_page() above

    for item in successes:
        question = item["question"]
        sql = item.get("sql", "")
        answer = item.get("answer", "")
        role = item.get("role", "")
        section = item.get("section", "")

        # Force each Q&A block onto its own page.
        # The very first item uses the page already opened above; all subsequent
        # items get a fresh page so the PDF reader can navigate to any question
        # by exact page number without risk of two questions sharing a page.
        if not is_first:
            pdf.add_page()
        is_first = False

        # Print role/section headers when they change
        if role != current_role:
            _write_role_header(pdf, role)
            current_role = role
            current_section = None

        if section != current_section:
            _write_section_header(pdf, section)
            current_section = section

        # ── record page BEFORE writing this Q&A block ──────────────────────
        start_page = pdf.page_no()
        page_meta.append(
            {
                "question": question,
                "source_pdf": source_filename,
                "source_page": start_page,
                "role": role,
                "section": section,
            }
        )

        # ── write Q&A block ─────────────────────────────────────────────────
        _write_question(pdf, question)
        _write_sql_block(pdf, sql)
        _write_answer(pdf, answer)
        pdf.ln(_SECTION_GAP)

        # ── overflow check ──────────────────────────────────────────────────
        # fpdf2's auto-page-break means a very long block can overflow into
        # additional pages even after our forced add_page(). Log a warning so
        # operators can identify questions that need shorter answers.
        end_page = pdf.page_no()
        if end_page - start_page > 1:
            logger.warning(
                "[pdf] Question overflowed onto %d pages: %s",
                end_page - start_page + 1,
                question[:60],
            )

    tmp_path = f"{output_path}.tmp"
    pdf.output(tmp_path)
    os.replace(tmp_path, output_path)   # atomic on same filesystem: readers never see a partial file
    logger.info("PDF written to %s (%d Q&A blocks)", output_path, len(successes))
    return page_meta


# ── formatting helpers ────────────────────────────────────────────────────────

def _write_role_header(pdf: FPDF, role: str) -> None:
    if not role:
        return
    pdf.ln(_SECTION_GAP * 2)
    pdf.set_font(_FONT_FAMILY, "B", 14)
    pdf.set_text_color(30, 80, 160)
    pdf.multi_cell(0, 8, role, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_draw_color(30, 80, 160)
    pdf.set_line_width(0.5)
    pdf.line(pdf.get_x(), pdf.get_y(), pdf.w - _MARGIN, pdf.get_y())
    pdf.ln(3)
    _reset_color(pdf)


def _write_section_header(pdf: FPDF, section: str) -> None:
    if not section:
        return
    pdf.ln(_SECTION_GAP)
    pdf.set_font(_FONT_FAMILY, "BI", 11)
    pdf.set_text_color(60, 60, 60)
    pdf.multi_cell(0, 6, section, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(1)
    _reset_color(pdf)


def _write_question(pdf: FPDF, question: str) -> None:
    pdf.set_font(_FONT_FAMILY, "B", 10)
    pdf.set_text_color(20, 20, 20)
    pdf.multi_cell(0, _LINE_H, f"Q: {question}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(1)


def _write_sql_block(pdf: FPDF, sql: str) -> None:
    if not sql:
        return
    pdf.set_font("Courier", "", 8)
    pdf.set_text_color(50, 50, 50)
    pdf.set_fill_color(240, 240, 245)

    # Box background
    lines = sql.strip().splitlines()
    block_h = len(lines) * 4.5 + 4
    pdf.rect(pdf.get_x(), pdf.get_y(), pdf.w - 2 * _MARGIN, block_h, style="F")

    pdf.set_xy(pdf.get_x() + 2, pdf.get_y() + 2)
    for line in lines:
        pdf.cell(0, 4.5, line, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.ln(2)
    _reset_color(pdf)


def _write_answer(pdf: FPDF, answer: str) -> None:
    pdf.set_font(_FONT_FAMILY, "", 10)
    pdf.set_text_color(20, 20, 20)
    
    try:
        data = json.loads(answer)
        if "answer" in data and "title" in data["answer"]:
            # It's our structured JSON
            ans = data["answer"]
            pdf.set_font(_FONT_FAMILY, "B", 10)
            pdf.multi_cell(0, _LINE_H, f"{ans.get('title', '')}: {ans.get('value', '')}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            
            evidence = data.get("evidence", [])
            if evidence:
                pdf.ln(2)
                pdf.set_font(_FONT_FAMILY, "", 9)
                pdf.set_text_color(50, 50, 50)
                for item in evidence:
                    pdf.multi_cell(0, _LINE_H, f" - {item.get('label')}: {item.get('value')}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            
            limitations = data.get("limitations")
            if limitations:
                pdf.ln(2)
                pdf.set_font(_FONT_FAMILY, "I", 8)
                pdf.set_text_color(120, 100, 100)
                pdf.multi_cell(0, _LINE_H, f"Limitations: {limitations}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            
            _reset_color(pdf)
            return
    except Exception:
        pass # Fallback to raw text

    # Strip markdown bold markers for plain PDF rendering
    clean = answer.replace("**", "").replace("__", "")
    pdf.multi_cell(0, _LINE_H, clean, new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def _reset_color(pdf: FPDF) -> None:
    pdf.set_text_color(0, 0, 0)
    pdf.set_fill_color(255, 255, 255)
