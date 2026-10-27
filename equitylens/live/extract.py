"""Short-lived source extraction subprocess. Bytes arrive over stdin, not a file."""

import io
import json
import sys

from bs4 import BeautifulSoup
from pypdf import PdfReader


def extract(data, kind):
    blocks, total, seen = [], 0, set()

    def add(text, locator):
        nonlocal total
        text = " ".join(text.split())
        if len(text) < 40 or text in seen or total >= 40_000:
            return
        seen.add(text)
        text = text[: min(8000, 40_000 - total)]
        total += len(text)
        blocks.append({"text": text, "locator": locator})

    notice = ""
    if kind == "application/pdf":
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ValueError("Encrypted PDF")
        for number, page in enumerate(reader.pages[:20], 1):
            add(page.extract_text() or "", f"PDF page {number}")
            if total >= 40_000:
                break
        notice = "PDF extraction is limited to the first 20 pages and 40,000 characters; no OCR."
    elif kind in {"text/html", "application/xhtml+xml"}:
        soup = BeautifulSoup(data, "html.parser")
        for node in soup(["script", "style", "nav", "header", "footer", "form", "noscript"]):
            node.decompose()
        main = soup.find("main") or soup.find("article") or soup.body or soup
        for number, node in enumerate(main.find_all(["h1", "h2", "h3", "p", "li", "tr"]), 1):
            add(node.get_text(" ", strip=True), f"HTML block {number}")
        if not blocks:
            add(main.get_text(" ", strip=True), "HTML body")
        notice = "HTML text excerpt; extracted text does not verify claims or financial table semantics."
    else:
        add(data.decode("utf-8", errors="replace"), "Plain text")
    return {"blocks": blocks, "notice": notice}


if __name__ == "__main__":
    try:
        data = sys.stdin.buffer.read(3 * 1024 * 1024 + 1)
        if len(data) > 3 * 1024 * 1024:
            raise ValueError("Too large")
        output = extract(data, sys.argv[1])
        sys.stdout.buffer.write(json.dumps(output, ensure_ascii=False).encode("utf-8"))
    except Exception:
        raise SystemExit(1) from None
