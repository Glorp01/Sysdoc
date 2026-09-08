"""Give each CI run a monotonically increasing patch version within this series."""
from pathlib import Path
import re
import sys


def stamp(run_number: int) -> str:
    if run_number < 1:
        raise ValueError("The workflow run number must be positive")
    path = Path(__file__).resolve().parents[1] / "sysdoc" / "__init__.py"
    source = path.read_text(encoding="utf-8")
    match = re.search(r'__version__ = "(\d+)\.(\d+)\.(\d+)"', source)
    if match is None:
        raise ValueError("No release series found in sysdoc/__init__.py")
    major, minor, patch = (int(part) for part in match.groups())
    version = f"{major}.{minor}.{patch + run_number}"
    path.write_text(source[:match.start()] + f'__version__ = "{version}"' + source[match.end():], encoding="utf-8")
    return version


if __name__ == "__main__":
    print(stamp(int(sys.argv[1])))
