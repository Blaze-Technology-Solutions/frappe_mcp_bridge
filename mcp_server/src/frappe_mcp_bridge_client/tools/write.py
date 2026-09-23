# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""Tools that change data.

Every one of these refuses outright when FRAPPE_MCP_READ_ONLY is true, and every one
takes dry_run. A dry run does the real work inside a savepoint and then rolls it back,
so validation errors surface exactly as they would on a live call.
"""

from typing import Any

from ..client import Bridge
from .common import render


def register(mcp, bridge: Bridge) -> None:
	@mcp.tool()
	async def create_document(
		doctype: str,
		values: dict,
		submit: bool = False,
		dry_run: bool | None = None,
	) -> str:
		"""Create one document.

		values holds the fieldnames; child tables go in as lists of dicts, e.g.
		{"items": [{"item_code": "X", "qty": 2}]}. Check get_doctype_schema first if you
		are unsure of a fieldname. submit=true submits it after insert, which needs the
		submit capability as well as write.
		"""
		return render(
			await bridge.call(
				"create_document",
				{"doctype": doctype, "values": values, "submit": submit},
				writes=True,
				dry_run=dry_run,
			)
		)

	@mcp.tool()
	async def update_document(
		doctype: str,
		name: str,
		values: dict,
		dry_run: bool | None = None,
	) -> str:
		"""Update fields on one document and re-run its validations.

		This is the normal way to correct data: the controller still runs, so linked
		totals and statuses stay consistent. The reply lists what actually changed, which
		is not always what was asked for. If validation refuses a repair that genuinely
		needs to happen, force_set_values is the fallback.
		"""
		return render(
			await bridge.call(
				"update_document",
				{"doctype": doctype, "name": name, "values": values},
				writes=True,
				dry_run=dry_run,
			)
		)

	@mcp.tool()
	async def bulk_update_documents(
		doctype: str,
		filters: Any,
		values: dict,
		limit: int | None = None,
		stop_on_error: bool = True,
		dry_run: bool | None = None,
	) -> str:
		"""Apply the same field values to every document matching a filter.

		filters is required; to mean every row, say so explicitly with
		{"name": ["!=", ""]}. Count first with count_documents, then dry-run, then commit.
		The site refuses batches larger than Max Documents Per Write Batch. With
		stop_on_error=true a single failure rolls the whole batch back.
		"""
		return render(
			await bridge.call(
				"bulk_update_documents",
				{
					"doctype": doctype,
					"filters": filters,
					"values": values,
					"limit": limit,
					"stop_on_error": stop_on_error,
				},
				writes=True,
				dry_run=dry_run,
			)
		)

	@mcp.tool()
	async def import_records(
		doctype: str,
		rows: list[dict] | None = None,
		csv_content: str | None = None,
		mode: str = "insert",
		match_by: str = "name",
		default_values: dict | None = None,
		submit: bool = False,
		stop_on_error: bool = True,
		dry_run: bool | None = None,
	) -> str:
		"""Import many documents from JSON rows or CSV text.

		Pass either rows or csv_content, not both. mode is "insert" to always create,
		"update" to only touch rows that already exist, or "upsert" for both; match_by
		names the field used to find the existing document and defaults to "name".
		default_values is merged into every row, which is handy for company or posting
		date. Each row goes through the normal document lifecycle, so controller
		validations still apply. Dry-run first: the reply names the failing row.
		"""
		return render(
			await bridge.call(
				"import_records",
				{
					"doctype": doctype,
					"rows": rows,
					"csv_content": csv_content,
					"mode": mode,
					"match_by": match_by,
					"default_values": default_values,
					"submit": submit,
					"stop_on_error": stop_on_error,
				},
				writes=True,
				dry_run=dry_run,
			)
		)

	@mcp.tool()
	async def submit_document(doctype: str, name: str, dry_run: bool | None = None) -> str:
		"""Submit a draft document, taking it from docstatus 0 to 1."""
		return render(
			await bridge.call(
				"submit_document", {"doctype": doctype, "name": name}, writes=True, dry_run=dry_run
			)
		)

	@mcp.tool()
	async def cancel_document(doctype: str, name: str, dry_run: bool | None = None) -> str:
		"""Cancel a submitted document, taking it to docstatus 2.

		Frappe cancels linked documents along with it, so check what points at this one
		before committing.
		"""
		return render(
			await bridge.call(
				"cancel_document", {"doctype": doctype, "name": name}, writes=True, dry_run=dry_run
			)
		)

	@mcp.tool()
	async def amend_document(
		doctype: str,
		name: str,
		values: dict | None = None,
		dry_run: bool | None = None,
	) -> str:
		"""Create a draft amendment of a cancelled document, optionally with corrections applied."""
		return render(
			await bridge.call(
				"amend_document",
				{"doctype": doctype, "name": name, "values": values},
				writes=True,
				dry_run=dry_run,
			)
		)

	@mcp.tool()
	async def delete_document(
		doctype: str,
		name: str,
		force: bool = False,
		dry_run: bool | None = None,
	) -> str:
		"""Delete a document.

		Without force, Frappe refuses when something still links to it, which is usually
		the right answer. force=true deletes permanently and skips the link check, so
		confirm with the user before using it.
		"""
		return render(
			await bridge.call(
				"delete_document",
				{"doctype": doctype, "name": name, "force": force},
				writes=True,
				dry_run=dry_run,
			)
		)

	@mcp.tool()
	async def rename_document(
		doctype: str,
		name: str,
		new_name: str,
		merge: bool = False,
		dry_run: bool | None = None,
	) -> str:
		"""Rename a document, updating every link to it.

		merge=true folds this document into an existing one of the new name and deletes
		this one; that cannot be undone, so dry-run it first.
		"""
		return render(
			await bridge.call(
				"rename_document",
				{"doctype": doctype, "name": name, "new_name": new_name, "merge": merge},
				writes=True,
				dry_run=dry_run,
			)
		)
