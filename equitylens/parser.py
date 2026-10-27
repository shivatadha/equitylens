"""Runs in a separate process with a wall-clock timeout enforced by ingestion."""

import io
import json
import sys
from importlib.metadata import version

from bs4 import BeautifulSoup
from pypdf import PdfReader

MAX_TEXT = 5_000_000


def parse(data: bytes, kind: str):
    pages = []
    warnings = []
    if kind == "application/pdf":
        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise ValueError("Encrypted PDFs are not supported. Upload an unlocked copy.")
        if len(reader.pages) > 1000:
            raise ValueError("Maximum 1,000 PDF pages per import.")
        total = 0
        blank = 0
        for number, page in enumerate(reader.pages, 1):
            text = (page.extract_text() or "").strip()
            total += len(text)
            if total > MAX_TEXT:
                raise ValueError("Extracted text exceeds the size limit.")
            if not text:
                blank += 1
            else:
                pages.append({"locator": f"PDF page {number}", "text": text})
        if blank:
            warnings.append(f"{blank} pages have no extractable text. OCR is not included in milestone 1.")
        parser = f"pypdf-{version('pypdf')}"
    elif kind == "text/html":
        soup = BeautifulSoup(data, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "noscript"]):
            tag.decompose()
        main = soup.find("main") or soup.find("article") or soup.body or soup
        text = main.get_text("\n", strip=True)
        if len(text) > MAX_TEXT:
            raise ValueError("Extracted text exceeds the size limit.")
        pages = [{"locator": "HTML text", "text": text}]
        parser = f"beautifulsoup4-{version('beautifulsoup4')}"
        warnings.append("HTML text extraction preserves text order; table relationships are not normalized.")
    elif kind == "text/plain":
        text = data.decode("utf-8-sig")
        if len(text) > MAX_TEXT:
            raise ValueError("Text exceeds the size limit.")
        pages = [{"locator": "Text", "text": text}]
        parser = "utf8-v1"
    else:
        raise ValueError("Unsupported document type.")
    chunks = []
    for page in pages:
        text = page["text"]
        for start in range(0, len(text), 1600):
            part = text[start : start + 1800]
            if part.strip():
                chunks.append(
                    {"locator": f"{page['locator']}, chars {start}–{start + len(part)}", "text": part}
                )
    if not chunks:
        warnings.append("No searchable text. This document requires OCR or manual review.")
    return {"chunks": chunks, "parser": parser, "warning": " ".join(warnings) or None}


if __name__ == "__main__":
    from pathlib import Path

    try:
        result = parse(Path(sys.argv[1]).read_bytes(), sys.argv[2])
        sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))
    except Exception as exc:
        sys.stderr.write(f"Document extraction failed: {type(exc).__name__}: {exc}")
        raise SystemExit(1) from exc
