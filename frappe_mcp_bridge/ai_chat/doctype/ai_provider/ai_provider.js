// Copyright (c) 2026, Blaze Technology Solutions and contributors
// For license information, please see license.txt

const METHOD = "frappe_mcp_bridge.ai_chat.doctype.ai_provider.ai_provider";

frappe.ui.form.on("AI Provider", {
	refresh(frm) {
		if (frm.is_new()) return;

		frm.add_custom_button(__("Test Connection"), () => {
			frappe.call({
				method: `${METHOD}.test_connection`,
				args: { provider: frm.doc.name },
				freeze: true,
				freeze_message: __("Checking the API key…"),
				callback: ({ message }) => {
					frappe.show_alert(
						{ message: message.message, indicator: message.ok ? "green" : "red" },
						8
					);
					frm.reload_doc();
				},
			});
		});

		frm.add_custom_button(__("Fetch Models"), () => fetch_models(frm));
	},

	provider_type(frm) {
		// A new type means a new endpoint: let validate fill in the preset again.
		frm.set_value("base_url", "");
	},
});

function fetch_models(frm) {
	frappe.call({
		method: `${METHOD}.fetch_models`,
		args: { provider: frm.doc.name },
		freeze: true,
		freeze_message: __("Asking {0} for its models…", [frm.doc.provider_name]),
		callback: ({ message: ids }) => {
			if (!ids || !ids.length) {
				frappe.msgprint(__("The provider listed no models."));
				return;
			}

			const dialog = new frappe.ui.Dialog({
				title: __("Add Models from {0}", [frm.doc.provider_name]),
				fields: [
					{
						fieldtype: "HTML",
						options: `<p class="text-muted">${__(
							"Picked models are added disabled. Open each one, enter its prices, then enable it."
						)}</p>`,
					},
					{
						fieldname: "model_ids",
						fieldtype: "MultiCheck",
						label: __("Models"),
						columns: 2,
						options: ids.map((id) => ({ label: id, value: id })),
					},
				],
				primary_action_label: __("Add"),
				primary_action({ model_ids }) {
					frappe.call({
						method: `${METHOD}.add_models`,
						args: { provider: frm.doc.name, model_ids },
						callback: ({ message: created }) => {
							dialog.hide();
							frappe.show_alert({
								message: __("{0} models added", [created.length]),
								indicator: "green",
							});
							frappe.set_route("List", "AI Model", { provider: frm.doc.name });
						},
					});
				},
			});
			dialog.show();
		},
	});
}
