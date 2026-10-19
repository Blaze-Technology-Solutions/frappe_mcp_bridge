// Copyright (c) 2026, Blaze Technology Solutions and contributors
// For license information, please see license.txt

import { createApp, h } from "vue";
import AIChat from "./AIChat.vue";

// The full page at /app/ai-chat, and the Ask AI drawer on forms, share one component.
class AIChatApp {
	constructor({ wrapper, page, mode = "page", conversation = null, context = null, on_close = null }) {
		this.wrapper = wrapper;
		this.page = page;
		this.app = createApp({
			render: () =>
				h(AIChat, {
					ref: "chat",
					mode,
					initialConversation: conversation,
					context,
					onClose: on_close,
					onTitle: (title) => page && page.set_title(title || __("AI Chat")),
				}),
		});
		SetVueGlobals(this.app);
		this.root = this.app.mount(wrapper);
	}

	open(conversation) {
		this.root.$refs.chat && this.root.$refs.chat.open_from_route(conversation);
	}

	destroy() {
		this.app.unmount();
	}
}

class AIChatDrawer {
	static open(context) {
		if (AIChatDrawer.current) AIChatDrawer.current.close();
		AIChatDrawer.current = new AIChatDrawer(context);
	}

	constructor(context) {
		this.element = document.createElement("div");
		this.element.className = "ai-chat-drawer";
		document.body.appendChild(this.element);
		this.chat = new AIChatApp({
			wrapper: this.element,
			mode: "drawer",
			context,
			on_close: () => this.close(),
		});
	}

	close() {
		this.chat.destroy();
		this.element.remove();
		if (AIChatDrawer.current === this) AIChatDrawer.current = null;
	}
}

// Leaving the form closes its drawer. Registered once: frappe.router's off() cannot
// remove a handler, so a per-drawer listener would pile up.
frappe.router.on("change", () => AIChatDrawer.current && AIChatDrawer.current.close());

frappe.provide("frappe.ui");
frappe.ui.AIChat = AIChatApp;
frappe.ui.AIChatDrawer = AIChatDrawer;

export default AIChatApp;
