// Copyright (c) 2026, Blaze Technology Solutions and contributors
// For license information, please see license.txt

const { defineConfig, devices } = require("@playwright/test");

// Replies arrive by polling every few seconds when socket.io isn't running, so waits are long.
module.exports = defineConfig({
	testDir: "./tests",
	timeout: 120_000,
	expect: { timeout: 45_000 },
	workers: 1,
	retries: process.env.CI ? 1 : 0,
	reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
	use: {
		...devices["Desktop Chrome"],
		baseURL: process.env.E2E_BASE_URL || "http://127.0.0.1:8000",
		trace: "retain-on-failure",
	},
});
