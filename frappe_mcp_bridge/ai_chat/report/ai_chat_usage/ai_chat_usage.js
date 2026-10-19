// Copyright (c) 2026, Blaze Technology Solutions and contributors
// For license information, please see license.txt

frappe.query_reports["AI Chat Usage"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From"),
			fieldtype: "Date",
			default: frappe.datetime.month_start(),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "group_by",
			label: __("Group By"),
			fieldtype: "Select",
			options: ["User", "Model", "Provider", "Day", "Month"],
			default: "User",
		},
		{ fieldname: "user", label: __("User"), fieldtype: "Link", options: "User" },
		{ fieldname: "model", label: __("Model"), fieldtype: "Link", options: "AI Model" },
	],
};
