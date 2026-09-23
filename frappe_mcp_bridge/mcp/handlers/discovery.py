# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""Tools that describe the site so Claude can work out what to ask for next."""

import frappe

from frappe_mcp_bridge.frappe_mcp_bridge.doctype.mcp_bridge_settings.mcp_bridge_settings import (
	CAPABILITY_FIELDS,
	get_settings,
)
from frappe_mcp_bridge.mcp.gate import assert_doctype_allowed, max_rows
from frappe_mcp_bridge.mcp.registry import tool

# Schema fields worth sending; the rest is layout noise Claude does not need.
SCHEMA_FIELD_KEYS = (
	"fieldname",
	"label",
	"fieldtype",
	"options",
	"reqd",
	"read_only",
	"hidden",
	"default",
	"depends_on",
	"unique",
	"in_list_view",
	"allow_on_submit",
	"precision",
	"description",
)

LAYOUT_FIELDTYPES = {"Section Break", "Column Break", "Tab Break", "HTML", "Fold"}


@tool("describe_site", "read", summary="Site name, versions, the signed-in user and what MCP may do.")
def describe_site() -> dict:
	from frappe.utils.change_log import get_versions

	settings = get_settings()

	return {
		"site": frappe.local.site,
		"user": frappe.session.user,
		"full_name": frappe.db.get_value("User", frappe.session.user, "full_name"),
		"roles": sorted(frappe.get_roles()),
		"server_time": frappe.utils.now(),
		"time_zone": frappe.db.get_single_value("System Settings", "time_zone"),
		"apps": {
			app: {"version": info.get("version"), "branch": info.get("branch")}
			for app, info in (get_versions() or {}).items()
		},
		"mcp": {
			"enabled": bool(settings.enabled),
			"read_only_mode": settings.read_only_active(),
			"writes_allowed_until": None if settings.read_only_active() else settings.writes_allowed_until,
			"default_dry_run": bool(settings.default_dry_run),
			"max_rows": settings.max_rows,
			"max_write_batch": settings.max_write_batch,
			"capabilities": {
				name: bool(settings.get(field)) for name, field in sorted(CAPABILITY_FIELDS.items())
			},
			"restrict_doctypes": bool(settings.restrict_doctypes),
			"allowed_doctypes": [row.document_type for row in (settings.allowed_doctypes or [])],
			"blocked_doctypes": [row.document_type for row in (settings.blocked_doctypes or [])],
			# Names only, so Claude can explain a ••• rather than guess at it.
			"masked_fields": [
				f"{row.document_type}.{row.fieldname}" if row.document_type else row.fieldname
				for row in (settings.masked_fields or [])
			],
		},
	}


@tool("list_doctypes", "read", summary="Find doctypes by name, module or app.")
def list_doctypes(
	search: str | None = None,
	module: str | None = None,
	app: str | None = None,
	include_child_tables: bool = False,
	limit: int = 100,
) -> dict:
	filters = {}
	if not include_child_tables:
		filters["istable"] = 0

	if module:
		filters["module"] = module

	or_filters = {"name": ("like", f"%{search}%")} if search else None

	rows = frappe.get_all(
		"DocType",
		filters=filters,
		or_filters=or_filters,
		fields=["name", "module", "istable", "issingle", "is_submittable", "custom"],
		order_by="name asc",
		limit=min(int(limit or 100), max_rows()),
	)

	if app:
		modules = set(frappe.get_all("Module Def", filters={"app_name": app}, pluck="name"))
		rows = [row for row in rows if row.module in modules]

	return {"count": len(rows), "doctypes": rows}


@tool(
	"get_doctype_schema",
	"read",
	summary="Fields, options and naming for one doctype, optionally with its child tables.",
	doctype_param="doctype",
)
def get_doctype_schema(
	doctype: str,
	include_children: bool = True,
	include_layout_fields: bool = False,
) -> dict:
	meta = frappe.get_meta(doctype)

	schema = _describe_meta(meta, include_layout_fields)
	schema["permissions_for_current_user"] = {
		action: frappe.has_permission(doctype, action)
		for action in ("read", "write", "create", "delete", "submit", "cancel")
	}

	if include_children:
		children = {}
		for field in meta.get_table_fields():
			if field.options in children:
				continue

			children[field.options] = _describe_meta(frappe.get_meta(field.options), include_layout_fields)

		schema["child_doctypes"] = children

	return schema


def _describe_meta(meta, include_layout_fields: bool) -> dict:
	fields = []
	for field in meta.fields:
		if not include_layout_fields and field.fieldtype in LAYOUT_FIELDTYPES:
			continue

		fields.append({key: field.get(key) for key in SCHEMA_FIELD_KEYS if field.get(key) not in (None, "")})

	return {
		"doctype": meta.name,
		"module": meta.module,
		"is_single": bool(meta.issingle),
		"is_child_table": bool(meta.istable),
		"is_submittable": bool(meta.is_submittable),
		"is_tree": bool(meta.get("is_tree")),
		"autoname": meta.autoname,
		"title_field": meta.title_field,
		"track_changes": bool(meta.track_changes),
		"fields": fields,
	}


@tool("list_reports", "read", summary="Query and script reports available on this site.")
def list_reports(search: str | None = None, doctype: str | None = None, limit: int = 100) -> dict:
	filters = {"disabled": 0}
	if doctype:
		assert_doctype_allowed(doctype)
		filters["ref_doctype"] = doctype

	if search:
		filters["name"] = ("like", f"%{search}%")

	return {
		"reports": frappe.get_all(
			"Report",
			filters=filters,
			fields=["name", "ref_doctype", "report_type", "module"],
			order_by="name asc",
			limit=min(int(limit or 100), max_rows()),
		)
	}
