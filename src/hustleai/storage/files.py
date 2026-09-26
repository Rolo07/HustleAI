"""Private, atomic local file replacement."""
import os
import tempfile
from pathlib import Path

def private_write(path, text):
    """Atomically replace a local text file using an owner-only temporary file.

    Args:
        path: Destination path; its parent directory must already exist.
        text: Complete text content to write using the default text encoding.

    Raises:
        OSError: Creating, writing, replacing, or cleaning up a file fails.

    Creates the temporary file beside the destination with mode 0600, then uses os.replace.
    A pre-replacement failure leaves the prior destination intact. Replacement
    is atomic, but read-modify-write callers must handle their own concurrency;
    this helper does not lock concurrent read-modify-write callers. File and
    parent directory are fsynced to make a completed replacement durable.
    """
    fd, temporary = tempfile.mkstemp(prefix='.zoho-local-', dir=Path(path).parent)
    try:
        with os.fdopen(fd, 'w') as output:
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory = os.open(Path(path).parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
