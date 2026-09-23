# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""Reading back what MCP did, and what the site complained about."""

import frappe

from frappe_mcp_bridge.mcp.gate import max_rows
from frappe_mcp_bridge.mcp.registry import tool


@tool("get_mcp_logs", "read", summary="Recent MCP tool calls made against this site.")
def get_mcp_logs(
	limit: int = 20,
	status: str | None = None,
	tool_name: str | None = None,
	user: str | None = None,
	include_payloads: bool = False,
) -> dict:
	filters = {}
	if status:
		filters["status"] = status
	if tool_name:
		filters["tool"] = tool_name
	if user:
		filters["user"] = user

	fields = [
		"name",
		"creation",
		"tool",
		"capability",
		"status",
		"dry_run",
		"user",
		"client",
		"reference_doctype",
		"reference_name",
		"affected_count",
		"duration",
		"error",
	]
	if include_payloads:
		fields += ["request_payload", "response_payload"]

	return {
		"entries": frappe.get_all(
			"MCP Bridge Log",
			filters=filters,
			fields=fields,
			order_by="creation desc",
			limit=min(int(limit or 20), max_rows()),
		)
	}


@tool("get_error_logs", "read", summary="Recent entries from the site's Error Log.")
def get_error_logs(limit: int = 10, search: str | None = None, since: str | None = None) -> dict:
	filters = {}
	if search:
		filters["error"] = ("like", f"%{search}%")
	if since:
		filters["creation"] = (">", since)

	return {
		"entries": frappe.get_all(
			"Error Log",
			filters=filters,
			fields=["name", "creation", "method", "error"],
			order_by="creation desc",
			limit=min(int(limit or 10), max_rows()),
		)
	}
