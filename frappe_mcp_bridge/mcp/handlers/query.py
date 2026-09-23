# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""Ad hoc SQL and saved reports."""

import frappe
from frappe import _
from frappe.utils.safe_exec import check_safe_sql_query

from frappe_mcp_bridge.mcp.gate import assert_doctype_allowed, max_rows
from frappe_mcp_bridge.mcp.registry import tool


@tool(
	"run_sql",
	"sql",
	summary="Run a read-only SELECT. Writes are refused by Frappe's SQL guard, not by convention.",
)
def run_sql(query: str, values: list | dict | None = None, limit: int = 200) -> dict:
	# Raises frappe.PermissionError for anything that is not a select/explain/CTE.
	check_safe_sql_query(query)

	limit = min(int(limit or 200), max_rows())
	# frappe.db.sql expects a sequence; an explicit None reaches MySQLdb and breaks mogrify.
	rows = frappe.db.sql(query, values or (), as_dict=True)

	return {
		"count": min(len(rows), limit),
		"total_matched": len(rows),
		"truncated": len(rows) > limit,
		"rows": rows[:limit],
	}


@tool("run_report", "read", summary="Run a query or script report and return its columns and rows.")
def run_report(
	report_name: str,
	filters: dict | None = None,
	limit: int = 200,
) -> dict:
	from frappe.desk.query_report import run as run_query_report

	report = frappe.get_cached_doc("Report", report_name)
	if report.ref_doctype:
		assert_doctype_allowed(report.ref_doctype)

	if not frappe.has_permission("Report", "read", report):
		frappe.throw(_("Not permitted to run {0}").format(report_name), frappe.PermissionError)

	limit = min(int(limit or 200), max_rows())
	result = run_query_report(report_name, filters=filters or {}, ignore_prepared_report=True)

	rows = result.get("result") or []

	return {
		"report": report_name,
		"columns": result.get("columns"),
		"count": min(len(rows), limit),
		"total_matched": len(rows),
		"truncated": len(rows) > limit,
		"rows": rows[:limit],
		"message": result.get("message"),
	}
