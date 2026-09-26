"""Verified HTTPS context for Zoho OAuth and API requests."""
import ssl
from pathlib import Path

def tls_context():
    """Build a verified HTTPS context with a macOS-friendly CA fallback.

    Returns:
        ssl.SSLContext using system defaults plus certifi certificates when
        available, otherwise /etc/ssl/cert.pem if present.

    Raises:
        OSError: A chosen certificate bundle cannot be loaded.
        ssl.SSLError: TLS context or certificate initialization fails.

    Certificate and hostname verification remain enabled. This compensates
    for Python installations that lack a configured default certificate file.
    """
    context = ssl.create_default_context()
    # Python.org macOS installs may lack a default CA file.
    try:
        import certifi
    except ImportError:
        if Path('/etc/ssl/cert.pem').is_file():
            context.load_verify_locations('/etc/ssl/cert.pem')
    else:
        context.load_verify_locations(certifi.where())
    return context
