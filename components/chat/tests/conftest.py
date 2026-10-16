import pathlib
import sys

# Tests run from anywhere: make the component's odd1_chat package importable.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
