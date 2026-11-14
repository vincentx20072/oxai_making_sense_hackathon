"""
Scripted offline agent for the demo questions (used by make_demo_runs.py --fake
and demo/test_demo.py). It mimics groq's chat.completions.create: list the
files, search for "founded", select the hit, answer in one cited sentence.
It is honest: it reports whatever year the (possibly perturbed) line says.
"""

import json
import re
from types import SimpleNamespace as NS


class FakeDemoClient:
    def __init__(self):
        self.chat = NS(completions=NS(create=self.create))

    def create(self, model, messages, tools=None, tool_choice=None, **kw):
        tool_out = [m["content"] for m in messages
                    if isinstance(m, dict) and m.get("role") == "tool"]
        step = len(tool_out)
        if step == 0:
            return self._reply("", "First see which PDFs I have.", ("list_files", {}))
        filename = tool_out[0].split("\n")[0].strip()
        if step == 1:
            return self._reply("", "Search the note for the founding date.",
                               ("search_pdf", {"filename": filename, "query": "founded"}))
        hit = re.search(r"\[P(\d+)-L(\d+)\] (.*)", tool_out[1])
        if not hit:
            return self._reply("I could not find a founding date in the document.", "", None)
        p, l, text = hit.groups()
        if step == 2:
            return self._reply("", "", ("select_evidence", {
                "filename": filename, "page": int(p), "start_line": int(l),
                "end_line": int(l), "purpose": "founding date"}))
        year = re.search(r"\b(\d{4})\b", text)
        subject = messages[1]["content"].split(" founded")[0].replace("When was ", "")
        answer = (f"{subject} was founded in {year.group(1)} [{filename}, P{p}-L{l}]."
                  if year else "The document does not give a founding year.")
        return self._reply(answer, "", None)

    @staticmethod
    def _reply(content, reasoning, call):
        calls = None
        if call:
            name, args = call
            calls = [NS(id=f"call_{name}", function=NS(name=name, arguments=json.dumps(args)))]
        return NS(choices=[NS(message=NS(content=content, reasoning=reasoning,
                                         tool_calls=calls))])
