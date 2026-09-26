"""Compatibility entry point; application code now lives in src/hustleai."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / 'src'))
from hustleai.cli.contacts import main, run
from hustleai.config import ROOT, CONFIG, MAPPING
from hustleai.integrations.zoho.client import Client
from hustleai.domain.phones import normalize
from hustleai.storage.files import private_write
from hustleai.storage.legacy.phone_mapping import sync, lookup

if __name__ == "__main__":
    run()
