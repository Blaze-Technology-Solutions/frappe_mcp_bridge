# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""Configuration, read once at start-up from the environment and a .env file."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

TRUTHY = {"1", "true", "yes", "on"}
FALSY = {"0", "false", "no", "off"}


class ConfigError(RuntimeError):
	pass


@dataclass(frozen=True)
class Config:
	url: str
	api_key: str
	api_secret: str
	timeout: float = 120.0
	verify_ssl: bool = True
	client_name: str = "claude-code"
	read_only: bool = True
	dry_run: bool | None = None
	transport: str = "stdio"
	host: str = "127.0.0.1"
	port: int = 8765
	log_level: str = "INFO"
	log_file: str | None = None

	@property
	def endpoint(self) -> str:
		return f"{self.url}/api/method"

	@property
	def auth_header(self) -> str:
		return f"token {self.api_key}:{self.api_secret}"


def find_env_file(explicit: str | None = None) -> Path | None:
	"""FRAPPE_MCP_ENV_FILE wins, then .env beside the working directory, then the package root.

	The package root matters because Claude Code launches the server with whatever
	working directory it happens to have.
	"""
	candidates = []

	if explicit:
		candidates.append(Path(explicit))

	if os.environ.get("FRAPPE_MCP_ENV_FILE"):
		candidates.append(Path(os.environ["FRAPPE_MCP_ENV_FILE"]))

	candidates.append(Path.cwd() / ".env")
	# src/frappe_mcp_bridge_client/config.py -> src/frappe_mcp_bridge_client -> src -> mcp_server
	candidates.append(Path(__file__).resolve().parents[2] / ".env")

	for candidate in candidates:
		if candidate.is_file():
			return candidate

	return None


def load(env_file: str | None = None) -> Config:
	path = find_env_file(env_file)
	if path:
		load_dotenv(path, override=False)

	url = (os.environ.get("FRAPPE_MCP_URL") or "").strip().rstrip("/")
	api_key = (os.environ.get("FRAPPE_MCP_API_KEY") or "").strip()
	api_secret = (os.environ.get("FRAPPE_MCP_API_SECRET") or "").strip()

	missing = [
		name
		for name, value in (
			("FRAPPE_MCP_URL", url),
			("FRAPPE_MCP_API_KEY", api_key),
			("FRAPPE_MCP_API_SECRET", api_secret),
		)
		if not value
	]
	if missing:
		looked_in = str(path) if path else "no .env file found"
		raise ConfigError(
			f"Missing {', '.join(missing)}. Set them in your .env ({looked_in}) "
			f"or in the environment. See .env.example."
		)

	if not url.startswith(("http://", "https://")):
		raise ConfigError(f"FRAPPE_MCP_URL must start with http:// or https://, got {url!r}.")

	transport = (os.environ.get("FRAPPE_MCP_TRANSPORT") or "stdio").strip().lower()
	if transport not in ("stdio", "streamable-http"):
		raise ConfigError(f"FRAPPE_MCP_TRANSPORT must be stdio or streamable-http, got {transport!r}.")

	return Config(
		url=url,
		api_key=api_key,
		api_secret=api_secret,
		timeout=_float("FRAPPE_MCP_TIMEOUT", 120.0),
		verify_ssl=_bool("FRAPPE_MCP_VERIFY_SSL", True),
		client_name=(os.environ.get("FRAPPE_MCP_CLIENT_NAME") or "claude-code").strip(),
		# Defaults to read only: connecting to production should never be the moment
		# you discover writes were on.
		read_only=_bool("FRAPPE_MCP_READ_ONLY", True),
		dry_run=_optional_bool("FRAPPE_MCP_DRY_RUN"),
		transport=transport,
		host=(os.environ.get("FRAPPE_MCP_HOST") or "127.0.0.1").strip(),
		port=int(_float("FRAPPE_MCP_PORT", 8765)),
		log_level=(os.environ.get("FRAPPE_MCP_LOG_LEVEL") or "INFO").strip().upper(),
		log_file=(os.environ.get("FRAPPE_MCP_LOG_FILE") or "").strip() or None,
	)


def _bool(name: str, default: bool) -> bool:
	value = _optional_bool(name)
	return default if value is None else value


def _optional_bool(name: str) -> bool | None:
	raw = (os.environ.get(name) or "").strip().lower()
	if not raw:
		return None

	if raw in TRUTHY:
		return True

	if raw in FALSY:
		return False

	raise ConfigError(f"{name} must be true or false, got {raw!r}.")


def _float(name: str, default: float) -> float:
	raw = (os.environ.get(name) or "").strip()
	if not raw:
		return default

	try:
		return float(raw)
	except ValueError:
		raise ConfigError(f"{name} must be a number, got {raw!r}.")
