# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""Reading the bench's files, and exporting apps as a zip.

What may be touched is decided in frappe_mcp_bridge.mcp.file_guard, not here. An export
is written to the site's private folder and handed out as a signed link that works for
an hour without signing in, so a browser or curl can fetch it. Switching off MCP access
or Allow Server Files stops every outstanding link at once.
"""

import datetime
import fnmatch
import os
import re
import time
import zipfile
from collections import deque

import frappe
from frappe import _

from frappe_mcp_bridge.mcp import file_guard, links
from frappe_mcp_bridge.mcp.gate import max_rows
from frappe_mcp_bridge.mcp.registry import tool

# Enough of a file to page through, not so much that one call floods the context.
MAX_READ_LINES = 2_000
MAX_READ_CHARS = 100_000

# A null byte this early means the file is not text.
BINARY_SNIFF_BYTES = 8_192

# Search passes over minified bundles and big logs, and gives up after this many files.
MAX_SEARCH_FILE_BYTES = 2 * 1024 * 1024
MAX_SEARCH_FILES = 20_000
MAX_MATCH_CHARS = 300

# Inside the site's private folder, which the file tools themselves can never read.
EXPORT_FOLDER = "mcp_exports"
EXPORT_LINK_MINUTES = 60
DOWNLOAD_METHOD = "frappe_mcp_bridge.mcp.handlers.files.download_export"


@tool(
	"list_server_files",
	"files",
	summary="Files and folders in the bench directory, or every file below it matching a pattern.",
	path_param="path",
)
def list_server_files(path: str = "", pattern: str | None = None, limit: int = 200) -> dict:
	top = file_guard.resolve(path)
	limit = min(int(limit or 200), max_rows())

	if pattern:
		entries = []
		for file in file_guard.walk(top):
			if _matches(file, top, pattern):
				entries.append({"path": file_guard.relative(file), **_stat(file)})
				if len(entries) > limit:
					break
	else:
		if not os.path.isdir(top):
			frappe.throw(_("{0} is not a folder. Use read_server_file to read a file.").format(path))

		entries = [
			_entry(entry)
			for entry in sorted(os.scandir(top), key=lambda entry: (not entry.is_dir(), entry.name))
		]

	return {
		"path": file_guard.relative(top),
		"count": min(len(entries), limit),
		"truncated": len(entries) > limit,
		"entries": entries[:limit],
	}


@tool(
	"read_server_file",
	"files",
	summary="Read a text file from the bench directory, a page of lines at a time.",
	path_param="path",
)
def read_server_file(path: str, start_line: int = 1, line_count: int = 500) -> str:
	target = file_guard.resolve(path)
	if not os.path.isfile(target):
		frappe.throw(_("{0} is not a file.").format(path))

	if _is_binary(target):
		frappe.throw(_("{0} is not a text file.").format(file_guard.relative(target)))

	start_line = int(start_line or 1)
	line_count = max(1, min(int(line_count or 500), MAX_READ_LINES))

	# A negative start counts from the end, which is what a log wants.
	tail = deque(maxlen=-start_line) if start_line < 0 else None
	total, lines = 0, []

	with open(target, encoding="utf-8", errors="replace") as handle:
		for total, line in enumerate(handle, 1):
			if tail is not None:
				tail.append(line)
			elif start_line <= total < start_line + line_count:
				lines.append(line)

	first = start_line
	if tail is not None:
		first, lines = total - len(tail) + 1, list(tail)[:line_count]

	body, chars = [], 0
	for number, line in enumerate(lines, first):
		numbered = f"{number:>6}\t{line.rstrip(chr(10) + chr(13))}"
		chars += len(numbered) + 1
		if chars > MAX_READ_CHARS and body:
			break
		body.append(numbered)

	name, size = file_guard.relative(target), os.path.getsize(target)
	if not body:
		return f"{name} · no lines there · {total} lines, {size} bytes"

	last = first + len(body) - 1
	header = f"{name} · lines {first}-{last} of {total} · {size} bytes"
	if last < total:
		header += f" · continue with start_line={last + 1}"

	# Plain text rather than a dict, so the code reaches the model as code, not as an
	# escaped JSON string.
	return header + "\n\n" + "\n".join(body)


@tool(
	"search_server_files",
	"files",
	summary="Search the text of files in the bench directory, like grep.",
	path_param="path",
)
def search_server_files(
	query: str,
	path: str = "apps",
	pattern: str | None = None,
	regex: bool = False,
	ignore_case: bool = False,
	limit: int = 100,
) -> dict:
	if not query:
		frappe.throw(_("Pass the text to search for."))

	try:
		matcher = re.compile(query if regex else re.escape(query), re.IGNORECASE if ignore_case else 0)
	except re.error as error:
		frappe.throw(_("query is not a valid regular expression: {0}").format(error))

	top = file_guard.resolve(path)
	limit = min(int(limit or 100), max_rows())
	matches, scanned, truncated = [], 0, False

	for file in file_guard.walk(top):
		if not _matches(file, top, pattern) or not _is_searchable(file):
			continue

		if scanned >= MAX_SEARCH_FILES:
			truncated = True
			break

		scanned += 1
		with open(file, encoding="utf-8", errors="replace") as handle:
			for number, line in enumerate(handle, 1):
				if not matcher.search(line):
					continue

				matches.append(
					{
						"path": file_guard.relative(file),
						"line": number,
						"text": line.strip()[:MAX_MATCH_CHARS],
					}
				)
				if len(matches) >= limit:
					break

		if len(matches) >= limit:
			truncated = True
			break

	return {
		"query": query,
		"path": file_guard.relative(top),
		"files_searched": scanned,
		"count": len(matches),
		"truncated": truncated,
		"matches": matches,
	}


@tool(
	"export_apps",
	"files",
	summary="Zip the source of this site's custom apps and return a download link that works for an hour.",
	read_only=False,
)
def export_apps(apps: list[str] | None = None) -> dict:
	installed = frappe.get_installed_apps()

	if apps:
		unknown = sorted(set(apps) - set(frappe.get_all_apps()))
		if unknown:
			frappe.throw(_("Not an app on this bench: {0}").format(", ".join(unknown)))

		chosen, frappe_apps = list(dict.fromkeys(apps)), []
	else:
		chosen = [app for app in installed if not _is_frappe_app(app)]
		frappe_apps = [app for app in installed if app not in chosen]

	if not chosen:
		frappe.throw(_("Every app on this site is maintained by Frappe. Name the apps to export in apps."))

	folder = _export_folder()
	os.makedirs(folder, exist_ok=True)

	stamp = frappe.utils.now_datetime().strftime("%Y%m%d-%H%M%S")
	name = f"{frappe.scrub(frappe.local.site)}-apps-{stamp}-{frappe.generate_hash(length=8)}.zip"
	path = os.path.join(folder, name)
	exported, left_out = [], []

	try:
		with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
			for app in chosen:
				root = file_guard.resolve(os.path.join("apps", app))
				if not os.path.isdir(root):
					frappe.throw(_("{0} has no folder under apps/.").format(app))

				files = size = 0
				for file in file_guard.walk(root, refused=left_out):
					archive.write(file, os.path.join(app, os.path.relpath(file, root)))
					files += 1
					size += os.path.getsize(file)

				exported.append({"app": app, "files": files, "size": size})
	except Exception:
		# A half-written zip is no use to anyone.
		if os.path.exists(path):
			os.remove(path)
		raise

	return {
		"apps": exported,
		"frappe_apps_left_out": frappe_apps,
		"files_left_out": left_out,
		"file": name,
		"size": os.path.getsize(path),
		"download_url": links.url(DOWNLOAD_METHOD, "export", name, EXPORT_LINK_MINUTES),
		"link_expires_in_minutes": EXPORT_LINK_MINUTES,
	}


@frappe.whitelist(allow_guest=True, methods=["GET"])
def download_export(file: str | None = None, expires: str | None = None, signature: str | None = None):
	"""Serve a zip made by export_apps. The signed link is the credential, so a browser or
	curl can fetch it without signing in."""
	if not re.fullmatch(r"[\w.-]+\.zip", file or ""):
		raise links.invalid()

	return links.download(
		tool="download_export",
		capability="files",
		kind="export",
		name=file,
		expires=expires,
		signature=signature,
		path=os.path.join(_export_folder(), file),
	)


def clear_old_exports():
	"""Scheduler hook. Every link to these zips has stopped working, so they can go."""
	folder = _export_folder()
	if not os.path.isdir(folder):
		return

	cutoff = time.time() - EXPORT_LINK_MINUTES * 60
	for entry in os.scandir(folder):
		if entry.is_file() and entry.stat().st_mtime < cutoff:
			os.remove(entry.path)


def _export_folder() -> str:
	# Where send_private_file looks, so the download can be handed to nginx in production.
	return frappe.get_site_path(frappe.local.conf.get("private_path", "private"), EXPORT_FOLDER)


def _is_frappe_app(app: str) -> bool:
	"""frappe, erpnext, hrms and the other apps Frappe publishes. Everything else is custom."""
	publisher = (frappe.get_hooks("app_publisher", app_name=app) or [""])[0]
	return publisher.startswith("Frappe Technologies")


def _matches(path: str, top: str, pattern: str | None) -> bool:
	"""A pattern with a slash matches the path below top; one without matches the file name."""
	if not pattern:
		return True

	if "/" in pattern:
		return fnmatch.fnmatch(os.path.relpath(path, top), pattern)

	return fnmatch.fnmatch(os.path.basename(path), pattern)


def _entry(entry: os.DirEntry) -> dict:
	kind = "link" if entry.is_symlink() else "folder" if entry.is_dir() else "file"
	row = {"name": entry.name, "type": kind, **_stat(entry.path)}

	if file_guard.is_blocked_name(entry.name) or file_guard.is_site_private(entry.path):
		row["blocked"] = True

	return row


def _stat(path: str) -> dict:
	try:
		stat = os.stat(path)
	except OSError:
		# A broken symlink.
		return {}

	row = {"modified": datetime.datetime.fromtimestamp(stat.st_mtime).isoformat(sep=" ", timespec="seconds")}
	if not os.path.isdir(path):
		row["size"] = stat.st_size

	return row


def _is_binary(path: str) -> bool:
	with open(path, "rb") as handle:
		return b"\0" in handle.read(BINARY_SNIFF_BYTES)


def _is_searchable(path: str) -> bool:
	try:
		return os.path.getsize(path) <= MAX_SEARCH_FILE_BYTES and not _is_binary(path)
	except OSError:
		return False
