# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""Signed, expiring download links for files MCP prepares on the server.

The link is the credential: it works without signing in, so curl or a browser can fetch
it. Each one names a single file and an expiry, both covered by an HMAC keyed from the
site's encryption key, so it can be neither pointed at another file nor extended.

The signature is not the only lock. A download also needs its capability to be switched
on and the caller's address to be on the IP list, when there is one, and every file served
is written to MCP Bridge Log with the address that fetched it.
"""

import hashlib
import hmac
import os
import re
import time
from urllib.parse import quote, urlencode

import frappe
import werkzeug.utils
from frappe import _
from frappe.utils.password import get_encryption_key
from frappe.utils.response import send_private_file

from frappe_mcp_bridge.frappe_mcp_bridge.doctype.mcp_bridge_settings.mcp_bridge_settings import get_settings
from frappe_mcp_bridge.mcp import logger


def url(method: str, kind: str, name: str, minutes: int) -> str:
	"""Absolute URL of the whitelisted download `method` for one file, good for `minutes`."""
	expires = int(time.time()) + minutes * 60
	query = urlencode({"file": name, "expires": expires, "signature": _signature(kind, name, expires)})

	return frappe.utils.get_url(f"/api/method/{method}?{query}")


def is_valid(kind: str, name: str | None, expires: str | None, signature: str | None) -> bool:
	if not re.fullmatch(r"[0-9]{1,12}", str(expires or "")) or int(expires) < time.time():
		return False

	return hmac.compare_digest(
		_signature(kind, name or "", int(expires)).encode(), (signature or "").encode()
	)


def invalid() -> frappe.PermissionError:
	return frappe.PermissionError(_("This download link is invalid or has expired. Ask for a new one."))


def download(*, tool, capability, kind, name, expires, signature, path):
	"""Serve the file at `path` if the link is genuine and the site still allows the download."""
	if not is_valid(kind, name, expires, signature):
		# Not logged: anyone can send junk, and a row for each would let them fill the table.
		raise invalid()

	settings = get_settings()
	ip = getattr(frappe.local, "request_ip", None)

	allowed, reason = settings.is_capability_allowed(capability)
	if allowed and not settings.is_ip_allowed(ip):
		allowed, reason = (
			False,
			_("{0} is not in the allowed IP list for MCP access.").format(ip or _("Unknown IP")),
		)

	if not allowed:
		_record(tool, capability, "Blocked", name, reason)
		raise frappe.PermissionError(reason)

	if not os.path.isfile(path):
		raise frappe.DoesNotExistError(_("This file has been removed from the server. Ask for a new one."))

	_record(tool, capability, "Success", name)
	return send(path, name)


def send(path: str, filename: str):
	path = os.path.normpath(path)
	private = os.path.normpath(frappe.get_site_path(frappe.local.conf.get("private_path", "private")))

	if path.startswith(private + os.sep):
		# Where Frappe hands the file to nginx, which is what a multi-gigabyte backup needs
		# rather than a web worker held for the length of the download.
		response = send_private_file(os.path.relpath(path, private))
	else:
		response = werkzeug.utils.send_file(
			path,
			environ=frappe.local.request.environ,
			conditional=True,
			as_attachment=True,
			download_name=filename,
		)

	# The URL ends in the method name, which is no name for a file.
	response.headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(filename)}"
	response.headers["Cache-Control"] = "no-store"

	return response


def _record(tool: str, capability: str, status: str, name: str, error: str | None = None) -> None:
	logger.record(
		tool=tool,
		capability=capability,
		status=status,
		params={"file": name},
		error=error,
		client=(frappe.get_request_header("User-Agent") or "download")[:140],
	)
	# A GET request rolls back whatever it wrote, and this row is the audit trail.
	frappe.db.commit()


def _signature(kind: str, name: str, expires: int) -> str:
	# Derived, so the encryption key itself is never used as an HMAC key.
	key = hashlib.sha256(b"frappe_mcp_bridge download link\0" + get_encryption_key().encode()).digest()
	message = f"{frappe.local.site}\n{kind}\n{name}\n{expires}".encode()

	return hmac.new(key, message, hashlib.sha256).hexdigest()
