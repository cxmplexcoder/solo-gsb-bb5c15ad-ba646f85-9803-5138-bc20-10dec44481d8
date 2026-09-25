"""坐标自适应网格匿名化接口 POST /api/v1/geo-anonymize 的端到端测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

ENDPOINT = "/api/v1/geo-anonymize"


def make_record(obs_id: str, lat: float, lon: float) -> dict:
    return {"id": obs_id, "latitude": lat, "longitude": lon}


# 东北象限两条、西南象限两条；每个象限内部坐标相近。
RECORDS = [
    make_record("obs-a", 10.0, 10.0),
    make_record("obs-b", 20.0, 30.0),
    make_record("obs-c", -10.0, -10.0),
    make_record("obs-d", -20.0, -30.0),
]


def payload(k: int = 2, max_zoom: int = 3, records=None) -> dict:
    return {
        "k": k,
        "max_zoom": max_zoom,
        "records": records if records is not None else RECORDS,
    }


# ---------------------------------------------------------------- 基本功能

def test_adaptive_split_basic():
    response = client.post(ENDPOINT, json=payload(k=2, max_zoom=3))
    assert response.status_code == 200
    body = response.json()

    assert body["k"] == 2
    assert body["max_zoom"] == 3
    assert body["total_records"] == 4

    # 按输入顺序返回各 id 的最终格。
    assert [r["id"] for r in body["records"]] == ["obs-a", "obs-b", "obs-c", "obs-d"]
    assert [(r["z"], r["x"], r["y"]) for r in body["records"]] == [
        (3, 4, 3),
        (3, 4, 3),
        (3, 3, 4),
        (3, 3, 4),
    ]

    # 两个发布格，人数均 >= k，边界为逐层对分的结果。
    assert body["cells"] == [
        {
            "z": 3, "x": 4, "y": 3,
            "lat_min": 0.0, "lat_max": 22.5,
            "lon_min": 0.0, "lon_max": 45.0,
            "count": 2,
        },
        {
            "z": 3, "x": 3, "y": 4,
            "lat_min": -22.5, "lat_max": 0.0,
            "lon_min": -45.0, "lon_max": 0.0,
            "count": 2,
        },
    ]


def test_max_zoom_zero_keeps_root_cell():
    response = client.post(ENDPOINT, json=payload(k=2, max_zoom=0))
    assert response.status_code == 200
    body = response.json()
    assert [(r["z"], r["x"], r["y"]) for r in body["records"]] == [(0, 0, 0)] * 4
    assert body["cells"] == [
        {
            "z": 0, "x": 0, "y": 0,
            "lat_min": -90.0, "lat_max": 90.0,
            "lon_min": -180.0, "lon_max": 180.0,
            "count": 4,
        }
    ]


def test_split_stops_when_a_child_has_less_than_k():
    # 西南象限只有 1 条（< k=2），根格整格停止。
    records = [
        make_record("obs-a", 10.0, 10.0),
        make_record("obs-b", 20.0, 30.0),
        make_record("obs-c", -10.0, -10.0),
    ]
    response = client.post(ENDPOINT, json=payload(k=2, max_zoom=12, records=records))
    assert response.status_code == 200
    body = response.json()
    assert [(r["z"], r["x"], r["y"]) for r in body["records"]] == [(0, 0, 0)] * 3
    assert len(body["cells"]) == 1
    assert body["cells"][0]["count"] == 3


def test_k_one_splits_to_max_zoom():
    # k=1 时每个非空子格都满足条件，单点也一路拆到 max_zoom。
    records = [make_record("obs-a", 10.0, 10.0)]
    response = client.post(ENDPOINT, json=payload(k=1, max_zoom=12, records=records))
    assert response.status_code == 200
    body = response.json()
    assert body["records"][0]["z"] == 12
    assert body["cells"][0]["count"] == 1
    # 每级对分后格子的经纬跨度为 360/2^z 与 180/2^z。
    cell = body["cells"][0]
    assert cell["lon_max"] - cell["lon_min"] == 360.0 / 2**12
    assert cell["lat_max"] - cell["lat_min"] == 180.0 / 2**12


def test_boundary_points_go_north_and_east():
    # 落在中点上的点归入最北、最东子格。
    records = [make_record("obs-origin", 0.0, 0.0)]
    response = client.post(ENDPOINT, json=payload(k=1, max_zoom=1, records=records))
    assert response.status_code == 200
    body = response.json()
    assert (body["records"][0]["z"], body["records"][0]["x"], body["records"][0]["y"]) == (1, 1, 0)
    assert body["cells"][0]["lat_min"] == 0.0
    assert body["cells"][0]["lon_min"] == 0.0


def test_extreme_coordinates_are_valid():
    records = [
        make_record("obs-ne", 90.0, 180.0),
        make_record("obs-sw", -90.0, -180.0),
    ]
    response = client.post(ENDPOINT, json=payload(k=1, max_zoom=2, records=records))
    assert response.status_code == 200
    body = response.json()
    # 东北角一路归东北格，西南角一路归西南格。
    assert (body["records"][0]["z"], body["records"][0]["x"], body["records"][0]["y"]) == (2, 3, 0)
    assert (body["records"][1]["z"], body["records"][1]["x"], body["records"][1]["y"]) == (2, 0, 3)


def test_response_keeps_input_order_and_drops_original_fields():
    records = [
        {**make_record("obs-c", -10.0, -10.0), "observer": "志愿者C"},
        make_record("obs-a", 10.0, 10.0),
        make_record("obs-d", -20.0, -30.0),
        make_record("obs-b", 20.0, 30.0),
    ]
    response = client.post(ENDPOINT, json=payload(k=2, max_zoom=3, records=records))
    assert response.status_code == 200
    body = response.json()

    # 按输入顺序返回，且只含 id 与 z/x/y。
    assert [r["id"] for r in body["records"]] == ["obs-c", "obs-a", "obs-d", "obs-b"]
    for entry in body["records"]:
        assert set(entry) == {"id", "z", "x", "y"}
    # 发布格按格内记录在输入中首次出现的顺序排列。
    assert [(c["z"], c["x"], c["y"]) for c in body["cells"]] == [(3, 3, 4), (3, 4, 3)]
    # 不回传原坐标或其他原字段。
    assert "latitude" not in response.text
    assert "longitude" not in response.text
    assert "observer" not in response.text


# ---------------------------------------------------------------- 422 校验

def test_422_when_total_less_than_k():
    response = client.post(ENDPOINT, json=payload(k=5))
    assert response.status_code == 422
    assert "小于 k" in response.json()["detail"]


def test_422_on_empty_records():
    response = client.post(ENDPOINT, json=payload(k=1, records=[]))
    assert response.status_code == 422


def test_422_when_k_not_positive_integer():
    for bad_k in (0, -2, True, "2", 2.5):
        response = client.post(ENDPOINT, json=payload(k=bad_k))
        assert response.status_code == 422


def test_422_when_max_zoom_out_of_range():
    for bad_zoom in (-1, 13, 100):
        response = client.post(ENDPOINT, json=payload(max_zoom=bad_zoom))
        assert response.status_code == 422
        assert "max_zoom" in response.json()["detail"]


def test_422_when_max_zoom_not_integer():
    for bad_zoom in (True, "3", 2.5):
        response = client.post(ENDPOINT, json=payload(max_zoom=bad_zoom))
        assert response.status_code == 422


def test_422_when_id_invalid():
    variants = [
        [{"latitude": 10.0, "longitude": 10.0}],  # 缺 id
        [make_record("", 10.0, 10.0)],  # 空 id
        [make_record("   ", 10.0, 10.0)],  # 空白 id
        [{"id": 123, "latitude": 10.0, "longitude": 10.0}],  # 非字符串 id
    ]
    for records in variants:
        response = client.post(ENDPOINT, json=payload(k=1, records=records))
        assert response.status_code == 422
        assert "id" in response.json()["detail"]


def test_422_when_id_duplicated():
    records = [make_record("obs-a", 10.0, 10.0), make_record("obs-a", 20.0, 20.0)]
    response = client.post(ENDPOINT, json=payload(k=1, records=records))
    assert response.status_code == 422
    assert "唯一" in response.json()["detail"]


def test_422_when_coordinate_not_finite_number():
    bad_values = ("10", True, None, [10.0], {"v": 10.0})
    for bad_value in bad_values:
        for field in ("latitude", "longitude"):
            record = make_record("obs-a", 10.0, 10.0)
            record[field] = bad_value
            response = client.post(ENDPOINT, json=payload(k=1, records=[record]))
            assert response.status_code == 422, (field, bad_value)


def test_422_when_coordinate_is_nan_or_infinity():
    # httpx 的 json= 无法序列化 NaN/Infinity，直接发送原始 JSON 文本。
    for bad_literal in ("NaN", "Infinity", "-Infinity"):
        for field in ("latitude", "longitude"):
            record = {"id": "obs-a", "latitude": 10.0, "longitude": 10.0}
            record[field] = bad_literal
            body = (
                '{"k": 1, "max_zoom": 1, "records": ['
                + '{"id": "obs-a", "latitude": %s, "longitude": %s}' % (
                    bad_literal if field == "latitude" else "10.0",
                    bad_literal if field == "longitude" else "10.0",
                )
                + "]}"
            )
            response = client.post(
                ENDPOINT,
                content=body,
                headers={"Content-Type": "application/json"},
            )
            assert response.status_code == 422, (field, bad_literal)
            assert "有限数值" in response.json()["detail"]


def test_422_when_coordinate_missing():
    for field in ("latitude", "longitude"):
        record = make_record("obs-a", 10.0, 10.0)
        del record[field]
        response = client.post(ENDPOINT, json=payload(k=1, records=[record]))
        assert response.status_code == 422


def test_422_when_coordinate_out_of_range():
    for record in (
        make_record("obs-a", 90.0001, 0.0),
        make_record("obs-a", -90.0001, 0.0),
        make_record("obs-a", 0.0, 180.0001),
        make_record("obs-a", 0.0, -180.0001),
    ):
        response = client.post(ENDPOINT, json=payload(k=1, records=[record]))
        assert response.status_code == 422
        assert "之间" in response.json()["detail"]


def test_422_on_missing_or_extra_body_fields():
    response = client.post(ENDPOINT, json={"k": 2, "max_zoom": 3})
    assert response.status_code == 422

    response = client.post(ENDPOINT, json={**payload(), "extra": 1})
    assert response.status_code == 422


def test_422_when_record_is_not_object():
    response = client.post(ENDPOINT, json=payload(k=1, records=[["not", "a", "dict"]]))
    assert response.status_code == 422
