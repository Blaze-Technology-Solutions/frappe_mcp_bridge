# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""Long tool descriptions for the native HTTP endpoint.

They match the docstrings on the stdio bridge's tools (mcp_server/), so Claude gets the same
guidance whichever transport it connects through. A tool missing here falls back to
its registry summary.
"""

DESCRIPTIONS = {
	"site_ping": """\
Check the connection to the Frappe site and whether MCP access is switched on.

Answers even while MCP is disabled, so this is the tool to reach for when another
tool says it was blocked. Reports the site, the user the API key belongs to, its
roles, and whether Read Only Mode is on.""",
	"site_capabilities": """\
List every MCP tool the site knows and whether it is currently permitted.

Each entry says which capability it needs and, when unavailable, exactly which
checkbox in MCP Bridge Settings is off. Use this before telling the user that
something cannot be done.""",
	"describe_site": """\
Site name, installed app versions, time zone, and the current MCP permissions.

Worth calling once at the start of a session so later work targets the right
versions and the right scope.""",
	"list_doctypes": """\
Find doctypes by partial name, module or app.

Use it to confirm a doctype's exact spelling before any other call; "Work Order"
and "Workorder" are not the same thing to Frappe.""",
	"get_doctype_schema": """\
Fields, types, link targets, mandatory flags and naming rules for one doctype.

Read this before creating or updating documents of a doctype you have not touched
yet, so field names and Select options are right the first time. Child table
schemas come along by default.""",
	"list_reports": """\
List the query and script reports on the site, optionally filtered by doctype.""",
	"get_document": """\
Fetch one document in full, including its child tables.

Args are the doctype and the document's name, e.g. doctype="Work Order",
name="MFG-WO-2026-00012".""",
	"get_single": """\
Fetch a Single doctype, such as Manufacturing Settings or Stock Settings.""",
	"list_documents": """\
List documents with Frappe filters.

filters takes the usual Frappe shapes: {"status": "Draft"} for equality, or
{"qty": [">", 100]} and {"name": ["like", "%WO%"]} for operators. Pass fields to
keep the response small; it defaults to every field. order_by is SQL-ish, e.g.
"creation desc". The site caps the row count, and the reply says whether the
result was truncated.""",
	"count_documents": """\
Count matching documents without fetching them. Use this before a bulk change.""",
	"export_records": """\
Export matching documents as CSV text.

The output feeds straight back into import_records, so this is the way to pull
data out, correct it, and put it back.""",
	"run_report": """\
Run a saved query or script report and return its columns and rows.

Call list_reports first if you are not sure of the exact report name.""",
	"run_sql": """\
Run a read-only SELECT against the site database.

Frappe's own SQL guard rejects anything that is not a SELECT, EXPLAIN or CTE, so
this cannot write even by accident. Table names are the doctype with a "tab"
prefix, e.g. `tabWork Order`. Pass values for placeholders rather than
interpolating them into the query.""",
	"get_mcp_logs": """\
Read back the audit trail of MCP calls made against this site.

status is one of Success, Failed or Blocked. Use include_payloads to see the
arguments and responses that were stored.""",
	"get_error_logs": """\
Read the site's Error Log, newest first. since takes "YYYY-MM-DD HH:MM:SS".""",
	"create_document": """\
Create one document.

values holds the fieldnames; child tables go in as lists of dicts, e.g.
{"items": [{"item_code": "X", "qty": 2}]}. Check get_doctype_schema first if you
are unsure of a fieldname. submit=true submits it after insert, which needs the
submit capability as well as write.""",
	"update_document": """\
Update fields on one document and re-run its validations.

This is the normal way to correct data: the controller still runs, so linked
totals and statuses stay consistent. The reply lists what actually changed, which
is not always what was asked for. If validation refuses a repair that genuinely
needs to happen, force_set_values is the fallback.""",
	"bulk_update_documents": """\
Apply the same field values to every document matching a filter.

filters is required; to mean every row, say so explicitly with
{"name": ["!=", ""]}. Count first with count_documents, then dry-run, then commit.
The site refuses batches larger than Max Documents Per Write Batch. With
stop_on_error=true a single failure rolls the whole batch back.""",
	"import_records": """\
Import many documents from JSON rows or CSV text.

Pass either rows or csv_content, not both. mode is "insert" to always create,
"update" to only touch rows that already exist, or "upsert" for both; match_by
names the field used to find the existing document and defaults to "name".
default_values is merged into every row, which is handy for company or posting
date. Each row goes through the normal document lifecycle, so controller
validations still apply. Dry-run first: the reply names the failing row.""",
	"submit_document": """\
Submit a draft document, taking it from docstatus 0 to 1.""",
	"cancel_document": """\
Cancel a submitted document, taking it to docstatus 2.

Frappe cancels linked documents along with it, so check what points at this one
before committing.""",
	"amend_document": """\
Create a draft amendment of a cancelled document, optionally with corrections applied.""",
	"delete_document": """\
Delete a document.

Without force, Frappe refuses when something still links to it, which is usually
the right answer. force=true deletes permanently and skips the link check, so
confirm with the user before using it.""",
	"rename_document": """\
Rename a document, updating every link to it.

merge=true folds this document into an existing one of the new name and deletes
this one; that cannot be undone, so dry-run it first.""",
	"list_patches": """\
List patch modules and whether each has already run on this site.

Pass app to look at one app; leave it out to cover every installed app.
pending_only=true narrows it to the ones a bench migrate would still apply.""",
	"get_patch_log": """\
Read the Patch Log: which patches ran on this site, newest first.""",
	"run_patch": """\
Run one patch module, e.g. "myapp.patches.v1_0.fix_customer_names".

A patch commits as it goes, so unlike the other write tools it cannot be rolled
back. dry_run therefore reports whether it would run and whether the module
imports, without running it — do that first. An already-applied patch is skipped
unless force=true. background=true queues it on the long worker and returns a job
id, which is what you want for anything that touches many rows.""",
	"clear_cache": """\
Clear the site cache, or just one doctype's. Run this after a schema change.""",
	"reload_doctype": """\
Re-sync a doctype's schema from its json file into the database.

The equivalent of what bench migrate does for one doctype. Useful after deploying
a field change without a full migrate.""",
	"force_set_values": """\
Write field values straight to the table, skipping controller validation.

The repair tool for data the normal update refuses: a submitted document with a
wrong status, a stale total, a field a validation now rejects. Because nothing
recalculates, it can leave a document inconsistent with its own totals — reach
for update_document first, and use this only when that is genuinely blocked.
Always dry-run and show the user the before/after before committing.""",
	"run_scheduled_job": """\
Run a Scheduled Job Type now instead of waiting for the scheduler.

dry_run reports the job's method and frequency without running it.""",
	"run_server_script": """\
Run a snippet in Frappe's Server Script sandbox.

The widest tool here, and off by default in MCP Bridge Settings. It runs under
RestrictedPython, so imports and file access are unavailable, but frappe.db and
frappe.get_doc are not. Assign to `result` to return a value:

    doc = frappe.get_doc("Work Order", "MFG-WO-2026-00012")
    result = {"status": doc.status, "produced": doc.produced_qty}

Prefer a purpose-built tool when one fits; reach for this for a repair that needs
real logic. Dry-run it first, and show the user the script before committing.""",
}
