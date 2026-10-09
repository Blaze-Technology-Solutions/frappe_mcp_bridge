# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""Taking a backup on the server and downloading it. Off unless Allow Backups is ticked."""

from ..client import Bridge
from .common import render


def register(mcp, bridge: Bridge) -> None:
	@mcp.tool()
	async def create_backup(with_files: bool = False, wait_seconds: int = 20) -> str:
		"""Start a backup of this site on the server: the database, and with_files=true the
		public and private files as well.

		It runs in the background, so a large site does not hit the request timeout.
		wait_seconds (up to 30) holds the call open for a quick finish; if it comes back
		running, call list_backups with wait_seconds=30 until the backup is ready, then
		get_backup_links. It is Frappe's own backup, so the files are the same as the
		scheduled ones, and Frappe first removes this site's backup files older than 23 hours
		(keep_backups_for_hours), as the scheduled backup does. It is refused when the disk has
		no room for it.

		A backup holds every record, including the fields Sensitive Fields hides: masking does
		not apply to it.
		"""
		return render(
			await bridge.call("create_backup", {"with_files": with_files, "wait_seconds": wait_seconds})
		)

	@mcp.tool()
	async def list_backups(limit: int = 10, wait_seconds: int = 0) -> str:
		"""List the backups on this site's server, newest first, whether the scheduled backup or
		create_backup made them, and what a running backup is doing.

		Each backup lists its files (database, public_files, private_files) with sizes.
		in_progress means it is still being written. wait_seconds (up to 30) waits for a
		running backup to finish before listing. disk reports the free space on the server.
		"""
		return render(await bridge.call("list_backups", {"limit": limit, "wait_seconds": wait_seconds}))

	@mcp.tool()
	async def get_backup_links(backup: str | None = None, kinds: list[str] | None = None) -> str:
		"""Signed download links for one backup (the newest finished one unless backup is given),
		good for 30 minutes, and the steps to restore it on the user's machine.

		Each download_url fetches one file without signing in, so anyone holding it can
		download the site's data: show it to the user only. kinds narrows the files, e.g.
		["database"] for the data without the uploads, which can be large. Download with
		curl -fL -o <file> "<download_url>" (quote the URL), saving outside any git repository,
		for example in ~/Downloads or the local site's private/backups folder, then follow
		restore_locally. Restoring replaces the local site's database, so ask the user which
		local site to restore into and never restore over one they did not name. The server's
		site_config.json is never offered.
		"""
		return render(await bridge.call("get_backup_links", {"backup": backup, "kinds": kinds}))
