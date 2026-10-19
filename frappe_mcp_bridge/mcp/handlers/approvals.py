# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""How a write waiting for approval in Desk ended. Each person sees only their own."""

import json

import frappe
from frappe import _

from frappe_mcp_bridge.mcp.registry import tool


@tool("get_write_approval", "read", summary="See whether a write waiting for approval in Desk was approved.")
def get_write_approval(approval: str) -> dict:
	doc = frappe.db.get_value(
		"MCP Write Approval",
		approval,
		["name", "user", "tool", "status", "expires_at", "decided_at", "result", "error"],
		as_dict=True,
	)
	if not doc or doc.user != frappe.session.user:
		frappe.throw(_("There is no approval {0} of yours.").format(approval), frappe.DoesNotExistError)

	return {
		"approval": doc.name,
		"tool": doc.tool,
		"status": doc.status,
		"saved": doc.status == "Approved",
		"expires_at": doc.expires_at,
		"decided_at": doc.decided_at,
		"result": json.loads(doc.result) if doc.result else None,
		"error": doc.error,
	}
