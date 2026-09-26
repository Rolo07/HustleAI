"""Compatibility entry point; application code now lives in src/hustleai."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / 'src'))
from hustleai.workflows.service import Service
from hustleai.integrations.zoho.client import API
from hustleai.domain.validation import identifier, positive
from hustleai.workflows.invoice_review import fingerprint
