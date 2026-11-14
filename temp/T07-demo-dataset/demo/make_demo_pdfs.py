"""
Build the demo PDFs from text we wrote ourselves (no real people's documents).

    python demo/make_demo_pdfs.py          -> demo/pdfs/known_fact.pdf, invented_fact.pdf

Each PDF is one page of short lines, one line per sentence, so the agent's
line numbers are stable: line 1 is the title and the fact sits on P1-L4.
The texts and the facts they contain live in demo/questions.json.

No third-party dependency: a minimal PDF 1.4 writer (Helvetica, one page)
is enough for plain text, and pypdf (already required) reads it back
line by line.
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
QUESTIONS = HERE / "questions.json"
OUT_DIR = HERE / "pdfs"

PAGE_W, PAGE_H = 595, 842          # A4 in points
MARGIN_X, TOP_Y = 72, 770
TITLE_SIZE, BODY_SIZE, LEADING = 16, 12, 22


def _escape(text):
    text = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    try:
        text.encode("latin-1")
    except UnicodeEncodeError:
        raise ValueError(f"demo text must be Latin-1 (plain ASCII is safest): {text!r}")
    return text


def pdf_bytes(lines, title=None):
    """One A4 page; first line in bold as a title, the rest as body lines."""
    ops = ["BT", f"{MARGIN_X} {TOP_Y} Td", f"{LEADING} TL"]
    for i, line in enumerate(lines):
        font = f"/F2 {TITLE_SIZE} Tf" if i == 0 else f"/F1 {BODY_SIZE} Tf"
        ops.append(font)
        ops.append(f"({_escape(line)}) Tj")
        ops.append("T*")
    ops.append("ET")
    stream = "\n".join(ops).encode("latin-1")

    info_title = _escape(title or lines[0])
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
         f"/Resources << /Font << /F1 4 0 R /F2 5 0 R >> >> /Contents 6 0 R >>").encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        (f"<< /Title ({info_title}) /Producer (Jenga demo/make_demo_pdfs.py) >>").encode("latin-1"),
    ]
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for n, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += (b"trailer\n<< /Size %d /Root 1 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objects) + 1, len(objects), xref))
    return bytes(out)


def load_questions(path=QUESTIONS):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build(out_dir=OUT_DIR, questions_path=QUESTIONS):
    """Write one PDF per distinct `pdf` in questions.json; return the paths."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for q in load_questions(questions_path):
        path = out_dir / q["pdf"]
        if path in written:
            continue
        path.write_bytes(pdf_bytes(q["lines"]))
        written.append(path)
    return written


if __name__ == "__main__":
    for p in build(*(sys.argv[1:2] or [])):
        print(f"wrote {p} ({p.stat().st_size} bytes)")
