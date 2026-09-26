"""Point the app at a throwaway copy of the data directory so tests never touch live state.

Layouts come from tests/fixtures, not data/layouts, because the Setup page edits the
live layout and tests must not depend on whatever was last annotated.
"""

import os
import shutil
import tempfile
from pathlib import Path

_DATA = Path(__file__).resolve().parents[1] / "data"
FIXTURE_LAYOUTS = Path(__file__).resolve().parent / "fixtures" / "layouts"
_TMP = Path(tempfile.mkdtemp(prefix="pharma-test-data-"))
_SKIP = shutil.ignore_patterns("upload-*", "video.*", "poses.json", "thumb.jpg")
shutil.copytree(_DATA / "scenarios", _TMP / "scenarios", ignore=_SKIP)
shutil.copytree(FIXTURE_LAYOUTS, _TMP / "layouts")
shutil.copy(FIXTURE_LAYOUTS.parent / "catalog.json", _TMP / "catalog.json")
os.environ["DATA_DIR"] = str(_TMP)  # read by pharma.config.Settings at import time
