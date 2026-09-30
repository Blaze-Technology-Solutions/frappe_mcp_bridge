# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""Decides which files on the server the file tools may touch.

Everything is relative to the bench directory: apps, sites, logs, config. Nothing
outside it can be reached, and a symlink cannot lead out of it. Some files are refused
whatever the settings say, because they hold the credentials that would get a reader
past every other lock: the database password and encryption key, private keys, Redis
passwords, git remotes that carry tokens, and each site's private files and backups.
"""

import fnmatch
import os

import frappe
from frappe import _

# Matched against every part of a path, so a folder with one of these names is refused too.
BLOCKED_NAMES = (
	"*site_config*.json",  # database password and encryption key, and backups of them
	".git",  # remote URLs can carry access tokens
	".env",
	".env.*",
	"*.pem",
	"*.key",
	"*.p12",
	"*.pfx",
	"*.acl",  # Redis passwords, in config/
	"id_rsa*",
	"id_ecdsa*",
	"id_ed25519*",
	".netrc",
	".pgpass",
	".my.cnf",
	"*-database.sql*",  # bench backups
	"*-files.tar*",
)

# Templates that only name the settings, committed to most repositories.
ENV_TEMPLATES = (".env.example", ".env.sample", ".env.template")

# Folders a walk never goes into: dependencies, build output and caches, noise for search
# and export. Virtualenvs too, found by their pyvenv.cfg. .git is here as well, so an
# export leaves it out without listing it as a refusal.
SKIPPED_DIRS = (".git", "node_modules", "__pycache__", "*.egg-info", ".*_cache")


def bench_path() -> str:
	return os.path.realpath(frappe.utils.get_bench_path())


def relative(path: str) -> str:
	"""How a path is shown to the caller: relative to the bench, "." for the bench itself."""
	return os.path.relpath(path, bench_path())


def refusal(path: str | None) -> str | None:
	"""A reason to refuse this path, or None. It may be bench-relative or absolute."""
	bench = bench_path()
	target = os.path.normpath(os.path.join(bench, path or ""))

	if not _inside(target, bench):
		return _("{0} is outside the bench directory, which is as far as the file tools reach.").format(path)

	real = os.path.realpath(target)
	root = next((root for root in _roots(bench) if _inside(real, root)), None)
	if not root:
		return _("{0} links to somewhere outside the bench directory.").format(path)

	# The name as asked for and the file it resolves to must both be allowed, so a
	# harmless-looking symlink cannot point at site_config.json.
	for base, current in ((bench, target), (root, real)):
		if _is_blocked(base, current):
			return _("{0} holds credentials or private files, so it can never be read through MCP.").format(
				relative(target)
			)

	return None


def resolve(path: str | None) -> str:
	"""The absolute path for `path`, or throw if it is refused."""
	reason = refusal(path)
	if reason:
		frappe.throw(reason, frappe.PermissionError)

	return os.path.normpath(os.path.join(bench_path(), path or ""))


def is_blocked_name(name: str) -> bool:
	return name not in ENV_TEMPLATES and any(fnmatch.fnmatch(name, pattern) for pattern in BLOCKED_NAMES)


def is_site_private(folder: str) -> bool:
	"""A site's private folder: attachments that Frappe guards per document, and backups."""
	return os.path.basename(folder) == "private" and os.path.isfile(
		os.path.join(os.path.dirname(folder), "site_config.json")
	)


def walk(top: str, refused: list | None = None):
	"""Yield every readable file under top, or top itself when it is a file.

	Refused files and folders are skipped, and added to `refused` when a list is passed.
	Symlinked folders are not followed, so a walk cannot loop or leave the bench.
	Virtualenvs are skipped unless the walk starts inside one.
	"""
	if os.path.isfile(top):
		yield top
		return

	for folder, dirnames, filenames in os.walk(top):
		kept = []
		for dirname in sorted(dirnames):
			path = os.path.join(folder, dirname)
			if _is_skipped_dir(path):
				continue

			if is_blocked_name(dirname) or is_site_private(path):
				if refused is not None:
					refused.append(relative(path))
				continue

			kept.append(dirname)

		dirnames[:] = kept

		for filename in sorted(filenames):
			path = os.path.join(folder, filename)
			if is_blocked_name(filename) or (os.path.islink(path) and refusal(path)):
				if refused is not None:
					refused.append(relative(path))
				continue

			yield path


def _is_skipped_dir(path: str) -> bool:
	name = os.path.basename(path)
	return any(fnmatch.fnmatch(name, pattern) for pattern in SKIPPED_DIRS) or os.path.isfile(
		os.path.join(path, "pyvenv.cfg")
	)


def _is_blocked(base: str, path: str) -> bool:
	current = base
	for part in os.path.relpath(path, base).split(os.sep):
		if part in (".", ""):
			continue

		current = os.path.join(current, part)
		if is_blocked_name(part) or is_site_private(current):
			return True

	return False


def _roots(bench: str) -> list[str]:
	"""The bench, plus the real location of any app folder that is a symlink to elsewhere."""
	apps = os.path.join(bench, "apps")
	roots = [bench]

	if os.path.isdir(apps):
		roots += [os.path.realpath(os.path.join(apps, app)) for app in os.listdir(apps)]

	return roots


def _inside(path: str, root: str) -> bool:
	return path == root or path.startswith(root.rstrip(os.sep) + os.sep)
