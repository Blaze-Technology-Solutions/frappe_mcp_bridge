// Copyright (c) 2026, Blaze Technology Solutions and contributors
// For license information, please see license.txt

const { test, expect } = require("@playwright/test");
const { modelRequests, openNewChat, send, sentToModel, signIn, xcall } = require("./helpers");

test.beforeEach(async ({ page }) => {
	await signIn(page);
	await page.goto("/app");
});

async function todo(page, description) {
	return (await xcall(page, "frappe.client.insert", { doc: { doctype: "ToDo", description } })).name;
}

async function askAI(page) {
	const button = page.locator(".page-actions:visible").getByRole("button", { name: "Ask AI" });
	await expect(button).toBeVisible();
	await button.click();
	await expect(page.locator(".ai-chat-drawer .ai-chat-composer__input")).toBeVisible();
}

test("Ask AI on a list sends the ticked rows", async ({ page, request }) => {
	const picked = [await todo(page, "picked from the list A"), await todo(page, "picked from the list B")];
	await todo(page, "left out of the list C");

	await page.goto("/app/todo?status=Open");
	for (const name of picked) {
		await page.locator(`.list-row-checkbox[data-name="${name}"]`).check();
	}
	await askAI(page);
	await expect(page.locator(".ai-chat-drawer .ai-chat__context")).toHaveText("ToDo · 2 selected");

	const since = await modelRequests(request);
	await send(page, "Summarise the ticked rows");
	await expect(page.locator(".ai-chat-drawer .ai-chat-msg--assistant").last()).toContainText("quick summary");
	const sent = await sentToModel(request, since);
	expect(sent).toContain("picked from the list A");
	expect(sent).toContain("picked from the list B");
	expect(sent).not.toContain("left out of the list C");
});

test("Ask AI on a report sends what it shows", async ({ page, request }) => {
	await todo(page, "a row of the E2E report");
	await page.goto("/app/query-report/E2E ToDos");
	await askAI(page);
	await expect(page.locator(".ai-chat-drawer .ai-chat__context")).toHaveText("Report · E2E ToDos");

	const since = await modelRequests(request);
	await send(page, "What does this report show?");
	await expect(page.locator(".ai-chat-drawer .ai-chat-msg--assistant").last()).toContainText("quick summary");
	expect(await sentToModel(request, since)).toContain("a row of the E2E report");
});

test("Draft with AI fills the email and sends nothing", async ({ page }) => {
	const name = await todo(page, "Ship forty units to Acme on Friday");
	await page.goto(`/app/todo/${name}`);
	await page.waitForFunction((docname) => window.cur_frm && cur_frm.docname === docname, name);
	await page.evaluate(() => cur_frm.email_doc());

	const dialog = page.locator(".modal:visible").filter({ has: page.locator('[data-fieldname="content"]') });
	await dialog.getByRole("button", { name: "Draft with AI" }).click();
	const prompt = page.locator(".modal:visible").filter({ hasText: "What should the email say?" });
	await prompt.locator("textarea").fill("Confirm the shipping date");
	await prompt.getByRole("button", { name: "Draft" }).click();

	await expect(dialog.locator('[data-fieldname="content"] .ql-editor[contenteditable="true"]')).toContainText("quick summary");
	// Drafting sent no email.
	const sent = await xcall(page, "frappe.client.get_count", {
		doctype: "Communication",
		filters: { reference_doctype: "ToDo", reference_name: name },
	});
	expect(sent).toBe(0);
});

test("a dashboard chart downloads as a PNG", async ({ page }) => {
	await openNewChat(page);
	await send(page, "Make an artifact dashboard for ops");
	const card = page.locator(".ai-chat-dashboard__card").filter({ has: page.locator(".frappe-chart") });
	await expect(card.locator(".frappe-chart")).toBeVisible();

	const [download] = await Promise.all([page.waitForEvent("download"), card.getByRole("button", { name: "PNG" }).click()]);
	expect(download.suggestedFilename()).toMatch(/\.png$/);
});
