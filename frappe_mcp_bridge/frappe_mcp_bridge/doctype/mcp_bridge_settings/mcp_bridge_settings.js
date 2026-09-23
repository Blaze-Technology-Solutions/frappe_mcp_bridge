// Copyright (c) 2026, Blaze Technology Solutions and contributors
// For license information, please see license.txt

const SETTINGS_MODULE = "frappe_mcp_bridge.frappe_mcp_bridge.doctype.mcp_bridge_settings.mcp_bridge_settings";

frappe.ui.form.on("MCP Bridge Settings", {
	refresh(frm) {
		frm.trigger("render_status");
		frm.trigger("render_connection");

		frm.add_custom_button(__("View Request Log"), () => {
			frappe.set_route("List", "MCP Bridge Log");
		});

		frm.add_custom_button(__("Generate API Keys"), () => frm.trigger("generate_keys"));
	},

	enabled(frm) {
		frm.trigger("render_status");
	},

	read_only_mode(frm) {
		frm.trigger("render_status");
	},

	render_status(frm) {
		const [indicator, text] = frm.doc.enabled
			? frm.doc.read_only_mode
				? ["blue", __("MCP is on and can only read.")]
				: ["orange", __("MCP is on and may change data on this site.")]
			: ["red", __("MCP is off. Every tool call is refused.")];

		frm.get_field("status_html").$wrapper.html(`
			<div class="form-message ${indicator === "red" ? "red" : ""}">
				<span class="indicator ${indicator}">${frappe.utils.escape_html(text)}</span>
			</div>
		`);
	},

	render_connection(frm) {
		frappe.call(`${SETTINGS_MODULE}.get_connection_info`).then(({ message }) => {
			frm.get_field("connection_html").$wrapper.html(connection_html(message));
		});
	},

	generate_keys(frm) {
		if (frm.is_dirty()) {
			frappe.msgprint(__("Save the settings first."));
			return;
		}

		if (!frm.doc.mcp_user) {
			frappe.msgprint(__("Set MCP User and save, then generate its keys."));
			return;
		}

		frappe.confirm(
			__(
				"Issue a new API key pair for {0}? Any secret issued to this user before stops working immediately.",
				[frappe.utils.escape_html(frm.doc.mcp_user)]
			),
			() =>
				frappe
					.call({ method: `${SETTINGS_MODULE}.generate_keys_for_mcp_user`, type: "POST" })
					.then(({ message }) => show_keys(message))
		);
	},
});

function show_keys(info) {
	const dialog = new frappe.ui.Dialog({
		title: __("API keys for {0}", [info.user]),
		size: "large",
		fields: [{ fieldtype: "HTML", fieldname: "body" }],
		primary_action_label: __("I have copied them"),
		primary_action() {
			dialog.hide();
		},
	});

	dialog.fields_dict.body.$wrapper.html(`
		<div class="form-message yellow">
			${__("The secret is shown this once. Copy what you need now.")}
		</div>
		${connection_html(info)}
	`);
	dialog.show();
}

function connection_html(info) {
	const block = (label, text) => `
		<div class="mb-3">
			<div class="text-muted small mb-1">${label}</div>
			<pre class="small p-2" style="white-space: pre-wrap; word-break: break-all; user-select: all;">${frappe.utils.escape_html(text)}</pre>
		</div>`;

	return `
		${block(__("MCP endpoint (Streamable HTTP)"), info.endpoint)}
		${block(__("Claude Code"), info.claude_code)}
		${block(__("Codex"), info.codex)}
		${block(__("Local stdio bridge (.env)"), info.stdio_env)}
	`;
}
