#!/usr/bin/env python3
"""Convert MULTI_INBOX_RUN_GUIDE.md to a professional PDF."""

import re
from fpdf import FPDF
from fpdf.enums import XPos, YPos

INPUT_FILE = "MULTI_INBOX_RUN_GUIDE.md"
OUTPUT_FILE = "JobCart_Email_Automation_Guide.pdf"

PAGE_W = 210  # A4 width mm
MARGIN = 10
CONTENT_W = PAGE_W - 2 * MARGIN


def safe(text):
    """Replace non-latin1 chars with safe ASCII equivalents."""
    repl = {
        '\u2014': '-', '\u2013': '-', '\u2018': "'", '\u2019': "'",
        '\u201c': '"', '\u201d': '"', '\u2026': '...', '\u2192': '->',
        '\u2190': '<-', '\u2022': '*', '\u25cf': '*', '\u2502': '|',
        '\u251c': '|', '\u2500': '-', '\u2514': '\\', '\u00bb': '>>',
        '\u00ab': '<<', '\u2264': '<=', '\u2265': '>=',
    }
    for old, new in repl.items():
        text = text.replace(old, new)
    text = re.sub(r'[\U00010000-\U0010FFFF]', '', text)
    return text.encode('latin-1', errors='replace').decode('latin-1')


def strip_md(text):
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'`(.+?)`', r'\1', text)
    text = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', text)
    return safe(text.strip())


class Doc(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 8)
        self.set_text_color(130, 130, 130)
        self.cell(0, 6, "JobCart Email Automation - elsysayla@gmail.com", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="R")
        self.ln(4)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(160, 160, 160)
        self.cell(0, 8, f"Page {self.page_no()}", align="C")


def build():
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        raw_lines = f.readlines()

    pdf = Doc()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    in_code = False
    tbl_rows = []

    def do_table():
        nonlocal tbl_rows
        if not tbl_rows:
            return
        rows = []
        for r in tbl_rows:
            cells = [safe(c.strip()) for c in r.strip().strip("|").split("|")]
            if all(re.match(r'^[-:]+$', c) for c in cells):
                continue
            rows.append(cells)
        tbl_rows = []
        if not rows:
            return
        ncols = max(len(r) for r in rows)
        cw = CONTENT_W / ncols
        for i, row in enumerate(rows):
            while len(row) < ncols:
                row.append("")
            if i == 0:
                pdf.set_font("Helvetica", "B", 8)
                pdf.set_fill_color(41, 65, 122)
                pdf.set_text_color(255, 255, 255)
            else:
                pdf.set_font("Helvetica", "", 8)
                pdf.set_fill_color(245, 245, 250) if i % 2 == 0 else pdf.set_fill_color(255, 255, 255)
                pdf.set_text_color(30, 30, 30)
            for cell in row:
                trunc = cell[:55] if len(cell) > 55 else cell
                pdf.cell(cw, 6, trunc, fill=True)
            pdf.ln(6)
        pdf.set_text_color(0, 0, 0)
        pdf.ln(3)

    for raw in raw_lines:
        line = raw.rstrip("\n")
        s = line.strip()

        # Code fence
        if s.startswith("```"):
            if tbl_rows:
                do_table()
            in_code = not in_code
            if in_code:
                pdf.set_font("Courier", "", 7)
                pdf.set_fill_color(245, 245, 245)
            else:
                pdf.ln(2)
            continue

        if in_code:
            pdf.set_text_color(60, 60, 60)
            txt = safe(line)
            if len(txt) > 100:
                txt = txt[:100] + "..."
            pdf.cell(CONTENT_W, 4, "  " + txt, fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_text_color(0, 0, 0)
            continue

        # Table
        if "|" in s and s.startswith("|"):
            tbl_rows.append(line)
            continue
        elif tbl_rows:
            do_table()

        if not s:
            pdf.ln(2)
            continue

        if s == "---":
            y = pdf.get_y()
            pdf.set_draw_color(210, 210, 210)
            pdf.line(MARGIN, y, PAGE_W - MARGIN, y)
            pdf.ln(4)
            continue

        # H1
        if s.startswith("# "):
            pdf.set_font("Helvetica", "B", 16)
            pdf.set_text_color(20, 45, 90)
            pdf.ln(3)
            pdf.cell(CONTENT_W, 10, strip_md(s[2:]), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_text_color(0, 0, 0)
            pdf.ln(2)
            continue

        # H2
        if s.startswith("## "):
            pdf.set_font("Helvetica", "B", 13)
            pdf.set_text_color(41, 65, 122)
            pdf.ln(3)
            pdf.cell(CONTENT_W, 8, strip_md(s[3:]), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_text_color(0, 0, 0)
            pdf.ln(1)
            continue

        # H3
        if s.startswith("### "):
            pdf.set_font("Helvetica", "B", 10)
            pdf.set_text_color(60, 80, 130)
            pdf.ln(2)
            pdf.cell(CONTENT_W, 7, strip_md(s[4:]), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_text_color(0, 0, 0)
            continue

        # Blockquote
        if s.startswith("> "):
            txt = strip_md(s[2:])
            pdf.set_font("Helvetica", "I", 8)
            pdf.set_text_color(80, 80, 80)
            pdf.set_fill_color(245, 248, 255)
            pdf.multi_cell(CONTENT_W, 5, txt, fill=True)
            pdf.set_text_color(0, 0, 0)
            continue

        # Bullet
        if s.startswith("- ") or s.startswith("* "):
            txt = strip_md(s[2:])
            pdf.set_font("Helvetica", "", 9)
            pdf.cell(5, 5, "*")
            pdf.multi_cell(CONTENT_W - 5, 5, txt)
            continue

        # Normal text
        pdf.set_font("Helvetica", "", 9)
        pdf.multi_cell(CONTENT_W, 5, strip_md(s))

    if tbl_rows:
        do_table()

    pdf.output(OUTPUT_FILE)
    print(f"PDF generated: {OUTPUT_FILE}")


if __name__ == "__main__":
    build()
