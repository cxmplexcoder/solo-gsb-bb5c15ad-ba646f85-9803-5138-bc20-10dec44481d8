"""时间窗口匿名化接口 POST /api/v1/time-anonymize 的端到端测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

ENDPOINT = "/api/v1/time-anonymize"


def make_record(obs_id: str, observed_at: str, **extra) -> dict:
    return {"id": obs_id, "observed_at": observed_at, **extra}


# UTC 08 时 2 条、09 时 2 条、14 时 2 条。
RECORDS = [
    make_record("obs-a", "2026-03-15T08:12:00+00:00"),
    make_record("obs-b", "2026-03-15T08:45:00+00:00"),
    make_record("obs-c", "2026-03-15T09:05:00+00:00"),
    make_record("obs-d", "2026-03-15T09:30:00+00:00"),
    make_record("obs-e", "2026-03-15T14:00:00+00:00"),
    make_record("obs-f", "2026-03-15T14:30:00+00:00"),
]


def payload(k: int = 2, max_window_hours: int = 4, records=None) -> dict:
    return {
        "k": k,
        "max_window_hours": max_window_hours,
        "records": records if records is not None else RECORDS,
    }


# ---------------------------------------------------------------- 基本功能

def test_optimal_partition_splits_into_single_hours():
    response = client.post(ENDPOINT, json=payload(k=2, max_window_hours=4))
    assert response.status_code == 200
    body = response.json()

    assert body["k"] == 2
    assert body["max_window_hours"] == 4
    assert body["total_records"] == 6

    # 每小时槽都满足 k=2，拆分代价最小：三个单小时窗口。
    assert body["windows"] == [
        {
            "window_start": "2026-03-15T08:00:00Z",
            "window_end": "2026-03-15T09:00:00Z",
            "count": 2,
        },
        {
            "window_start": "2026-03-15T09:00:00Z",
            "window_end": "2026-03-15T10:00:00Z",
            "count": 2,
        },
        {
            "window_start": "2026-03-15T14:00:00Z",
            "window_end": "2026-03-15T15:00:00Z",
            "count": 2,
        },
    ]

    # 按输入顺序返回各 id 的窗口（半开区间）。
    assert [r["id"] for r in body["records"]] == [
        "obs-a", "obs-b", "obs-c", "obs-d", "obs-e", "obs-f",
    ]
    for entry in body["records"][:2]:
        assert entry["window_start"] == "2026-03-15T08:00:00Z"
        assert entry["window_end"] == "2026-03-15T09:00:00Z"
    for entry in body["records"][2:4]:
        assert entry["window_start"] == "2026-03-15T09:00:00Z"
        assert entry["window_end"] == "2026-03-15T10:00:00Z"
    for entry in body["records"][4:]:
        assert entry["window_start"] == "2026-03-15T14:00:00Z"
        assert entry["window_end"] == "2026-03-15T15:00:00Z"


def test_merges_hours_when_single_hour_below_k():
    # 08 时 1 条、09 时 1 条、10 时 2 条、14 时 2 条；k=2 时 08/09 必须合并。
    records = [
        make_record("obs-a", "2026-03-15T08:10:00Z"),
        make_record("obs-b", "2026-03-15T09:20:00Z"),
        make_record("obs-c", "2026-03-15T10:05:00Z"),
        make_record("obs-d", "2026-03-15T10:50:00Z"),
        make_record("obs-e", "2026-03-15T14:00:00Z"),
        make_record("obs-f", "2026-03-15T14:30:00Z"),
    ]
    response = client.post(
        ENDPOINT, json=payload(k=2, max_window_hours=3, records=records)
    )
    assert response.status_code == 200
    body = response.json()

    # [08,09] 代价 2×2，[10] 与 [14] 各 2×1，总代价 8 为全局最优。
    assert body["windows"] == [
        {
            "window_start": "2026-03-15T08:00:00Z",
            "window_end": "2026-03-15T10:00:00Z",
            "count": 2,
        },
        {
            "window_start": "2026-03-15T10:00:00Z",
            "window_end": "2026-03-15T11:00:00Z",
            "count": 2,
        },
        {
            "window_start": "2026-03-15T14:00:00Z",
            "window_end": "2026-03-15T15:00:00Z",
            "count": 2,
        },
    ]
    assert body["records"][0]["window_end"] == "2026-03-15T10:00:00Z"
    assert body["records"][1]["window_end"] == "2026-03-15T10:00:00Z"


def test_tie_break_picks_earliest_end_hour_sequence():
    """08 时 2 条、09 时 1 条、10 时 2 条，k=2、max_window_hours=3。

    [08]+[09,10] 与 [08,09]+[10] 的代价都是 2×1 + 3×2 = 8；
    结束小时序列 (8, 10) 字典序小于 (9, 10)，应选前者。
    """
    records = [
        make_record("obs-a", "2026-03-15T08:05:00Z"),
        make_record("obs-b", "2026-03-15T08:35:00Z"),
        make_record("obs-c", "2026-03-15T09:15:00Z"),
        make_record("obs-d", "2026-03-15T10:10:00Z"),
        make_record("obs-e", "2026-03-15T10:45:00Z"),
    ]
    response = client.post(
        ENDPOINT, json=payload(k=2, max_window_hours=3, records=records)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["windows"] == [
        {
            "window_start": "2026-03-15T08:00:00Z",
            "window_end": "2026-03-15T09:00:00Z",
            "count": 2,
        },
        {
            "window_start": "2026-03-15T09:00:00Z",
            "window_end": "2026-03-15T11:00:00Z",
            "count": 3,
        },
    ]


def test_observed_at_converted_to_utc_before_slotting():
    # 不同时区的时刻先转 UTC 再归槽。
    records = [
        make_record("obs-a", "2026-03-15T10:30:00+08:00"),  # UTC 02:30
        make_record("obs-b", "2026-03-15T02:45:00Z"),  # UTC 02:45
        make_record("obs-c", "2026-03-15T09:15:00+05:45"),  # UTC 03:30
        make_record("obs-d", "2026-03-15T03:59:00+00:00"),  # UTC 03:59
    ]
    response = client.post(
        ENDPOINT, json=payload(k=2, max_window_hours=1, records=records)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["windows"] == [
        {
            "window_start": "2026-03-15T02:00:00Z",
            "window_end": "2026-03-15T03:00:00Z",
            "count": 2,
        },
        {
            "window_start": "2026-03-15T03:00:00Z",
            "window_end": "2026-03-15T04:00:00Z",
            "count": 2,
        },
    ]
    for entry in body["records"][:2]:
        assert entry["window_start"] == "2026-03-15T02:00:00Z"
    for entry in body["records"][2:]:
        assert entry["window_start"] == "2026-03-15T03:00:00Z"


def test_local_dates_may_differ_as_long_as_utc_date_matches():
    # 本地日期不同，但转换到 UTC 后落在同一天。
    records = [
        make_record("obs-a", "2026-03-15T23:30:00-02:00"),  # UTC 2026-03-16T01:30
        make_record("obs-b", "2026-03-16T01:00:00Z"),  # UTC 2026-03-16T01:00
    ]
    response = client.post(
        ENDPOINT, json=payload(k=2, max_window_hours=24, records=records)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["windows"] == [
        {
            "window_start": "2026-03-16T01:00:00Z",
            "window_end": "2026-03-16T02:00:00Z",
            "count": 2,
        }
    ]


def test_window_covering_last_hour_ends_at_midnight_next_day():
    records = [
        make_record("obs-a", "2026-03-15T23:10:00Z"),
        make_record("obs-b", "2026-03-15T23:50:00Z"),
    ]
    response = client.post(
        ENDPOINT, json=payload(k=2, max_window_hours=1, records=records)
    )
    assert response.status_code == 200
    body = response.json()
    # 半开区间的右端落在次日 00:00。
    assert body["windows"] == [
        {
            "window_start": "2026-03-15T23:00:00Z",
            "window_end": "2026-03-16T00:00:00Z",
            "count": 2,
        }
    ]


def test_response_keeps_input_order_and_drops_original_fields():
    records = [
        {**make_record("obs-c", "2026-03-15T09:05:00Z"), "observer": "志愿者C"},
        make_record("obs-a", "2026-03-15T08:12:00Z"),
        make_record("obs-d", "2026-03-15T09:30:00Z"),
        make_record("obs-b", "2026-03-15T08:45:00Z"),
    ]
    response = client.post(
        ENDPOINT, json=payload(k=2, max_window_hours=4, records=records)
    )
    assert response.status_code == 200
    body = response.json()

    # 按输入顺序返回，且只含 id 与半开区间两端。
    assert [r["id"] for r in body["records"]] == ["obs-c", "obs-a", "obs-d", "obs-b"]
    for entry in body["records"]:
        assert set(entry) == {"id", "window_start", "window_end"}
    # 窗口按时间升序排列，计数均 >= k。
    assert [w["count"] for w in body["windows"]] == [2, 2]
    assert body["windows"][0]["window_start"] < body["windows"][1]["window_start"]
    # 不回传原时间或其他原字段。
    assert "observed_at" not in response.text
    assert "observer" not in response.text
    assert "08:12" not in response.text
    assert "09:05" not in response.text


# ---------------------------------------------------------------- 422 校验

def test_422_when_total_less_than_k():
    response = client.post(ENDPOINT, json=payload(k=7))
    assert response.status_code == 422
    assert "小于 k" in response.json()["detail"]


def test_422_on_empty_records():
    response = client.post(ENDPOINT, json=payload(k=1, records=[]))
    assert response.status_code == 422


def test_422_when_k_not_positive_integer():
    for bad_k in (0, -2, True, "2", 2.5):
        response = client.post(ENDPOINT, json=payload(k=bad_k))
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
        [{"observed_at": "2026-03-15T08:00:00Z"}],  # 缺 id
        [make_record("", "2026-03-15T08:00:00Z")],  # 空 id
        [make_record("   ", "2026-03-15T08:00:00Z")],  # 空白 id
        [{"id": 123, "observed_at": "2026-03-15T08:00:00Z"}],  # 非字符串 id
    ]
    for records in variants:
        response = client.post(ENDPOINT, json=payload(k=1, records=records))
        assert response.status_code == 422
        assert "id" in response.json()["detail"]


def test_422_when_id_duplicated():
    records = [
        make_record("obs-a", "2026-03-15T08:00:00Z"),
        make_record("obs-a", "2026-03-15T09:00:00Z"),
    ]
    response = client.post(ENDPOINT, json=payload(k=1, records=records))
    assert response.status_code == 422
    assert "唯一" in response.json()["detail"]


def test_422_when_observed_at_missing_or_not_string():
    variants = [
        [{"id": "obs-a"}],  # 缺 observed_at
        [{"id": "obs-a", "observed_at": 123}],
        [{"id": "obs-a", "observed_at": None}],
        [{"id": "obs-a", "observed_at": ["2026-03-15T08:00:00Z"]}],
        [{"id": "obs-a", "observed_at": "   "}],
    ]
    for records in variants:
        response = client.post(ENDPOINT, json=payload(k=1, records=records))
        assert response.status_code == 422
        assert "observed_at" in response.json()["detail"]


def test_422_when_observed_at_not_parseable():
    for bad_value in ("not-a-time", "2026-13-45T99:99:99Z", "08:00:00"):
        records = [make_record("obs-a", bad_value)]
        response = client.post(ENDPOINT, json=payload(k=1, records=records))
        assert response.status_code == 422, bad_value
        assert "observed_at" in response.json()["detail"]


def test_422_when_observed_at_has_no_timezone():
    # 日期型与不带时区的日期时间型都必须拒绝。
    for naive_value in ("2026-03-15", "2026-03-15T08:00:00"):
        records = [make_record("obs-a", naive_value)]
        response = client.post(ENDPOINT, json=payload(k=1, records=records))
        assert response.status_code == 422, naive_value
        assert "时区" in response.json()["detail"]


def test_422_when_records_span_multiple_utc_dates():
    records = [
        make_record("obs-a", "2026-03-15T23:30:00-02:00"),  # UTC 2026-03-16
        make_record("obs-b", "2026-03-15T10:00:00Z"),  # UTC 2026-03-15
    ]
    response = client.post(ENDPOINT, json=payload(k=1, records=records))
    assert response.status_code == 422
    assert "同一 UTC 日期" in response.json()["detail"]


def test_422_when_no_feasible_partition():
    # 每小时槽只有 1 条（< k=2），合并跨度 2 又超过 max_window_hours=1。
    records = [
        make_record("obs-a", "2026-03-15T08:10:00Z"),
        make_record("obs-b", "2026-03-15T09:20:00Z"),
    ]
    response = client.post(
        ENDPOINT, json=payload(k=2, max_window_hours=1, records=records)
    )
    assert response.status_code == 422
    assert "划分" in response.json()["detail"]


def test_422_on_missing_or_extra_body_fields():
    response = client.post(ENDPOINT, json={"k": 2, "max_window_hours": 4})
    assert response.status_code == 422

    response = client.post(ENDPOINT, json={**payload(), "extra": 1})
    assert response.status_code == 422


def test_422_when_record_is_not_object():
    response = client.post(ENDPOINT, json=payload(k=1, records=[["not", "a", "dict"]]))
    assert response.status_code == 422
