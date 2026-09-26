"""Compatibility entry point; application code now lives in src/hustleai."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / 'src'))
from tests.unit.test_zoho_tools import *

if __name__ == "__main__":
    unittest.main()
