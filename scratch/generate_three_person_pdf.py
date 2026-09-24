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


def parse_major_items(md_path):
    QUICK_FIX_IDS = {
        "B01", "B02", "B03", "B04", "B05", "B06", "N03",
        "D05", "D06", "D08", "D10", "D11", "D12", "D13", "D14", "D15", "D16", "D17", "Q03",
        "C02", "C03", "C10", "R03", "R09", "J01", "J02", "M02", "L06", "S08"
    }
    
    content = Path(md_path).read_text(encoding="utf-8")
    major_rows = []
    for line in content.splitlines():
        if line.startswith("|"):
            parts = [p.strip() for p in line.split("|")[1:-1]]
            if len(parts) >= 8 and re.match(r'^[A-S]\d{2}$', parts[0]):
                status = parts[4]
                item_id = parts[0]
                if ("PARTIALLY" in status or "NOT COMPLETED" in status) and item_id not in QUICK_FIX_IDS:
                    # Determine assignment
                    cat = parts[1]
                    assignee = "Person 1"
                    ui_change = "OCR text viewer & image quality modal"
                    
                    if item_id.startswith("E") or item_id.startswith("F") or item_id.startswith("G"):
                        assignee = "Person 1 (OCR & Preprocessing)"
                        ui_change = "OCR raw text viewer, bounding box overlay & scan quality preview modal"
                    elif item_id.startswith("H") or item_id.startswith("I") or item_id.startswith("J"):
                        assignee = "Person 2 (Extractor & Validation)"
                        ui_change = "Color-coded confidence badges, rule failure list & GIS area mismatch overlay"
                    else:
                        assignee = "Person 3 (Review Queue & Audit Lead)"
                        ui_change = "Dedicated /review page, side-by-side verification drawer & audit log detail"

                    major_rows.append({
                        "id": item_id,
                        "category": parts[1],
                        "module": parts[2],
                        "expected": parts[3],
                        "status": parts[4],
                        "missing": parts[6],
                        "priority": parts[7],
                        "assignee": assignee,
                        "ui_change": ui_change
                    })
    return major_rows


def build_3person_pdf(major_rows, pdf_path):
    doc = SimpleDocTemplate(pdf_path, pagesize=letter, leftMargin=54, rightMargin=54, topMargin=54, bottomMargin=54)
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle('DocTitle', parent=styles['Heading1'], fontName='Helvetica-Bold', fontSize=20, leading=24, textColor=colors.HexColor("#0f172a"), spaceAfter=4)
    subtitle_style = ParagraphStyle('DocSubTitle', parent=styles['Normal'], fontName='Helvetica', fontSize=11, leading=15, textColor=colors.HexColor("#475569"), spaceAfter=12)
    h2_style = ParagraphStyle('H2Style', parent=styles['Heading2'], fontName='Helvetica-Bold', fontSize=13, leading=17, textColor=colors.HexColor("#1e293b"), spaceBefore=14, spaceAfter=6, keepWithNext=True)
    h3_style = ParagraphStyle('H3Style', parent=styles['Heading3'], fontName='Helvetica-Bold', fontSize=10.5, leading=14, textColor=colors.HexColor("#0f172a"), spaceBefore=10, spaceAfter=4, keepWithNext=True)
    body_style = ParagraphStyle('BodyDark', parent=styles['Normal'], fontName='Helvetica', fontSize=8.5, leading=11, textColor=colors.HexColor("#1e293b"))
    bullet_style = ParagraphStyle('Bullet', parent=body_style, leftIndent=12, spaceAfter=3)
    table_cell = ParagraphStyle('Cell', parent=styles['Normal'], fontName='Helvetica', fontSize=7.5, leading=9.5, textColor=colors.HexColor("#1e293b"))
    table_header = ParagraphStyle('Header', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=8, leading=10, textColor=colors.white)

    story = []
    story.append(Paragraph("LandScan AI — Major Updates (3-Person Work Division)", title_style))
    story.append(Paragraph(f"181 Major Modules Divided Across 3 Engineers | Includes Backend & UI/UX Tasks", subtitle_style))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#dc2626"), spaceAfter=12))

    # Executive Team Assignment Box
    story.append(Paragraph("Team Workload Distribution Overview", h2_style))
    
    p1_rows = [r for r in major_rows if "Person 1" in r["assignee"]]
    p2_rows = [r for r in major_rows if "Person 2" in r["assignee"]]
    p3_rows = [r for r in major_rows if "Person 3" in r["assignee"]]

    team_summary_data = [
        [Paragraph("<b>Team Member</b>", table_header), Paragraph("<b>Core Role & Focus</b>", table_header), Paragraph("<b>Modules</b>", table_header), Paragraph("<b>Key UI/UX Deliverable</b>", table_header)],
        [
            Paragraph("<b>Person 1</b>", table_cell),
            Paragraph("OCR Processing & Image Preprocessing Specialist", table_cell),
            Paragraph(f"<b>{len(p1_rows)} Modules</b><br/>(Cat E, F, G)", table_cell),
            Paragraph("OCR raw text viewer, polygon bounding box overlay & scan quality preview modal", table_cell)
        ],
        [
            Paragraph("<b>Person 2</b>", table_cell),
            Paragraph("NLP Field Extractor & Land Rule Validator Lead", table_cell),
            Paragraph(f"<b>{len(p2_rows)} Modules</b><br/>(Cat H, I, J)", table_cell),
            Paragraph("Color-coded confidence badges, rule failure inspection list & GIS area mismatch overlay", table_cell)
        ],
        [
            Paragraph("<b>Person 3</b>", table_cell),
            Paragraph("Human Review Queue, Audit Trail & API Lead", table_cell),
            Paragraph(f"<b>{len(p3_rows)} Modules</b><br/>(Cat K, L, M, N, O, P, Q, S)", table_cell),
            Paragraph("Dedicated /review queue page, side-by-side verification drawer & audit log details", table_cell)
        ],
    ]
    
    t_team = Table(team_summary_data, colWidths=[80, 150, 70, 187])
    t_team.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#1e293b")),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
    ]))
    story.append(t_team)
    story.append(Spacer(1, 12))

    # Person 1 Breakdown
    story.append(Paragraph(f"👨‍💻 PERSON 1: OCR Processing & Image Preprocessing Specialist ({len(p1_rows)} Modules)", h2_style))
    story.append(Paragraph("<b>Backend Tasks:</b>", h3_style))
    story.append(Paragraph("• Build OpenCV preprocessing engine (grayscale, binarization, contrast boost, deskewing) in `app/services/preprocessing.py` (E01–E10).", bullet_style))
    story.append(Paragraph("• Build PaddleOCR / Tesseract engine (`app/services/ocr_service.py`) for PDF rasterization, text line parsing, polygon bounding box generation & multilingual support (F01–F20).", bullet_style))
    story.append(Paragraph("• Build CER / WER OCR ground-truth evaluation script (G01–G10).", bullet_style))
    story.append(Paragraph("<b>UI/UX Deliverables (Person 1):</b>", h3_style))
    story.append(Paragraph("• In-Browser Document OCR Raw Text & Polygon Overlay Inspection Drawer component.", bullet_style))
    story.append(Paragraph("• Document Scan Quality Preview & Preprocessing Toggle Modal in upload window.", bullet_style))
    story.append(Paragraph("• OCR Accuracy Analytics Dashboard Widget (Accuracy by Language & Image Quality).", bullet_style))
    story.append(Spacer(1, 10))

    # Person 2 Breakdown
    story.append(Paragraph(f"👩‍💻 PERSON 2: NLP Field Extractor & Land Rule Validator Lead ({len(p2_rows)} Modules)", h2_style))
    story.append(Paragraph("<b>Backend Tasks:</b>", h3_style))
    story.append(Paragraph("• Build Structured Land Field Extractor (`app/services/extractor.py`): Regex & NLP parsers for Khasra, Khata, Patta, Owner Name, Survey No, Area (ha/sq.m), Village, District, Dates & Multilingual Aliases (H01–H30).", bullet_style))
    story.append(Paragraph("• Build Field & Document Confidence Scoring Engine: Dynamic weighting (OCR score + Regex precision + Gazetteer lookup), score thresholds (I01–I15).", bullet_style))
    story.append(Paragraph("• Build Land Record Business Rule Engine (`app/services/land_validator.py`): Survey/Khasra format checks, Duplicate parcel detection, Joint ownership 100% share sum check, and GIS vs Document area mismatch calculator (J01–J30).", bullet_style))
    story.append(Paragraph("<b>UI/UX Deliverables (Person 2):</b>", h3_style))
    story.append(Paragraph("• Color-Coded Field Confidence Badges (`Green ≥90%`, `Yellow 70-89%`, `Orange 40-69%`, `Red <40%`) with tooltip explanations.", bullet_style))
    story.append(Paragraph("• Land Validation Error & Warning Rule Inspection List Panel in document detail view.", bullet_style))
    story.append(Paragraph("• GIS Map Area Mismatch & Validation Status Layer Toggle (`Green=Verified`, `Yellow=In Review`, `Red=Failed`) on Leaflet map.", bullet_style))
    story.append(Spacer(1, 10))

    # Person 3 Breakdown
    story.append(Paragraph(f"👨‍💻 PERSON 3: Human Review Queue, Audit Trail & API Lead ({len(p3_rows)} Modules)", h2_style))
    story.append(Paragraph("<b>Backend Tasks:</b>", h3_style))
    story.append(Paragraph("• Build Human Review Queue Workflow APIs (`/api/review`, approve, reject, field correction endpoints) and status transitions (K01–K24, P04–P10).", bullet_style))
    story.append(Paragraph("• Extend Cryptographic SHA-256 Audit Log (`app/audit.py`) for OCR completion, field extraction, validation failures, manual corrections & approval events (L03–L08).", bullet_style))
    story.append(Paragraph("• Relational DB Schemas (`DocumentOCRResult`, `ExtractedField`, `ValidationResult`, `ReviewQueue`) & automated test suite (Q04–Q08, S02–S05).", bullet_style))
    story.append(Paragraph("<b>UI/UX Deliverables (Person 3):</b>", h3_style))
    story.append(Paragraph("• Dedicated Human Review Queue Page (`/review`) with searchable/filterable queue table.", bullet_style))
    story.append(Paragraph("• Side-by-Side Reviewer Interface Drawer: Left side original document scan preview, Right side editable extracted field form with Approve, Reject, Escalate buttons and Comment box.", bullet_style))
    story.append(Paragraph("• Reviewer Action History & Cryptographic Audit Log Detail Drawer.", bullet_style))

    # Full Itemized Table
    story.append(PageBreak())
    story.append(Paragraph("Complete Module Assignment Table (181 Modules)", h2_style))
    story.append(Paragraph("Itemized breakdown showing the exact assignment and UI/UX responsibility for every module:", subtitle_style))
    
    table_data = [
        [Paragraph("<b>ID</b>", table_header), Paragraph("<b>Module / Requirement</b>", table_header), Paragraph("<b>Assigned Engineer</b>", table_header), Paragraph("<b>Priority</b>", table_header), Paragraph("<b>UI/UX & Engineering Task</b>", table_header)]
    ]

    for r in major_rows:
        p_color = "#dc2626" if r["priority"] == "P0" else ("#d97706" if r["priority"] == "P1" else "#2563eb")
        table_data.append([
            Paragraph(r["id"], table_cell),
            Paragraph(r["module"], table_cell),
            Paragraph(f"<b>{r['assignee'].split(' ')[0]} {r['assignee'].split(' ')[1]}</b>", table_cell),
            Paragraph(f"<font color='{p_color}'><b>{r['priority']}</b></font>", table_cell),
            Paragraph(f"<b>Task:</b> {r['missing']}<br/><b>UI/UX:</b> {r['ui_change']}", table_cell)
        ])

    t = Table(table_data, colWidths=[30, 140, 75, 40, 202])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#0f172a")),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3.5),
        ('TOPPADDING', (0, 0), (-1, -1), 3.5),
    ]))
    story.append(t)

    doc.build(story, canvasmaker=lambda *args, **kwargs: NumberedCanvas("Major Updates (3-Person Work Division)", *args, **kwargs))
    print(f"Successfully generated updated 3-Person PDF: {pdf_path}")


if __name__ == "__main__":
    md_file = r"c:\Users\mahes\OneDrive\Desktop\projects\landscan26016\PROJECT_COMPLETION_CHECKLIST.md"
    pdf2_path = r"c:\Users\mahes\OneDrive\Desktop\projects\landscan26016\LandScan_AI_Major_Updates_Checklist.pdf"
    
    major_rows = parse_major_items(md_file)
    build_3person_pdf(major_rows, pdf2_path)
