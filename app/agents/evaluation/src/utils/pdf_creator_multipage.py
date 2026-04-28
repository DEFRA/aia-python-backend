"""ReportLab PDF builder for multi-section security assessment reports."""

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, StyleSheet1, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    Flowable,
    LongTable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    TableStyle,
)


def rating_colors(val: str) -> tuple[colors.Color, colors.Color]:
    """Return background and foreground colours for a rating value.

    Args:
        val: Rating string — one of "Green", "Amber", or "Red" (case-insensitive).

    Returns:
        A (background_colour, foreground_colour) tuple. Defaults to white/black
        for unrecognised values.
    """
    v: str = (val or "").strip().lower()
    if v == "green":
        return colors.HexColor("#D1FAE5"), colors.HexColor("#065F46")
    if v == "amber":
        return colors.HexColor("#FEF3C7"), colors.HexColor("#92400E")
    if v == "red":
        return colors.HexColor("#FEE2E2"), colors.HexColor("#7F1D1D")
    return colors.white, colors.black


def _format_reference(ref: object) -> str:
    """Render a Reference dict as ReportLab paragraph markup.

    Accepts ``{"text": str, "url": str | None}`` and returns either
    ``<link href="...">text</link>`` when a URL is present, or the plain
    text otherwise. Returns an empty string for missing or malformed values.
    """
    if not isinstance(ref, dict):
        return ""
    text: str = str(ref.get("text", "") or "")
    url: object = ref.get("url")
    if isinstance(url, str) and url:
        return f'<link href="{url}" color="#1D4ED8">{text}</link>'
    return text


def footer(canvas: Canvas, doc: SimpleDocTemplate) -> None:
    """Draw a page number in the bottom-right corner of each page.

    Args:
        canvas: The ReportLab canvas for the current page.
        doc: The active SimpleDocTemplate (unused directly, required by ReportLab callback signature).
    """
    canvas.saveState()
    w, _ = A4
    canvas.setFont("Helvetica", 9)
    canvas.setFillColor(colors.HexColor("#6B7280"))
    canvas.drawRightString(w - 36, 18, f"Page {canvas.getPageNumber()}")
    canvas.restoreState()


def build_security_report(
    datasets: list[dict[str, object]],
    output_path: str = "Security_Assessment_Multi.pdf",
) -> str:
    """Build a multi-page PDF where each dataset dict becomes a titled section.

    Each dataset must contain exactly one top-level key (e.g. "Security" or "Privacy")
    whose value is a dict with:
        - "Assessments": list of dicts with "Question", "Rating", "Comments", and
          "Reference" keys (Reference is a dict with "text" and optional "url").
        - "Final_Summary": dict with "Interpretation" and "Overall_Comments" keys.

    Args:
        datasets: Ordered list of assessment dataset dicts, one per report section.
        output_path: File path for the generated PDF.

    Returns:
        The output_path string, allowing callers to log or display the destination.

    Raises:
        ValueError: If a dataset does not contain exactly one top-level key.
    """
    doc: SimpleDocTemplate = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=36,
        rightMargin=36,
        topMargin=48,
        bottomMargin=36,
    )

    styles: StyleSheet1 = getSampleStyleSheet()

    h1: ParagraphStyle = ParagraphStyle(
        "H1", parent=styles["Heading1"], fontSize=20, leading=24, spaceAfter=12
    )
    h2: ParagraphStyle = ParagraphStyle(
        "H2", parent=styles["Heading2"], fontSize=14, leading=18, spaceBefore=6, spaceAfter=6
    )
    body: ParagraphStyle = ParagraphStyle(
        "Body", parent=styles["BodyText"], fontSize=10, leading=14
    )
    wrap_style: ParagraphStyle = ParagraphStyle(
        "CellWrap", fontName="Helvetica", fontSize=9, leading=12, wordWrap="CJK"
    )
    wrap_center: ParagraphStyle = ParagraphStyle("CellWrapCenter", parent=wrap_style, alignment=1)
    wrap_header: ParagraphStyle = ParagraphStyle(
        "HeaderWrap", parent=wrap_style, fontName="Helvetica-Bold"
    )

    story: list[Flowable] = []

    for idx, dataset in enumerate(datasets, start=1):
        if not isinstance(dataset, dict) or len(dataset.keys()) != 1:
            raise ValueError(
                "Each dataset must contain exactly one top-level key (e.g. 'Security')."
            )

        top_key: str = next(iter(dataset.keys()))
        content: dict[str, object] = dataset[top_key]  # type: ignore[assignment]

        story.append(Paragraph(top_key, h1))
        story.append(Spacer(1, 6))

        fs: dict[str, str] = content.get("Final_Summary", {})  # type: ignore[assignment]
        interp: str = fs.get("Interpretation", "")
        comments: str = fs.get("Overall_Comments", "")

        story.append(Paragraph("Summary", h2))
        story.append(Paragraph(f"<b>Interpretation:</b> {interp}", body))
        story.append(Spacer(1, 4))
        story.append(Paragraph(f"<b>Overall Comments:</b> {comments}", body))
        story.append(Spacer(1, 12))

        story.append(Paragraph("Assessments", h2))
        assessments: list[dict[str, str]] = content.get("Assessments", [])  # type: ignore[assignment]

        table_data: list[list[Paragraph]] = [
            [
                Paragraph("Question", wrap_header),
                Paragraph("Rating", wrap_header),
                Paragraph("Comments", wrap_header),
                Paragraph("Reference", wrap_header),
            ]
        ]

        for a in assessments:
            table_data.append(
                [
                    Paragraph(a.get("Question", ""), wrap_style),
                    Paragraph(a.get("Rating", ""), wrap_center),
                    Paragraph(a.get("Comments", ""), wrap_style),
                    Paragraph(_format_reference(a.get("Reference")), wrap_style),
                ]
            )

        col_widths: list[float] = [2.0 * inch, 0.9 * inch, 3.2 * inch, 1.2 * inch]
        tbl: LongTable = LongTable(table_data, colWidths=col_widths, repeatRows=1)

        ts: TableStyle = TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F0F3F7")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )

        for r in range(1, len(table_data)):
            rating: str = assessments[r - 1].get("Rating", "")
            bg: colors.Color
            fg: colors.Color
            bg, fg = rating_colors(rating)

            ts.add("BACKGROUND", (1, r), (1, r), bg)
            ts.add("TEXTCOLOR", (1, r), (1, r), fg)

            if r % 2 == 0:  # zebra rows — improves readability for long tables
                ts.add("BACKGROUND", (0, r), (0, r), colors.whitesmoke)
                ts.add("BACKGROUND", (2, r), (2, r), colors.whitesmoke)
                ts.add("BACKGROUND", (3, r), (3, r), colors.whitesmoke)

        tbl.setStyle(ts)
        story.append(tbl)

        if idx < len(datasets):
            story.append(PageBreak())

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return output_path


# ---------- Example usage ----------

if __name__ == "__main__":
    caf_url: str = "https://www.ncsc.gov.uk/collection/caf"
    example1: dict[str, object] = {
        "Security": {
            "Assessments": [
                {
                    "Question": "Is authentication defined?",
                    "Rating": "Green",
                    "Comments": "SSO via Azure AD, OAuth2, MFA enforced.",
                    "Reference": {"text": "B2.a", "url": caf_url},
                },
                {
                    "Question": "Is logging and monitoring implemented?",
                    "Rating": "Amber",
                    "Comments": "Logs centralised in Splunk; rules for privileged access in progress.",
                    "Reference": {"text": "C1.a", "url": caf_url},
                },
                {
                    "Question": "Is data encrypted at rest and in transit?",
                    "Rating": "Red",
                    "Comments": "TLS noted; no at-rest encryption or KMS details provided.",
                    "Reference": {"text": "B3.c", "url": caf_url},
                },
            ],
            "Final_Summary": {
                "Interpretation": "Minor gaps - needs remediation",
                "Overall_Comments": "Authentication strong; complete SIEM alerting; address encryption at rest.",
            },
        }
    }

    example2: dict[str, object] = {
        "Privacy": {
            "Assessments": [
                {
                    "Question": "Are secrets managed securely?",
                    "Rating": "Amber",
                    "Comments": "Environment variables used; migration to Azure Key Vault planned.",
                    "Reference": {"text": "B2.b", "url": caf_url},
                },
                {
                    "Question": "Is third-party risk assessed?",
                    "Rating": "Green",
                    "Comments": "Vendor risk assessments completed annually; ISO27001 alignment.",
                    "Reference": {"text": "A4.a", "url": caf_url},
                },
            ],
            "Final_Summary": {
                "Interpretation": "Minor gaps - needs remediation",
                "Overall_Comments": "Vendor governance strong; prioritise secrets management migration.",
            },
        }
    }

    outfile: str = build_security_report(
        [example1, example2], output_path="Security_Assessment_Multi.pdf"
    )
    print(f"Created: {outfile}")
