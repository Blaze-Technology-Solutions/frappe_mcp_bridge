// Copyright (c) 2026, Blaze Technology Solutions and contributors
// For license information, please see license.txt

const { test, expect } = require("@playwright/test");
const { modelRequests, openNewChat, send, sentToModel, signIn, xcall } = require("./helpers");

test.beforeEach(async ({ page }) => {
	await signIn(page);
	await openNewChat(page);
});

test("a reply arrives and the chat is listed", async ({ page }) => {
	await send(page, "Hello from the browser test");

	const reply = page.locator(".ai-chat__bubble--assistant").last();
	await expect(reply).toContainText("quick summary");
	await expect(reply.locator("table")).toBeVisible();
	await expect(page.locator(".ai-chat-sidebar__item-title").first()).toContainText("Hello from the browser test");
});

test("a change waits for approval and is made only when approved", async ({ page }) => {
	const todo = (
		await xcall(page, "frappe.client.insert", { doc: { doctype: "ToDo", description: "E2E browser todo" } })
	).name;
	const description = async () =>
		(await xcall(page, "frappe.client.get_value", { doctype: "ToDo", filters: todo, fieldname: "description" }))
			.description;

	await send(page, `change todo ${todo} to Changed in the browser test`);
	const card = page.locator(".ai-chat-approval");
	const status = card.locator(".ai-chat-approval__head .indicator-pill");
	await expect(status).toHaveText("Pending");
	expect(await description()).toContain("E2E browser todo");

	await card.getByRole("button", { name: "Approve" }).click();
	await expect(status).toHaveText("Approved");
	expect(await description()).toContain("Changed in the browser test");
});

test("secrets in an upload are announced and never reach the model", async ({ page, request }) => {
	await page.locator(".ai-chat input[type=file]").setInputFiles({
		name: "creds.txt",
		mimeType: "text/plain",
		buffer: Buffer.from("item,price\nwidget,4\npassword: hunter2-e2e\n"),
	});
	await expect(page.getByText("Before sending, this will be taken out of creds.txt: password")).toBeVisible();

	const since = await modelRequests(request);
	await send(page, "Summarise the attached file");
	await expect(page.locator(".ai-chat__bubble--assistant").last()).toContainText("quick summary");

	const sent = await sentToModel(request, since);
	expect(sent).toContain("widget,4");
	expect(sent).not.toContain("hunter2-e2e");
});
