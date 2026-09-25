"""时空联合匿名发布接口 POST /api/v1/spacetime-anonymize 的端到端测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

ENDPOINT = "/api/v1/spacetime-anonymize"


def make_record(obs_id: str, lat: float, lon: float, observed_at: str, **extra) -> dict:
    return {
        "id": obs_id,
        "latitude": lat,
        "longitude": lon,
        "observed_at": observed_at,
        **extra,
    }


# 两处位置、两个小时，每个“格 × 小时”联合组恰好 2 条。
RECORDS = [
    make_record("obs-a", 10.0, 10.0, "2026-03-15T08:12:00+00:00"),
    make_record("obs-b", 10.0, 10.0, "2026-03-15T08:45:00+00:00"),
    make_record("obs-c", 20.0, 100.0, "2026-03-15T09:05:00+00:00"),
    make_record("obs-d", 20.0, 100.0, "2026-03-15T09:30:00+00:00"),
]


def payload(k: int = 2, max_zoom: int = 4, max_window_hours: int = 4, records=None) -> dict:
    return {
        "k": k,
        "max_zoom": max_zoom,
        "max_window_hours": max_window_hours,
        "records": records if records is not None else RECORDS,
    }


# ---------------------------------------------------------------- 基本功能

def test_joint_groups_pick_finest_scheme():
    response = client.post(ENDPOINT, json=payload())
    assert response.status_code == 200
    body = response.json()

    assert body["k"] == 2
    assert body["max_zoom"] == 4
    assert body["max_window_hours"] == 4
    assert body["total_records"] == 4

    # 每个非空联合组都满足 k=2，最细方案 z=4、宽度 1 小时可行。
    assert [(r["z"], r["x"], r["y"]) for r in body["records"]] == [
        (4, 8, 7),
        (4, 8, 7),
        (4, 12, 6),
        (4, 12, 6),
    ]
    assert [r["id"] for r in body["records"]] == ["obs-a", "obs-b", "obs-c", "obs-d"]
    for entry in body["records"][:2]:
        assert entry["window_start"] == "2026-03-15T08:00:00Z"
        assert entry["window_end"] == "2026-03-15T09:00:00Z"
    for entry in body["records"][2:]:
        assert entry["window_start"] == "2026-03-15T09:00:00Z"
        assert entry["window_end"] == "2026-03-15T10:00:00Z"

    # 联合组按首次出现顺序，人数均 >= k。
    assert body["groups"] == [
        {
            "z": 4, "x": 8, "y": 7,
            "window_start": "2026-03-15T08:00:00Z",
            "window_end": "2026-03-15T09:00:00Z",
            "count": 2,
        },
        {
            "z": 4, "x": 12, "y": 6,
            "window_start": "2026-03-15T09:00:00Z",
            "window_end": "2026-03-15T10:00:00Z",
            "count": 2,
        },
    ]


def test_wider_window_chosen_when_narrow_infeasible():
    # 两条记录同格，分别在 13 时与 14 时：宽度 1、2 都会把它们拆到
    # 不同的窗（每组 1 条 < k=2）；整除 24 的宽度 3 的窗口 [12,15) 能装下两条。
    records = [
        make_record("obs-a", 10.0, 10.0, "2026-03-15T13:10:00Z"),
        make_record("obs-b", 10.0, 10.0, "2026-03-15T14:50:00Z"),
    ]
    response = client.post(
        ENDPOINT, json=payload(k=2, max_zoom=2, max_window_hours=6, records=records)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["groups"] == [
        {
            "z": 2, "x": 2, "y": 1,
            "window_start": "2026-03-15T12:00:00Z",
            "window_end": "2026-03-15T15:00:00Z",
            "count": 2,
        }
    ]
    for entry in body["records"]:
        assert entry["window_start"] == "2026-03-15T12:00:00Z"
        assert entry["window_end"] == "2026-03-15T15:00:00Z"


def test_coarser_zoom_chosen_when_finer_splits_group():
    # 两条记录同小时、相邻 z=3 格（经度分居 45° 两侧）；z=3 会拆成
    # 两组各 1 条，z=2 同格。
    records = [
        make_record("obs-a", 10.0, 10.0, "2026-03-15T08:00:00Z"),
        make_record("obs-b", 10.0, 50.0, "2026-03-15T08:00:00Z"),
    ]
    response = client.post(
        ENDPOINT, json=payload(k=2, max_zoom=3, max_window_hours=1, records=records)
    )
    assert response.status_code == 200
    body = response.json()
    assert [(r["z"], r["x"], r["y"]) for r in body["records"]] == [(2, 2, 1)] * 2
    assert body["groups"] == [
        {
            "z": 2, "x": 2, "y": 1,
            "window_start": "2026-03-15T08:00:00Z",
            "window_end": "2026-03-15T09:00:00Z",
            "count": 2,
        }
    ]


def test_tie_break_prefers_shorter_window():
    """k=2、max_zoom=2、max_window_hours=8。

    (z=1, 宽度1) 与 (z=2, 宽度4) 的联合组总数都是 4^z×24/宽度 = 96，
    且都可行（z=2 时每格两条记录落在 08 与 11 时，宽度 4 的 [08,12) 才能
    装下同格两条）；平局应选较短窗，即 z=1、宽度 1。
    """
    records = [
        make_record("obs-a", 10.0, 10.0, "2026-03-15T08:00:00Z"),
        make_record("obs-b", 10.0, 10.0, "2026-03-15T11:00:00Z"),
        make_record("obs-c", 10.0, 100.0, "2026-03-15T08:00:00Z"),
        make_record("obs-d", 10.0, 100.0, "2026-03-15T11:00:00Z"),
    ]
    response = client.post(
        ENDPOINT, json=payload(k=2, max_zoom=2, max_window_hours=8, records=records)
    )
    assert response.status_code == 200
    body = response.json()
    assert [(r["z"], r["x"], r["y"]) for r in body["records"]] == [(1, 1, 0)] * 4
    assert body["groups"] == [
        {
            "z": 1, "x": 1, "y": 0,
            "window_start": "2026-03-15T08:00:00Z",
            "window_end": "2026-03-15T09:00:00Z",
            "count": 2,
        },
        {
            "z": 1, "x": 1, "y": 0,
            "window_start": "2026-03-15T11:00:00Z",
            "window_end": "2026-03-15T12:00:00Z",
            "count": 2,
        },
    ]


def test_k_one_picks_finest_zoom_and_width():
    records = [make_record("obs-a", 10.0, 10.0, "2026-03-15T08:00:00Z")]
    response = client.post(
        ENDPOINT, json=payload(k=1, max_zoom=3, max_window_hours=24, records=records)
    )
    assert response.status_code == 200
    body = response.json()
    assert (body["records"][0]["z"], body["records"][0]["x"], body["records"][0]["y"]) == (3, 4, 3)
    assert body["groups"][0]["window_start"] == "2026-03-15T08:00:00Z"
    assert body["groups"][0]["window_end"] == "2026-03-15T09:00:00Z"
    assert body["groups"][0]["count"] == 1


def test_observed_at_converted_to_utc_before_slotting():
    # 不同时区的时刻先转 UTC 再归槽。
    records = [
        make_record("obs-a", 10.0, 10.0, "2026-03-15T16:20:00+08:00"),  # UTC 08:20
        make_record("obs-b", 10.0, 10.0, "2026-03-15T08:50:00Z"),  # UTC 08:50
    ]
    response = client.post(
        ENDPOINT, json=payload(k=2, max_zoom=2, max_window_hours=1, records=records)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["groups"] == [
        {
            "z": 2, "x": 2, "y": 1,
            "window_start": "2026-03-15T08:00:00Z",
            "window_end": "2026-03-15T09:00:00Z",
            "count": 2,
        }
    ]


def test_full_day_window_ends_at_midnight_next_day():
    # 08 时与 20 时各 1 条：只有宽度 24 的全天窗能把它们装入同一组。
    records = [
        make_record("obs-a", 10.0, 10.0, "2026-03-15T08:00:00Z"),
        make_record("obs-b", 10.0, 10.0, "2026-03-15T20:00:00Z"),
    ]
    response = client.post(
        ENDPOINT, json=payload(k=2, max_zoom=1, max_window_hours=24, records=records)
    )
    assert response.status_code == 200
    body = response.json()
    # 半开区间的右端落在次日 00:00。
    assert body["groups"] == [
        {
            "z": 1, "x": 1, "y": 0,
            "window_start": "2026-03-15T00:00:00Z",
            "window_end": "2026-03-16T00:00:00Z",
            "count": 2,
        }
    ]


def test_response_keeps_input_order_and_drops_original_fields():
    records = [
        {**make_record("obs-c", 20.0, 100.0, "2026-03-15T09:05:00Z"), "observer": "志愿者C"},
        make_record("obs-a", 10.0, 10.0, "2026-03-15T08:12:00Z"),
        {**make_record("obs-d", 20.0, 100.0, "2026-03-15T09:30:00Z"), "species": "林麝"},
        make_record("obs-b", 10.0, 10.0, "2026-03-15T08:45:00Z"),
    ]
    response = client.post(ENDPOINT, json=payload(records=records))
    assert response.status_code == 200
    body = response.json()

    # 按输入顺序返回，且只含 id、z/x/y 与半开区间两端。
    assert [r["id"] for r in body["records"]] == ["obs-c", "obs-a", "obs-d", "obs-b"]
    for entry in body["records"]:
        assert set(entry) == {"id", "z", "x", "y", "window_start", "window_end"}
    # 联合组按组内记录在输入中首次出现的顺序排列（obs-c 所在组在前）。
    assert [(g["x"], g["y"]) for g in body["groups"]] == [(12, 6), (8, 7)]
    assert [g["count"] for g in body["groups"]] == [2, 2]
    # 不回传原坐标、原时刻或其他原字段。
    assert "latitude" not in response.text
    assert "longitude" not in response.text
    assert "observed_at" not in response.text
    assert "observer" not in response.text
    assert "species" not in response.text
    assert "08:12" not in response.text
    assert "100.0" not in response.text


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


def test_422_when_max_window_hours_out_of_range():
    for bad_hours in (-1, 0, 25, 100):
        response = client.post(ENDPOINT, json=payload(max_window_hours=bad_hours))
        assert response.status_code == 422
        assert "max_window_hours" in response.json()["detail"]


def test_422_when_max_window_hours_not_integer():
    for bad_hours in (True, "4", 2.5):
        response = client.post(ENDPOINT, json=payload(max_window_hours=bad_hours))
        assert response.status_code == 422


def test_422_when_id_invalid():
    variants = [
        [{"latitude": 10.0, "longitude": 10.0, "observed_at": "2026-03-15T08:00:00Z"}],  # 缺 id
        [make_record("", 10.0, 10.0, "2026-03-15T08:00:00Z")],  # 空 id
        [make_record("   ", 10.0, 10.0, "2026-03-15T08:00:00Z")],  # 空白 id
        [{"id": 123, "latitude": 10.0, "longitude": 10.0, "observed_at": "2026-03-15T08:00:00Z"}],  # 非字符串 id
    ]
    for records in variants:
        response = client.post(ENDPOINT, json=payload(k=1, records=records))
        assert response.status_code == 422
        assert "id" in response.json()["detail"]


def test_422_when_id_duplicated():
    records = [
        make_record("obs-a", 10.0, 10.0, "2026-03-15T08:00:00Z"),
        make_record("obs-a", 20.0, 20.0, "2026-03-15T09:00:00Z"),
    ]
    response = client.post(ENDPOINT, json=payload(k=1, records=records))
    assert response.status_code == 422
    assert "唯一" in response.json()["detail"]


def test_422_when_coordinate_not_finite_number():
    bad_values = ("10", True, None, [10.0], {"v": 10.0})
    for bad_value in bad_values:
        for field in ("latitude", "longitude"):
            record = make_record("obs-a", 10.0, 10.0, "2026-03-15T08:00:00Z")
            record[field] = bad_value
            response = client.post(ENDPOINT, json=payload(k=1, records=[record]))
            assert response.status_code == 422, (field, bad_value)


def test_422_when_coordinate_is_nan_or_infinity():
    # httpx 的 json= 无法序列化 NaN/Infinity，直接发送原始 JSON 文本。
    for bad_literal in ("NaN", "Infinity", "-Infinity"):
        for field in ("latitude", "longitude"):
            body = (
                '{"k": 1, "max_zoom": 1, "max_window_hours": 1, "records": ['
                + '{"id": "obs-a", "latitude": %s, "longitude": %s, '
                '"observed_at": "2026-03-15T08:00:00Z"}' % (
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
        record = make_record("obs-a", 10.0, 10.0, "2026-03-15T08:00:00Z")
        del record[field]
        response = client.post(ENDPOINT, json=payload(k=1, records=[record]))
        assert response.status_code == 422


def test_422_when_coordinate_out_of_range():
    for record in (
        make_record("obs-a", 90.0001, 0.0, "2026-03-15T08:00:00Z"),
        make_record("obs-a", -90.0001, 0.0, "2026-03-15T08:00:00Z"),
        make_record("obs-a", 0.0, 180.0001, "2026-03-15T08:00:00Z"),
        make_record("obs-a", 0.0, -180.0001, "2026-03-15T08:00:00Z"),
    ):
        response = client.post(ENDPOINT, json=payload(k=1, records=[record]))
        assert response.status_code == 422
        assert "之间" in response.json()["detail"]


def test_422_when_observed_at_missing_or_not_string():
    variants = [
        [{"id": "obs-a", "latitude": 10.0, "longitude": 10.0}],  # 缺 observed_at
        [{"id": "obs-a", "latitude": 10.0, "longitude": 10.0, "observed_at": 123}],
        [{"id": "obs-a", "latitude": 10.0, "longitude": 10.0, "observed_at": None}],
        [{"id": "obs-a", "latitude": 10.0, "longitude": 10.0, "observed_at": "   "}],
    ]
    for records in variants:
        response = client.post(ENDPOINT, json=payload(k=1, records=records))
        assert response.status_code == 422
        assert "observed_at" in response.json()["detail"]


def test_422_when_observed_at_not_parseable():
    for bad_value in ("not-a-time", "2026-13-45T99:99:99Z", "08:00:00"):
        records = [make_record("obs-a", 10.0, 10.0, bad_value)]
        response = client.post(ENDPOINT, json=payload(k=1, records=records))
        assert response.status_code == 422, bad_value
        assert "observed_at" in response.json()["detail"]


def test_422_when_observed_at_has_no_timezone():
    # 日期型与不带时区的日期时间型都必须拒绝。
    for naive_value in ("2026-03-15", "2026-03-15T08:00:00"):
        records = [make_record("obs-a", 10.0, 10.0, naive_value)]
        response = client.post(ENDPOINT, json=payload(k=1, records=records))
        assert response.status_code == 422, naive_value
        assert "时区" in response.json()["detail"]


def test_422_when_records_span_multiple_utc_dates():
    records = [
        make_record("obs-a", 10.0, 10.0, "2026-03-15T23:30:00-02:00"),  # UTC 2026-03-16
        make_record("obs-b", 10.0, 10.0, "2026-03-15T10:00:00Z"),  # UTC 2026-03-15
    ]
    response = client.post(ENDPOINT, json=payload(k=1, records=records))
    assert response.status_code == 422
    assert "同一 UTC 日期" in response.json()["detail"]


def test_422_when_no_feasible_scheme():
    # 08 时与 20 时各 1 条：整除 24 且不超过 6 的宽度都无法把它们装入同一窗。
    records = [
        make_record("obs-a", 10.0, 10.0, "2026-03-15T08:00:00Z"),
        make_record("obs-b", 10.0, 10.0, "2026-03-15T20:00:00Z"),
    ]
    response = client.post(
        ENDPOINT, json=payload(k=2, max_zoom=2, max_window_hours=6, records=records)
    )
    assert response.status_code == 422
    assert "联合组" in response.json()["detail"]


def test_422_on_missing_or_extra_body_fields():
    response = client.post(ENDPOINT, json={"k": 2, "max_zoom": 3})
    assert response.status_code == 422

    response = client.post(ENDPOINT, json={**payload(), "extra": 1})
    assert response.status_code == 422


def test_422_when_record_is_not_object():
    response = client.post(ENDPOINT, json=payload(k=1, records=[["not", "a", "dict"]]))
    assert response.status_code == 422
