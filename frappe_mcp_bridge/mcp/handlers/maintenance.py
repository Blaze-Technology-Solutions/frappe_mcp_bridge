# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""Patches and the bench-style maintenance actions."""

import frappe
from frappe import _
from frappe.modules.patch_handler import PatchType, executed, get_patches_from_app, run_single

from frappe_mcp_bridge.mcp.gate import assert_doctype_allowed, max_rows
from frappe_mcp_bridge.mcp.registry import tool


@tool(
	"list_patches",
	"read",
	summary="Patches shipped by an app, or by every installed app, and whether they have run.",
)
def list_patches(app: str | None = None, pending_only: bool = False) -> dict:
	installed = frappe.get_installed_apps()
	if app and app not in installed:
		frappe.throw(_("{0} is not installed on this site.").format(app))

	patches = []
	for current_app in [app] if app else installed:
		for patch_type in PatchType:
			for patch in get_patches_from_app(current_app, patch_type=patch_type):
				has_run = bool(executed(patch))
				if pending_only and has_run:
					continue

				patches.append(
					{"app": current_app, "patch": patch, "stage": patch_type.value, "executed": has_run}
				)

	return {"app": app or "all", "count": len(patches), "patches": patches}


@tool(
	"run_patch",
	"patch",
	summary="Run one patch module. dry_run reports what would run without running it.",
	writes=True,
)
def run_patch(
	patch_module: str,
	force: bool = False,
	background: bool = False,
	dry_run: bool = False,
) -> dict:
	already_run = bool(executed(patch_module))

	if dry_run:
		# A patch commits as it goes, so it cannot be rolled back the way the other
		# write tools are. Dry run therefore reports the plan instead of simulating it.
		return {
			"patch": patch_module,
			"dry_run": True,
			"executed": already_run,
			"would_run": force or not already_run,
			"resolved": _resolve_patch(patch_module),
		}

	if already_run and not force:
		return {"patch": patch_module, "executed": True, "skipped": True, "reason": "Already applied"}

	if background:
		job = frappe.enqueue(
			"frappe.modules.patch_handler.run_single",
			queue="long",
			timeout=3600,
			patchmodule=patch_module,
			force=force,
		)
		return {"patch": patch_module, "queued": True, "job_id": job.id}

	run_single(patchmodule=patch_module, force=force)

	return {"patch": patch_module, "executed": True, "forced": force}


@tool("get_patch_log", "read", summary="Recently applied patches, newest first.")
def get_patch_log(limit: int = 50, search: str | None = None) -> dict:
	filters = {"patch": ("like", f"%{search}%")} if search else None

	return {
		"entries": frappe.get_all(
			"Patch Log",
			filters=filters,
			fields=["patch", "skipped", "creation"],
			order_by="creation desc",
			limit=min(int(limit or 50), max_rows()),
		)
	}


@tool("clear_cache", "admin", summary="Clear the site cache, or just one doctype's.", writes=True)
def clear_cache(doctype: str | None = None, dry_run: bool = False) -> dict:
	if doctype:
		assert_doctype_allowed(doctype)
		frappe.clear_cache(doctype=doctype)
	else:
		frappe.clear_cache()

	return {"cleared": doctype or "site", "dry_run": dry_run}


@tool(
	"reload_doctype",
	"admin",
	summary="Re-sync a doctype's schema from its json file into the database.",
	writes=True,
	doctype_param="doctype",
)
def reload_doctype(doctype: str, force: bool = False, dry_run: bool = False) -> dict:
	frappe.reload_doctype(doctype, force=force)
	return {"doctype": doctype, "reloaded": True, "forced": force, "dry_run": dry_run}


@tool(
	"force_set_values",
	"admin",
	summary="Write field values straight to the table, skipping controller validation. For repairing data the normal update refuses.",
	writes=True,
	doctype_param="doctype",
)
def force_set_values(
	doctype: str,
	name: str,
	values: dict,
	update_modified: bool = True,
	dry_run: bool = False,
) -> dict:
	if not values:
		frappe.throw(_("Pass the values to set."))

	if not frappe.db.exists(doctype, name):
		frappe.throw(_("{0} {1} does not exist.").format(doctype, name))

	before = frappe.db.get_value(doctype, name, list(values), as_dict=True) or {}
	frappe.db.set_value(doctype, name, values, update_modified=update_modified)
	after = frappe.db.get_value(doctype, name, list(values), as_dict=True) or {}

	return {
		"doctype": doctype,
		"name": name,
		"dry_run": dry_run,
		"validation_skipped": True,
		"changed": {
			field: {"from": before.get(field), "to": after.get(field)}
			for field in values
			if before.get(field) != after.get(field)
		},
	}


@tool(
	"run_scheduled_job",
	"admin",
	summary="Run a Scheduled Job Type now instead of waiting for the scheduler.",
	writes=True,
)
def run_scheduled_job(scheduled_job_type: str, dry_run: bool = False) -> dict:
	job = frappe.get_doc("Scheduled Job Type", scheduled_job_type)

	if dry_run:
		return {
			"scheduled_job_type": scheduled_job_type,
			"dry_run": True,
			"method": job.method,
			"frequency": job.frequency,
			"stopped": bool(job.stopped),
		}

	job.execute()
	return {"scheduled_job_type": scheduled_job_type, "method": job.method, "executed": True}


def _resolve_patch(patch_module: str) -> str:
	"""Confirm the module is importable before a dry run claims it would run."""
	if patch_module.startswith("execute:"):
		return "inline execute: statement"

	try:
		frappe.get_attr(f"{patch_module.split(maxsplit=1)[0]}.execute")
	except Exception as exception:
		frappe.throw(_("{0} cannot be imported: {1}").format(patch_module, exception))

	return f"{patch_module}.execute"
