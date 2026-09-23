# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""Writes one MCP Bridge Log row per call, including refusals."""

import json

import frappe

from frappe_mcp_bridge.frappe_mcp_bridge.doctype.mcp_bridge_settings.mcp_bridge_settings import get_settings

# Payloads are for reading back later, not for replaying. A runaway import would
# otherwise put megabytes into every row.
MAX_PAYLOAD_CHARS = 40_000

# Never store the values of these keys, however deeply nested in the payload.
REDACTED_KEYS = {"password", "new_password", "api_key", "api_secret", "secret", "token", "pwd"}


def record(
	*,
	tool: str,
	capability: str,
	status: str,
	params: dict | None = None,
	result=None,
	error: str | None = None,
	duration: float = 0.0,
	dry_run: bool = False,
	client: str | None = None,
	reference_doctype: str | None = None,
	reference_name: str | None = None,
	affected_count: int = 0,
) -> str | None:
	"""Return the log name, or None when logging is off or the write itself failed."""
	try:
		settings = get_settings()
	except Exception:
		return None

	if not settings.log_requests:
		return None

	log = frappe.new_doc("MCP Bridge Log")
	log.update(
		{
			"tool": tool,
			"capability": capability,
			"status": status,
			"dry_run": 1 if dry_run else 0,
			"user": frappe.session.user,
			"client": client,
			"ip_address": getattr(frappe.local, "request_ip", None),
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"affected_count": affected_count,
			"duration": round(duration, 3),
			"error": _truncate(error) if error else None,
		}
	)

	if settings.log_request_payload:
		log.request_payload = _serialise(params)

	if settings.log_response_payload and status == "Success":
		log.response_payload = _serialise(result)

	try:
		log.insert(ignore_permissions=True)
	except Exception:
		# A broken log must not turn a working call into a failed one.
		frappe.log_error(title="Frappe MCP Bridge: could not write request log")
		return None

	if status != "Success":
		# The failing work was rolled back; the log row is all that is left to keep.
		frappe.db.commit()

	return log.name


def _serialise(value) -> str | None:
	if value is None:
		return None

	try:
		return _truncate(json.dumps(_redact(value), indent=1, default=str, sort_keys=True))
	except Exception:
		return _truncate(str(value))


def _redact(value):
	if isinstance(value, dict):
		return {
			key: "***" if str(key).lower() in REDACTED_KEYS else _redact(val) for key, val in value.items()
		}

	if isinstance(value, list | tuple):
		return [_redact(item) for item in value]

	return value


def _truncate(text: str) -> str:
	text = str(text)
	if len(text) <= MAX_PAYLOAD_CHARS:
		return text

	return f"{text[:MAX_PAYLOAD_CHARS]}\n... truncated, {len(text) - MAX_PAYLOAD_CHARS} more characters"
