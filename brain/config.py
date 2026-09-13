"""Runtime configuration read from the environment."""

import os

DEFAULT_MASTER_NAME = "Praveen"
_MASTER_PLACEHOLDER = "{master}"


def master_name() -> str:
    """Return the master's name from ``PICO_MASTER_NAME`` (default: Praveen)."""
    return (os.getenv("PICO_MASTER_NAME") or "").strip() or DEFAULT_MASTER_NAME


def personalize(text: str) -> str:
    """Replace ``{master}`` placeholders in prompt text with the configured name."""
    return text.replace(_MASTER_PLACEHOLDER, master_name())