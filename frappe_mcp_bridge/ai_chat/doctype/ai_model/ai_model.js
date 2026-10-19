// Copyright (c) 2026, Blaze Technology Solutions and contributors
// For license information, please see license.txt

frappe.ui.form.on("AI Model", {
	refresh(frm) {
		if (frm.is_new() || !frappe.user.has_role("System Manager")) return;

		frm.add_custom_button(__("Run Live Check"), () => {
			frappe.confirm(
				__(
					"This sends two or three short requests to {0} with its real key. They cost a fraction of a cent, recorded in AI Chat usage as an archived chat of yours.",
					[frappe.utils.escape_html(frm.doc.provider)]
				),
				() => run_live_check(frm)
			);
		});
	},
});

function run_live_check(frm) {
	frappe.call({
		method: "frappe_mcp_bridge.chat.live_check.run",
		args: { model: frm.doc.name },
		freeze: true,
		freeze_message: __("Trying {0}…", [frappe.utils.escape_html(frm.doc.model_label)]),
		callback: ({ message: result }) => show_result(result),
	});
}

function show_result(result) {
	const escape = frappe.utils.escape_html;
	const mark = (ok) => (ok === true ? "✅" : ok === false ? "❌" : "➖");
	// Details carry the model's own words, so every cell is escaped.
	const rows = result.checks
		.map((check) => `<tr><td>${mark(check.ok)}</td><td>${escape(check.check)}</td><td>${escape(check.detail)}</td></tr>`)
		.join("");
	const totals = __("{0} tokens in, {1} out, {2}, {3} seconds", [
		format_number(result.input_tokens, null, 0),
		format_number(result.output_tokens, null, 0),
		format_currency(result.cost_usd, "USD", 6),
		result.seconds,
	]);

	frappe.msgprint({
		title: result.ok ? __("Live check passed") : __("Live check found a problem"),
		indicator: result.ok ? "green" : "red",
		message: `<table class="table table-bordered table-sm"><tbody>${rows}</tbody></table><p class="text-muted">${escape(totals)}</p>`,
	});
}
