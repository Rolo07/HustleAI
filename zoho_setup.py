"""Compatibility entry point; application code now lives in src/hustleai."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / 'src'))
from hustleai.cli.setup import main, run
from hustleai.integrations.zoho.auth import tls_context

if __name__ == "__main__":
    run()
