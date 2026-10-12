"""Make the project root importable from tests regardless of the cwd pytest runs in."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
