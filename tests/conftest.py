"""Test setup: point the backend at a throwaway data folder BEFORE it is imported.

backend.config reads GWR_DATA_DIR / GWR_RESOLUTION at import time, so these
must be set here, at the top of conftest.py.
"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP = Path(tempfile.mkdtemp(prefix="gwr-test-"))
os.environ["GWR_DATA_DIR"] = str(_TMP)
os.environ["GWR_RESOLUTION"] = "500"

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def demo_data():
    """Synthetic inputs + full pipeline run at 500 m (takes ~20 s)."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import make_synthetic_data
    from backend import pipeline

    make_synthetic_data.main(["--out", str(_TMP)])
    assert pipeline.main(["all", "--interp", "idw"]) == 0
    return _TMP
