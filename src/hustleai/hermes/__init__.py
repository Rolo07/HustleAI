"""Hermes Agent plugin for HustleAI.

Registers the owner-assistant skill. The tools themselves come from the
HustleAI MCP server, configured per tenant profile by
`hustleai-tenant hermes-install <slug>`, so they keep working outside Hermes
and each profile only reaches its own tenant's data folder.
"""
from pathlib import Path

PACKAGE = Path(__file__).parent


def register(ctx):
    """Hermes plugin entry point: register bundled skills (read-only)."""
    for folder in sorted((PACKAGE / 'skills').iterdir()):
        if (folder / 'SKILL.md').is_file():
            ctx.register_skill(folder.name, folder / 'SKILL.md')
