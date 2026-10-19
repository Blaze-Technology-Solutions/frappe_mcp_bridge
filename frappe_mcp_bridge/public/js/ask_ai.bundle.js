// Copyright (c) 2026, Blaze Technology Solutions and contributors
// For license information, please see license.txt

// The Ask AI button on forms. Loaded on every desk page, so it stays tiny: the chat
// itself is fetched only when the button is pressed. The server decides who may chat
// and which doctypes the AI may see; this only spares people a button that would refuse.

$(document).on("form-refresh", (event, frm) => {
	const access = frappe.boot.ai_chat;
	if (!access || !access.can_use || !access.erp || !frm || frm.is_new()) return;

	const doctype = frm.doctype;
	if ((access.blocked_doctypes || []).includes(doctype)) return;
	if (access.allowed_doctypes && !access.allowed_doctypes.includes(doctype)) return;

	frm.add_custom_button(__("Ask AI"), () => {
		frappe.require("ai_chat.bundle.js").then(() => {
			frappe.ui.AIChatDrawer.open({ doctype, name: frm.docname });
		});
	});
});
