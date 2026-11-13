"""
Minimal website for Jenga: upload agent logs, get the infographic.

    pip install -r requirements.txt
    python app.py                     # http://localhost:5000

Routes
  GET  /              upload form
  POST /analyse       one or more log files -> infographic HTML
  POST /api/analyse   one or more log files -> JSON report (for your own front end)

Only the static (log-only) analysis runs here: it makes no model calls, so an
uploaded log can't trigger cost or reach the network. Run causal replays
offline with:  python jenga.py run.log --replay
"""

import html

from flask import Flask, jsonify, request

from jenga import analyse_log_text, render_html

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB of logs per request

FORM = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Jenga log check</title>
<style>:root{--bg:#f7f6f3;--card:#fff;--ink:#1d1d1f;--sub:#5f6368;--line:#e3e1dc;--acc:#3b5bdb}
@media (prefers-color-scheme:dark){:root{--bg:#141416;--card:#1f1f23;--ink:#ececf0;--sub:#a3a3ad;
--line:#33333a;--acc:#8ea6ff}}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.5 system-ui,sans-serif}.w{max-width:640px;margin:0 auto;padding:40px 16px}
.c{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:22px}
button{background:var(--acc);color:#fff;border:0;border-radius:8px;padding:10px 18px;
font-weight:600;cursor:pointer;margin-top:14px}p{color:var(--sub)}.e{color:#b3261e}</style>
</head><body><div class="w"><h1>Jenga log check</h1>
<p>Upload one or more agent logs (agent4.log format). Each cited claim is checked for
post-rationalisation: citations that support an answer without having produced it.</p>
<div class="c"><form method="post" action="/analyse" enctype="multipart/form-data">
<input type="file" name="logs" multiple required accept=".log,.txt"><br>
<button type="submit">Analyse</button></form>__ERROR__</div></div></body></html>"""


def _read_uploads():
    texts = []
    for f in request.files.getlist("logs"):
        raw = f.read()
        try:
            texts.append(raw.decode("utf-8"))
        except UnicodeDecodeError:
            texts.append(raw.decode("latin-1"))
    if not texts and request.data:
        texts.append(request.get_data(as_text=True))
    reports = []
    for t in texts:
        reports += analyse_log_text(t)
    return reports


@app.get("/")
def index():
    return FORM.replace("__ERROR__", "")


@app.post("/analyse")
def analyse():
    reports = _read_uploads()
    if not reports:
        msg = "No completed agent runs (NEW QUESTION ... FINAL ANSWER) found in the upload."
        return FORM.replace("__ERROR__", f'<p class="e">{html.escape(msg)}</p>'), 400
    return render_html(reports)


@app.post("/api/analyse")
def api_analyse():
    reports = _read_uploads()
    if not reports:
        return jsonify({"error": "no completed runs found"}), 400
    return jsonify(reports)


if __name__ == "__main__":
    app.run(debug=False)
