import pathlib
import sys

# Tests run from anywhere: make the component's any_app package importable.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
