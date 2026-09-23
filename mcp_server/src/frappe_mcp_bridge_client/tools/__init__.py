# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""Tool registration.

Every tool is declared by hand rather than generated from the site's catalogue, so the
server still starts, and still describes itself accurately, when the site is unreachable.
"""

from ..client import Bridge
from . import maintenance, read, site, write


def register_all(mcp, bridge: Bridge) -> None:
	site.register(mcp, bridge)
	read.register(mcp, bridge)
	write.register(mcp, bridge)
	maintenance.register(mcp, bridge)
