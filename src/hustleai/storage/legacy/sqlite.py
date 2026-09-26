"""SQLite compatibility bootstrap during the staged Supabase migration."""
import os
import sqlite3


def connect_operations(root):
    """Open the existing private journal, preserving all operation history.

    Args: root: Directory containing the legacy database.
    Returns: sqlite3 connection owned by the caller.
    Raises: OSError or sqlite3.Error when storage is unavailable.
    """
    path = root / '.zoho-operations.sqlite3'
    fd = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
    os.close(fd)
    db = sqlite3.connect(path, timeout=30)
    db.execute('CREATE TABLE IF NOT EXISTS operations (id TEXT PRIMARY KEY, org TEXT, kind TEXT, payload TEXT, preview TEXT, created REAL, status TEXT, result TEXT)')
    db.commit()
    return db
