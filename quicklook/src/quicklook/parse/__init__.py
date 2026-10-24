"""Parse port. Two adapters: the model parser and a deterministic offline parser."""
from __future__ import annotations

import os

from .base import ParseError, Parser
from .offline import OfflineParser


def get_parser(force_offline: bool = False) -> Parser:
    """Model parser when ANTHROPIC_API_KEY is set, else the offline parser (brief shows a banner)."""
    if not force_offline and os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("QUICKLOOK_OFFLINE"):
        from .claude import ClaudeParser

        return ClaudeParser()
    return OfflineParser()


__all__ = ["ParseError", "Parser", "OfflineParser", "get_parser"]
