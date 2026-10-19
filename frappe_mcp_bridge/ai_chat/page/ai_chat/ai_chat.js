// Copyright (c) 2026, Blaze Technology Solutions and contributors
// For license information, please see license.txt

frappe.pages["ai-chat"].on_page_load = function (wrapper) {
	frappe.ui.make_app_page({ parent: wrapper, title: __("AI Chat"), single_column: true });
	$(wrapper).find(".layout-main-section").addClass("ai-chat-page-section");

	if (frappe.boot.developer_mode) {
		frappe.hot_update = frappe.hot_update || [];
		frappe.hot_update.push(() => mount(wrapper, true));
	}
};

frappe.pages["ai-chat"].on_page_show = function (wrapper) {
	mount(wrapper, false);
};

function mount(wrapper, force) {
	const conversation = frappe.get_route()[1] || null;

	frappe.require("ai_chat.bundle.js").then(() => {
		if (wrapper.ai_chat && !force) {
			wrapper.ai_chat.open(conversation);
			return;
		}

		wrapper.ai_chat && wrapper.ai_chat.destroy();
		const $parent = $(wrapper).find(".layout-main-section").empty();
		wrapper.ai_chat = new frappe.ui.AIChat({
			wrapper: $parent.get(0),
			page: wrapper.page,
			mode: "page",
			conversation,
		});
	});
}
