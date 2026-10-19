// Copyright (c) 2026, Blaze Technology Solutions and contributors
// For license information, please see license.txt

// The Ask AI button on forms, list views and query reports. Loaded on every desk page, so
// it stays small: the chat itself is fetched only when the button is pressed. The server
// decides who may chat and what the AI may read; this only spares people a button that
// would refuse.

function chat_allowed(doctype) {
	const access = frappe.boot.ai_chat;
	if (!access || !access.can_use || !access.erp) return false;
	if (!doctype) return true;
	if ((access.blocked_doctypes || []).includes(doctype)) return false;
	return !access.allowed_doctypes || access.allowed_doctypes.includes(doctype);
}

function open_chat(context) {
	frappe.require("ai_chat.bundle.js").then(() => frappe.ui.AIChatDrawer.open(context));
}

$(document).on("form-refresh", (event, frm) => {
	if (!frm || frm.is_new() || !chat_allowed(frm.doctype)) return;
	frm.add_custom_button(__("Ask AI"), () => open_chat({ doctype: frm.doctype, name: frm.docname }));
});

// Lists and reports have no refresh event to hook, and a report redraws its toolbar when it
// runs again, so while one is open the button is put back whenever it has gone.
function has_button(page) {
	return page.wrapper.find(".ai-chat-ask-button").length > 0;
}

function add_view_button() {
	const [view, name] = frappe.get_route();

	const list = window.cur_list;
	if (view === "List" && list && list.doctype === name && chat_allowed(name) && !has_button(list.page)) {
		list.page.add_inner_button(__("Ask AI"), () => {
			// The ticked rows, or the list as it is filtered when none are ticked.
			const names = list.get_checked_items(true);
			open_chat({
				kind: "list",
				doctype: list.doctype,
				names,
				filters: names.length ? [] : list.get_filters_for_args(),
			});
		}).addClass("ai-chat-ask-button");
	}

	const report = frappe.query_report;
	if (view === "query-report" && report && report.page && chat_allowed() && !has_button(report.page)) {
		// One report page serves every report, so read which one at the click.
		report.page
			.add_inner_button(__("Ask AI"), () =>
				open_chat({
					kind: "report",
					report: frappe.query_report.report_name,
					filters: frappe.query_report.get_filter_values(),
				})
			)
			.addClass("ai-chat-ask-button");
	}
}

let watching = null;
frappe.router.on("change", () => {
	clearInterval(watching);
	const view = frappe.get_route()[0];
	if (view === "List" || view === "query-report") {
		setTimeout(add_view_button, 400);
		watching = setInterval(add_view_button, 1500);
	}
});

// Draft with AI in Frappe's email dialog. It fills in the message; the user edits and sends.
if (frappe.views && frappe.views.CommunicationComposer) {
	const Composer = frappe.views.CommunicationComposer;
	frappe.views.CommunicationComposer = class extends Composer {
		make() {
			super.make();
			if (this.frm && !this.frm.is_new() && chat_allowed(this.frm.doctype)) {
				// Under the message: the email dialog hides the dialog's own footer, and above the
				// message the recipients box overlaps it.
				const button = $(
					`<button type="button" class="btn btn-default btn-xs" style="display: inline-block; width: auto; margin-top: 6px">`
				)
					.text(__("Draft with AI"))
					.on("click", () => draft_email(this));
				this.dialog.fields_dict.content.$wrapper.append(button);
			}
		}
	};
}

function draft_email(composer) {
	frappe.prompt(
		{
			fieldname: "instruction",
			fieldtype: "Small Text",
			label: __("What should the email say?"),
			description: __("Optional. The AI reads this document and the email you are replying to."),
		},
		async ({ instruction }) => {
			const { message: draft } = await frappe.call({
				method: "frappe_mcp_bridge.api.chat.draft_email",
				args: {
					doctype: composer.frm.doctype,
					name: composer.frm.docname,
					subject: composer.dialog.get_value("subject"),
					instruction,
					reply_to: composer.is_a_reply && composer.last_email ? composer.last_email.name : null,
				},
				freeze: true,
				freeze_message: __("Drafting…"),
			});
			composer.dialog.set_value("content", draft.html);
			if (draft.removed && draft.removed.length) {
				frappe.show_alert(
					{ message: __("Left out of what the AI read: {0}", [draft.removed.join(", ")]), indicator: "orange" },
					8
				);
			}
		},
		__("Draft with AI"),
		__("Draft")
	);
}
