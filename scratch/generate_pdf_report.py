import sys
import re
from pathlib import Path
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether, HRFlowable
)
from reportlab.pdfgen import canvas

class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
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
        
        # Header (pages > 1)
        if self._pageNumber > 1:
            self.drawString(54, 800, "LandScan AI — Project Completion & Audit Report")
            self.setStrokeColor(colors.HexColor("#e2e8f0"))
            self.setLineWidth(0.5)
            self.line(54, 792, 541, 792)

        # Footer
        page_text = f"Page {self._pageNumber} of {page_count}"
        self.drawRightString(541, 36, page_text)
        self.drawString(54, 36, "CONFIDENTIAL — LandScan AI Audit Report")
        self.setStrokeColor(colors.HexColor("#e2e8f0"))
        self.setLineWidth(0.5)
        self.line(54, 48, 541, 48)
        self.restoreState()


def parse_checklist_markdown(md_path):
    content = Path(md_path).read_text(encoding="utf-8")
    
    rows = []
    # parse markdown table lines matching ID pattern (e.g. A01 to S14)
    for line in content.splitlines():
        if line.startswith("|"):
            parts = [p.strip() for p in line.split("|")[1:-1]]
            if len(parts) >= 8 and re.match(r'^[A-S]\d{2}$', parts[0]):
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


def build_pdf(md_path, pdf_path):
    rows = parse_checklist_markdown(md_path)
    
    doc = SimpleDocTemplate(
        pdf_path,
        pagesize=letter,
        leftMargin=54,
        rightMargin=54,
        topMargin=54,
        bottomMargin=54
    )
    
    styles = getSampleStyleSheet()
    
    # Custom styles
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=22,
        leading=26,
        textColor=colors.HexColor("#0f172a"),
        spaceAfter=6
    )
    
    subtitle_style = ParagraphStyle(
        'DocSubTitle',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=12,
        leading=16,
        textColor=colors.HexColor("#475569"),
        spaceAfter=15
    )
    
    h2_style = ParagraphStyle(
        'H2Style',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=14,
        leading=18,
        textColor=colors.HexColor("#1e293b"),
        spaceBefore=14,
        spaceAfter=8,
        keepWithNext=True
    )
    
    body_style = ParagraphStyle(
        'BodyDark',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#1e293b")
    )
    
    table_cell_style = ParagraphStyle(
        'TableCell',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#1e293b")
    )
    
    table_header_style = ParagraphStyle(
        'TableHeader',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=8,
        leading=10,
        textColor=colors.white
    )

    story = []
    
    # Title & Header
    story.append(Paragraph("LandScan AI — Audit & Completion Report", title_style))
    story.append(Paragraph("Intelligent Land Record Digitization & Validation System | Implementation Status", subtitle_style))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#2563eb"), spaceAfter=15))
    
    # Summary Metrics Box
    completed_count = sum(1 for r in rows if r["status"].startswith("✅"))
    partial_count = sum(1 for r in rows if r["status"].startswith("🟡"))
    not_completed_count = sum(1 for r in rows if r["status"].startswith("🔴"))
    total_count = len(rows)
    pct = round(100.0 * completed_count / max(1, total_count), 2)
    
    summary_data = [
        [Paragraph("<b>Metric</b>", table_header_style), Paragraph("<b>Value</b>", table_header_style), Paragraph("<b>Details / Formula</b>", table_header_style)],
        [Paragraph("Total Modules Audited", table_cell_style), Paragraph(f"<b>{total_count}</b>", table_cell_style), Paragraph("Complete list across Categories A through S", table_cell_style)],
        [Paragraph("✅ Completed Modules", table_cell_style), Paragraph(f"<font color='#16a34a'><b>{completed_count}</b></font>", table_cell_style), Paragraph("Fully implemented & functional without mock code", table_cell_style)],
        [Paragraph("🟡 Partially Completed", table_cell_style), Paragraph(f"<font color='#d97706'><b>{partial_count}</b></font>", table_cell_style), Paragraph("UI or backend partially present, pending connection", table_cell_style)],
        [Paragraph("🔴 Not Completed", table_cell_style), Paragraph(f"<font color='#dc2626'><b>{not_completed_count}</b></font>", table_cell_style), Paragraph("Features currently missing from codebase", table_cell_style)],
        [Paragraph("Overall Completion %", table_cell_style), Paragraph(f"<b>{pct}%</b>", table_cell_style), Paragraph(f"({completed_count} / {total_count}) × 100", table_cell_style)],
        [Paragraph("Hackathon Readiness", table_cell_style), Paragraph("<font color='#d97706'><b>Partially Ready</b></font>", table_cell_style), Paragraph("Core backend/GIS/audit working; OCR pipeline pending", table_cell_style)],
        [Paragraph("Overall Audit Score", table_cell_style), Paragraph("<b>57 / 100</b>", table_cell_style), Paragraph("Weighted evaluation across 7 core categories", table_cell_style)],
    ]
    
    t_summary = Table(summary_data, colWidths=[130, 90, 267])
    t_summary.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#1e293b")),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
    ]))
    
    story.append(Paragraph("Executive Summary & Readiness Metrics", h2_style))
    story.append(t_summary)
    story.append(Spacer(1, 15))

    # Category Summary Table
    story.append(Paragraph("Module Breakdown by Category", h2_style))
    
    categories = sorted(list(set(r["category"] for r in rows)))
    cat_summary = [
        [Paragraph("<b>Category</b>", table_header_style), Paragraph("<b>Total</b>", table_header_style), Paragraph("<b>Completed</b>", table_header_style), Paragraph("<b>Partial</b>", table_header_style), Paragraph("<b>Not Done</b>", table_header_style)]
    ]
    
    for cat in categories:
        cat_rows = [r for r in rows if r["category"] == cat]
        tot = len(cat_rows)
        comp = sum(1 for r in cat_rows if r["status"].startswith("✅"))
        part = sum(1 for r in cat_rows if r["status"].startswith("🟡"))
        nd = sum(1 for r in cat_rows if r["status"].startswith("🔴"))
        cat_summary.append([
            Paragraph(cat, table_cell_style),
            Paragraph(str(tot), table_cell_style),
            Paragraph(f"<font color='#16a34a'><b>{comp}</b></font>", table_cell_style),
            Paragraph(f"<font color='#d97706'><b>{part}</b></font>", table_cell_style),
            Paragraph(f"<font color='#dc2626'><b>{nd}</b></font>", table_cell_style),
        ])
        
    t_cat = Table(cat_summary, colWidths=[200, 70, 70, 70, 77])
    t_cat.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#0f172a")),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(t_cat)
    story.append(Spacer(1, 15))

    # Completed Modules Section
    story.append(PageBreak())
    story.append(Paragraph(f"✅ Completed Modules List ({completed_count} Modules)", h2_style))
    story.append(Paragraph("These modules are fully implemented, functional, and integrated into the codebase with no manual database editing required.", subtitle_style))
    
    comp_table_data = [
        [Paragraph("<b>ID</b>", table_header_style), Paragraph("<b>Category</b>", table_header_style), Paragraph("<b>Module / Requirement</b>", table_header_style), Paragraph("<b>Evidence Found</b>", table_header_style)]
    ]
    
    for r in rows:
        if r["status"].startswith("✅"):
            comp_table_data.append([
                Paragraph(r["id"], table_cell_style),
                Paragraph(r["category"].replace("Category ", ""), table_cell_style),
                Paragraph(r["module"], table_cell_style),
                Paragraph(r["evidence"], table_cell_style)
            ])
            
    t_comp = Table(comp_table_data, colWidths=[40, 110, 170, 167])
    t_comp.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#15803d")),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor("#f0fdf4")]),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(t_comp)
    
    # Partially Completed Modules Section
    story.append(PageBreak())
    story.append(Paragraph(f"🟡 Partially Completed Modules ({partial_count} Modules)", h2_style))
    story.append(Paragraph("These modules are partially implemented (e.g. backend exists but UI is missing, or UI exists but uses placeholder values).", subtitle_style))
    
    part_table_data = [
        [Paragraph("<b>ID</b>", table_header_style), Paragraph("<b>Module / Requirement</b>", table_header_style), Paragraph("<b>Evidence Found</b>", table_header_style), Paragraph("<b>What Is Missing</b>", table_header_style)]
    ]
    
    for r in rows:
        if r["status"].startswith("🟡"):
            part_table_data.append([
                Paragraph(r["id"], table_cell_style),
                Paragraph(r["module"], table_cell_style),
                Paragraph(r["evidence"], table_cell_style),
                Paragraph(r["missing"], table_cell_style)
            ])
            
    t_part = Table(part_table_data, colWidths=[40, 140, 150, 157])
    t_part.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#b45309")),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor("#fffbeb")]),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(t_part)

    # Not Completed Modules Overview
    story.append(Spacer(1, 15))
    story.append(Paragraph(f"🔴 Not Completed Modules Overview ({not_completed_count} Modules)", h2_style))
    story.append(Paragraph("The following major feature clusters are currently absent and require development for the full Land Record Digitization & Validation workflow:", body_style))
    story.append(Spacer(1, 6))
    
    missing_clusters = [
        "<b>1. Image Preprocessing (Category E — 10 Modules):</b> Grayscale, denoising, contrast boost, deskewing, binarization using OpenCV.",
        "<b>2. OCR Integration (Category F & G — 30 Modules):</b> PaddleOCR / Tesseract integration, OCR confidence capture, bounding box storage, raw text viewer UI.",
        "<b>3. Land Record Field Extraction (Category H — 30 Modules):</b> Regex / NLP extractors for Khasra, Khata, Patta, Owner Name, Survey No, Area (ha/sq.m), Village, District, Land Use, Mutation/Registration dates.",
        "<b>4. Confidence Scoring (Category I — 15 Modules):</b> Field-level confidence computation, color-coded badges (Green/Yellow/Red), and auto-routing trigger.",
        "<b>5. Land Record Validation Engine (Category J — 27 Modules):</b> Rule checks for Survey format, Khasra format, Duplicate parcel detection, 100% Share sum check, and GIS vs text area mismatch.",
        "<b>6. Human Review Queue (Category K — 24 Modules):</b> Review Queue page (`/review`), side-by-side document image preview vs extracted fields, Approve, Reject, and Field Correction actions.",
        "<b>7. Validation & Accuracy Analytics (Category O & M — 16 Modules):</b> OCR accuracy reports by language and validation error frequency charts.",
    ]
    for cluster in missing_clusters:
        story.append(Paragraph(f"• {cluster}", ParagraphStyle('BulletText', parent=body_style, leftIndent=12, spaceAfter=4)))

    story.append(Spacer(1, 15))
    story.append(Paragraph("Action Plan & Priority Fix Roadmap", h2_style))
    p0_items = [
        "<b>P0-1: Branding & Synthetic Disclaimer:</b> Update header title to 'LandScan AI – Intelligent Land Record Digitization & Validation System' and display persistent legal disclaimer.",
        "<b>P0-2: OCR Integration Engine:</b> Build `app/services/ocr_service.py` to process uploaded PDF/image files.",
        "<b>P0-3: Field Extractor:</b> Build `app/services/extractor.py` to extract Khasra, Khata, Patta, Owner, Area, Village, District, and Dates.",
        "<b>P0-4: Land Validation Engine:</b> Build `app/services/land_validator.py` executing rules for Survey format, Khasra format, Duplicate check, 100% Share sum check, and GIS vs text area mismatch.",
        "<b>P0-5: Human Review Queue:</b> Build `/review` queue frontend view in `app.js` and side-by-side verification drawer in `drawer.js` with Approve, Reject, and Field Correction handlers.",
    ]
    for p0 in p0_items:
        story.append(Paragraph(f"• {p0}", ParagraphStyle('BulletTextP0', parent=body_style, leftIndent=12, spaceAfter=4)))

    doc.build(story, canvasmaker=NumberedCanvas)
    print(f"PDF successfully generated at {pdf_path}")

if __name__ == "__main__":
    md_file = r"c:\Users\mahes\OneDrive\Desktop\projects\landscan26016\PROJECT_COMPLETION_CHECKLIST.md"
    pdf_file = r"c:\Users\mahes\OneDrive\Desktop\projects\landscan26016\LandScan_AI_Project_Completion_Audit.pdf"
    build_pdf(md_file, pdf_file)
