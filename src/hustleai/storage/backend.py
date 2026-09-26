"""Explicit backend selection: hosted outages never activate the legacy journal."""
from hustleai.storage.legacy.repository import SQLiteRepository


def open_repository(config, root):
    """Open the configured backend; reject unknown values rather than guessing."""
    if (root / '.storage-maintenance').exists():
        raise ValueError('Storage maintenance is active; workflow calls are paused until migration completes.')
    backend = config.get('storage_backend', 'sqlite')
    if backend == 'sqlite':
        return SQLiteRepository(root, config['organization_id'])
    if backend == 'postgres':
        from hustleai.storage.postgres.repository import PostgresRepository
        return PostgresRepository(root, config['organization_id'])
    raise ValueError('Unknown storage_backend; expected sqlite or postgres.')
