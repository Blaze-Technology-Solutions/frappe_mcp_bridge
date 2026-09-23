# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""Read-only tools. None of these change anything, so they work in read-only mode."""

from typing import Any

from ..client import Bridge
from .common import render


def register(mcp, bridge: Bridge) -> None:
	@mcp.tool()
	async def get_document(doctype: str, name: str, include_children: bool = True) -> str:
		"""Fetch one document in full, including its child tables.

		Args are the doctype and the document's name, e.g. doctype="Work Order",
		name="MFG-WO-2026-00012".
		"""
		return render(
			await bridge.call(
				"get_document",
				{"doctype": doctype, "name": name, "include_children": include_children},
			)
		)

	@mcp.tool()
	async def get_single(doctype: str) -> str:
		"""Fetch a Single doctype, such as Manufacturing Settings or Stock Settings."""
		return render(await bridge.call("get_single", {"doctype": doctype}))

	@mcp.tool()
	async def list_documents(
		doctype: str,
		filters: Any = None,
		or_filters: Any = None,
		fields: list[str] | None = None,
		order_by: str | None = None,
		limit: int = 20,
		start: int = 0,
		group_by: str | None = None,
	) -> str:
		"""List documents with Frappe filters.

		filters takes the usual Frappe shapes: {"status": "Draft"} for equality, or
		{"qty": [">", 100]} and {"name": ["like", "%WO%"]} for operators. Pass fields to
		keep the response small; it defaults to every field. order_by is SQL-ish, e.g.
		"creation desc". The site caps the row count, and the reply says whether the
		result was truncated.
		"""
		return render(
			await bridge.call(
				"list_documents",
				{
					"doctype": doctype,
					"filters": filters,
					"or_filters": or_filters,
					"fields": fields,
					"order_by": order_by,
					"limit": limit,
					"start": start,
					"group_by": group_by,
				},
			)
		)

	@mcp.tool()
	async def count_documents(doctype: str, filters: Any = None) -> str:
		"""Count matching documents without fetching them. Use this before a bulk change."""
		return render(await bridge.call("count_documents", {"doctype": doctype, "filters": filters}))

	@mcp.tool()
	async def export_records(
		doctype: str,
		fields: list[str] | None = None,
		filters: Any = None,
		order_by: str | None = None,
		limit: int = 200,
	) -> str:
		"""Export matching documents as CSV text.

		The output feeds straight back into import_records, so this is the way to pull
		data out, correct it, and put it back.
		"""
		return render(
			await bridge.call(
				"export_records",
				{
					"doctype": doctype,
					"fields": fields,
					"filters": filters,
					"order_by": order_by,
					"limit": limit,
				},
			)
		)

	@mcp.tool()
	async def run_report(report_name: str, filters: dict | None = None, limit: int = 200) -> str:
		"""Run a saved query or script report and return its columns and rows.

		Call list_reports first if you are not sure of the exact report name.
		"""
		return render(
			await bridge.call(
				"run_report",
				{"report_name": report_name, "filters": filters, "limit": limit},
			)
		)

	@mcp.tool()
	async def run_sql(query: str, values: Any = None, limit: int = 200) -> str:
		"""Run a read-only SELECT against the site database.

		Frappe's own SQL guard rejects anything that is not a SELECT, EXPLAIN or CTE, so
		this cannot write even by accident. Table names are the doctype with a "tab"
		prefix, e.g. `tabWork Order`. Pass values for placeholders rather than
		interpolating them into the query.
		"""
		return render(await bridge.call("run_sql", {"query": query, "values": values, "limit": limit}))

	@mcp.tool()
	async def get_mcp_logs(
		limit: int = 20,
		status: str | None = None,
		tool_name: str | None = None,
		user: str | None = None,
		include_payloads: bool = False,
	) -> str:
		"""Read back the audit trail of MCP calls made against this site.

		status is one of Success, Failed or Blocked. Use include_payloads to see the
		arguments and responses that were stored.
		"""
		return render(
			await bridge.call(
				"get_mcp_logs",
				{
					"limit": limit,
					"status": status,
					"tool_name": tool_name,
					"user": user,
					"include_payloads": include_payloads,
				},
			)
		)

	@mcp.tool()
	async def get_error_logs(limit: int = 10, search: str | None = None, since: str | None = None) -> str:
		"""Read the site's Error Log, newest first. since takes "YYYY-MM-DD HH:MM:SS"."""
		return render(
			await bridge.call(
				"get_error_logs",
				{"limit": limit, "search": search, "since": since},
			)
		)
