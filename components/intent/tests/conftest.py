"""Make the odd1_intent package importable when the tests run from a checkout."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
