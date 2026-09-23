# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""Bulk import and export.

Rows arrive either as JSON dicts or as CSV text. Everything runs through the normal
document lifecycle, so controller validations still apply; nothing is written straight
to the table. Pair it with dry_run to see the failures before they are real.
"""

import csv
import io

import frappe
from frappe import _

from frappe_mcp_bridge.frappe_mcp_bridge.doctype.mcp_bridge_settings.mcp_bridge_settings import get_settings
from frappe_mcp_bridge.mcp import masking
from frappe_mcp_bridge.mcp.gate import max_rows, max_write_batch, require_capability
from frappe_mcp_bridge.mcp.registry import tool

IMPORT_MODES = ("insert", "update", "upsert")


@tool(
	"import_records",
	"import",
	summary="Insert, update or upsert many documents from JSON rows or CSV text.",
	writes=True,
	doctype_param="doctype",
)
def import_records(
	doctype: str,
	rows: list | None = None,
	csv_content: str | None = None,
	mode: str = "insert",
	match_by: str = "name",
	default_values: dict | None = None,
	submit: bool = False,
	stop_on_error: bool = True,
	dry_run: bool = False,
) -> dict:
	if mode not in IMPORT_MODES:
		frappe.throw(_("mode must be one of {0}").format(", ".join(IMPORT_MODES)))

	if submit:
		require_capability("submit")

	records = _collect_rows(rows, csv_content)
	if not records:
		frappe.throw(_("Pass either rows or csv_content."))

	batch = max_write_batch()
	if len(records) > batch:
		frappe.throw(
			_("{0} rows exceeds the Max Documents Per Write Batch limit of {1}.").format(len(records), batch)
		)

	results = {"inserted": [], "updated": [], "skipped": [], "errors": []}

	for index, row in enumerate(records):
		row = {**(default_values or {}), **row}
		try:
			outcome, name = _apply_row(doctype, row, mode, match_by, submit)
			results[outcome].append({"row": index, "name": name})
		except Exception as exception:
			if stop_on_error:
				# Raising rolls the whole import back, so the caller never lands
				# on a half-imported set.
				frappe.throw(
					_("Row {0} failed: {1}").format(index, str(exception)),
					title=_("Import stopped"),
				)

			results["errors"].append({"row": index, "error": str(exception), "data": row})

	return {
		"doctype": doctype,
		"mode": mode,
		"dry_run": dry_run,
		"total_rows": len(records),
		"inserted": len(results["inserted"]),
		"updated": len(results["updated"]),
		"skipped": len(results["skipped"]),
		"failed": len(results["errors"]),
		"details": results,
	}


def _apply_row(doctype: str, row: dict, mode: str, match_by: str, submit: bool):
	existing = None
	if mode in ("update", "upsert"):
		key = row.get(match_by)
		if key:
			existing = (
				key
				if match_by == "name" and frappe.db.exists(doctype, key)
				else frappe.db.get_value(doctype, {match_by: key}, "name")
			)

	if existing:
		doc = frappe.get_doc(doctype, existing)
		doc.check_permission("write")
		doc.update({field: value for field, value in row.items() if field != match_by})
		doc.save()
		return "updated", doc.name

	if mode == "update":
		return "skipped", row.get(match_by)

	doc = frappe.get_doc({**row, "doctype": doctype})
	doc.insert()

	if submit:
		doc.submit()

	return "inserted", doc.name


def _collect_rows(rows: list | None, csv_content: str | None) -> list[dict]:
	if rows:
		return [dict(row) for row in rows]

	if not csv_content:
		return []

	reader = csv.DictReader(io.StringIO(csv_content))
	parsed = []
	for row in reader:
		# A trailing comma in the header gives DictReader a None key; drop it, and treat
		# an empty cell as "not supplied" rather than as an empty string.
		parsed.append(
			{
				(key or "").strip(): (value.strip() if isinstance(value, str) else value)
				for key, value in row.items()
				if key and (value or "").strip() != ""
			}
		)

	return parsed


@tool(
	"export_records",
	"read",
	summary="Export matching documents as CSV text, ready to edit and import back.",
	doctype_param="doctype",
)
def export_records(
	doctype: str,
	fields: list | None = None,
	filters=None,
	order_by: str | None = None,
	limit: int = 200,
) -> dict:
	limit = min(int(limit or 200), max_rows())
	fields = fields or ["name"]

	rows = frappe.get_list(
		doctype,
		filters=filters,
		fields=fields,
		order_by=order_by,
		limit_page_length=limit,
	)

	# The CSV is text by the time execute sees it, so mask the rows before writing them.
	rows = masking.mask_rows(get_settings(), doctype, rows)

	buffer = io.StringIO()
	writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
	writer.writeheader()
	for row in rows:
		writer.writerow({field: row.get(field) for field in fields})

	return {
		"doctype": doctype,
		"count": len(rows),
		"truncated": len(rows) == limit,
		"csv": buffer.getvalue(),
	}
