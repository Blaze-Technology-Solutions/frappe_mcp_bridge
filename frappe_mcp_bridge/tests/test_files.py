# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

import contextlib
import os
import shutil
import tempfile
import time
import zipfile
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import frappe

from frappe_mcp_bridge.api import mcp as api
from frappe_mcp_bridge.mcp import http
from frappe_mcp_bridge.mcp.handlers import files
from frappe_mcp_bridge.tests.test_http import rpc
from frappe_mcp_bridge.tests.utils import IntegrationTestCase, mcp_settings, request

# On every bench that runs these tests, so export_apps accepts the name.
APP = "frappe_mcp_bridge"

# "needle" is in every file, so a search shows which ones it was allowed into.
FILES = {
	f"apps/{APP}/README.md": "# demo\n",
	f"apps/{APP}/{APP}/hooks.py": "app_name = 'demo'\nneedle = 1\n",
	f"apps/{APP}/{APP}/public/js/app.js": "// needle\n",
	f"apps/{APP}/{APP}/tests/server.key": "needle\n",
	f"apps/{APP}/.env": "needle\n",
	f"apps/{APP}/.env.example": "needle=\n",
	f"apps/{APP}/.git/config": "[remote] url = https://needle@github.com/x\n",
	f"apps/{APP}/node_modules/pkg/index.js": "needle\n",
	f"apps/{APP}/tools/.venv/pyvenv.cfg": "home = /usr/bin\n",
	f"apps/{APP}/tools/.venv/lib/site.py": "needle\n",
	"env/pyvenv.cfg": "home = /usr/bin\n",
	"env/lib/site.py": "needle\n",
	"sites/common_site_config.json": '{"needle": 1}\n',
	"sites/site1/site_config.json": '{"db_password": "needle"}\n',
	"sites/site1/private/files/salaries.csv": "needle\n",
	"sites/site1/public/files/logo.txt": "needle\n",
	"config/redis_cache.acl": "user default on >needle\n",
	"logs/web.log": "".join(f"line {number}\n" for number in range(1, 1001)),
}


class FileTestCase(IntegrationTestCase):
	"""Points the file tools at a throwaway bench, so nothing real is read or exported."""

	def setUp(self):
		self.bench = os.path.realpath(tempfile.mkdtemp())
		self.addCleanup(shutil.rmtree, self.bench)

		for path, content in FILES.items():
			full = os.path.join(self.bench, path)
			os.makedirs(os.path.dirname(full), exist_ok=True)
			with open(full, "w") as handle:
				handle.write(content)

		app = os.path.join(self.bench, "apps", APP)
		os.symlink(
			os.path.join(self.bench, "sites/site1/site_config.json"), os.path.join(app, "innocent.txt")
		)
		os.symlink("/etc", os.path.join(app, "etc_link"))

		bench_root = patch.dict(os.environ, {"FRAPPE_BENCH_ROOT": self.bench})
		bench_root.start()
		self.addCleanup(bench_root.stop)

	def call(self, tool, **params):
		with mcp_settings(allow_server_files=1):
			return api.execute(tool=tool, params=params)


class TestFileGate(FileTestCase):
	def test_capability_is_off_by_default(self):
		with mcp_settings():
			result = api.execute(tool="list_server_files", params={})

		self.assertFalse(result["ok"])
		self.assertIn("not ticked", result["error"]["message"])

	def test_system_managers_only(self):
		with (
			mcp_settings(allow_server_files=1, allowed_roles=[{"role": "Accounts User"}]),
			patch("frappe.get_roles", return_value=["Accounts User"]),
		):
			result = api.execute(tool="list_server_files", params={})

		self.assertFalse(result["ok"])
		self.assertIn("System Managers only", result["error"]["message"])

	def test_credentials_and_escapes_are_refused(self):
		for path in (
			"sites/common_site_config.json",
			"sites/site1/site_config.json",
			"sites/site1/private",
			"sites/site1/private/files/salaries.csv",
			"config/redis_cache.acl",
			f"apps/{APP}/.env",
			f"apps/{APP}/.git/config",
			f"apps/{APP}/{APP}/tests/server.key",
			f"apps/{APP}/innocent.txt",
			f"apps/{APP}/etc_link/hosts",
			"../outside.txt",
			"/etc/hosts",
		):
			with self.subTest(path=path):
				result = self.call("read_server_file", path=path)
				self.assertFalse(result["ok"], path)
				self.assertEqual(result["error"]["type"], "Blocked", result)


class TestReading(FileTestCase):
	def test_folder_listing_marks_blocked_entries(self):
		result = self.call("list_server_files", path="sites/site1")
		self.assertTrue(result["ok"], result)

		entries = {entry["name"]: entry for entry in result["result"]["entries"]}
		self.assertTrue(entries["site_config.json"]["blocked"])
		self.assertTrue(entries["private"]["blocked"])
		self.assertEqual(entries["public"]["type"], "folder")
		self.assertNotIn("blocked", entries["public"])

	def test_pattern_finds_files_at_any_depth(self):
		result = self.call("list_server_files", pattern="*.js")

		paths = [entry["path"] for entry in result["result"]["entries"]]
		self.assertEqual(paths, [f"apps/{APP}/{APP}/public/js/app.js"])

	def test_read_numbers_the_lines(self):
		result = self.call("read_server_file", path=f"apps/{APP}/{APP}/hooks.py")
		self.assertTrue(result["ok"], result)

		text = result["result"]
		self.assertTrue(text.startswith(f"apps/{APP}/{APP}/hooks.py · lines 1-2 of 2"), text)
		self.assertIn("     2\tneedle = 1", text)

	def test_read_pages_and_tails(self):
		page = self.call("read_server_file", path="logs/web.log", start_line=10, line_count=5)["result"]
		self.assertIn("lines 10-14 of 1000", page)
		self.assertIn("continue with start_line=15", page)

		tail = self.call("read_server_file", path="logs/web.log", start_line=-3)["result"]
		self.assertIn("lines 998-1000 of 1000", tail)
		self.assertTrue(tail.endswith("  1000\tline 1000"), tail)

	def test_absolute_path_inside_the_bench(self):
		result = self.call("read_server_file", path=os.path.join(self.bench, "apps", APP, "README.md"))
		self.assertTrue(result["ok"], result)

	def test_http_sends_file_text_as_text(self):
		arguments = {"path": f"apps/{APP}/{APP}/hooks.py"}
		with mcp_settings(allow_server_files=1):
			_status, body = http.handle(
				rpc("tools/call", {"name": "read_server_file", "arguments": arguments}), "t"
			)

		text = body["result"]["content"][0]["text"]
		# A JSON-encoded string would have escaped the quotes and the newlines.
		self.assertIn("\n     1\tapp_name = 'demo'\n", text)

	def test_search_skips_credentials_and_dependencies(self):
		result = self.call("search_server_files", query="needle", path="")
		self.assertTrue(result["ok"], result)

		paths = sorted({match["path"] for match in result["result"]["matches"]})
		self.assertEqual(
			paths,
			[
				f"apps/{APP}/.env.example",
				f"apps/{APP}/{APP}/hooks.py",
				f"apps/{APP}/{APP}/public/js/app.js",
				"sites/site1/public/files/logo.txt",
			],
		)

	def test_virtualenv_is_skipped_by_walks_but_readable(self):
		self.assertTrue(self.call("read_server_file", path="env/lib/site.py")["ok"])

		result = self.call("search_server_files", query="needle", path="env")
		self.assertEqual(result["result"]["count"], 1)

	def test_search_by_regex_and_pattern(self):
		result = self.call("search_server_files", query=r"needle = \d", regex=True, pattern="*.py")

		self.assertEqual(
			result["result"]["matches"],
			[{"path": f"apps/{APP}/{APP}/hooks.py", "line": 2, "text": "needle = 1"}],
		)


class TestExport(FileTestCase):
	def export(self):
		result = self.call("export_apps", apps=[APP])
		self.assertTrue(result["ok"], result)

		export = result["result"]
		self.addCleanup(os.remove, os.path.join(files._export_folder(), export["file"]))
		return export

	def test_zip_leaves_out_secrets_and_dependencies(self):
		export = self.export()

		with zipfile.ZipFile(os.path.join(files._export_folder(), export["file"])) as archive:
			names = sorted(archive.namelist())

		exported = sorted(
			[
				f"{APP}/.env.example",
				f"{APP}/README.md",
				f"{APP}/{APP}/hooks.py",
				f"{APP}/{APP}/public/js/app.js",
			]
		)
		self.assertEqual(names, exported)
		self.assertEqual(
			sorted(export["files_left_out"]),
			sorted([f"apps/{APP}/.env", f"apps/{APP}/innocent.txt", f"apps/{APP}/{APP}/tests/server.key"]),
		)

		size = sum(len(FILES[f"apps/{name}"]) for name in exported)
		self.assertEqual(export["apps"], [{"app": APP, "files": 4, "size": size}])

	def test_unknown_app_is_refused(self):
		result = self.call("export_apps", apps=["../../etc"])

		self.assertFalse(result["ok"])
		self.assertIn("Not an app on this bench", result["error"]["message"])

	def test_frappe_apps_are_not_custom(self):
		self.assertTrue(files._is_frappe_app("frappe"))
		self.assertFalse(files._is_frappe_app(APP))

	def test_download_link(self):
		export = self.export()
		link = {key: value[0] for key, value in parse_qs(urlparse(export["download_url"]).query).items()}

		with mcp_settings(allow_server_files=1), request(method="GET"):
			response = files.download_export(**link)
			response.close()

		self.assertEqual(response.status_code, 200)
		self.assertIn(export["file"], response.headers["Content-Disposition"])

		expired = patch.object(files.time, "time", return_value=int(link["expires"]) + 1)
		for label, settings, arguments, clock in (
			("tampered signature", {"allow_server_files": 1}, {**link, "signature": "0" * 64}, None),
			(
				"longer expiry",
				{"allow_server_files": 1},
				{**link, "expires": str(int(link["expires"]) + 60)},
				None,
			),
			("another file", {"allow_server_files": 1}, {**link, "export": "other.zip"}, None),
			("capability switched off", {"allow_server_files": 0}, link, None),
			("expired", {"allow_server_files": 1}, link, expired),
		):
			with (
				self.subTest(label),
				mcp_settings(**settings),
				clock or contextlib.nullcontext(),
				self.assertRaises(frappe.PermissionError),
			):
				files.download_export(**arguments)

	def test_old_exports_are_cleared(self):
		folder = files._export_folder()
		os.makedirs(folder, exist_ok=True)

		old, fresh = os.path.join(folder, "old-test.zip"), os.path.join(folder, "fresh-test.zip")
		for path in (old, fresh):
			open(path, "w").close()
		self.addCleanup(lambda: os.path.exists(fresh) and os.remove(fresh))

		an_hour_and_more_ago = time.time() - files.EXPORT_LINK_MINUTES * 60 - 5
		os.utime(old, (an_hour_and_more_ago, an_hour_and_more_ago))
		files.clear_old_exports()

		self.assertFalse(os.path.exists(old))
		self.assertTrue(os.path.exists(fresh))
