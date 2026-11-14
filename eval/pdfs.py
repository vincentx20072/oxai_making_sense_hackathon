"""Tiny dependency-free PDF writer for the eval questions (one page, one line per text line).

    python eval/pdfs.py            # (re)build eval/pdfs/<id>.pdf from eval/questions.jsonl

pypdf extracts each written line as one line, so [P1-Ln] citations map to text lines.
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PDF_DIR = HERE / "pdfs"


def _esc(s):
    s = s.encode("latin-1", "replace").decode("latin-1")
    return s.replace("\\", "\\\\").replace("(", r"\(").replace(")", r"\)")


def pdf_bytes(lines):
    """A valid single-page PDF (Helvetica 11pt) with one text line per item."""
    body = ["BT", "/F1 11 Tf", "14 TL", "72 760 Td"]
    for i, line in enumerate(lines):
        body.append(("" if i == 0 else "T* ") + f"({_esc(line)}) Tj")
    body.append("ET")
    stream = "\n".join(body).encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for n, o in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % off for off in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


def write_pdf(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pdf_bytes(text.split("\n")))
    return path


def build_all(questions, out_dir=PDF_DIR):
    return [write_pdf(Path(out_dir) / q["pdf"], q["text"]) for q in questions]


if __name__ == "__main__":
    sys.path.insert(0, str(HERE.parent))
    from eval.run_eval import load_questions
    for p in build_all(load_questions()):
        print("wrote", p)
