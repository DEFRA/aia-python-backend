import io

from docx import Document
from pypdf import PdfReader


def _parse_pdf(file_bytes: bytes) -> list[dict[str, str | int | bool]]:
    reader = PdfReader(io.BytesIO(file_bytes))
    chunks: list[dict[str, str | int | bool]] = []

    for page_index, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if not text:
            continue
        chunks.append(
            {
                "chunk_index": len(chunks),
                "page": page_index,
                "is_heading": False,
                "char_count": len(text),
                "text": text,
            }
        )

    return chunks


def _parse_docx(file_bytes: bytes) -> list[dict[str, str | int | bool]]:
    doc = Document(io.BytesIO(file_bytes))
    chunks: list[dict[str, str | int | bool]] = []

    for para_index, para in enumerate(doc.paragraphs, start=1):
        text = para.text.strip()
        if not text:
            continue
        style_name = para.style.name if para.style else ""
        chunks.append(
            {
                "chunk_index": len(chunks),
                "page": para_index,
                "is_heading": style_name.startswith("Heading"),
                "char_count": len(text),
                "text": text,
            }
        )

    return chunks


def _parse_bytes(file_bytes: bytes, s3_key: str, doc_id: str) -> list[dict[str, str | int | bool]]:
    _ = doc_id
    extension = s3_key.rsplit(".", maxsplit=1)[-1].lower() if "." in s3_key else ""

    if extension == "pdf":
        return _parse_pdf(file_bytes)

    if extension == "docx":
        return _parse_docx(file_bytes)

    raise ValueError(f"Unsupported file extension: '{extension}' for s3_key={s3_key}")
