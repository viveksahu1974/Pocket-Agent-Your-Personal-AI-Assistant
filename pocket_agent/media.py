"""Small, bounded attachment parser; image bytes only passed to a multimodal model."""
from __future__ import annotations

import base64
import io
import mimetypes
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree


@dataclass
class Attachment:
    name: str
    mime: str
    data: bytes


@dataclass
class MediaResult:
    note: str
    text: str = ""
    image: dict | None = None


def prepare_attachments(items: list[Attachment], max_bytes: int) -> list[MediaResult]:
    output: list[MediaResult] = []
    remaining = max_bytes
    for a in items[:6]:
        safe_name = Path(a.name).name[:100]
        mime = a.mime or mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
        if len(a.data) > remaining:
            output.append(MediaResult(f"{safe_name}: skipped (attachment size limit)"))
            continue
        remaining -= len(a.data)
        if mime in ("image/png", "image/jpeg", "image/webp", "image/gif"):
            output.append(MediaResult(
                f"Attached image: {safe_name}",
                image={"mime": mime, "base64": base64.b64encode(a.data).decode("ascii")},
            ))
        elif mime.startswith("text/") or Path(safe_name).suffix.lower() in (
            ".txt", ".md", ".py", ".js", ".json", ".csv", ".log", ".xml", ".html", ".yaml", ".yml"
        ):
            txt = a.data.decode("utf-8", errors="replace")[:15000]
            output.append(MediaResult(f"Attached text file: {safe_name}", txt))
        elif mime == "application/pdf" or safe_name.lower().endswith(".pdf"):
            try:
                from pypdf import PdfReader
                reader = PdfReader(io.BytesIO(a.data))
                txt = "\n".join(page.extract_text() or "" for page in reader.pages[:12])[:15000]
                output.append(MediaResult(f"Attached PDF: {safe_name} (first 12 pages max)", txt or "[No extractable text; scanned PDF may need OCR.]"))
            except ImportError:
                output.append(MediaResult(f"{safe_name}: PDF support requires pip install '.[pdf]'"))
            except Exception:
                output.append(MediaResult(f"{safe_name}: unable to read PDF"))
        elif safe_name.lower().endswith(".docx"):
            try:
                with zipfile.ZipFile(io.BytesIO(a.data)) as z:
                    xml = z.read("word/document.xml")
                root = ElementTree.fromstring(xml)
                ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
                paragraphs = []
                for p in root.findall(".//w:p", ns):
                    s = "".join(t.text or "" for t in p.findall(".//w:t", ns))
                    if s.strip():
                        paragraphs.append(s)
                output.append(MediaResult(f"Attached DOCX: {safe_name}", "\n".join(paragraphs)[:15000]))
            except Exception:
                output.append(MediaResult(f"{safe_name}: unable to read DOCX"))
        else:
            output.append(MediaResult(f"Attached file: {safe_name} ({mime}); content not supported"))
    if len(items) > 6:
        output.append(MediaResult(f"{len(items)-6} additional files skipped (6-file limit)"))
    return output
