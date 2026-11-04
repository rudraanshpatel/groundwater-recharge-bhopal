import numpy as np
import pandas as pd
import pytest
import xarray as xr

from backend.preprocessing.dem import compute_slope, hillshade, line_density
from backend.preprocessing.geology import lithology_rank
from backend.preprocessing.rainfall import idw, mean_seasonal_total, seasonal_totals
from backend.preprocessing.soil import usda_texture_class


def test_slope_of_inclined_plane():
    res = 30.0
    rows, cols = np.mgrid[0:20, 0:20]
    dem = cols * res * np.tan(np.radians(10))      # rises 10 degrees eastward
    slope = compute_slope(dem, res)
    assert np.allclose(slope[2:-2, 2:-2], 10.0, atol=1e-4)


def test_slope_flat_and_nan():
    dem = np.full((5, 5), 100.0)
    dem[2, 2] = np.nan
    s = compute_slope(dem, 30)
    assert np.isnan(s[2, 2]) and s[0, 0] == 0


def test_hillshade_faces_light():
    # Plane dipping toward the east: lit from the east (90) is brighter than from the west (270).
    cols = np.mgrid[0:10, 0:10][1]
    dem = -cols * 10.0
    assert hillshade(dem, 30, 90)[5, 5] > hillshade(dem, 30, 270)[5, 5]


def test_line_density_units():
    # One straight line through a 10 km x 10 km block of 100 m pixels:
    # 10 km of line in 100 km^2 -> 0.1 km/km^2 when the window covers everything.
    res = 100.0
    length = np.zeros((100, 100))
    length[50, :] = res
    d = line_density(length, np.ones_like(length, bool), res, radius_m=20_000)
    assert d[50, 50] == pytest.approx(0.1, rel=1e-6)


@pytest.mark.parametrize("sand,silt,clay,code", [
    (92, 5, 3, 1),      # sand
    (82, 12, 6, 2),     # loamy sand
    (65, 25, 10, 3),    # sandy loam
    (40, 40, 20, 4),    # loam
    (20, 65, 15, 5),    # silt loam
    (5, 88, 7, 6),      # silt
    (60, 12, 28, 7),    # sandy clay loam
    (33, 34, 33, 8),    # clay loam
    (10, 55, 35, 9),    # silty clay loam
    (52, 5, 43, 10),    # sandy clay
    (5, 47, 48, 11),    # silty clay
    (20, 20, 60, 12),   # clay
])
def test_usda_texture(sand, silt, clay, code):
    assert usda_texture_class(np.array([sand]), np.array([silt]), np.array([clay]))[0] == code


def test_texture_nan_is_nodata():
    assert usda_texture_class(np.array([np.nan]), np.array([1.0]), np.array([1.0]))[0] == 0


def test_lithology_rank_keywords():
    assert lithology_rank("Narmada valley alluvium") == 5
    assert lithology_rank("Deccan Trap basalt") == 3
    assert lithology_rank("Weathered Deccan basalt") == 4   # 'weathered' listed first
    assert lithology_rank("Granite gneiss") == 1
    assert lithology_rank("Something unknown") == 2
    assert lithology_rank(None) == 2


def _daily(start, end, value_fn):
    time = pd.date_range(start, end, freq="D")
    vals = np.array([value_fn(t) for t in time], dtype="float32")[:, None, None]
    return xr.DataArray(vals, dims=("time", "lat", "lon"),
                        coords={"time": time, "lat": [23.0], "lon": [77.5]})


def test_seasonal_mean_total():
    # 10 mm every monsoon day, 0 otherwise, 2 full years.
    da = _daily("2020-01-01", "2021-12-31", lambda t: 10.0 if 6 <= t.month <= 9 else 0.0)
    m = mean_seasonal_total(da, [6, 7, 8, 9])
    assert m.values.item() == pytest.approx(10.0 * 122)
    assert m.attrs["n_years"] == 2


def test_wrapping_season_drops_partial_years():
    # Season Nov-Jan: only Nov 2020-Jan 2021 is complete in a 2020-2021 record.
    da = _daily("2020-01-01", "2021-12-31", lambda t: 1.0)
    totals = seasonal_totals(da, [11, 12, 1])
    assert totals.season_year.values.tolist() == [2020]
    assert totals.isel(season_year=0).values.item() == pytest.approx(30 + 31 + 31)


def test_idw_exact_at_points_and_bounded():
    x = np.array([0.0, 1000.0, 0.0])
    y = np.array([0.0, 0.0, 1000.0])
    z = np.array([10.0, 20.0, 30.0])
    out = idw(x, y, z, np.array([0.0, 500.0]), np.array([0.0, 500.0]))
    assert out[0, 0] == pytest.approx(10.0)
    assert 10 <= out[1, 1] <= 30
