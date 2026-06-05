# Copyright 2026 Andrew C. Young <andrew@vaelen.org>
# SPDX-License-Identifier: MIT

class BrigidError(Exception):
    """Base error for Brigid."""


class ConfigError(BrigidError):
    """Raised when configuration is invalid or missing."""


class ToolError(BrigidError):
    """Raised when a tool fails to execute."""


class PermissionDenied(BrigidError):
    """Raised when a tool call is denied by policy."""


class MCPConnectionError(BrigidError):
    """Raised when an MCP server fails to start or respond."""
