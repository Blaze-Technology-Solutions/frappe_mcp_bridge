# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

import contextlib
import os
import shutil
import tempfile
import time
from collections import namedtuple
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import frappe

from frappe_mcp_bridge.api import mcp as api
from frappe_mcp_bridge.mcp import http, links
from frappe_mcp_bridge.mcp.handlers import backups
from frappe_mcp_bridge.tests.test_http import rpc
from frappe_mcp_bridge.tests.utils import IntegrationTestCase, mcp_settings, request

STAMP = "20261008_101010"
NEWER = "20261008_160000"

EXTENSIONS = {
	"database": "sql.gz",
	"files": "tar",
	"private-files": "tar",
	"site_config_backup": "json",
}

Usage = namedtuple("Usage", "total used free")
GIB = 1 << 30
QUEUED = SimpleNamespace(id="queued job")


class BackupTestCase(IntegrationTestCase):
	"""Points the backup tools at a throwaway folder, so no real backup is listed or served."""

	def setUp(self):
		self.folder = tempfile.mkdtemp()
		self.addCleanup(shutil.rmtree, self.folder)

		folder = patch("frappe.utils.backups.get_backup_path", return_value=self.folder)
		folder.start()
		self.addCleanup(folder.stop)

	def make(self, stamp=STAMP, kinds=tuple(EXTENSIONS), site=None, partial=False, encrypted=False, age=0):
		"""Write one backup's files and return their names. age is how many seconds old."""
		site = site or backups.site_slug()
		names = []

		for kind in kinds:
			name = (
				f"{stamp}-{site}{'-partial' if partial and kind == 'database' else ''}"
				f"-{kind}{'-enc' if encrypted else ''}.{EXTENSIONS[kind]}"
			)
			path = os.path.join(self.folder, name)
			with open(path, "w") as handle:
				handle.write(f"contents of {name}")
			if age:
				os.utime(path, (time.time() - age, time.time() - age))
			names.append(name)

		return names

	def call(self, tool, settings=None, **params):
		with mcp_settings(**{"allow_backups": 1, **(settings or {})}):
			return api.execute(tool=tool, params=params)

	@staticmethod
	def job(status, **extra):
		return patch.object(backups, "job_state", return_value={"status": status, **extra})


class TestGate(BackupTestCase):
	def test_capability_is_off_by_default(self):
		with mcp_settings():
			result = api.execute(tool="list_backups", params={})

		self.assertFalse(result["ok"])
		self.assertIn("not ticked", result["error"]["message"])

	def test_system_managers_only(self):
		with (
			mcp_settings(allow_backups=1, allowed_roles=[{"role": "Accounts User"}]),
			patch("frappe.get_roles", return_value=["Accounts User"]),
		):
			result = api.execute(tool="list_backups", params={})

		self.assertFalse(result["ok"])
		self.assertIn("System Managers only", result["error"]["message"])

	def test_works_in_read_only_mode(self):
		# Taking a backup first, then opening a write window, is the point of having one.
		with self.job("finished"):
			result = self.call("list_backups", settings={"read_only_mode": 1})

		self.assertTrue(result["ok"], result)

	def test_tool_hints(self):
		with mcp_settings(allow_backups=1):
			_status, body = http.handle(rpc("tools/list"), "test")

		tools = {tool["name"]: tool["annotations"] for tool in body["result"]["tools"]}
		self.assertFalse(tools["create_backup"]["readOnlyHint"])
		self.assertFalse(tools["export_apps"]["readOnlyHint"])
		self.assertTrue(tools["list_backups"]["readOnlyHint"])
		self.assertTrue(tools["get_backup_links"]["readOnlyHint"])


class TestFileNames(BackupTestCase):
	def test_parse(self):
		slug = backups.site_slug()

		self.assertEqual(
			backups.parse(f"{STAMP}-{slug}-database.sql.gz"),
			{"stamp": STAMP, "kind": "database", "partial": False, "encrypted": False},
		)
		self.assertEqual(backups.parse(f"{STAMP}-{slug}-partial-database-enc.sql.gz")["partial"], True)
		self.assertEqual(backups.parse(f"{STAMP}-{slug}-partial-database-enc.sql.gz")["encrypted"], True)
		self.assertEqual(backups.parse(f"{STAMP}-{slug}-files.tgz")["kind"], "public_files")
		self.assertEqual(backups.parse(f"{STAMP}-{slug}-private-files.tar")["kind"], "private_files")
		self.assertEqual(backups.parse(f"{STAMP}-{slug}-site_config_backup.json")["kind"], "config")

	def test_parse_refuses_what_is_not_this_sites_backup(self):
		slug = backups.site_slug()

		for name in (
			f"{STAMP}-other_site-database.sql.gz",
			f"{STAMP}-{slug}-database.tar",
			f"{STAMP}-{slug}-files.sql.gz",
			f"{STAMP}-{slug}-database.sql.gz.bak",
			f"../{STAMP}-{slug}-database.sql.gz",
			f"{STAMP}-{slug}-notes.txt",
			"site_config.json",
			"",
		):
			with self.subTest(name=name):
				self.assertIsNone(backups.parse(name))


class TestListing(BackupTestCase):
	def test_newest_first_without_the_config_copy(self):
		self.make(STAMP, age=3600)
		self.make(NEWER)

		with self.job("finished"):
			result = self.call("list_backups")["result"]

		self.assertEqual([backup["backup"] for backup in result["backups"]], [NEWER, STAMP])
		self.assertEqual(result["count"], 2)

		newest = result["backups"][0]
		self.assertEqual(newest["created"], "2026-10-08 16:00:00")
		self.assertEqual(
			[file["kind"] for file in newest["files"]], ["database", "public_files", "private_files"]
		)
		self.assertEqual(newest["size"], sum(file["size"] for file in newest["files"]))
		self.assertNotIn("site_config_backup", str(result))
		self.assertGreater(result["disk"]["free"], 0)

	def test_other_sites_stray_files_and_dumpless_sets_are_ignored(self):
		self.make(STAMP, kinds=("database",))
		self.make(NEWER, site="another_site")
		self.make("20261009_090000", kinds=("files", "private-files"))
		open(os.path.join(self.folder, "notes.txt"), "w").close()

		with self.job("finished"):
			result = self.call("list_backups")["result"]

		self.assertEqual([backup["backup"] for backup in result["backups"]], [STAMP])

	def test_partial_and_encrypted_are_flagged(self):
		self.make(STAMP, kinds=("database",), partial=True, encrypted=True)

		with self.job("finished"):
			backup = self.call("list_backups")["result"]["backups"][0]

		self.assertTrue(backup["partial"])
		self.assertTrue(backup["encrypted"])

	def test_limit(self):
		for stamp in (STAMP, NEWER, "20261009_090000"):
			self.make(stamp, kinds=("database",))

		with self.job("finished"):
			result = self.call("list_backups", limit=2)["result"]

		self.assertEqual(result["count"], 3)
		self.assertEqual(len(result["backups"]), 2)

	def test_backup_being_written_is_marked(self):
		self.make(STAMP, age=3600)
		self.make(NEWER)

		with self.job("started", started_epoch=time.time() - 5, started="now"):
			result = self.call("list_backups")["result"]

		self.assertEqual(
			{backup["backup"]: backup["in_progress"] for backup in result["backups"]},
			{NEWER: True, STAMP: False},
		)
		self.assertEqual(result["job"], {"status": "running", "started": "now"})

	def test_no_folder_yet(self):
		shutil.rmtree(self.folder)
		self.addCleanup(os.makedirs, self.folder)

		with self.job("finished"):
			result = self.call("list_backups")["result"]

		self.assertEqual(result["backups"], [])

	def test_waiting_polls_until_the_job_ends(self):
		states = [{"status": "started"}, {"status": "started"}, {"status": "finished"}]

		with (
			patch.object(backups, "job_state", side_effect=states),
			patch.object(backups.time, "sleep") as sleep,
		):
			result = self.call("list_backups", wait_seconds=30)["result"]

		self.assertEqual(sleep.call_count, 2)
		self.assertEqual(result["job"]["status"], "finished")

	def test_waiting_gives_up_after_the_limit(self):
		clock = {"now": 0.0}

		def sleep(seconds):
			clock["now"] += seconds

		with (
			patch.object(backups, "job_state", return_value={"status": "started"}),
			patch.object(backups.time, "sleep", side_effect=sleep) as slept,
			patch.object(backups.time, "monotonic", side_effect=lambda: clock["now"]),
		):
			result = self.call("list_backups", wait_seconds=500)["result"]

		self.assertEqual(slept.call_count, backups.MAX_WAIT_SECONDS // backups.POLL_SECONDS)
		self.assertEqual(result["job"]["status"], "running")

	def test_unreachable_queue_still_lists(self):
		self.make(STAMP)

		with patch("frappe.utils.background_jobs.get_job", side_effect=ConnectionError):
			result = self.call("list_backups")["result"]

		self.assertEqual(len(result["backups"]), 1)
		self.assertEqual(result["job"], {"status": "unknown"})


class TestCreate(BackupTestCase):
	def create(self, enqueued=QUEUED, **params):
		with (
			patch("frappe.enqueue", return_value=enqueued) as enqueue,
			patch.object(backups, "preflight", return_value=None),
		):
			result = self.call("create_backup", **params)

		return result, enqueue

	def test_queues_a_background_job_and_reports_the_backup_it_wrote(self):
		self.make(STAMP, age=3600)
		self.make(NEWER)

		with self.job("finished", started_epoch=time.time() - 10):
			result, enqueue = self.create(with_files=True)

		enqueue.assert_called_once_with(
			backups.RUN_METHOD,
			queue="long",
			timeout=backups.JOB_TIMEOUT,
			job_id=backups.JOB_ID,
			deduplicate=True,
			with_files=True,
		)
		self.assertTrue(result["ok"], result)
		self.assertEqual(result["result"]["status"], "ready")
		self.assertEqual(result["result"]["backup"]["backup"], NEWER)
		self.assertFalse(result["result"]["already_running"])

	def test_database_only_by_default(self):
		with self.job("queued"):
			_result, enqueue = self.create()

		self.assertIs(enqueue.call_args.kwargs["with_files"], False)

	def test_still_running_after_the_wait(self):
		with self.job("started", started_epoch=time.time()):
			result, _enqueue = self.create(wait_seconds=0)

		self.assertEqual(result["result"]["status"], "running")
		self.assertIn("list_backups", result["result"]["message"])

	def test_waiting_for_a_worker(self):
		with self.job("queued"):
			result, _enqueue = self.create(wait_seconds=0)

		self.assertEqual(result["result"]["status"], "queued")
		self.assertIn("no worker", result["result"]["message"])

	def test_second_call_does_not_start_another(self):
		with self.job("started", started_epoch=time.time()):
			result, _enqueue = self.create(enqueued=None, wait_seconds=0)

		self.assertTrue(result["result"]["already_running"])
		self.assertEqual(result["result"]["status"], "running")
		self.assertIn("already in progress", result["result"]["message"])

	def test_failure_says_why(self):
		with self.job("failed", error="mysqldump: Got error: 1045", ended="then"):
			result, _enqueue = self.create(wait_seconds=0)

		self.assertEqual(result["result"]["status"], "failed")
		self.assertEqual(result["result"]["error"], "mysqldump: Got error: 1045")

	def test_refused_before_queueing_when_there_is_no_room(self):
		with (
			patch("frappe.enqueue") as enqueue,
			patch.object(backups.shutil, "disk_usage", return_value=Usage(100 * GIB, 99 * GIB, GIB // 2)),
		):
			result = self.call("create_backup")

		self.assertFalse(result["ok"])
		self.assertIn("Not enough free disk space", result["error"]["message"])
		enqueue.assert_not_called()


class TestPreflight(BackupTestCase):
	def test_database_estimate_and_margin(self):
		# A 10 GB database is estimated at 5 GB of dump, plus the 1 GB margin.
		with (
			patch.object(backups.frappe.db, "get_database_size", return_value=10 * 1024),
			patch.object(backups.shutil, "disk_usage", return_value=Usage(100 * GIB, 0, 7 * GIB)),
		):
			self.assertIsNone(backups.preflight(False))

		with (
			patch.object(backups.frappe.db, "get_database_size", return_value=10 * 1024),
			patch.object(backups.shutil, "disk_usage", return_value=Usage(100 * GIB, 0, 5 * GIB)),
		):
			self.assertIn("5.0 GB free, about 6.0 GB needed", backups.preflight(False))

	def test_files_are_counted_only_by_the_job(self):
		with (
			patch.object(backups.frappe.db, "get_database_size", return_value=0),
			patch.object(backups, "_tree_size", return_value=20 * GIB),
			patch.object(backups.shutil, "disk_usage", return_value=Usage(100 * GIB, 0, 10 * GIB)),
		):
			self.assertIsNone(backups.preflight(True))
			self.assertIsNone(backups.preflight(False, count_files=True))
			self.assertIn("Not enough free disk space", backups.preflight(True, count_files=True))

	def test_unknown_database_size_still_keeps_the_margin(self):
		with (
			patch.object(backups.frappe.db, "get_database_size", side_effect=NotImplementedError),
			patch.object(backups.shutil, "disk_usage", return_value=Usage(100 * GIB, 0, GIB // 2)),
		):
			self.assertIn("Not enough free disk space", backups.preflight(False))

	def test_encrypted_backups_need_gpg(self):
		with (
			patch("frappe.get_system_settings", return_value=1),
			patch.object(backups.shutil, "which", return_value=None),
		):
			self.assertIn("gpg", backups.preflight(False))

	def test_tree_size(self):
		os.makedirs(os.path.join(self.folder, "a", "b"))
		for path, size in (("x", 3), ("a/y", 5), ("a/b/z", 7)):
			with open(os.path.join(self.folder, path), "wb") as handle:
				handle.write(b"." * size)

		self.assertEqual(backups._tree_size(self.folder), 15)
		self.assertEqual(backups._tree_size(os.path.join(self.folder, "missing")), 0)


class TestJob(BackupTestCase):
	def test_runs_frappes_own_backup_and_forces_it(self):
		fake = SimpleNamespace(backup_path_db=os.path.join(self.folder, f"{STAMP}-x-database.sql.gz"))

		for with_files in (False, True):
			with (
				self.subTest(with_files=with_files),
				patch.object(backups, "preflight", return_value=None),
				patch("frappe.utils.backups.scheduled_backup", return_value=fake) as backup,
			):
				result = backups.run_backup(with_files=with_files)

			# force, or Frappe hands back a backup under six hours old instead of making one.
			backup.assert_called_once_with(ignore_files=not with_files, force=True)
			self.assertEqual(result, {"database": f"{STAMP}-x-database.sql.gz", "with_files": with_files})

	def test_refuses_to_start_without_room(self):
		with (
			patch.object(backups, "preflight", return_value="Not enough free disk space"),
			patch("frappe.utils.backups.scheduled_backup") as backup,
			self.assertRaises(frappe.ValidationError),
		):
			backups.run_backup(with_files=True)

		backup.assert_not_called()

	def test_state_of_a_failed_job(self):
		job = SimpleNamespace(
			started_at=None,
			ended_at=None,
			exc_info="Traceback (most recent call last):\n  File x\nfrappe.exceptions.ValidationError: No room left",
			get_status=lambda refresh=False: "failed",
		)

		with patch("frappe.utils.background_jobs.get_job", return_value=job):
			state = backups.job_state()

		self.assertEqual(state["status"], "failed")
		self.assertEqual(state["error"], "No room left")

	def test_no_job_on_record(self):
		with patch("frappe.utils.background_jobs.get_job", return_value=None):
			self.assertIsNone(backups.job_state())


class TestLinks(BackupTestCase):
	def links(self, **params):
		return self.call("get_backup_links", **params)

	def test_database_and_files_but_never_the_config_copy(self):
		self.make(STAMP)

		with self.job("finished"):
			result = self.links()["result"]

		self.assertEqual(
			[file["kind"] for file in result["files"]], ["database", "public_files", "private_files"]
		)
		self.assertEqual(result["link_expires_in_minutes"], 30)
		self.assertNotIn("site_config_backup", str(result))

		for file in result["files"]:
			query = {key: value[0] for key, value in parse_qs(urlparse(file["download_url"]).query).items()}
			self.assertEqual(query["file"], file["file"])
			self.assertTrue(links.is_valid("backup", file["file"], query["expires"], query["signature"]))
			self.assertIn(backups.DOWNLOAD_METHOD, file["download_url"])

	def test_defaults_to_the_newest_finished_backup(self):
		self.make(STAMP, age=3600)
		self.make(NEWER)

		with self.job("started", started_epoch=time.time() - 5):
			result = self.links()["result"]
			refused = self.links(backup=NEWER)

		self.assertEqual(result["backup"], STAMP)
		self.assertFalse(refused["ok"])
		self.assertIn("still being written", refused["error"]["message"])

	def test_named_backup_and_kinds(self):
		self.make(STAMP)
		self.make(NEWER)

		with self.job("finished"):
			result = self.links(backup=STAMP, kinds=["database"])["result"]

		self.assertEqual(result["backup"], STAMP)
		self.assertEqual([file["kind"] for file in result["files"]], ["database"])

	def test_bad_requests(self):
		self.make(STAMP, kinds=("database",))

		cases = {
			"unknown backup": (self.links(backup="20200101_000000"), "No backup named"),
			"path in name": (self.links(backup="../../etc/passwd"), "No backup named"),
			"unknown kind": (self.links(kinds=["config"]), "kinds can be"),
			"kind not in backup": (self.links(kinds=["private_files"]), "has no private_files"),
		}

		for label, (result, message) in cases.items():
			with self.subTest(label):
				self.assertFalse(result["ok"])
				self.assertIn(message, result["error"]["message"])

	def test_nothing_to_link_before_a_backup_exists(self):
		result = self.links()

		self.assertFalse(result["ok"])
		self.assertIn("no finished backup", result["error"]["message"])

	def test_restore_steps_name_the_files(self):
		self.make(STAMP)

		with self.job("finished"):
			steps = "\n".join(self.links()["result"]["restore_locally"])

		self.assertIn(
			f"bench --site <local-site> restore {STAMP}-{backups.site_slug()}-database.sql.gz", steps
		)
		self.assertIn(f"--with-public-files {STAMP}-{backups.site_slug()}-files.tar", steps)
		self.assertIn(f"--with-private-files {STAMP}-{backups.site_slug()}-private-files.tar", steps)
		self.assertIn("confirm with the user", steps)
		self.assertIn("mute_emails 1", steps)
		self.assertIn("disable-scheduler", steps)
		self.assertIn("encryption_key", steps)
		self.assertNotIn("partial-restore", steps)
		self.assertNotIn("--encryption-key", steps)

	def test_restore_steps_for_database_only(self):
		self.make(STAMP, kinds=("database",))

		with self.job("finished"):
			steps = "\n".join(self.links()["result"]["restore_locally"])

		self.assertNotIn("--with-public-files", steps)

	def test_restore_steps_for_partial_and_encrypted(self):
		self.make(STAMP, kinds=("database",), partial=True, encrypted=True)

		with self.job("finished"):
			steps = "\n".join(self.links()["result"]["restore_locally"])

		self.assertIn("partial-restore", steps)
		self.assertIn("--encryption-key", steps)


class TestDownload(BackupTestCase):
	def link(self, name, kind="backup", minutes=30):
		url = links.url(backups.DOWNLOAD_METHOD, kind, name, minutes)
		return {key: value[0] for key, value in parse_qs(urlparse(url).query).items()}

	def fetch(self, link, settings=None, **request_args):
		with mcp_settings(**{"allow_backups": 1, **(settings or {})}), request(method="GET", **request_args):
			response = backups.download_backup(**link)
			response.direct_passthrough = False
			return response

	def test_serves_the_file(self):
		name = self.make(STAMP)[0]

		response = self.fetch(self.link(name))

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.get_data(as_text=True), f"contents of {name}")
		self.assertIn(f"filename*=UTF-8''{name}", response.headers["Content-Disposition"])
		self.assertTrue(response.headers["Content-Disposition"].startswith("attachment"))
		self.assertEqual(response.headers["Cache-Control"], "no-store")

	def test_a_backup_inside_the_private_folder_goes_through_frappes_file_sender(self):
		folder = frappe.get_site_path("private", f"mcp_test_{frappe.generate_hash(length=6)}")
		os.makedirs(folder)
		self.addCleanup(shutil.rmtree, folder)

		with patch("frappe.utils.backups.get_backup_path", return_value=folder):
			name = backups.site_slug() and f"{STAMP}-{backups.site_slug()}-database.sql.gz"
			with open(os.path.join(folder, name), "w") as handle:
				handle.write("dump")

			with patch.object(links, "send_private_file", wraps=links.send_private_file) as send:
				response = self.fetch(self.link(name))

		send.assert_called_once_with(os.path.join(os.path.basename(folder), name))
		self.assertEqual(response.status_code, 200)

	def test_rejected_links(self):
		name, other = self.make(STAMP)[0], self.make(NEWER)[0]
		config = f"{STAMP}-{backups.site_slug()}-site_config_backup.json"
		good = self.link(name)

		cases = {
			"tampered signature": ({**good, "signature": "0" * 64}, None),
			"no signature": ({**good, "signature": None}, None),
			"longer expiry": ({**good, "expires": str(int(good["expires"]) + 60)}, None),
			"expiry that is not a number": ({**good, "expires": "soon"}, None),
			"another file": ({**good, "file": other}, None),
			"another site's file": ({**self.link("x"), "file": f"{STAMP}-other_site-database.sql.gz"}, None),
			"the config copy, even properly signed": (self.link(config), None),
			"a file that is not a backup, even properly signed": (self.link("../site_config.json"), None),
			"a zip export link": (self.link(name, kind="export"), None),
			"expired": (good, patch.object(links.time, "time", return_value=int(good["expires"]) + 1)),
		}

		for label, (link, clock) in cases.items():
			with (
				self.subTest(label),
				clock or contextlib.nullcontext(),
				self.assertRaises(frappe.PermissionError),
			):
				self.fetch(link)

	def test_refused_when_the_capability_or_mcp_is_off(self):
		name = self.make(STAMP)[0]

		for label, settings in (("capability", {"allow_backups": 0}), ("mcp", {"enabled": 0})):
			with self.subTest(label), self.assertRaises(frappe.PermissionError):
				self.fetch(self.link(name), settings=settings)

	def test_removed_file(self):
		name = self.make(STAMP)[0]
		link = self.link(name)
		os.remove(os.path.join(self.folder, name))

		with self.assertRaises(frappe.DoesNotExistError):
			self.fetch(link)

	def test_ip_list_applies(self):
		name = self.make(STAMP)[0]
		settings = {"allowed_ips": "10.0.0.0/8"}

		self.assertEqual(self.fetch(self.link(name), settings=settings, ip="10.1.2.3").status_code, 200)

		with self.assertRaises(frappe.PermissionError):
			self.fetch(self.link(name), settings=settings, ip="203.0.113.9")

	def test_downloads_are_logged(self):
		name = self.make(STAMP)[0]

		def count(status):
			return frappe.db.count("MCP Bridge Log", {"tool": "download_backup", "status": status})

		before = count("Success"), count("Blocked")

		self.fetch(
			self.link(name),
			settings={"log_requests": 1},
			ip="10.1.2.3",
			headers={"User-Agent": "curl/8.7.1"},
		)

		self.assertEqual(count("Success"), before[0] + 1)
		entry = frappe.get_all(
			"MCP Bridge Log",
			filters={"tool": "download_backup", "status": "Success"},
			fields=["capability", "ip_address", "client", "request_payload"],
			order_by="creation desc",
			limit=1,
		)[0]
		self.assertEqual(
			(entry.capability, entry.ip_address, entry.client), ("backup", "10.1.2.3", "curl/8.7.1")
		)
		self.assertIn(name, entry.request_payload)

		# A genuine link used from an address that is not allowed is worth a row.
		with self.assertRaises(frappe.PermissionError):
			self.fetch(
				self.link(name), settings={"log_requests": 1, "allowed_ips": "192.0.2.1"}, ip="203.0.113.9"
			)
		self.assertEqual(count("Blocked"), before[1] + 1)

		# Junk is not: anyone could fill the table with it.
		with self.assertRaises(frappe.PermissionError):
			self.fetch({**self.link(name), "signature": "bad"}, settings={"log_requests": 1})
		self.assertEqual(count("Blocked"), before[1] + 1)
