# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""Patches, cache and the repair tools that skip the usual validations."""

from ..client import Bridge
from .common import render


def register(mcp, bridge: Bridge) -> None:
	@mcp.tool()
	async def list_patches(app: str | None = None, pending_only: bool = False) -> str:
		"""List patch modules and whether each has already run on this site.

		Pass app to look at one app; leave it out to cover every installed app.
		pending_only=true narrows it to the ones a bench migrate would still apply.
		"""
		return render(await bridge.call("list_patches", {"app": app, "pending_only": pending_only}))

	@mcp.tool()
	async def get_patch_log(limit: int = 50, search: str | None = None) -> str:
		"""Read the Patch Log: which patches ran on this site, newest first."""
		return render(await bridge.call("get_patch_log", {"limit": limit, "search": search}))

	@mcp.tool()
	async def run_patch(
		patch_module: str,
		force: bool = False,
		background: bool = False,
		dry_run: bool | None = None,
	) -> str:
		"""Run one patch module, e.g. "myapp.patches.v1_0.fix_customer_names".

		A patch commits as it goes, so unlike the other write tools it cannot be rolled
		back. dry_run therefore reports whether it would run and whether the module
		imports, without running it — do that first. An already-applied patch is skipped
		unless force=true. background=true queues it on the long worker and returns a job
		id, which is what you want for anything that touches many rows.
		"""
		return render(
			await bridge.call(
				"run_patch",
				{"patch_module": patch_module, "force": force, "background": background},
				writes=True,
				dry_run=dry_run,
			)
		)

	@mcp.tool()
	async def clear_cache(doctype: str | None = None, dry_run: bool | None = None) -> str:
		"""Clear the site cache, or just one doctype's. Run this after a schema change."""
		return render(await bridge.call("clear_cache", {"doctype": doctype}, writes=True, dry_run=dry_run))

	@mcp.tool()
	async def reload_doctype(doctype: str, force: bool = False, dry_run: bool | None = None) -> str:
		"""Re-sync a doctype's schema from its json file into the database.

		The equivalent of what bench migrate does for one doctype. Useful after deploying
		a field change without a full migrate.
		"""
		return render(
			await bridge.call(
				"reload_doctype", {"doctype": doctype, "force": force}, writes=True, dry_run=dry_run
			)
		)

	@mcp.tool()
	async def force_set_values(
		doctype: str,
		name: str,
		values: dict,
		update_modified: bool = True,
		dry_run: bool | None = None,
	) -> str:
		"""Write field values straight to the table, skipping controller validation.

		The repair tool for data the normal update refuses: a submitted document with a
		wrong status, a stale total, a field a validation now rejects. Because nothing
		recalculates, it can leave a document inconsistent with its own totals — reach
		for update_document first, and use this only when that is genuinely blocked.
		Always dry-run and show the user the before/after before committing.
		"""
		return render(
			await bridge.call(
				"force_set_values",
				{
					"doctype": doctype,
					"name": name,
					"values": values,
					"update_modified": update_modified,
				},
				writes=True,
				dry_run=dry_run,
			)
		)

	@mcp.tool()
	async def run_scheduled_job(scheduled_job_type: str, dry_run: bool | None = None) -> str:
		"""Run a Scheduled Job Type now instead of waiting for the scheduler.

		dry_run reports the job's method and frequency without running it.
		"""
		return render(
			await bridge.call(
				"run_scheduled_job", {"scheduled_job_type": scheduled_job_type}, writes=True, dry_run=dry_run
			)
		)

	@mcp.tool()
	async def run_server_script(script: str, dry_run: bool | None = None) -> str:
		"""Run a snippet in Frappe's Server Script sandbox.

		The widest tool here, and off by default in MCP Bridge Settings. It runs under
		RestrictedPython, so imports and file access are unavailable, but frappe.db and
		frappe.get_doc are not. Assign to `result` to return a value:

		    doc = frappe.get_doc("Work Order", "MFG-WO-2026-00012")
		    result = {"status": doc.status, "produced": doc.produced_qty}

		Prefer a purpose-built tool when one fits; reach for this for a repair that needs
		real logic. Dry-run it first, and show the user the script before committing.
		"""
		return render(
			await bridge.call("run_server_script", {"script": script}, writes=True, dry_run=dry_run)
		)
