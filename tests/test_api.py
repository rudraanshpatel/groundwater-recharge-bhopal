"""End-to-end: synthetic data -> pipeline -> API."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(demo_data):
    from backend.app import app
    return TestClient(app)


def test_status_ready(client):
    assert client.get("/api/status").json()["ready"] is True


def test_regions(client):
    regions = client.get("/api/regions").json()
    assert len(regions["tehsil"]) == 9 and len(regions["watershed"]) == 25
    gj = client.get("/api/regions/tehsil").json()
    assert len(gj["features"]) == 9


def test_ahp_endpoints(client):
    assert client.get("/api/ahp").json()["is_consistent"]
    bad = client.post("/api/ahp", json={"matrix": [[1, 2], [2, 1]], "criteria": ["a", "b"]})
    assert bad.status_code == 400


@pytest.mark.parametrize("season", ["annual", "pre_monsoon", "monsoon", "post_monsoon"])
def test_analyze_each_season(client, season):
    r = client.post("/analyze", json={"region_type": "tehsil", "region_id": "tehsil-4", "season": season})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["geojson"]["type"] == "FeatureCollection" and data["geojson"]["features"]
    assert sum(s["percent"] for s in data["stats"]) == pytest.approx(100, abs=0.1)
    assert len(data["layers_used"]) == 7


def test_dry_season_has_lower_potential(client):
    def high_share(season):
        stats = client.post("/analyze", json={"season": season}).json()["stats"]
        return sum(s["percent"] for s in stats if s["class"] >= 4)
    assert high_share("monsoon") > high_share("pre_monsoon")


def test_custom_weights_and_errors(client):
    r = client.post("/analyze", json={"weights": {"slope": 1}})
    assert r.status_code == 200 and r.json()["weights"] == {"slope": 1.0}
    assert client.post("/analyze", json={"weights": {"nope": 1}}).status_code == 400
    assert client.post("/analyze", json={"region_type": "tehsil", "region_id": "x"}).status_code == 404


def test_layers(client):
    r = client.get("/layers/geology")
    assert r.status_code == 200 and r.json()["features"]
    assert client.get("/layers/unknown").status_code == 404


def test_exports(client):
    tif = client.post("/export/geotiff", json={"region_type": "watershed", "region_id": "watershed-0"})
    assert tif.status_code == 200 and tif.content[:2] in (b"II", b"MM")
    pdf = client.post("/export/pdf", json={"season": "monsoon"})
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"


def test_frontend_served(client):
    assert "Leaflet" in client.get("/").text or "leaflet" in client.get("/").text
    assert client.get("/js/map.js").status_code == 200
