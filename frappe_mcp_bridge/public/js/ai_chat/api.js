// Copyright (c) 2026, Blaze Technology Solutions and contributors
// For license information, please see license.txt

const BASE = "frappe_mcp_bridge.api.chat";

export function call(method, args = {}) {
	return new Promise((resolve, reject) => {
		frappe.call({
			method: `${BASE}.${method}`,
			args,
			callback: (response) => resolve(response.message),
			error: (error) => reject(error),
		});
	});
}

export async function upload(conversation, file) {
	const form = new FormData();
	form.append("conversation", conversation);
	form.append("file", file, file.name);

	const response = await fetch(`/api/method/${BASE}.upload_attachment`, {
		method: "POST",
		headers: { "X-Frappe-CSRF-Token": frappe.csrf_token, Accept: "application/json" },
		body: form,
	});
	const body = await response.json().catch(() => ({}));

	if (!response.ok) {
		throw new Error(server_message(body) || __("The file could not be uploaded."));
	}
	return body.message;
}

function server_message(body) {
	try {
		const messages = JSON.parse(body._server_messages || "[]");
		return messages.map((m) => JSON.parse(m).message).join(" ");
	} catch (e) {
		return body.exception || "";
	}
}

export const EVENT = "ai_chat_event";
