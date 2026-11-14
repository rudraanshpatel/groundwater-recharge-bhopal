import sys
from pathlib import Path

import pandas as pd


def test_seasonal_rise_uses_may_and_november_with_fallback():
    from backend.validation import seasonal_rise

    rows = [
        # well A: May and November in 2020, April and October in 2021
        ("A", "2020-05-10", -10.0), ("A", "2020-11-10", -6.0),
        ("A", "2021-04-10", -11.0), ("A", "2021-10-10", -9.0),
        # well B: only pre-monsoon -> dropped
        ("B", "2020-05-10", -5.0),
    ]
    df = pd.DataFrame(rows, columns=["stationCode", "dataTime", "dataValue"])
    df["latitude"], df["longitude"] = 23.0, 77.5
    out = seasonal_rise(df).set_index("stationCode")
    assert list(out.index) == ["A"]
    assert out.loc["A", "years"] == 2
    assert abs(out.loc["A", "rise_m"] - 3.0) < 1e-9          # (4 + 2) / 2


def test_download_wells_pages_and_dedupes(tmp_path, monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import download_wells

    calls = []

    def fake_fetch(district, start, end, page, retries=4):
        calls.append((district, start[:4], page))
        if district != "Bhopal" or start[:4] != "2020":
            return []
        n = download_wells.PAGE_SIZE if page == 0 else 3
        return [{"stationCode": f"S{page}-{i}", "dataTime": start, "datatypeCode": "x",
                 "latitude": 23, "longitude": 77, "dataValue": -5} for i in range(n)]

    monkeypatch.setattr(download_wells, "fetch_page", fake_fetch)
    assert download_wells.main(["--start", "2020", "--end", "2020"]) == 0
    assert ("Bhopal", "2020", 1) in calls
    from backend import config
    out = pd.read_csv(config.RAW_DIR / "wells" / "wells_raw.csv")
    assert len(out) == download_wells.PAGE_SIZE + 3


def test_exclusion_reasons():
    from backend.validation import exclusion_reason

    base = {"years": 5, "rise_m": 3.0}
    assert exclusion_reason({**base, "stationName": "Berasia(D)"}) == "deep well / piezometer"
    assert exclusion_reason({**base, "stationName": "Sanchi Pz"}) == "deep well / piezometer"
    assert exclusion_reason({**base, "stationName": "Raisen", "years": 2}).startswith("fewer")
    assert exclusion_reason({**base, "stationName": "X", "rise_m": 40.9}).startswith("rise beyond")
    assert exclusion_reason({**base, "stationName": "Raisen"}) == ""
