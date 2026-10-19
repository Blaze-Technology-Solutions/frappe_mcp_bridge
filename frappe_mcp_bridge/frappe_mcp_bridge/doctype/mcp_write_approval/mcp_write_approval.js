// Copyright (c) 2026, Blaze Technology Solutions and contributors
// For license information, please see license.txt

frappe.ui.form.on("MCP Write Approval", {
	refresh(frm) {
		if (frm.doc.status !== "Pending") return;
		if (frm.doc.user !== frappe.session.user) {
			frm.set_intro(__("Waiting for {0} to approve or reject it.", [frm.doc.user]), "orange");
			return;
		}

		frm.set_intro(__("Nothing has been saved yet. Read the preview, then approve or reject the change."), "orange");
		frm.add_custom_button(__("Approve"), () => decide(frm, 1)).addClass("btn-primary");
		frm.add_custom_button(__("Reject"), () => decide(frm, 0));
	},
});

function decide(frm, approve) {
	frappe.call({
		method: "frappe_mcp_bridge.mcp.approvals.decide",
		args: { approval: frm.doc.name, approve },
		freeze: true,
		freeze_message: approve ? __("Making the change…") : __("Rejecting…"),
		callback: () => frm.reload_doc(),
	});
}
