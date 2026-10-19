# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""Approve MCP writes in Desk: an alternative to write windows.

With Approve Writes in Desk ticked, a write from an MCP client is not made. It runs as a dry
run, or is previewed without running where a dry run could leave traces, and waits as an
MCP Write Approval. The person who asked approves or rejects it in Desk; only then does it
run, through the same gate, as them. The client is told the change is waiting, and can call
get_write_approval to see how it ended.

AI Chat has approval cards of its own, so its calls are not held here.
"""

import json
import time

import frappe
from frappe import _
from frappe.utils import add_to_date, get_datetime, get_url_to_form, now_datetime

from frappe_mcp_bridge.frappe_mcp_bridge.doctype.mcp_bridge_settings.mcp_bridge_settings import get_settings

DOCTYPE = "MCP Write Approval"
CHAT_CLIENT = "AI Chat"


def required(client: str) -> bool:
	"""Whether a real write from this client has to wait for approval."""
	if client == CHAT_CLIENT or frappe.flags.mcp_write_approved:
		return False
	return bool(get_settings().writes_need_approval)


def propose(definition, params: dict, client: str, started: float) -> dict:
	"""Preview the write and leave it waiting. Returns the envelope the client gets back."""
	from frappe_mcp_bridge.api import mcp as api
	from frappe_mcp_bridge.chat.tools import _preview_without_running, _render, can_dry_run

	log = None
	if can_dry_run(definition.name, params):
		envelope = api.execute(tool=definition.name, params=params, client=client, dry_run=True)
		if not envelope.get("ok"):
			return envelope
		preview, log = _render(envelope), envelope.get("log")
	else:
		preview, refusal = _preview_without_running(definition, params)
		if refusal:
			frappe.throw(refusal)

	minutes = int(get_settings().write_approval_minutes or 60)
	approval = frappe.get_doc(
		{
			"doctype": DOCTYPE,
			"tool": definition.name,
			"capability": definition.capability,
			"user": frappe.session.user,
			"client": client,
			"reference_doctype": params.get("doctype"),
			"reference_name": params.get("name"),
			"status": "Pending",
			"params": json.dumps(params, indent=1, default=str, sort_keys=True),
			"preview": preview[:100_000],
			"dry_run_log": log,
			"expires_at": add_to_date(now_datetime(), minutes=minutes),
		}
	)
	approval.flags.mcp_internal = True
	approval.insert(ignore_permissions=True)
	_notify(approval)

	link = get_url_to_form(DOCTYPE, approval.name)
	return {
		"ok": True,
		"tool": definition.name,
		# Not marked a dry run: the MCP reply would then say to pass dry_run=false, which here
		# only asks for the same approval twice.
		"dry_run": False,
		"duration": round(time.monotonic() - started, 3),
		"log": log,
		"result": {
			"approval": approval.name,
			"status": "Pending",
			"saved": False,
			"message": _(
				"NOT SAVED YET. This change is waiting for approval in Desk: {0}. Ask the user to "
				"approve or reject it there within {1} minutes, then call get_write_approval with "
				"approval={2} to see how it ended. Calling {3} again only adds a second approval."
			).format(link, minutes, approval.name, definition.name),
			"preview": preview[:20_000],
		},
	}


@frappe.whitelist(methods=["POST"])
def decide(approval: str, approve: int = 1) -> dict:
	"""The requester's Approve or Reject, from the MCP Write Approval form."""
	from frappe_mcp_bridge.api import mcp as api

	status = frappe.db.sql(f"select status from `tab{DOCTYPE}` where name = %s for update", approval)
	if not status:
		raise frappe.DoesNotExistError(_("That approval does not exist."))

	doc = frappe.get_doc(DOCTYPE, approval)
	if doc.user != frappe.session.user:
		frappe.throw(
			_("Only the person who asked for this change can approve or reject it."), frappe.PermissionError
		)
	if status[0][0] != "Pending":
		frappe.throw(_("{0} is already {1}.").format(doc.name, _(status[0][0]).lower()))
	if doc.expires_at and now_datetime() > get_datetime(doc.expires_at):
		_update(doc, status="Expired")
		frappe.db.commit()
		frappe.throw(_("{0} expired. Ask for the change again.").format(doc.name))

	if not frappe.utils.cint(approve):
		_update(doc, status="Rejected", decided_at=now_datetime())
		return {"status": doc.status}

	# Run for real, through the gate again: the settings may have changed since.
	frappe.flags.mcp_write_approved = True
	try:
		envelope = api.execute(
			tool=doc.tool, params=json.loads(doc.params or "{}"), client=doc.client, dry_run=False
		)
	finally:
		frappe.flags.mcp_write_approved = False

	if envelope.get("ok"):
		_update(
			doc,
			status="Approved",
			decided_at=now_datetime(),
			result=json.dumps(envelope.get("result"), indent=1, default=str)[:100_000],
			result_log=envelope.get("log"),
		)
	else:
		error = envelope.get("error") or {}
		_update(
			doc,
			status="Failed",
			decided_at=now_datetime(),
			error=f"{error.get('type', 'Error')}: {error.get('message', '')}"[:10_000],
			result_log=envelope.get("log"),
		)
	return {"status": doc.status}


def expire_pending() -> None:
	"""Scheduler hook: an approval nobody acted on can no longer be approved."""
	frappe.db.set_value(
		DOCTYPE,
		{"status": "Pending", "expires_at": ("<", now_datetime())},
		"status",
		"Expired",
		update_modified=False,
	)


def _update(doc, **values) -> None:
	doc.update(values)
	doc.flags.mcp_internal = True
	doc.save(ignore_permissions=True)


def _notify(approval) -> None:
	"""A bell notification, so the requester finds the waiting change in Desk."""
	frappe.get_doc(
		{
			"doctype": "Notification Log",
			"for_user": approval.user,
			"type": "Alert",
			"document_type": DOCTYPE,
			"document_name": approval.name,
			"subject": _("{0} from {1} is waiting for your approval").format(approval.tool, approval.client),
		}
	).insert(ignore_permissions=True)
