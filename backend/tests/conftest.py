"""Point the app at a throwaway copy of the data directory so tests never touch live state."""

import os
import shutil
import tempfile
from pathlib import Path

_DATA = Path(__file__).resolve().parents[1] / "data"
_TMP = Path(tempfile.mkdtemp(prefix="pharma-test-data-"))
_SKIP = shutil.ignore_patterns("upload-*", "video.*", "poses.json", "thumb.jpg")
for sub in ("scenarios", "layouts"):
    shutil.copytree(_DATA / sub, _TMP / sub, ignore=_SKIP)
os.environ["DATA_DIR"] = str(_TMP)  # read by pharma.config.Settings at import time
