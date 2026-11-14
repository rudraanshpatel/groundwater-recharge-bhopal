"""Download CGWB groundwater-level readings for the study area from India-WRIS.

Usage (from the repository root):

    python scripts/download_wells.py                    # 2014-2024, all 3 districts
    python scripts/download_wells.py --start 2020 --end 2024

Uses the India-WRIS "Ground Water Level" API (admin hierarchy), one year and
one district at a time, 1000 records per page, and writes every record to
data/raw/wells/wells_raw.csv. Nothing else is needed: no login or API key.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

API = "https://indiawris.gov.in/Dataset/Ground%20Water%20Level"
STATE = "Madhya Pradesh"
# GADM and older records say Hoshangabad; newer ones may say Narmadapuram.
DISTRICTS = ["Bhopal", "Raisen", "Hoshangabad", "Narmadapuram"]
PAGE_SIZE = 1000


def fetch_page(district: str, start: str, end: str, page: int, retries: int = 4) -> list[dict]:
    params = {"stateName": STATE, "districtName": district, "agencyName": "CGWB",
              "startdate": start, "enddate": end, "download": "false",
              "page": page, "size": PAGE_SIZE}
    url = API + "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    req = urllib.request.Request(url, data=b"", method="POST",
                                 headers={"accept": "application/json"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                body = json.loads(r.read().decode("utf-8"))
            break
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            if attempt == retries - 1:
                raise
            wait = 5 * (attempt + 1)
            print(f"    retry in {wait}s ({exc})")
            time.sleep(wait)
    data = body.get("data") or []
    if body.get("statusCode") != 200 and not data:
        return []           # "No data found for the given criteria."
    return data if isinstance(data, list) else []


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", type=int, default=2014)
    parser.add_argument("--end", type=int, default=2024)
    parser.add_argument("--data-dir", help="data folder (default: data, or GWR_DATA_DIR)")
    args = parser.parse_args(argv)
    if args.data_dir:
        os.environ["GWR_DATA_DIR"] = args.data_dir
    from backend import config

    out = config.RAW_DIR / "wells" / "wells_raw.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    rows, seen = [], set()
    for district in DISTRICTS:
        n_district = 0
        for year in range(args.start, args.end + 1):
            page = 0
            while True:
                data = fetch_page(district, f"{year}-01-01", f"{year}-12-31", page)
                for rec in data:
                    key = (rec.get("stationCode"), rec.get("dataTime"), rec.get("datatypeCode"))
                    if key not in seen:
                        seen.add(key)
                        rows.append(rec)
                        n_district += 1
                if len(data) < PAGE_SIZE:
                    break
                page += 1
                time.sleep(0.5)
            print(f"  {district} {year}: {n_district} records so far")
        print(f"{district}: {n_district} records")
    if not rows:
        print("No records returned. Check the district names on the India-WRIS API page.")
        return 1
    fields = list(dict.fromkeys(k for r in rows for k in r))
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    stations = {r.get("stationCode") for r in rows}
    print(f"Wrote {out}: {len(rows)} readings from {len(stations)} wells")
    return 0


if __name__ == "__main__":
    sys.exit(main())
