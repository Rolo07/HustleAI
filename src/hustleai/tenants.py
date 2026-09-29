"""Registry of tenants on a multi-tenant install.

Each tenant is a private folder under TENANTS_ROOT holding tenant.json and
its secrets. A folder is only used if it is a real directory (not a
symlink) owned by the current user and closed to group and others, so a
misconfigured folder cannot silently join a running gateway.
"""
import os
from pathlib import Path

from hustleai.config import TENANTS_ROOT, tenant_dir
from hustleai.tenant import TENANT_FILE, load_tenant


def folder_problem(folder):
    """Return why a tenant folder is unsafe to use, or None if it is fine."""
    folder = Path(folder)
    if folder.is_symlink() or not folder.is_dir():
        return 'not a real directory'
    info = folder.stat()
    if info.st_uid != os.getuid():
        return 'owned by another user'
    if info.st_mode & 0o077:
        return 'readable by group or others (use chmod 700)'
    if not (folder / TENANT_FILE).is_file():
        return f'missing {TENANT_FILE}'
    return None


def list_tenants(root=None):
    """Return {slug: (tenant, folder)} for every usable tenant, plus skipped reasons.

    Returns (tenants, skipped) where skipped maps a folder name to its problem.
    """
    root = Path(root or TENANTS_ROOT)
    tenants, skipped = {}, {}
    if not root.is_dir():
        return tenants, skipped
    for folder in sorted(root.iterdir()):
        if folder.name.startswith('.'):
            continue
        problem = folder_problem(folder)
        if problem:
            skipped[folder.name] = problem
            continue
        try:
            tenants[folder.name] = (load_tenant(folder, slug=folder.name), folder)
        except ValueError as error:
            skipped[folder.name] = str(error)
    return tenants, skipped


def tenant_by_slug(slug, root=None):
    """Return (tenant, folder) for one slug, or raise ValueError with the reason."""
    folder = Path(root) / slug if root else tenant_dir(slug)
    problem = folder_problem(folder)
    if problem:
        raise ValueError(f'Tenant {slug}: {problem}.')
    return load_tenant(folder, slug=slug), folder
