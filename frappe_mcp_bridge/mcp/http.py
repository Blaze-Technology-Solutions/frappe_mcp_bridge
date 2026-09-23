# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""MCP over Streamable HTTP, served by Frappe itself.

Stateless: every POST carries its own Frappe credentials, so there is no session to keep
and no SSE stream to hold open. Each request is answered with plain JSON. Tool calls go
through the same `execute` as the stdio bridge, so the gate, dry run and log all apply.
"""

import json

import frappe

from frappe_mcp_bridge import __version__
from frappe_mcp_bridge.mcp import registry
from frappe_mcp_bridge.mcp.descriptions import DESCRIPTIONS

# Newest first. A client asking for one of these gets it echoed back; anything else
# gets the newest, and the client decides whether it can live with that.
SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602

# Enough of a traceback to see where it broke, not so much that it floods the context.
TRACEBACK_CHARS = 2_000

# Capabilities that change data in ways a dry run or a confirmation should precede.
DESTRUCTIVE_CAPABILITIES = {"delete", "admin", "script", "patch"}

DRY_RUN_BANNER = (
	"DRY RUN — the work ran and then the transaction was rolled back. "
	"Nothing below was saved. Pass dry_run=false to make it real.\n\n"
)

INSTRUCTIONS = """\
Tools for a Frappe / ERPNext site.

How to work here:

1. Call site_ping first in a session. It reports the site, the user the key belongs
   to, and whether MCP is enabled and read-only. If a tool comes back blocked,
   site_capabilities names the exact setting that refused it — say which one, rather
   than guessing at permissions.
2. Call get_doctype_schema before creating or updating a doctype you have not touched
   in this session. Field names and Select options are not guessable.
3. Before changing anything: count_documents to see the blast radius, then run the
   write with dry_run=true. A dry run does the real work and rolls it back, so the
   errors it reports are the errors a live call would hit. Show the user what would
   change, and only then run it for real.
4. Prefer update_document and import_records, which re-run the document's validations,
   over force_set_values and run_server_script, which do not.
5. Every call is logged on the site in MCP Bridge Log whatever its outcome, and the log
   name comes back with each reply. get_mcp_logs reads that history back.

The site holds the real limits. Read Only Mode, the per-capability checkboxes, the
doctype allow/block lists and the batch ceilings all live in MCP Bridge Settings and
are the user's to change, not yours to work around.
"""

# Tools that answer from the settings rather than through execute, so they still work
# while MCP is switched off and can say why everything else is refused.
SITE_TOOLS = ("site_ping", "site_capabilities")


class RPCError(Exception):
	def __init__(self, code: int, message: str):
		super().__init__(message)
		self.code = code
		self.message = message


def handle(body: bytes, client: str) -> tuple[int, object]:
	"""Return (HTTP status, JSON body). A None body means 202 with nothing to say."""
	try:
		message = json.loads(body or b"null")
	except ValueError:
		return 400, _error(None, PARSE_ERROR, "Request body is not valid JSON.")

	if isinstance(message, list):
		if not message:
			return 400, _error(None, INVALID_REQUEST, "Empty batch.")

		replies = [reply for reply in (_dispatch(item, client) for item in message) if reply]
		return (200, replies) if replies else (202, None)

	reply = _dispatch(message, client)
	return (200, reply) if reply else (202, None)


def _dispatch(message, client: str) -> dict | None:
	if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
		return _error(None, INVALID_REQUEST, "Expected a JSON-RPC 2.0 message.")

	method = message.get("method")
	request_id = message.get("id")
	is_notification = "id" not in message

	if not method:
		# A response to something we never sent; there is nothing to do with it.
		return None

	try:
		result = _call(method, message.get("params") or {}, client)
	except RPCError as error:
		return None if is_notification else _error(request_id, error.code, error.message)

	if is_notification:
		return None

	return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _call(method: str, params: dict, client: str):
	if method.startswith("notifications/"):
		return None

	if method == "initialize":
		return _initialize(params)

	if method == "ping":
		return {}

	if method == "tools/list":
		return {"tools": _tool_list()}

	if method == "tools/call":
		return _tool_call(params, client)

	if method in ("resources/list", "resources/templates/list"):
		return {"resources": []} if method == "resources/list" else {"resourceTemplates": []}

	if method == "prompts/list":
		return {"prompts": []}

	raise RPCError(METHOD_NOT_FOUND, f"Method not found: {method}")


def _initialize(params: dict) -> dict:
	requested = params.get("protocolVersion")
	version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else SUPPORTED_PROTOCOL_VERSIONS[0]

	return {
		"protocolVersion": version,
		"capabilities": {"tools": {"listChanged": False}},
		"serverInfo": {"name": "frappe-mcp-bridge", "title": "Frappe MCP Bridge", "version": __version__},
		"instructions": INSTRUCTIONS,
	}


def _tool_list() -> list[dict]:
	tools = [
		{
			"name": name,
			"description": DESCRIPTIONS.get(name, ""),
			"inputSchema": {"type": "object", "properties": {}},
			"annotations": {"readOnlyHint": True, "openWorldHint": False},
		}
		for name in SITE_TOOLS
	]

	for name, tool in sorted(registry.all_tools().items()):
		tools.append(
			{
				"name": name,
				"description": DESCRIPTIONS.get(name) or tool.summary,
				"inputSchema": registry.input_schema(tool),
				"annotations": {
					"readOnlyHint": not tool.writes,
					"destructiveHint": tool.capability in DESTRUCTIVE_CAPABILITIES,
					"openWorldHint": False,
				},
			}
		)

	return tools


def _tool_call(params: dict, client: str) -> dict:
	# Imported here: api.mcp imports this module to serve the endpoint.
	from frappe_mcp_bridge.api import mcp as api

	name = params.get("name")
	arguments = params.get("arguments") or {}
	if not name:
		raise RPCError(INVALID_PARAMS, "tools/call needs a tool name.")

	if not isinstance(arguments, dict):
		raise RPCError(INVALID_PARAMS, "arguments must be an object.")

	if name == "site_ping":
		return _text(api.ping())

	if name == "site_capabilities":
		return _text(api.list_tools())

	arguments = {key: value for key, value in arguments.items() if value is not None}
	dry_run = arguments.pop("dry_run", None)

	envelope = api.execute(tool=name, params=arguments, client=client, dry_run=dry_run)

	# Commit each call on its own, so one failing call later in a batch cannot roll
	# back an earlier one that succeeded. A dry run has already rolled itself back.
	frappe.db.commit()

	if envelope.get("ok"):
		return _text(_render_success(envelope))

	return _text(_render_failure(name, envelope), is_error=True)


def _text(value, is_error: bool = False) -> dict:
	text = value if isinstance(value, str) else json.dumps(value, indent=2, default=str, ensure_ascii=False)
	return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _render_success(envelope: dict) -> str:
	body = json.dumps(envelope.get("result"), indent=2, default=str, ensure_ascii=False)

	footer = [f"{envelope.get('duration', 0)}s"]
	if envelope.get("log"):
		footer.append(f"MCP Bridge Log {envelope['log']}")

	banner = DRY_RUN_BANNER if envelope.get("dry_run") else ""

	return f"{banner}{body}\n\n— {' · '.join(footer)}"


def _render_failure(tool: str, envelope: dict) -> str:
	error = envelope.get("error") or {}
	lines = [f"{tool} failed: {error.get('message') or error.get('type') or 'unknown error'}"]

	for message in error.get("messages") or []:
		if message and message != error.get("message"):
			lines.append(f"  {message}")

	if envelope.get("log"):
		lines.append(f"  logged as MCP Bridge Log {envelope['log']}")

	traceback = error.get("traceback")
	if traceback:
		lines.append("")
		lines.append(traceback[-TRACEBACK_CHARS:])

	return "\n".join(lines)


def _error(request_id, code: int, message: str) -> dict:
	return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
