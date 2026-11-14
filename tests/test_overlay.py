import numpy as np
import pytest

from backend.overlay import (classify_index, compute_breaks, rank_layer, reclassify_continuous,
                             weighted_overlay, zone_statistics)


def test_reclassify_continuous_edges_and_nan():
    v = np.array([0, 1.99, 2, 4.9, 5, 25, np.nan])
    out = reclassify_continuous(v, [2, 5, 10, 20], [5, 4, 3, 2, 1])
    assert out.tolist() == [5, 5, 4, 4, 3, 1, 0]


def test_reclassify_rejects_bad_rules():
    with pytest.raises(ValueError):
        reclassify_continuous(np.ones(3), [1, 2], [1, 2])
    with pytest.raises(ValueError):
        reclassify_continuous(np.ones(3), [2, 1], [1, 2, 3])


def test_rank_layer_categorical():
    assert rank_layer("lulc", np.array([10, 50, 80, 0, 255])).tolist() == [4, 1, 5, 0, 0]
    assert rank_layer("soil", np.array([1, 4, 12, 0])).tolist() == [5, 3, 1, 0]
    assert rank_layer("geology", np.array([3, 5, 0, 9])).tolist() == [3, 5, 0, 0]


def test_weighted_overlay_math_and_nodata():
    ranks = {"a": np.array([[5, 1], [3, 0]], "uint8"), "b": np.array([[1, 1], [3, 4]], "uint8")}
    idx = weighted_overlay(ranks, {"a": 3, "b": 1})        # normalised to 0.75 / 0.25
    assert idx[0, 0] == pytest.approx(4.0)
    assert idx[0, 1] == pytest.approx(1.0)
    assert idx[1, 0] == pytest.approx(3.0)
    assert np.isnan(idx[1, 1])                              # 'a' is nodata there


def test_weighted_overlay_ignores_zero_weight_layers():
    ranks = {"a": np.array([[5]], "uint8"), "b": np.array([[0]], "uint8")}
    assert weighted_overlay(ranks, {"a": 1, "b": 0})[0, 0] == pytest.approx(5)


def test_classification_methods():
    idx = np.linspace(1, 5, 1001).astype("float32")
    for method in ("quantile", "equal", "fixed"):
        brks = compute_breaks(idx, method)
        assert len(brks) == 4 and brks == sorted(brks)
        zones = classify_index(idx, brks)
        assert set(np.unique(zones)) == {1, 2, 3, 4, 5}
    q = classify_index(idx, compute_breaks(idx, "quantile"))
    assert np.allclose(np.bincount(q)[1:] / q.size, 0.2, atol=0.01)


def test_zone_statistics():
    zones = np.array([1, 1, 2, 5, 0], "uint8")
    stats = {s["class"]: s for s in zone_statistics(zones, 0.25)}
    assert stats[1]["area_km2"] == pytest.approx(0.5)
    assert stats[1]["percent"] == pytest.approx(50.0)
    assert stats[3]["area_km2"] == 0


def test_rainfall_breaks_relative_spreads_dry_season():
    import numpy as np
    from backend.overlay import rainfall_breaks, rank_layer

    dry = np.linspace(9, 80, 1000)              # pre-monsoon mm, all below 100
    absolute = rank_layer("rainfall", dry, rainfall_breaks(dry, "absolute"))
    assert set(np.unique(absolute)) == {1}
    relative = rank_layer("rainfall", dry, rainfall_breaks(dry, "relative"))
    counts = np.bincount(relative, minlength=6)[1:]
    assert all(150 <= c <= 250 for c in counts)


def test_rainfall_breaks_ignore_nodata():
    import numpy as np
    from backend.overlay import rainfall_breaks

    vals = np.array([np.nan, 10, 20, 30, 40, 50, np.nan])
    b = rainfall_breaks(vals, "relative")
    assert len(b) == 4 and all(10 < x < 50 for x in b)
