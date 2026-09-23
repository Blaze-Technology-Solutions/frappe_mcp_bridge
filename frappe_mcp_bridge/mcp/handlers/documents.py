# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""Reading and changing documents.

Reads go through frappe.get_list and frappe.get_doc, so the API key's own roles still
bound what MCP can see. The settings only ever narrow that, never widen it.
"""

import frappe
from frappe import _

from frappe_mcp_bridge.mcp.gate import max_rows, max_write_batch, require_capability
from frappe_mcp_bridge.mcp.registry import tool


@tool(
	"get_document",
	"read",
	summary="Fetch one document with its child tables.",
	doctype_param="doctype",
)
def get_document(doctype: str, name: str, include_children: bool = True) -> dict:
	doc = frappe.get_doc(doctype, name)
	doc.check_permission("read")

	data = doc.as_dict()
	if not include_children:
		for field in doc.meta.get_table_fields():
			data.pop(field.fieldname, None)

	return data


@tool(
	"get_single",
	"read",
	summary="Fetch a Single doctype such as Manufacturing Settings.",
	doctype_param="doctype",
)
def get_single(doctype: str) -> dict:
	doc = frappe.get_doc(doctype)
	doc.check_permission("read")
	return doc.as_dict()


@tool(
	"list_documents",
	"read",
	summary="List documents with filters, chosen fields and ordering.",
	doctype_param="doctype",
)
def list_documents(
	doctype: str,
	filters=None,
	or_filters=None,
	fields=None,
	order_by: str | None = None,
	limit: int = 20,
	start: int = 0,
	group_by: str | None = None,
) -> dict:
	limit = min(int(limit or 20), max_rows())

	rows = frappe.get_list(
		doctype,
		filters=filters,
		or_filters=or_filters,
		fields=fields or ["*"],
		order_by=order_by,
		group_by=group_by,
		limit_start=int(start or 0),
		limit_page_length=limit,
	)

	return {
		"doctype": doctype,
		"count": len(rows),
		"start": int(start or 0),
		"limit": limit,
		"truncated": len(rows) == limit,
		"rows": rows,
	}


@tool(
	"count_documents",
	"read",
	summary="Count matching documents without fetching them.",
	doctype_param="doctype",
)
def count_documents(doctype: str, filters=None) -> dict:
	return {"doctype": doctype, "count": frappe.db.count(doctype, filters)}


@tool(
	"create_document",
	"write",
	summary="Create a document. Child tables go in as lists of dicts.",
	writes=True,
	doctype_param="doctype",
)
def create_document(doctype: str, values: dict, submit: bool = False, dry_run: bool = False) -> dict:
	if submit:
		require_capability("submit")

	doc = frappe.get_doc({**(values or {}), "doctype": doctype})
	doc.insert()

	if submit:
		doc.submit()

	return {
		"doctype": doctype,
		"name": doc.name,
		"docstatus": doc.docstatus,
		"dry_run": dry_run,
		"document": doc.as_dict(),
	}


@tool(
	"update_document",
	"write",
	summary="Update fields on one document and re-run its validations.",
	writes=True,
	doctype_param="doctype",
)
def update_document(doctype: str, name: str, values: dict, dry_run: bool = False) -> dict:
	doc = frappe.get_doc(doctype, name)
	doc.check_permission("write")

	before = {field: doc.get(field) for field in (values or {})}
	doc.update(values or {})
	doc.save()

	return {
		"doctype": doctype,
		"name": doc.name,
		"dry_run": dry_run,
		"changed": _diff(before, doc, values or {}),
		"document": doc.as_dict(),
	}


@tool(
	"bulk_update_documents",
	"write",
	summary="Apply the same field values to every document matching a filter.",
	writes=True,
	doctype_param="doctype",
)
def bulk_update_documents(
	doctype: str,
	filters,
	values: dict,
	limit: int | None = None,
	stop_on_error: bool = True,
	dry_run: bool = False,
) -> dict:
	if not filters:
		# An empty filter here would rewrite the whole table. Make the caller say so.
		frappe.throw(_('bulk_update_documents needs filters. Pass {"name": ["!=", ""]} to mean every row.'))

	batch = min(int(limit or max_write_batch()), max_write_batch())
	names = frappe.get_list(doctype, filters=filters, pluck="name", limit_page_length=batch + 1)

	if len(names) > batch:
		frappe.throw(
			_("The filter matches more than {0} documents, the Max Documents Per Write Batch limit.").format(
				batch
			)
		)

	updated, failed = [], []
	for name in names:
		try:
			doc = frappe.get_doc(doctype, name)
			doc.check_permission("write")
			before = {field: doc.get(field) for field in (values or {})}
			doc.update(values or {})
			doc.save()
			updated.append({"name": name, "changed": _diff(before, doc, values or {})})
		except Exception as exception:
			if stop_on_error:
				raise

			failed.append({"name": name, "error": str(exception)})

	return {
		"doctype": doctype,
		"dry_run": dry_run,
		"matched": len(names),
		"updated": len(updated),
		"failed": len(failed),
		"details": updated,
		"errors": failed,
	}


@tool(
	"submit_document",
	"submit",
	summary="Submit a draft document.",
	writes=True,
	doctype_param="doctype",
)
def submit_document(doctype: str, name: str, dry_run: bool = False) -> dict:
	doc = frappe.get_doc(doctype, name)
	doc.submit()
	return {"doctype": doctype, "name": name, "docstatus": doc.docstatus, "dry_run": dry_run}


@tool(
	"cancel_document",
	"submit",
	summary="Cancel a submitted document.",
	writes=True,
	doctype_param="doctype",
)
def cancel_document(doctype: str, name: str, dry_run: bool = False) -> dict:
	doc = frappe.get_doc(doctype, name)
	doc.cancel()
	return {"doctype": doctype, "name": name, "docstatus": doc.docstatus, "dry_run": dry_run}


@tool(
	"amend_document",
	"submit",
	summary="Create a draft amendment of a cancelled document.",
	writes=True,
	doctype_param="doctype",
)
def amend_document(doctype: str, name: str, values: dict | None = None, dry_run: bool = False) -> dict:
	source = frappe.get_doc(doctype, name)
	if source.docstatus != 2:
		frappe.throw(_("Only a cancelled document can be amended."))

	amended = frappe.copy_doc(source)
	amended.amended_from = name
	amended.update(values or {})
	amended.insert()

	return {"doctype": doctype, "name": amended.name, "amended_from": name, "dry_run": dry_run}


@tool(
	"delete_document",
	"delete",
	summary="Delete a document.",
	writes=True,
	doctype_param="doctype",
)
def delete_document(doctype: str, name: str, force: bool = False, dry_run: bool = False) -> dict:
	frappe.delete_doc(doctype, name, force=force, delete_permanently=force)
	return {"doctype": doctype, "name": name, "deleted": True, "dry_run": dry_run}


@tool(
	"rename_document",
	"write",
	summary="Rename a document, optionally merging it into an existing one.",
	writes=True,
	doctype_param="doctype",
)
def rename_document(
	doctype: str, name: str, new_name: str, merge: bool = False, dry_run: bool = False
) -> dict:
	renamed = frappe.rename_doc(doctype, name, new_name, merge=merge)
	return {"doctype": doctype, "from": name, "to": renamed, "merged": merge, "dry_run": dry_run}


def _diff(before: dict, doc, values: dict) -> dict:
	"""What actually landed on the document, which is not always what was asked for."""
	changed = {}
	for field in values:
		after = doc.get(field)
		if before.get(field) != after:
			changed[field] = {"from": before.get(field), "to": after}

	return changed
