# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""The bench's files, and exporting apps as a zip. Off unless Allow Server Files is ticked."""

from ..client import Bridge
from .common import render


def register(mcp, bridge: Bridge) -> None:
	@mcp.tool()
	async def list_server_files(path: str = "", pattern: str | None = None, limit: int = 200) -> str:
		"""List files and folders in the bench directory: apps, sites, logs, config.

		path is relative to the bench, e.g. "apps/erpnext/erpnext/stock"; an absolute path
		inside the bench, as a traceback shows it, works too. Leave it out for the bench
		itself. pattern searches by file name below path instead: "hooks.py" or "*.json"
		matches the name at any depth, and a pattern with a slash, such as
		"*/doctype/*/*.py", matches the path below path. Entries holding credentials are
		marked blocked and cannot be read.
		"""
		return render(
			await bridge.call("list_server_files", {"path": path, "pattern": pattern, "limit": limit})
		)

	@mcp.tool()
	async def read_server_file(path: str, start_line: int = 1, line_count: int = 500) -> str:
		"""Read a text file from the bench directory, with line numbers.

		path is relative to the bench, e.g. "apps/myapp/myapp/hooks.py". Long files come a
		page at a time, and the first line says which lines you got and where to continue.
		A negative start_line counts from the end, so start_line=-200 reads the last 200
		lines of a log. site_config.json, keys, .git and each site's private files are
		always refused.
		"""
		return render(
			await bridge.call(
				"read_server_file", {"path": path, "start_line": start_line, "line_count": line_count}
			)
		)

	@mcp.tool()
	async def search_server_files(
		query: str,
		path: str = "apps",
		pattern: str | None = None,
		regex: bool = False,
		ignore_case: bool = False,
		limit: int = 100,
	) -> str:
		"""Search the contents of files in the bench directory, like grep.

		Searches apps/ unless path says otherwise. pattern narrows it to matching file
		names, e.g. "*.py". query is plain text unless regex=true. Binary files and files
		over 2 MB are skipped. Each match comes back with its path, line number and text;
		read_server_file shows the lines around it.
		"""
		return render(
			await bridge.call(
				"search_server_files",
				{
					"query": query,
					"path": path,
					"pattern": pattern,
					"regex": regex,
					"ignore_case": ignore_case,
					"limit": limit,
				},
			)
		)

	@mcp.tool()
	async def export_apps(apps: list[str] | None = None) -> str:
		"""Zip the source code of apps on this bench and return a download link.

		Leave apps out to export every app installed on this site that Frappe does not
		maintain itself, i.e. everything except frappe, erpnext, hrms and the like; or name
		the apps to export. Each app is a folder in the zip, without .git, node_modules,
		caches or credential files. files_left_out lists anything that was refused.

		download_url works for 60 minutes without signing in, so anyone holding it can
		download the code. Give it only to the user. To save the zip locally, run
		curl -fL -o <file> "<download_url>".
		"""
		return render(await bridge.call("export_apps", {"apps": apps}))
