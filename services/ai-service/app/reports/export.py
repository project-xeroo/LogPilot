"""Report export: Markdown and PDF (fpdf2 - pure Python, no system dependencies)."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from fpdf import FPDF

KIND_LABEL = {"incident": "Incident Report", "pre_mortem": "Pre-Mortem Report", "executive_summary": "Executive Summary"}


def to_markdown(report: dict[str, Any]) -> str:
    lines = [f"# {report['title']}", "",
             f"*{KIND_LABEL.get(report['kind'], 'Report')} · status: {report['status']} · generated {report['created_at'][:16].replace('T', ' ')} UTC by LogPilot Agent*", ""]
    if report["status"] != "approved":
        lines += ["> **DRAFT** – agent-authored; not yet signed off by a human reviewer.", ""]
    for s in report["sections"]:
        lines += [f"## {s['title']}", "", s["body"].strip(), ""]
    return "\n".join(lines).rstrip() + "\n"


def _latin(s: str) -> str:
    """Core PDF fonts are latin-1; map common unicode punctuation and drop the rest."""
    table = {"–": "-", "—": "-", "‘": "'", "’": "'", "“": '"', "”": '"', "•": "-", "→": "->",
             "…": "...", "²": "2", "·": "-", "≥": ">=", "≤": "<=", "×": "x"}
    for k, v in table.items():
        s = s.replace(k, v)
    return s.encode("latin-1", "replace").decode("latin-1")


def _strip_md(s: str) -> str:
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"(?<!\*)\*(?!\*)(.+?)\*", r"\1", s)
    s = re.sub(r"`(.+?)`", r"\1", s)
    return s


def to_pdf(report: dict[str, Any]) -> bytes:
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.set_margins(18, 18, 18)
    pdf.add_page()
    w = pdf.w - pdf.l_margin - pdf.r_margin

    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(90, 100, 120)
    pdf.cell(w, 5, _latin(f"LOGPILOT AGENT  |  {KIND_LABEL.get(report['kind'], 'REPORT').upper()}"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(20, 24, 36)
    pdf.multi_cell(w, 8, _latin(report["title"]), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(90, 100, 120)
    ts = report["created_at"][:16].replace("T", " ")
    pdf.cell(w, 5, _latin(f"Status: {report['status'].upper()}   |   Generated {ts} UTC"), new_x="LMARGIN", new_y="NEXT")
    if report["status"] != "approved":
        pdf.ln(1)
        pdf.set_fill_color(255, 244, 214)
        pdf.set_text_color(120, 80, 0)
        pdf.multi_cell(w, 6, _latin("DRAFT - agent-authored, awaiting human sign-off."), fill=True, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    for s in report["sections"]:
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(20, 24, 36)
        pdf.cell(w, 8, _latin(s["title"]), new_x="LMARGIN", new_y="NEXT")
        pdf.set_draw_color(210, 214, 224)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + w, pdf.get_y())
        pdf.ln(2)
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(40, 44, 56)
        for raw in s["body"].strip().splitlines():
            line = _strip_md(raw.rstrip())
            if not line.strip():
                pdf.ln(2)
                continue
            m = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", line)
            if m:
                indent = 4 + len(m.group(1)) * 2
                pdf.set_x(pdf.l_margin + indent)
                bullet = "-" if m.group(2) in "-*" else m.group(2)
                pdf.multi_cell(w - indent, 5.2, _latin(f"{bullet} {m.group(3)}"), new_x="LMARGIN", new_y="NEXT")
            else:
                pdf.multi_cell(w, 5.2, _latin(line.lstrip("> ")), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)
    pdf.set_y(-14)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(130, 136, 150)
    pdf.cell(w, 5, _latin(f"LogPilot AI Agent - {datetime.now().strftime('%Y-%m-%d')} - Confidential"), align="C")
    return bytes(pdf.output())
