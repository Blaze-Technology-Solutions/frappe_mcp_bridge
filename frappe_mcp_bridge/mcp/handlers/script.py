# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""The escape hatch: a Server Script sandbox snippet for repairs no other tool covers."""

import frappe
from frappe import _
from frappe.utils.safe_exec import is_safe_exec_enabled, safe_exec

from frappe_mcp_bridge.mcp.registry import tool


@tool(
	"run_server_script",
	"script",
	summary="Run a Server Script sandbox snippet. Assign to `result` to return a value.",
	writes=True,
)
def run_server_script(script: str, dry_run: bool = False) -> dict:
	if not script or not script.strip():
		frappe.throw(_("Pass the script to run."))

	if not is_safe_exec_enabled():
		frappe.throw(
			_("Server Scripts are disabled on this site. Set server_script_enabled in site_config.json."),
			title=_("Server Scripts Disabled"),
		)

	scope = {}
	safe_exec(script, _locals=scope, script_filename="frappe_mcp_bridge")

	return {
		"dry_run": dry_run,
		"result": scope.get("result"),
		"variables": sorted(key for key in scope if not key.startswith("_")),
	}
