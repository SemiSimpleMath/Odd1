import pathlib
import sys

# Tests run from anywhere: the screen package, and any-app's readers it builds on.
HERE = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE), str(HERE.parent / "any-app")]
