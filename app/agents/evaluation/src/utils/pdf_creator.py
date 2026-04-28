from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import LongTable, Paragraph, SimpleDocTemplate, Spacer, TableStyle

# -----------------------------
# SAMPLE JSON INPUT (replace with your actual object)
# Expected structure:
# {
#   "Security": {
#       "Assessments": [...],
#       "Final_Summary": {...}
#   }
# }
# -----------------------------
CAF_URL = "https://www.ncsc.gov.uk/collection/caf"
sample_json = {
    "Security": {
        "Assessments": [
            {
                "Question": "Is authentication defined?",
                "Rating": "Green",
                "Comments": "Section 2.1 describes Azure AD SSO, OAuth2 token flows, and mandatory MFA for all privileged roles.",
                "Reference": {"text": "B2.a", "url": CAF_URL},
            },
            {
                "Question": "Is logging and monitoring implemented?",
                "Rating": "Amber",
                "Comments": "Section 4.3 states that logs are collected in Splunk, but monitoring rules for privileged access are still under development.",
                "Reference": {"text": "C1.a", "url": CAF_URL},
            },
            {
                "Question": "Is data encrypted at rest and in transit?",
                "Rating": "Red",
                "Comments": "TLS noted; no at-rest encryption or KMS details provided.",
                "Reference": {"text": "B3.c", "url": CAF_URL},
            },
        ],
        "Final_Summary": {
            "Interpretation": "Minor gaps - needs remediation",
            "Overall_Comments": "Strong alignment for authentication controls. Logging and monitoring require completion of alerting rules. Encryption at rest is not addressed and should be prioritized.",
        },
    }
}

# -----------------------------
# Resolve top-level section (e.g., "Security") and extract data
# -----------------------------
if not isinstance(sample_json, dict) or len(sample_json) != 1:
    raise ValueError("Input JSON must have exactly one top-level key (e.g., 'Security').")

main_section = next(iter(sample_json.keys()))  # e.g., "Security"
section_data = sample_json[main_section]

assessments: list[dict[str, str]] = section_data.get("Assessments", [])  # type: ignore[assignment]
summary: dict[str, str] = section_data.get("Final_Summary", {})  # type: ignore[assignment]

# -----------------------------
# PDF OUTPUT
# -----------------------------
file_name = f"{main_section}_Assessment.pdf"

doc = SimpleDocTemplate(
    file_name, pagesize=A4, leftMargin=36, rightMargin=36, topMargin=48, bottomMargin=36
)

styles = getSampleStyleSheet()

# Styles
h1 = ParagraphStyle("H1", parent=styles["Heading1"], fontSize=20, leading=24, spaceAfter=12)
h2 = ParagraphStyle(
    "H2", parent=styles["Heading2"], fontSize=14, leading=18, spaceBefore=6, spaceAfter=6
)
body = ParagraphStyle("Body", parent=styles["BodyText"], fontSize=10, leading=14)

wrap_style = ParagraphStyle(
    "CellWrap",
    fontName="Helvetica",
    fontSize=9,
    leading=12,
    wordWrap="CJK",
)

wrap_center = ParagraphStyle("CellWrapCenter", parent=wrap_style, alignment=1)
wrap_header = ParagraphStyle("HeaderWrap", parent=wrap_style, fontName="Helvetica-Bold")

story = []

# -----------------------------
# Title (use top-level key)
# -----------------------------
story.append(Paragraph(main_section, h1))
story.append(Spacer(1, 6))

# -----------------------------
# Summary
# -----------------------------
story.append(Paragraph("Summary", h2))
if "Interpretation" in summary:
    story.append(Paragraph(f"<b>Interpretation:</b> {summary['Interpretation']}", body))
    story.append(Spacer(1, 4))
if "Overall_Comments" in summary:
    story.append(Paragraph(f"<b>Overall Comments:</b> {summary['Overall_Comments']}", body))
story.append(Spacer(1, 12))

# -----------------------------
# Assessments Table
# -----------------------------
story.append(Paragraph("Assessments", h2))


def _format_reference(ref: object) -> str:
    """Render a Reference dict as ReportLab markup with optional clickable link."""
    if not isinstance(ref, dict):
        return ""
    text: str = str(ref.get("text", "") or "")
    url: object = ref.get("url")
    if isinstance(url, str) and url:
        return f'<link href="{url}" color="#1D4ED8">{text}</link>'
    return text


# header row
data = [
    [
        Paragraph("Question", wrap_header),
        Paragraph("Rating", wrap_header),
        Paragraph("Comments", wrap_header),
        Paragraph("Reference", wrap_header),
    ]
]

# rows
for item in assessments:
    data.append(
        [
            Paragraph(item.get("Question", ""), wrap_style),
            Paragraph(item.get("Rating", ""), wrap_center),
            Paragraph(item.get("Comments", ""), wrap_style),
            Paragraph(_format_reference(item.get("Reference")), wrap_style),
        ]
    )

# column widths
col_widths = [2.0 * inch, 0.9 * inch, 3.2 * inch, 1.2 * inch]

table = LongTable(data, colWidths=col_widths, repeatRows=1)

# base styling
table_style = TableStyle(
    [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F0F3F7")),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
)

# color-code Rating column
for i in range(1, len(data)):
    rating_value = assessments[i - 1].get("Rating", "").lower()
    if rating_value == "green":
        bg = colors.HexColor("#D1FAE5")
        fg = colors.HexColor("#065F46")
    elif rating_value == "amber":
        bg = colors.HexColor("#FEF3C7")
        fg = colors.HexColor("#92400E")
    elif rating_value == "red":
        bg = colors.HexColor("#FEE2E2")
        fg = colors.HexColor("#7F1D1D")
    else:
        bg = colors.white
        fg = colors.black

    table_style.add("BACKGROUND", (1, i), (1, i), bg)
    table_style.add("TEXTCOLOR", (1, i), (1, i), fg)

# alternate row shading (excluding Rating column)
for i in range(1, len(data)):
    if i % 2 == 0:
        table_style.add("BACKGROUND", (0, i), (0, i), colors.whitesmoke)
        table_style.add("BACKGROUND", (2, i), (2, i), colors.whitesmoke)
        table_style.add("BACKGROUND", (3, i), (3, i), colors.whitesmoke)

table.setStyle(table_style)

story.append(table)

# -----------------------------
# Build PDF
# -----------------------------
doc.build(story)

print(f"PDF created: {file_name}")
