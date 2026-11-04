import numpy as np
import pytest

from backend import config
from backend.ahp import (AHPError, compute_ahp, default_result, geometric_mean_weights,
                         matrix_from_weights, normalize_weights)


def test_default_matrix_is_consistent_and_close_to_target_weights():
    res = default_result()
    assert res.is_consistent
    assert res.consistency_ratio < 0.10
    assert sum(res.weights.values()) == pytest.approx(1.0, abs=1e-6)
    for layer, target in config.DEFAULT_WEIGHTS.items():
        assert res.weights[layer] == pytest.approx(target, abs=0.012)


def test_textbook_three_criteria_example():
    # Classic example: A is 3x as important as B and 5x as C, B 2x as C.
    a = [[1, 3, 5], [1 / 3, 1, 2], [1 / 5, 1 / 2, 1]]
    res = compute_ahp(a, ["A", "B", "C"])
    assert res.weights["A"] == pytest.approx(0.648, abs=0.002)
    assert res.weights["B"] == pytest.approx(0.230, abs=0.002)
    assert res.weights["C"] == pytest.approx(0.122, abs=0.002)
    assert res.lambda_max == pytest.approx(3.004, abs=0.001)
    assert res.consistency_ratio == pytest.approx(0.0032, abs=0.001)


def test_perfectly_consistent_matrix_has_zero_cr():
    w = {"a": 0.5, "b": 0.3, "c": 0.2}
    res = compute_ahp(matrix_from_weights(w), list(w))
    assert res.consistency_ratio == pytest.approx(0, abs=1e-9)
    for k, v in w.items():
        assert res.weights[k] == pytest.approx(v)


def test_inconsistent_matrix_is_flagged():
    # A > B, B > C but C >> A: circular judgements.
    a = [[1, 9, 1 / 9], [1 / 9, 1, 9], [9, 1 / 9, 1]]
    assert not compute_ahp(a, ["A", "B", "C"]).is_consistent


def test_eigen_and_geometric_mean_agree_on_default():
    eig = np.array(list(default_result().weights.values()))
    gm = geometric_mean_weights(np.array(default_result().matrix))
    assert np.allclose(eig, gm, atol=0.01)


@pytest.mark.parametrize("bad", [
    [[1, 2], [2, 1]],              # not reciprocal
    [[1, 2, 3], [0.5, 1, 1]],      # not square
    [[2, 0.5], [2, 1]],            # diagonal != 1
    [[1, -1], [-1, 1]],            # negative
])
def test_invalid_matrices_raise(bad):
    with pytest.raises(AHPError):
        compute_ahp(bad, ["x", "y", "z"][: len(bad)])


def test_normalize_weights():
    assert normalize_weights({"a": 2, "b": 2}) == {"a": 0.5, "b": 0.5}
    with pytest.raises(AHPError):
        normalize_weights({"a": 0})
