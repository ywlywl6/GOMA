"""Environment setup shared by the public Timeloop entry points."""
import os
from pathlib import Path
import shutil
import sys


def prepare_environment():
    # An absolute Python invocation need not activate its console scripts.
    # Inherit this through the process rather than pytimeloop's shell-rendered
    # environment argument (which cannot safely quote paths with spaces).
    python_bin = str(Path(sys.executable).resolve().parent)
    entries = os.environ.get("PATH", "").split(os.pathsep)
    os.environ["PATH"] = os.pathsep.join(dict.fromkeys([python_bin, *entries]))
    for executable in ("accelergy", "timeloop-model"):
        if shutil.which(executable) is None:
            raise RuntimeError(f"{executable} not found on PATH; see README.md")
