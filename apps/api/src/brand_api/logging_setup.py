"""Re-export: logging setup is shared with the MCP server."""

from brand_shared.logging_setup import configure_logging, get_logger

__all__ = ["configure_logging", "get_logger"]
