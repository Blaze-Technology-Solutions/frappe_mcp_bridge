// Copyright (c) 2026, Blaze Technology Solutions and contributors
// For license information, please see license.txt

frappe.listview_settings["MCP Bridge Log"] = {
	add_fields: ["status"],
	get_indicator(doc) {
		return {
			Success: [__("Success"), "green", "status,=,Success"],
			Failed: [__("Failed"), "red", "status,=,Failed"],
			Blocked: [__("Blocked"), "orange", "status,=,Blocked"],
		}[doc.status];
	},
};
