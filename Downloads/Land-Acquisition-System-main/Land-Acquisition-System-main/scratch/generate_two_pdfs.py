import re
from pathlib import Path
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, HRFlowable
)
from reportlab.pdfgen import canvas

class NumberedCanvas(canvas.Canvas):
    def __init__(self, doc_title, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.doc_title = doc_title
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_number(num_pages)
            super().showPage()
        super().save()

    def draw_page_number(self, page_count):
        self.saveState()
        self.setFont("Helvetica", 9)
        self.setFillColor(colors.HexColor("#64748b"))
        
        if self._pageNumber > 1:
            self.drawString(54, 795, f"LandScan AI — {self.doc_title}")
            self.setStrokeColor(colors.HexColor("#cbd5e1"))
            self.setLineWidth(0.5)
            self.line(54, 787, 541, 787)

        page_text = f"Page {self._pageNumber} of {page_count}"
        self.drawRightString(541, 36, page_text)
        self.drawString(54, 36, f"LandScan AI — {self.doc_title}")
        self.setStrokeColor(colors.HexColor("#cbd5e1"))
        self.setLineWidth(0.5)
        self.line(54, 48, 541, 48)
        self.restoreState()


def parse_uncompleted_items(md_path):
    content = Path(md_path).read_text(encoding="utf-8")
    
    rows = []
    for line in content.splitlines():
        if line.startswith("|"):
            parts = [p.strip() for p in line.split("|")[1:-1]]
            if len(parts) >= 8 and re.match(r'^[A-S]\d{2}$', parts[0]):
                status = parts[4]
                if "PARTIALLY" in status or "NOT COMPLETED" in status:
                    rows.append({
                        "id": parts[0],
                        "category": parts[1],
                        "module": parts[2],
                        "expected": parts[3],
                        "status": parts[4],
                        "evidence": parts[5],
                        "missing": parts[6],
                        "priority": parts[7]
                    })
    return rows


# Categorize into Quick Fixes vs Major Updates
QUICK_FIX_IDS = {
    # Branding & Disclaimers
    "B01", "B02", "B03", "B04", "B05", "B06", "N03",
    # Upload Form UI Metadata Selectors
    "D05", "D06", "D08", "D10", "D11", "D12", "D13", "D14", "D15", "D16", "D17", "Q03",
    # Role & UI Labels
    "C02", "C03", "C10", "R03", "R09",
    # Quick Config / Labels
    "J01", "J02", "M02", "L06", "S08"
}

def create_quick_fixes_pdf(quick_rows, pdf_path):
    doc = SimpleDocTemplate(pdf_path, pagesize=letter, leftMargin=54, rightMargin=54, topMargin=54, bottomMargin=54)
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle('DocTitle', parent=styles['Heading1'], fontName='Helvetica-Bold', fontSize=20, leading=24, textColor=colors.HexColor("#0f172a"), spaceAfter=4)
    subtitle_style = ParagraphStyle('DocSubTitle', parent=styles['Normal'], fontName='Helvetica', fontSize=11, leading=15, textColor=colors.HexColor("#475569"), spaceAfter=12)
    h2_style = ParagraphStyle('H2Style', parent=styles['Heading2'], fontName='Helvetica-Bold', fontSize=13, leading=17, textColor=colors.HexColor("#1e293b"), spaceBefore=12, spaceAfter=6, keepWithNext=True)
    body_style = ParagraphStyle('BodyDark', parent=styles['Normal'], fontName='Helvetica', fontSize=8.5, leading=11, textColor=colors.HexColor("#1e293b"))
    table_cell = ParagraphStyle('Cell', parent=styles['Normal'], fontName='Helvetica', fontSize=8, leading=10, textColor=colors.HexColor("#1e293b"))
    table_header = ParagraphStyle('Header', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=8, leading=10, textColor=colors.white)

    story = []
    story.append(Paragraph("LandScan AI — Quick Fixes Checklist (1–2 Min Updates)", title_style))
    story.append(Paragraph(f"Instant Wins & UI Fixes ({len(quick_rows)} Modules) | Execution Time: 1–2 Mins Each", subtitle_style))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#2563eb"), spaceAfter=12))

    story.append(Paragraph("Overview of Quick Fixes", h2_style))
    story.append(Paragraph("These modules represent UI labels, metadata form dropdowns, branding updates, and legal disclaimer banners that can be completed immediately in 1 to 2 minutes without building new backend services.", body_style))
    story.append(Spacer(1, 10))

    table_data = [
        [Paragraph("<b>ID</b>", table_header), Paragraph("<b>Category</b>", table_header), Paragraph("<b>Module / Requirement</b>", table_header), Paragraph("<b>Status</b>", table_header), Paragraph("<b>Action Required (1-2 Mins)</b>", table_header)]
    ]

    for r in quick_rows:
        status_str = "<font color='#d97706'><b>PARTIAL</b></font>" if "PARTIALLY" in r["status"] else "<font color='#dc2626'><b>NOT DONE</b></font>"
        table_data.append([
            Paragraph(r["id"], table_cell),
            Paragraph(r["category"].replace("Category ", ""), table_cell),
            Paragraph(r["module"], table_cell),
            Paragraph(status_str, table_cell),
            Paragraph(r["missing"] if r["missing"] != "None" else "Update UI component label", table_cell)
        ])

    t = Table(table_data, colWidths=[35, 115, 155, 55, 127])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#1e293b")),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(t)
    
    doc.build(story, canvasmaker=lambda *args, **kwargs: NumberedCanvas("Quick Fixes (1-2 Mins)", *args, **kwargs))
    print(f"Created PDF 1: {pdf_path}")


def create_major_updates_pdf(major_rows, pdf_path):
    doc = SimpleDocTemplate(pdf_path, pagesize=letter, leftMargin=54, rightMargin=54, topMargin=54, bottomMargin=54)
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle('DocTitle', parent=styles['Heading1'], fontName='Helvetica-Bold', fontSize=20, leading=24, textColor=colors.HexColor("#0f172a"), spaceAfter=4)
    subtitle_style = ParagraphStyle('DocSubTitle', parent=styles['Normal'], fontName='Helvetica', fontSize=11, leading=15, textColor=colors.HexColor("#475569"), spaceAfter=12)
    h2_style = ParagraphStyle('H2Style', parent=styles['Heading2'], fontName='Helvetica-Bold', fontSize=13, leading=17, textColor=colors.HexColor("#1e293b"), spaceBefore=12, spaceAfter=6, keepWithNext=True)
    body_style = ParagraphStyle('BodyDark', parent=styles['Normal'], fontName='Helvetica', fontSize=8.5, leading=11, textColor=colors.HexColor("#1e293b"))
    table_cell = ParagraphStyle('Cell', parent=styles['Normal'], fontName='Helvetica', fontSize=8, leading=10, textColor=colors.HexColor("#1e293b"))
    table_header = ParagraphStyle('Header', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=8, leading=10, textColor=colors.white)

    story = []
    story.append(Paragraph("LandScan AI — Major Updates Checklist (Dedicated Tasks)", title_style))
    story.append(Paragraph(f"Core Architectural Modules ({len(major_rows)} Modules) | Requires Dedicated Development", subtitle_style))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#dc2626"), spaceAfter=12))

    story.append(Paragraph("Overview of Major Architectural Modules", h2_style))
    story.append(Paragraph("These modules comprise the main Land Record Digitization & Validation pipeline: Image Preprocessing, PaddleOCR Integration, Structured Field Extractors, Validation Rule Engine, Human Review Queue UI, and Analytics Reports.", body_style))
    story.append(Spacer(1, 10))

    table_data = [
        [Paragraph("<b>ID</b>", table_header), Paragraph("<b>Category</b>", table_header), Paragraph("<b>Module / Requirement</b>", table_header), Paragraph("<b>Priority</b>", table_header), Paragraph("<b>Development Requirements</b>", table_header)]
    ]

    for r in major_rows:
        p_color = "#dc2626" if r["priority"] == "P0" else ("#d97706" if r["priority"] == "P1" else "#2563eb")
        table_data.append([
            Paragraph(r["id"], table_cell),
            Paragraph(r["category"].replace("Category ", ""), table_cell),
            Paragraph(r["module"], table_cell),
            Paragraph(f"<font color='{p_color}'><b>{r['priority']}</b></font>", table_cell),
            Paragraph(r["missing"], table_cell)
        ])

    t = Table(table_data, colWidths=[35, 115, 150, 45, 142])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#0f172a")),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(t)
    
    doc.build(story, canvasmaker=lambda *args, **kwargs: NumberedCanvas("Major Updates Checklist", *args, **kwargs))
    print(f"Created PDF 2: {pdf_path}")


if __name__ == "__main__":
    md_file = r"c:\Users\mahes\OneDrive\Desktop\projects\landscan26016\PROJECT_COMPLETION_CHECKLIST.md"
    uncompleted = parse_uncompleted_items(md_file)
    
    quick_rows = [r for r in uncompleted if r["id"] in QUICK_FIX_IDS]
    major_rows = [r for r in uncompleted if r["id"] not in QUICK_FIX_IDS]
    
    print(f"Total uncompleted parsed: {len(uncompleted)} (Quick: {len(quick_rows)}, Major: {len(major_rows)})")
    
    pdf1_path = r"c:\Users\mahes\OneDrive\Desktop\projects\landscan26016\LandScan_AI_Quick_Fixes_1to2Mins.pdf"
    pdf2_path = r"c:\Users\mahes\OneDrive\Desktop\projects\landscan26016\LandScan_AI_Major_Updates_Checklist.pdf"
    
    create_quick_fixes_pdf(quick_rows, pdf1_path)
    create_major_updates_pdf(major_rows, pdf2_path)
