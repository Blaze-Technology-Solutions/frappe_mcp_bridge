// Copyright (c) 2026, Blaze Technology Solutions and contributors
// For license information, please see license.txt

const SETTINGS_MODULE = "frappe_mcp_bridge.frappe_mcp_bridge.doctype.mcp_bridge_settings.mcp_bridge_settings";

const WRITE_WINDOWS = [
	{ label: __("15 minutes"), value: 15 },
	{ label: __("30 minutes"), value: 30 },
	{ label: __("1 hour"), value: 60 },
	{ label: __("2 hours"), value: 120 },
	{ label: __("4 hours"), value: 240 },
	{ label: __("8 hours"), value: 480 },
];

frappe.ui.form.on("MCP Bridge Settings", {
	refresh(frm) {
		frm.trigger("render_status");
		frm.trigger("render_connection");

		frm.add_custom_button(__("View Request Log"), () => {
			frappe.set_route("List", "MCP Bridge Log");
		});

		frm.add_custom_button(__("Generate API Keys"), () => frm.trigger("generate_keys"));

		if (frm.doc.enabled) {
			frm.add_custom_button(__("Allow Writes For…"), () => frm.trigger("allow_writes_for"));
		}
	},

	enabled(frm) {
		frm.trigger("render_status");
	},

	read_only_mode(frm) {
		frm.trigger("render_status");
	},

	allow_oauth(frm) {
		frm.trigger("render_connection");
	},

	render_status(frm) {
		const until = frm.doc.writes_allowed_until;
		const [indicator, text] = !frm.doc.enabled
			? ["red", __("MCP is off. Every tool call is refused.")]
			: frm.doc.read_only_mode
				? ["blue", __("MCP is on and can only read.")]
				: until
					? [
							"orange",
							__("MCP may change data until {0}. Read Only Mode switches back on then.", [
								frappe.datetime.str_to_user(until),
							]),
						]
					: ["orange", __("MCP is on and may change data on this site, with no time limit.")];

		frm.get_field("status_html").$wrapper.html(`
			<div class="form-message ${indicator === "red" ? "red" : ""}">
				<span class="indicator ${indicator}">${frappe.utils.escape_html(text)}</span>
			</div>
		`);
	},

	render_connection(frm) {
		frappe.call(`${SETTINGS_MODULE}.get_connection_info`).then(({ message }) => {
			frm.get_field("connection_html").$wrapper.html(connection_html(message));
			frm.get_field("oauth_html").$wrapper.html(oauth_html(frm, message));
		});
	},

	allow_writes_for(frm) {
		if (frm.is_dirty()) {
			frappe.msgprint(__("Save the settings first."));
			return;
		}

		frappe.prompt(
			{
				fieldname: "minutes",
				fieldtype: "Select",
				label: __("Allow writes for"),
				options: WRITE_WINDOWS.map((w) => ({ label: w.label, value: String(w.value) })),
				default: "30",
				reqd: 1,
				description: __(
					"Turns Read Only Mode off now and back on automatically afterwards. The capability checkboxes still decide which writes are possible."
				),
			},
			({ minutes }) =>
				frappe
					.call({
						method: `${SETTINGS_MODULE}.allow_writes_for`,
						args: { minutes: cint(minutes) },
						type: "POST",
					})
					.then(() => frm.reload_doc()),
			__("Allow Writes"),
			__("Allow")
		);
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

function code_block(label, text) {
	return `
		<div class="mb-3">
			<div class="text-muted small mb-1">${label}</div>
			<pre class="small p-2" style="white-space: pre-wrap; word-break: break-all; user-select: all;">${frappe.utils.escape_html(text)}</pre>
		</div>`;
}

function connection_html(info) {
	return `
		${code_block(__("MCP endpoint (Streamable HTTP)"), info.endpoint)}
		${code_block(__("Claude Code"), info.claude_code)}
		${code_block(__("Codex"), info.codex)}
		${code_block(__("Local stdio bridge (.env)"), info.stdio_env)}
	`;
}

function oauth_html(frm, info) {
	if (!frm.doc.allow_oauth) {
		return `<p class="text-muted small">${__(
			"Tick Allow OAuth Sign-In and save to let people connect with their own Frappe login instead of a shared API key."
		)}</p>`;
	}

	const https_note = info.is_https
		? ""
		: `<div class="form-message yellow small">${__(
				"claude.ai and Claude Desktop connectors need this site on https. Claude Code and Codex work over http too."
			)}</div>`;

	return `
		${https_note}
		<p class="text-muted small">${__(
			"No keys to hand out: the client opens this site's login page, the person signs in and presses Allow, and every call then runs as them. Revoke access any time from OAuth Bearer Token."
		)}</p>
		${code_block(__("Claude Code"), info.oauth_claude_code)}
		${code_block(__("Codex"), info.oauth_codex)}
		${code_block(__("claude.ai / Claude Desktop"), info.oauth_claude_ai)}
	`;
}
