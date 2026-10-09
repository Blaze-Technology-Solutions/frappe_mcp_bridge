# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""Taking a backup on demand, and handing it out for download.

The backup is Frappe's own, the code `bench backup` and the scheduled backup run, so the
files land in the site's backups folder under the usual names and `bench restore` takes
them as they are. It runs on the long queue, because a real database takes longer than a
web request may. This module adds the gate, a free-space check, the listing and signed
download links.

Like every Frappe backup, starting one first removes the site's backup files older than
keep_backups_for_hours (23 by default).

Every backup also leaves a copy of site_config.json beside it: database password,
encryption key and whatever else the file holds. That copy is never listed and never
given a link.
"""

import calendar
import datetime
import os
import re
import shutil
import time

import frappe
from frappe import _

from frappe_mcp_bridge.mcp import links
from frappe_mcp_bridge.mcp.registry import tool

JOB_ID = "mcp_bridge_backup"
RUN_METHOD = "frappe_mcp_bridge.mcp.handlers.backups.run_backup"
DOWNLOAD_METHOD = "frappe_mcp_bridge.mcp.handlers.backups.download_backup"
QUEUE = "long"

# A dump of a very large site can take hours. This only stops one that has hung.
JOB_TIMEOUT = 4 * 60 * 60

LINK_MINUTES = 30

# A waiting call stays well inside the web server's request timeout.
MAX_WAIT_SECONDS = 30
POLL_SECONDS = 1

# A gzipped dump comes to a fraction of what the tables occupy, and half is a safe ceiling.
DUMP_FRACTION = 0.5

# Left free once the backup is written, so one that only just fits does not leave the site
# with nowhere to write.
HEADROOM_BYTES = 1 << 30

# RQ statuses of a job that has not finished.
ACTIVE = ("queued", "started")

KINDS = ("database", "public_files", "private_files")

# Frappe's names: <stamp>-<site>[-partial]-<what>[-enc].<extension>
FILE_NAME = re.compile(
	r"(?P<stamp>\d{8}_\d{6})-(?P<site>[\w-]+?)(?P<partial>-partial)?"
	r"-(?P<what>database|private-files|files|site_config_backup)(?P<enc>-enc)?"
	r"\.(?P<ext>sql\.gz|tar|tgz|json)"
)
WHAT = {
	"database": ("database", {"sql.gz"}),
	"files": ("public_files", {"tar", "tgz"}),
	"private-files": ("private_files", {"tar", "tgz"}),
	"site_config_backup": ("config", {"json"}),
}

STATUS_NAMES = {"started": "running"}

MESSAGES = {
	"ready": "The backup is ready. get_backup_links gives download links for it.",
	"running": "The backup is still running. Call list_backups with wait_seconds=30 until it is ready.",
	"queued": "The backup is waiting for a background worker. If it stays queued, no worker is running "
	"on this server.",
	"finished": "The job finished, but its backup is no longer on disk. list_backups shows what is.",
	"failed": "The backup failed. error says why, and get_error_logs has the full traceback.",
	"unknown": "Could not read the job's state. list_backups shows whether a new backup has appeared.",
}


@tool(
	"create_backup",
	"backup",
	summary="Start a backup of this site, the database and optionally the files, on the server.",
	read_only=False,
)
def create_backup(with_files: bool = False, wait_seconds: int = 20) -> dict:
	problem = preflight(bool(with_files))
	if problem:
		frappe.throw(problem)

	job = frappe.enqueue(
		RUN_METHOD,
		queue=QUEUE,
		timeout=JOB_TIMEOUT,
		job_id=JOB_ID,
		deduplicate=True,
		with_files=bool(with_files),
	)
	state = _wait(wait_seconds)

	backup = _made_by(state) if state and state["status"] == "finished" else None
	status = "ready" if backup else _job_status(state)

	result = {"status": status, "already_running": job is None, "message": MESSAGES.get(status, "")}
	if job is None:
		result["message"] = (
			"A backup was already in progress, so no second one was started. " + result["message"]
		)
	if backup:
		result["backup"] = _present(backup, state)
	if state and state.get("error"):
		result["error"] = state["error"]

	return result


@tool(
	"list_backups",
	"backup",
	summary="Backups on this site's server, whoever made them, and what a running backup is doing.",
)
def list_backups(limit: int = 10, wait_seconds: int = 0) -> dict:
	state = _wait(wait_seconds)
	backups = backup_sets()
	disk = shutil.disk_usage(_existing(backups_folder()))

	return {
		"site": frappe.local.site,
		"count": len(backups),
		"backups": [_present(backup, state) for backup in backups[: max(1, min(int(limit or 10), 100))]],
		"job": _present_job(state),
		"disk": {"free": disk.free, "total": disk.total},
	}


@tool(
	"get_backup_links",
	"backup",
	summary="Signed download links for one backup, good for 30 minutes, and how to restore it.",
)
def get_backup_links(backup: str | None = None, kinds: list[str] | None = None) -> dict:
	state = job_state()
	sets = backup_sets()

	if backup:
		chosen = next((candidate for candidate in sets if candidate["backup"] == backup), None)
		if not chosen:
			frappe.throw(_("No backup named {0}. list_backups shows what exists.").format(backup))
	else:
		chosen = next((candidate for candidate in sets if not _in_progress(candidate, state)), None)
		if not chosen:
			frappe.throw(_("There is no finished backup on this site. Run create_backup first."))

	if _in_progress(chosen, state):
		frappe.throw(
			_("Backup {0} is still being written. Wait for it with list_backups.").format(chosen["backup"])
		)

	wanted = list(dict.fromkeys(kinds or []))
	present = [file["kind"] for file in chosen["files"]]
	if unknown := [kind for kind in wanted if kind not in KINDS]:
		frappe.throw(_("kinds can be {0}, not {1}.").format(", ".join(KINDS), ", ".join(unknown)))
	if missing := [kind for kind in wanted if kind not in present]:
		frappe.throw(
			_("Backup {0} has no {1}. It has: {2}.").format(
				chosen["backup"], ", ".join(missing), ", ".join(present)
			)
		)

	files = [
		{**file, "download_url": links.url(DOWNLOAD_METHOD, "backup", file["file"], LINK_MINUTES)}
		for file in chosen["files"]
		if not wanted or file["kind"] in wanted
	]

	return {
		"backup": chosen["backup"],
		"created": chosen["created"],
		"partial": chosen["partial"],
		"encrypted": chosen["encrypted"],
		"files": files,
		"link_expires_in_minutes": LINK_MINUTES,
		"restore_locally": _restore_steps(chosen, files),
	}


@frappe.whitelist(allow_guest=True, methods=["GET"])
def download_backup(file: str | None = None, expires: str | None = None, signature: str | None = None):
	"""Serve one file of a backup made on this site. The signed link is the credential, so
	curl or a browser can fetch it without signing in."""
	info = parse(file or "")
	if not info or info["kind"] == "config":
		raise links.invalid()

	return links.download(
		tool="download_backup",
		capability="backup",
		kind="backup",
		name=file,
		expires=expires,
		signature=signature,
		path=os.path.join(backups_folder(), file),
	)


def run_backup(with_files: bool = False) -> dict:
	"""The background job. Frappe's own backup, forced so that a recent one is not handed
	back in place of a new one."""
	from frappe.utils.backups import scheduled_backup

	problem = preflight(with_files, count_files=True)
	if problem:
		frappe.throw(problem)

	backup = scheduled_backup(ignore_files=not with_files, force=True)

	return {"database": os.path.basename(backup.backup_path_db), "with_files": with_files}


def preflight(with_files: bool, count_files: bool = False) -> str | None:
	"""Why a backup should not start, or None. Counting the files means walking them, which
	is left to the job so the request stays quick."""
	if frappe.get_system_settings("encrypt_backup") and not shutil.which("gpg"):
		return _("This site encrypts its backups, which needs gpg, and gpg was not found on the server.")

	free = shutil.disk_usage(_existing(backups_folder())).free
	needed = _estimated_size(with_files and count_files) + HEADROOM_BYTES

	if free < needed:
		return _(
			"Not enough free disk space for a backup: {0} free, about {1} needed including a margin. "
			"Free some space or take the backup without files."
		).format(_human(free), _human(needed))


def backups_folder() -> str:
	from frappe.utils.backups import get_backup_path

	return get_backup_path()


def site_slug() -> str:
	return frappe.local.site.replace(".", "_")


def parse(name: str) -> dict | None:
	"""What a file in the backups folder is, or None if it is not one of this site's backup files."""
	match = FILE_NAME.fullmatch(name)
	if not match or match["site"] != site_slug():
		return None

	kind, extensions = WHAT[match["what"]]
	if match["ext"] not in extensions:
		return None

	return {
		"stamp": match["stamp"],
		"kind": kind,
		"partial": bool(match["partial"]),
		"encrypted": bool(match["enc"]),
	}


def backup_sets() -> list[dict]:
	"""This site's backups, newest first, however they were made. A backup is the files that
	share one timestamp; the config copy is left out."""
	folder = backups_folder()
	if not os.path.isdir(folder):
		return []

	sets = {}
	for entry in os.scandir(folder):
		info = parse(entry.name) if entry.is_file() else None
		if not info or info["kind"] == "config":
			continue

		stat = entry.stat()
		current = sets.setdefault(
			info["stamp"],
			{
				"backup": info["stamp"],
				"created": datetime.datetime.strptime(info["stamp"], "%Y%m%d_%H%M%S").strftime(
					"%Y-%m-%d %H:%M:%S"
				),
				"partial": False,
				"encrypted": False,
				"files": [],
				"modified": 0,
			},
		)
		current["files"].append({"kind": info["kind"], "file": entry.name, "size": stat.st_size})
		current["partial"] = current["partial"] or info["partial"]
		current["encrypted"] = current["encrypted"] or info["encrypted"]
		current["modified"] = max(current["modified"], stat.st_mtime)

	backups = []
	for current in sets.values():
		# Without the dump there is nothing to restore.
		if any(file["kind"] == "database" for file in current["files"]):
			current["files"].sort(key=lambda file: KINDS.index(file["kind"]))
			current["size"] = sum(file["size"] for file in current["files"])
			backups.append(current)

	return sorted(backups, key=lambda current: current["backup"], reverse=True)


def job_state() -> dict | None:
	"""What the backup job is doing, from the queue. None when it has not run lately."""
	from frappe.utils.background_jobs import get_job

	try:
		job = get_job(JOB_ID)
	except Exception:
		# The queue is unreachable. The listing from disk is still worth returning.
		return {"status": "unknown"}

	if not job:
		return None

	status = job.get_status(refresh=False)
	state = {"status": getattr(status, "value", status)}

	if job.started_at:
		state["started"] = _site_time(job.started_at)
		state["started_epoch"] = calendar.timegm(job.started_at.utctimetuple())

	if state["status"] == "failed":
		state["error"] = _last_line(job.exc_info)
		state["ended"] = _site_time(job.ended_at)

	return state


def _wait(seconds) -> dict | None:
	deadline = time.monotonic() + max(0, min(int(seconds or 0), MAX_WAIT_SECONDS))

	state = job_state()
	while state and state["status"] in ACTIVE and time.monotonic() < deadline:
		time.sleep(POLL_SECONDS)
		state = job_state()

	return state


def _job_status(state: dict | None) -> str:
	if not state:
		return "unknown"

	return STATUS_NAMES.get(state["status"], state["status"])


def _present_job(state: dict | None) -> dict | None:
	if not state:
		return None

	return {
		"status": _job_status(state),
		**{key: value for key, value in state.items() if key not in ("status", "started_epoch")},
	}


def _present(backup: dict, state: dict | None) -> dict:
	return {
		"backup": backup["backup"],
		"created": backup["created"],
		"size": backup["size"],
		"partial": backup["partial"],
		"encrypted": backup["encrypted"],
		"in_progress": _in_progress(backup, state),
		"files": backup["files"],
	}


def _in_progress(backup: dict, state: dict | None) -> bool:
	"""A backup the running job is still writing: it began after the job did."""
	if not state or state["status"] != "started" or "started_epoch" not in state:
		return False

	return backup["modified"] >= state["started_epoch"] - 1


def _made_by(state: dict) -> dict | None:
	"""The newest backup the finished job wrote."""
	if "started_epoch" not in state:
		return None

	return next(
		(backup for backup in backup_sets() if backup["modified"] >= state["started_epoch"] - 1), None
	)


def _restore_steps(backup: dict, files: list[dict]) -> list[str]:
	names = {file["kind"]: file["file"] for file in files}
	database = names.get("database")

	steps = [
		"Save the files outside any git repository, for example in the local site's private/backups "
		'folder or ~/Downloads: curl -fL -o <file> "<download_url>" for each one.',
	]

	if not database:
		return steps

	if backup["partial"]:
		steps.append(
			f"This is a partial backup, because the site's backup settings leave tables out, and "
			f"bench restore refuses those. Restore it into an existing local site with: "
			f"bench --site <local-site> partial-restore {database}"
		)
	else:
		command = f"bench --site <local-site> restore {database}"
		if "public_files" in names:
			command += f" --with-public-files {names['public_files']}"
		if "private_files" in names:
			command += f" --with-private-files {names['private_files']}"
		command += " --db-root-password <local MariaDB root password>"

		steps.append(
			f"Restore: {command} . This replaces the local site's database, so confirm with the user "
			"which local site to use. Add --force only if it refuses because the backup comes from a "
			"newer Frappe."
		)

	if backup["encrypted"]:
		steps.append(
			"The backup is encrypted. Add --encryption-key with the backup encryption key from System "
			"Settings on the server, or backup_encryption_key in its site_config.json. The download "
			"does not include it."
		)

	steps += [
		"If the local apps differ from the server's, run: bench --site <local-site> migrate",
		"Before starting the scheduler on the restored copy: bench --site <local-site> set-config "
		"mute_emails 1 and bench --site <local-site> disable-scheduler. The copy holds the server's "
		"unsent emails and its integration settings.",
		"Password fields and two-factor secrets are encrypted with the server's encryption_key, which "
		"is deliberately not part of the download, so on the copy they read as invalid and it cannot "
		"use the server's integrations. If they are needed, the user can copy encryption_key from the "
		"server's site_config.json into the local one.",
	]

	return steps


def _estimated_size(with_files: bool) -> int:
	try:
		database = int((frappe.db.get_database_size() or 0) * 1024 * 1024 * DUMP_FRACTION)
	except Exception:
		database = 0

	files = 0
	if with_files:
		# tar is not compressed here, and uploads are usually compressed already.
		files = sum(_tree_size(frappe.get_site_path(folder, "files")) for folder in ("public", "private"))

	return database + files


def _tree_size(path: str) -> int:
	total, pending = 0, [path]

	while pending:
		try:
			with os.scandir(pending.pop()) as entries:
				for entry in entries:
					if entry.is_dir(follow_symlinks=False):
						pending.append(entry.path)
					elif entry.is_file(follow_symlinks=False):
						total += entry.stat(follow_symlinks=False).st_size
		except OSError:
			continue

	return total


def _existing(path: str) -> str:
	"""The nearest folder that exists, since the backups folder may not yet."""
	while path and not os.path.exists(path):
		parent = os.path.dirname(path)
		if parent == path:
			break
		path = parent

	return path or "."


def _human(size: float) -> str:
	for unit in ("B", "KB", "MB", "GB", "TB"):
		if size < 1024 or unit == "TB":
			return f"{size:.1f} {unit}"
		size /= 1024


def _site_time(value) -> str | None:
	if not value:
		return None

	return frappe.utils.convert_utc_to_system_timezone(value).strftime("%Y-%m-%d %H:%M:%S")


def _last_line(exc_info: str | None) -> str:
	"""The message at the end of a traceback, without the exception's module path."""
	lines = (exc_info or "").strip().splitlines()
	line = lines[-1] if lines else ""

	head, separator, message = line.partition(": ")
	return message if separator and re.fullmatch(r"[\w.]+", head) else line
