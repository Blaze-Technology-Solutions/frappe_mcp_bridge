# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""Entry point: `python -m frappe_mcp_bridge_client` or the `frappe-mcp-bridge` console script."""

import argparse
import asyncio
import logging
import sys

from .client import Bridge, SiteError
from .config import Config, ConfigError, find_env_file
from .config import load as load_config
from .server import serve


def main() -> int:
	args = parse_args()
	configure_logging(args)

	try:
		config = load_config(args.env_file)
	except ConfigError as exception:
		print(f"frappe-mcp-bridge: {exception}", file=sys.stderr)
		return 2

	if args.transport:
		config = replace_transport(config, args.transport)

	if args.check:
		return asyncio.run(check(config, args.env_file))

	log = logging.getLogger("frappe_mcp_bridge_client")
	log.info(
		"serving %s over %s (read_only=%s)",
		config.url,
		config.transport,
		config.read_only,
	)

	serve(config)
	return 0


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(
		prog="frappe-mcp-bridge",
		description="MCP server exposing a Frappe site to Claude.",
	)
	parser.add_argument("--env-file", help="Path to the .env file. Defaults to ./.env then the package root.")
	parser.add_argument(
		"--transport",
		choices=("stdio", "streamable-http"),
		help="Override FRAPPE_MCP_TRANSPORT.",
	)
	parser.add_argument(
		"--check",
		action="store_true",
		help="Test the connection and permissions, print the result, and exit.",
	)
	parser.add_argument("--log-level", help="Override FRAPPE_MCP_LOG_LEVEL.")
	return parser.parse_args()


def configure_logging(args: argparse.Namespace) -> None:
	import os

	level = (args.log_level or os.environ.get("FRAPPE_MCP_LOG_LEVEL") or "INFO").upper()
	handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]

	log_file = os.environ.get("FRAPPE_MCP_LOG_FILE")
	if log_file:
		handlers.append(logging.FileHandler(log_file))

	logging.basicConfig(
		# stdio transport owns stdout, so every log line goes to stderr or the file.
		level=getattr(logging, level, logging.INFO),
		format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
		handlers=handlers,
	)


def replace_transport(config: Config, transport: str) -> Config:
	from dataclasses import replace

	return replace(config, transport=transport)


async def check(config: Config, env_file_arg: str | None = None) -> int:
	bridge = Bridge(config)
	env_file = find_env_file(env_file_arg)

	print(f"env file   {env_file or 'none found, using the environment'}")
	print(f"site       {config.url}")
	print(f"transport  {config.transport}")
	print(f"client     {config.client_name}")
	print(f"read only  {config.read_only} (this MCP server)")

	try:
		result = await bridge.ping()
	except SiteError as exception:
		print(f"\nFAILED\n{exception}", file=sys.stderr)
		return 1
	finally:
		await bridge.aclose()

	print(f"\nconnected as {result.get('user')} on {result.get('site')}")
	print(f"roles            {', '.join(result.get('roles') or [])}")
	print(f"mcp enabled      {result.get('enabled')}")
	print(f"read only mode   {result.get('read_only_mode')} (site setting)")
	print(f"default dry run  {result.get('default_dry_run')}")

	if not result.get("enabled"):
		print("\nMCP is off on the site. Tick Enable MCP Access in MCP Bridge Settings.")

	return 0


if __name__ == "__main__":
	raise SystemExit(main())
