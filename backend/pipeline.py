"""Command-line pipeline: raw downloads -> processed layers -> recharge zones.

Usage (from the repository root):

    python -m backend.pipeline all            # everything below, in order
    python -m backend.pipeline dem            # slope, drainage density, lineament density
    python -m backend.pipeline rainfall       # seasonal rainfall surfaces
    python -m backend.pipeline lulc | soil | geology
    python -m backend.pipeline rank           # reclassify layers to ranks 1..5
    python -m backend.pipeline overlay        # weighted overlay + zones per season
    python -m backend.pipeline download-rain  # fetch IMD rainfall with imdlib
    python -m backend.pipeline validate       # compare zones with CGWB well levels

Steps whose raw inputs are missing are skipped with a warning, so you can
build the map up layer by layer as data arrives.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from backend import config
from backend.grid import ReferenceGrid

log = logging.getLogger("gwr.pipeline")

PREPROCESS_STEPS = ("dem", "rainfall", "lulc", "soil", "geology")


def _run_step(step: str, grid: ReferenceGrid, args) -> bool:
    t0 = time.time()
    try:
        if step == "dem":
            from backend.preprocessing import dem
            dem.process(grid)
        elif step == "rainfall":
            from backend.preprocessing import rainfall
            rainfall.process(grid, method=args.interp)
        elif step == "lulc":
            from backend.preprocessing import lulc
            lulc.process(grid)
        elif step == "soil":
            from backend.preprocessing import soil
            soil.process(grid)
        elif step == "geology":
            from backend.preprocessing import geology
            geology.process(grid)
        elif step == "rank":
            from backend.overlay import rank_all
            rank_all(grid)
        elif step == "overlay":
            from backend.overlay import run_study_area
            results = run_study_area(grid, method=args.classification)
            for season, res in results.items():
                summary = ", ".join(f"{s['name']} {s['percent']:.1f}%" for s in res["stats"])
                log.info("%-13s %s", season, summary)
        elif step == "validate":
            from backend.validation import validate
            s = validate()
            log.info("Validation (%s map): %d usable wells of %d in the map; excluded %s",
                     s["season"], s["wells"], s["wells_in_map"], s["excluded"] or "none")
            if s["metric"] == "rise_m":
                log.info("  No geology file: comparing raw water-level rise (add geology for "
                         "rise x specific yield)")
            for z in s["zones"]:
                if z["wells"]:
                    extra = (f", median recharge {z['median_recharge_m']:.3f} m"
                             if "median_recharge_m" in z else "")
                    log.info("  %-9s %3d wells, median rise %5.2f m%s", z["name"], z["wells"],
                             z["median_rise_m"], extra)
            for col in ("rise_m", "recharge_m", "post_depth_m"):
                r = s.get(f"spearman_gwrpi_vs_{col}")
                if r:
                    log.info("  Spearman GWRPI vs %-12s rho = %6.3f (p = %.4f, n = %d)",
                             col, r["rho"], r["p_value"], r["n"])
            log.info("  Details: %s (validation_wells.csv, validation_summary.json, validation_boxplot.png)",
                     config.OUTPUT_DIR)
        elif step == "download-rain":
            from backend.preprocessing import rainfall
            rainfall.download_imd()
        else:
            raise ValueError(f"Unknown step {step}")
    except FileNotFoundError as exc:
        log.warning("Skipped %s: %s", step, exc)
        return False
    log.info("Step %s finished in %.1fs", step, time.time() - t0)
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("step", choices=["all", *PREPROCESS_STEPS, "rank", "overlay", "validate", "download-rain"])
    parser.add_argument("--interp", choices=["kriging", "idw"], default=config.RAINFALL_INTERP_METHOD,
                        help="rainfall interpolation method")
    parser.add_argument("--classification", choices=["fixed", "quantile", "equal", "jenks"],
                        default=config.CLASSIFICATION_METHOD,
                        help="how GWRPI is split into 5 classes (breaks from the annual map)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    grid = ReferenceGrid.default()
    log.info("Reference grid: %s, %d x %d px at %.0f m, data dir %s",
             grid.crs, grid.width, grid.height, grid.resolution, config.DATA_DIR)

    steps = [*PREPROCESS_STEPS, "rank", "overlay"] if args.step == "all" else [args.step]
    ok = [s for s in steps if _run_step(s, grid, args)]
    skipped = [s for s in steps if s not in ok]
    if skipped:
        log.warning("Skipped steps (missing inputs): %s", ", ".join(skipped))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
