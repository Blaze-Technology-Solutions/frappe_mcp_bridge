// Copyright (c) 2026, Blaze Technology Solutions and contributors
// For license information, please see license.txt

// The fake model fills every artifact with hostile content: scripts, event handlers, outside
// images and links, meta refresh, forms. None of it may run or load anything.

const { test, expect } = require("@playwright/test");
const { leaks, openNewChat, send, signIn } = require("./helpers");

test.beforeEach(async ({ page }) => {
	await signIn(page);
	await openNewChat(page);
});

test.afterEach(async ({ page, request }) => {
	expect(await page.evaluate(() => window.__pwned)).toBeUndefined();
	expect(await leaks(request)).toEqual([]);
});

test("a hostile page opens in a sandbox and loads nothing", async ({ page }) => {
	await send(page, "Make an artifact html letter");

	const frame = page.locator(".ai-chat-artifact__frame");
	await expect(frame).toHaveAttribute("sandbox", "");
	await expect(page.frameLocator(".ai-chat-artifact__frame").locator("h1")).toHaveText("Dear customer");
	await expect(page.frameLocator(".ai-chat-artifact__frame").locator("script, form, iframe, a[href]")).toHaveCount(0);
	// Give anything that slipped through time to call home.
	await page.waitForTimeout(2000);
});

test("chart labels written as markup stay text, even in the tooltip", async ({ page }) => {
	await send(page, "Make an artifact chart of sales");

	const chart = page.locator(".ai-chat-artifact .frappe-chart");
	await expect(chart).toBeVisible();
	const box = await chart.boundingBox();
	await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);

	const tip = page.locator(".ai-chat-artifact .graph-svg-tip");
	await expect(tip).toContainText("‹img");
	await expect(tip.locator("img, b")).toHaveCount(0);
});

test("a diagram is an image and a document has nothing live", async ({ page }) => {
	await send(page, "Make an artifact svg diagram");
	await expect(page.locator(".ai-chat-artifact__svg img")).toHaveAttribute("src", /^data:image\/svg\+xml;base64,/);

	await send(page, "Now an artifact document of the notes");
	const notes = page.locator(".ai-chat-artifact__document");
	await expect(notes).toContainText("Agreed");
	await expect(notes.locator("img, script, [onerror], a[href^='javascript']")).toHaveCount(0);
});

test("a table sorts, and a new version keeps the old one", async ({ page }) => {
	await send(page, "Make an artifact table of stock");
	const table = page.locator(".ai-chat-artifact .ai-chat-table");
	await table.getByRole("columnheader", { name: "Qty" }).click();
	await expect(table.locator("tbody tr").first()).toContainText("Bolt");

	// The fake's chart and its "v2" share a key, so the second saves version 2.
	await send(page, "Make an artifact chart of sales");
	await send(page, "Update it: artifact v2");
	await expect(page.locator(".ai-chat-artifact__actions select option")).toHaveCount(2);
});
