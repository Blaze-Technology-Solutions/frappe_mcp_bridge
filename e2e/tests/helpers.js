// Copyright (c) 2026, Blaze Technology Solutions and contributors
// For license information, please see license.txt

const { expect } = require("@playwright/test");

// Made by frappe_mcp_bridge.tests.e2e_fixtures.setup with the password in E2E_PASSWORD.
const USER = "ai-chat-e2e@example.com";
const FAKE = process.env.E2E_FAKE_URL || "http://127.0.0.1:18080";

async function signIn(page) {
	const response = await page.request.post("/api/method/login", {
		form: { usr: USER, pwd: process.env.E2E_PASSWORD },
	});
	expect(response.ok(), "sign in as the E2E user").toBeTruthy();
}

async function openNewChat(page) {
	await page.goto("/app/ai-chat");
	await expect(page.locator(".ai-chat-composer__input")).toBeVisible();
}

// While a reply runs, Send gives way to Stop and Enter does nothing, so this waits for Send.
async function send(page, text) {
	await page.locator(".ai-chat-composer__input").fill(text);
	const button = page.locator(".ai-chat-composer button", { hasText: "Send" });
	await expect(button).toBeEnabled();
	await button.click();
}

// Frappe's own client, so the request carries the session's CSRF token.
async function xcall(page, method, args) {
	return page.evaluate(([method, args]) => frappe.xcall(method, args), [method, args]);
}

// Paths under /leak are where hostile content points; nothing should ever ask for one.
async function leaks(request) {
	const response = await request.get(`${FAKE}/requests`);
	return (await response.json()).filter((path) => path.startsWith("/leak"));
}

// How many chat requests the fake model has had: note it before an action, so sentToModel
// can leave out what earlier tests sent.
async function modelRequests(request) {
	return (await (await request.get(`${FAKE}/bodies`)).json()).length;
}

// The chat requests the fake model received after the first `since`, as text.
async function sentToModel(request, since = 0) {
	const response = await request.get(`${FAKE}/bodies`);
	return JSON.stringify((await response.json()).slice(since));
}

module.exports = { leaks, modelRequests, openNewChat, send, sentToModel, signIn, xcall };
