# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""The only door the MCP server knocks on.

Every tool call arrives at `execute`, which gates it against MCP Bridge Settings,
dispatches it, and writes a MCP Bridge Log row whether it succeeded, failed or was
refused. Failures come back as `ok: false` with a readable message rather than as an
HTTP error, so the MCP client can show Claude something it can act on.
"""

import json
import time

import frappe
from frappe import _
from frappe.utils import strip_html
from werkzeug.wrappers import Response

from frappe_mcp_bridge.frappe_mcp_bridge.doctype.mcp_bridge_settings.mcp_bridge_settings import (
	CAPABILITY_FIELDS,
	get_settings,
)
from frappe_mcp_bridge.mcp import gate, logger, registry

DRY_RUN_SAVEPOINT = "mcp_bridge_dry_run"


@frappe.whitelist(methods=["GET", "POST"])
def ping() -> dict:
	"""Reachability and configuration check. Answers even while MCP is switched off."""
	settings = get_settings()

	return {
		"ok": True,
		"site": frappe.local.site,
		"user": frappe.session.user,
		"roles": sorted(frappe.get_roles()),
		"enabled": bool(settings.enabled),
		"read_only_mode": bool(settings.read_only_mode),
		"default_dry_run": bool(settings.default_dry_run),
		"server_time": frappe.utils.now(),
	}


@frappe.whitelist(methods=["GET", "POST"])
def list_tools() -> dict:
	"""The tool catalogue, each marked with whether it is currently permitted."""
	settings = get_settings()
	tools = []

	for name, tool in sorted(registry.all_tools().items()):
		allowed, reason = settings.is_capability_allowed(tool.capability)
		tools.append(
			{
				"name": name,
				"capability": tool.capability,
				"summary": tool.summary,
				"writes": tool.writes,
				"parameters": list(tool.params),
				"available": allowed,
				"unavailable_reason": None if allowed else reason,
			}
		)

	return {
		"ok": True,
		"enabled": bool(settings.enabled),
		"read_only_mode": bool(settings.read_only_mode),
		"capabilities": {name: bool(settings.get(field)) for name, field in CAPABILITY_FIELDS.items()},
		"tools": tools,
	}


@frappe.whitelist(methods=["GET", "POST", "DELETE"])
def serve() -> Response:
	"""The MCP endpoint itself, for clients that speak Streamable HTTP directly.

	Point Claude Code or Codex at /api/method/frappe_mcp_bridge.api.mcp.serve with an
	`Authorization: token <api_key>:<api_secret>` header. The body is read raw because
	JSON-RPC's own `method` and `params` keys mean something else to Frappe's form_dict.
	"""
	from frappe_mcp_bridge.mcp import http

	if frappe.request.method != "POST":
		# Stateless server: no SSE stream to open with GET and no session to end with DELETE.
		response = Response(status=405)
		response.headers["Allow"] = "POST"
		return response

	client = (
		frappe.get_request_header("X-MCP-Client")
		or (frappe.get_request_header("User-Agent") or "http").split(" ", 1)[0]
	)[:140]

	status, body = http.handle(frappe.request.get_data(), client)

	if body is None:
		return Response(status=status)

	return Response(json.dumps(body, default=str), status=status, mimetype="application/json")


@frappe.whitelist(methods=["POST"])
def execute(tool: str | None = None, params=None, client: str | None = None, dry_run=None) -> dict:
	started = time.monotonic()
	params = frappe.parse_json(params) or {}
	client = client or "unknown"

	if not isinstance(params, dict):
		return _failure(
			tool or "unknown", "", "ValidationError", _("params must be an object."), started, client
		)

	definition = registry.get(tool or "")
	if not definition:
		return _failure(
			tool or "unknown",
			"",
			"UnknownTool",
			_("No MCP tool named {0}. Call list_tools to see what exists.").format(tool),
			started,
			client,
			params=params,
			status="Blocked",
		)

	unexpected = set(params) - set(definition.params)
	if unexpected:
		return _failure(
			definition.name,
			definition.capability,
			"ValidationError",
			_("{0} does not take {1}. It takes: {2}.").format(
				definition.name, ", ".join(sorted(unexpected)), ", ".join(definition.params)
			),
			started,
			client,
			params=params,
		)

	try:
		gate.check_request(definition, params)
	except gate.MCPBlocked as blocked:
		return _failure(
			definition.name,
			definition.capability,
			"Blocked",
			str(blocked),
			started,
			client,
			params=params,
			status="Blocked",
		)

	is_dry_run = gate.resolve_dry_run(definition, _as_bool(dry_run))
	call_params = dict(params)
	if definition.writes:
		call_params["dry_run"] = is_dry_run

	try:
		result = _run(definition, call_params, is_dry_run)
	except Exception as exception:
		frappe.db.rollback()
		return _failure(
			definition.name,
			definition.capability,
			type(exception).__name__,
			_readable(exception),
			started,
			client,
			params=params,
			dry_run=is_dry_run,
			traceback=frappe.get_traceback(),
		)

	duration = time.monotonic() - started
	log_name = logger.record(
		tool=definition.name,
		capability=definition.capability,
		status="Success",
		params=params,
		result=result,
		duration=duration,
		dry_run=is_dry_run,
		client=client,
		reference_doctype=params.get("doctype"),
		reference_name=params.get("name") or _result_name(result),
		affected_count=_affected_count(result),
	)

	return {
		"ok": True,
		"tool": definition.name,
		"dry_run": is_dry_run,
		"duration": round(duration, 3),
		"log": log_name,
		"result": result,
	}


def _run(definition, call_params: dict, is_dry_run: bool):
	if not is_dry_run:
		return definition.handler(**call_params)

	# Run the real thing, then throw the transaction away. Validations, links and
	# naming all behave exactly as they would on a live call.
	frappe.db.savepoint(DRY_RUN_SAVEPOINT)
	try:
		return definition.handler(**call_params)
	finally:
		frappe.db.rollback(save_point=DRY_RUN_SAVEPOINT)


def _failure(
	tool: str,
	capability: str,
	error_type: str,
	message: str,
	started: float,
	client: str,
	*,
	params: dict | None = None,
	dry_run: bool = False,
	status: str = "Failed",
	traceback: str | None = None,
) -> dict:
	duration = time.monotonic() - started
	messages = _drain_message_log()

	log_name = logger.record(
		tool=tool,
		capability=capability,
		status=status,
		params=params,
		error=traceback or message,
		duration=duration,
		dry_run=dry_run,
		client=client,
		reference_doctype=(params or {}).get("doctype"),
		reference_name=(params or {}).get("name"),
	)

	return {
		"ok": False,
		"tool": tool,
		"dry_run": dry_run,
		"duration": round(duration, 3),
		"log": log_name,
		"error": {
			"type": error_type,
			"message": message,
			"messages": messages,
			"traceback": traceback,
		},
	}


def _readable(exception: Exception) -> str:
	"""frappe.throw hides its text in the message log and raises an empty exception."""
	text = _plain(str(exception))
	if text:
		return text

	for entry in reversed(frappe.local.message_log or []):
		message = _plain(entry.get("message"))
		if message:
			return message

	return type(exception).__name__


def _drain_message_log() -> list[str]:
	messages = [_plain(entry.get("message")) for entry in (frappe.local.message_log or [])]
	frappe.clear_messages()
	return [message for message in messages if message]


def _plain(message) -> str:
	"""msgprint can hold a string, a list or a table, so flatten before stripping tags."""
	if message is None:
		return ""

	if isinstance(message, list | tuple):
		return " ".join(_plain(item) for item in message).strip()

	return strip_html(str(message)).strip()


def _result_name(result) -> str | None:
	return result.get("name") if isinstance(result, dict) else None


def _affected_count(result) -> int:
	if not isinstance(result, dict):
		return 0

	if isinstance(result.get("affected_count"), int):
		return result["affected_count"]

	# An import reports inserted and updated separately, and either may legitimately be
	# zero, so sum them before falling back to the single-count keys.
	inserted, updated = result.get("inserted"), result.get("updated")
	if isinstance(inserted, int) or isinstance(updated, int):
		return (inserted if isinstance(inserted, int) else 0) + (updated if isinstance(updated, int) else 0)

	if isinstance(result.get("count"), int):
		return result["count"]

	return 1 if result.get("name") else 0


def _as_bool(value) -> bool | None:
	if value is None or value == "":
		return None

	if isinstance(value, bool):
		return value

	return str(value).strip().lower() in ("1", "true", "yes", "on")
