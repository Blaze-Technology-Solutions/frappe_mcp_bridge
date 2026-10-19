// Copyright (c) 2026, Blaze Technology Solutions and contributors
// For license information, please see license.txt

const { test, expect } = require("@playwright/test");
const { openNewChat, send, signIn, xcall } = require("./helpers");

test.beforeEach(async ({ page }) => {
	await signIn(page);
	await openNewChat(page);
});

test("a rating sticks and can be taken back", async ({ page }) => {
	await send(page, "Hello, rate me");
	const reply = page.locator(".ai-chat-msg--assistant").last();
	await expect(reply).toContainText("quick summary");

	const good = reply.getByRole("button", { name: "👍" });
	await good.click();
	await expect(good).toHaveAttribute("aria-pressed", "true");

	await page.reload();
	await expect(page.locator(".ai-chat-msg--assistant").last().getByRole("button", { name: "👍" })).toHaveAttribute(
		"aria-pressed",
		"true"
	);
	await page.locator(".ai-chat-msg--assistant").last().getByRole("button", { name: "👍" }).click();
	await expect(page.locator(".ai-chat-msg--assistant").last().getByRole("button", { name: "👍" })).toHaveAttribute(
		"aria-pressed",
		"false"
	);
});

test("regenerate replaces the last reply and keeps the old one", async ({ page }) => {
	await send(page, "Hello, answer twice");
	await expect(page.locator(".ai-chat-msg--assistant").last()).toContainText("quick summary");

	await page.getByRole("button", { name: "Regenerate" }).click();
	await expect(page.getByText(/Regenerated with/)).toBeVisible();
	await expect(page.locator(".ai-chat-msg--assistant")).toHaveCount(2);
	await expect(page.locator(".ai-chat-msg--assistant").first()).toContainText("Replaced");
});

test("search finds a chat by what the reply said", async ({ page }) => {
	await send(page, "Tell me about stock");
	await expect(page.locator(".ai-chat-msg--assistant").last()).toContainText("Gears");

	await page.locator(".ai-chat-sidebar input[type=search]").fill("Gears");
	await expect(page.locator(".ai-chat-sidebar__item-match").first()).toContainText("Gears");
});

test("a bulk change is approved for the ticked rows only", async ({ page }) => {
	const todos = [];
	for (const n of [1, 2, 3]) {
		todos.push((await xcall(page, "frappe.client.insert", { doc: { doctype: "ToDo", description: `bulk ${n}` } })).name);
	}

	await send(page, `bulk todos ${todos.join(",")} to bulk done`);
	const card = page.locator(".ai-chat-approval");
	await expect(card.locator(".ai-chat-approval__row")).toHaveCount(3);
	await card.locator(".ai-chat-approval__row", { hasText: todos[2] }).locator("input").uncheck();
	await card.getByRole("button", { name: "Approve 2 of 3" }).click();
	await expect(card.locator(".ai-chat-approval__head .indicator-pill")).toHaveText("Approved");

	const descriptions = [];
	for (const name of todos) {
		descriptions.push(
			(await xcall(page, "frappe.client.get_value", { doctype: "ToDo", filters: name, fieldname: "description" }))
				.description
		);
	}
	expect(descriptions).toEqual(["bulk done", "bulk done", "bulk 3"]);
});
