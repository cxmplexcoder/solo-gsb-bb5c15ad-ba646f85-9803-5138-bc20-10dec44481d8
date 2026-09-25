"""时空联合匿名化接口 POST /api/v1/spacetime-anonymize 的端到端测试。"""

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


def payload(k=2, max_zoom=8, max_window_hours=4, records=None) -> dict:
    return {
        "k": k,
        "max_zoom": max_zoom,
        "max_window_hours": max_window_hours,
        "records": records if records is not None else RECORDS,
    }


# 两个空间簇（z=8 各占一格），每簇各含 UTC 08 时、09 时一条。
RECORDS = [
    make_record("obs-001", 31.741, 110.682, "2026-03-15T08:12:00Z"),
    make_record("obs-002", 31.744, 110.679, "2026-03-15T09:40:00Z"),
    make_record("obs-003", 30.912, 102.971, "2026-03-15T16:20:00+08:00"),
    make_record("obs-004", 30.908, 102.975, "2026-03-15T17:35:00+08:00"),
]


# ---------------------------------------------------------------- 基本功能

def test_joint_groups_require_wider_window_at_fine_zoom():
    # z=8 时每个格内 08/09 各 1 条，宽度 1 不可行；宽度 2 合并可行，
    # 分数 4^8 × 24/2 = 786432 远高于任何更粗层级方案，故 z=8、宽度 2。
    response = client.post(ENDPOINT, json=payload())
    assert response.status_code == 200
    body = response.json()

    assert body["k"] == 2
    assert body["max_zoom"] == 8
    assert body["max_window_hours"] == 4
    assert body["total_records"] == 4

    assert body["groups"] == [
        {
            "z": 8, "x": 206, "y": 82,
            "window_start": "2026-03-15T08:00:00Z",
            "window_end": "2026-03-15T10:00:00Z",
            "count": 2,
        },
        {
            "z": 8, "x": 201, "y": 84,
            "window_start": "2026-03-15T08:00:00Z",
            "window_end": "2026-03-15T10:00:00Z",
            "count": 2,
        },
    ]

    # 按输入顺序只回传 id、z/x/y 与半开 UTC 窗口。
    assert [r["id"] for r in body["records"]] == [
        "obs-001", "obs-002", "obs-003", "obs-004",
    ]
    for entry in body["records"]:
        assert set(entry) == {"id", "z", "x", "y", "window_start", "window_end"}
        assert entry["z"] == 8
        assert entry["window_start"] == "2026-03-15T08:00:00Z"
        assert entry["window_end"] == "2026-03-15T10:00:00Z"
    assert [body["records"][0]["x"], body["records"][0]["y"]] == [206, 82]
    assert [body["records"][2]["x"], body["records"][2]["y"]] == [201, 84]
    for group in body["groups"]:
        assert group["count"] >= 2


def test_no_original_coordinates_or_times_in_response():
    response = client.post(
        ENDPOINT,
        json=payload(
            records=[
                {**make_record("obs-001", 31.741, 110.682, "2026-03-15T08:12:00Z"),
                 "species": "金丝猴"},
            ],
            k=1,
        ),
    )
    assert response.status_code == 200
    text = response.text
    assert "latitude" not in text
    assert "longitude" not in text
    assert "observed_at" not in text
    assert "species" not in text
    assert "31.741" not in text
    assert "08:12" not in text


def test_groups_ordered_by_first_appearance_in_input():
    # 调换簇出现顺序，groups 应跟随首个成员的输入位置。
    records = [RECORDS[2], RECORDS[3], RECORDS[0], RECORDS[1]]
    response = client.post(ENDPOINT, json=payload(records=records))
    assert response.status_code == 200
    body = response.json()
    assert [(g["x"], g["y"]) for g in body["groups"]] == [(201, 84), (206, 82)]
    assert [r["id"] for r in body["records"]] == [
        "obs-003", "obs-004", "obs-001", "obs-002",
    ]


def test_single_record_k_one_reaches_max_zoom_and_hour_window():
    # k=1 时最细粒度（z=max_zoom、宽度 1）必然可行且分数最大。
    records = [
        make_record("obs-001", 31.741, 110.682, "2026-03-15T08:12:00Z"),
        make_record("obs-002", 31.744, 110.679, "2026-03-15T08:50:00Z"),
    ]
    response = client.post(
        ENDPOINT, json=payload(k=1, max_zoom=6, max_window_hours=4, records=records)
    )
    assert response.status_code == 200
    body = response.json()
    for entry in body["records"]:
        assert entry["z"] == 6
        assert entry["window_start"] == "2026-03-15T08:00:00Z"
        assert entry["window_end"] == "2026-03-15T09:00:00Z"


def test_root_cell_when_max_zoom_zero():
    response = client.post(
        ENDPOINT, json=payload(max_zoom=0, max_window_hours=24)
    )
    assert response.status_code == 200
    body = response.json()
    assert {g["z"] for g in body["groups"]} == {0}
    for group in body["groups"]:
        assert group["x"] == 0 and group["y"] == 0
    for entry in body["records"]:
        assert (entry["z"], entry["x"], entry["y"]) == (0, 0, 0)


def test_width_must_divide_day_when_merging_windows():
    # 两个空间簇在 z=6 分离、z=5 合并；每簇各含 UTC 08 时、11 时一条。
    # 上限 5 时整除 24 的候选宽度为 1/2/3/4：宽度 1/2 无法跨 08、11 合并；
    # 宽度 3 虽整除 24，但午夜对齐的边界是 06/09/12（11 时落入 [09,12)，
    # 与 08 时的 [06,09) 分离）仍不可行；只有宽度 4 把两小时并入 [08,12)。
    # z=7 × 宽度 4 的分数 16384×6 高于 z=5 × 宽度 1 的 1024×24。
    records = [
        make_record("a", 31.741, 110.682, "2026-03-15T08:05:00Z"),
        make_record("b", 31.744, 110.679, "2026-03-15T11:55:00Z"),
        make_record("c", 30.912, 102.971, "2026-03-15T08:10:00Z"),
        make_record("d", 30.908, 102.975, "2026-03-15T11:50:00Z"),
    ]
    response = client.post(
        ENDPOINT, json=payload(k=2, max_zoom=7, max_window_hours=5, records=records)
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["groups"]) == 2
    for group in body["groups"]:
        assert group["z"] == 7
        assert group["window_start"] == "2026-03-15T08:00:00Z"
        assert group["window_end"] == "2026-03-15T12:00:00Z"
        assert group["count"] == 2


def test_windows_aligned_from_midnight():
    # 同格 4 条，小时为 4、5、8、9，k=2、上限 4：宽度 1 不可行，
    # 宽度 2 可行（05 时并入从午夜对齐的 [04:00,06:00)，而非 [05:00,07:00)）。
    records = [
        make_record("a", 31.7, 110.6, "2026-03-15T04:10:00Z"),
        make_record("b", 31.8, 110.7, "2026-03-15T05:50:00Z"),
        make_record("c", 31.7, 110.6, "2026-03-15T08:10:00Z"),
        make_record("d", 31.8, 110.7, "2026-03-15T09:50:00Z"),
    ]
    response = client.post(
        ENDPOINT, json=payload(k=2, max_zoom=6, max_window_hours=4, records=records)
    )
    assert response.status_code == 200
    windows = [
        (g["window_start"], g["window_end"]) for g in response.json()["groups"]
    ]
    assert windows == [
        ("2026-03-15T04:00:00Z", "2026-03-15T06:00:00Z"),
        ("2026-03-15T08:00:00Z", "2026-03-15T10:00:00Z"),
    ]


def test_window_covering_last_slot_ends_at_midnight_next_day():
    records = [
        make_record("a", 31.7, 110.6, "2026-03-15T23:10:00Z"),
        make_record("b", 31.7, 110.6, "2026-03-15T23:50:00Z"),
    ]
    response = client.post(
        ENDPOINT, json=payload(k=2, max_zoom=6, max_window_hours=24, records=records)
    )
    assert response.status_code == 200
    group = response.json()["groups"][0]
    assert group["window_start"] == "2026-03-15T23:00:00Z"
    assert group["window_end"] == "2026-03-16T00:00:00Z"


def test_tie_break_prefers_shorter_window_then_larger_zoom():
    """z=1 时两个经度象限各含 08 时与 11 时一条：宽度 1 不可行、宽度 4
    才可行（分数 4 × 6 = 24）；z=0 时宽度 1 已可行（分数 1 × 24 = 24）。
    分数打平，按平局规则先取较短窗：选 z=0、宽度 1（两个单小时窗）。"""
    records = [
        make_record("a", 40.0, 10.0, "2026-03-15T08:10:00Z"),
        make_record("b", 41.0, 11.0, "2026-03-15T11:10:00Z"),
        make_record("c", 40.0, -10.0, "2026-03-15T08:20:00Z"),
        make_record("d", 41.0, -11.0, "2026-03-15T11:20:00Z"),
    ]
    response = client.post(
        ENDPOINT, json=payload(k=2, max_zoom=1, max_window_hours=4, records=records)
    )
    assert response.status_code == 200
    body = response.json()
    assert [(g["z"], g["window_start"][11:16], g["window_end"][11:16])
            for g in body["groups"]] == [
        (0, "08:00", "09:00"),
        (0, "11:00", "12:00"),
    ]


def test_grid_lines_assigned_to_north_or_east_cell():
    # lat=0 / lon=0 是 z=1 的网格线，按规则归入东北格 (x=1, y=0)。
    records = [
        make_record("a", 0.0, 0.0, "2026-03-15T08:00:00Z"),
        make_record("b", 0.0, 0.0, "2026-03-15T08:30:00Z"),
    ]
    response = client.post(
        ENDPOINT, json=payload(k=2, max_zoom=1, max_window_hours=1, records=records)
    )
    assert response.status_code == 200
    group = response.json()["groups"][0]
    assert (group["z"], group["x"], group["y"]) == (1, 1, 0)


def test_global_boundary_points_clamped_to_edge_cells():
    records = [
        make_record("a", 90.0, -180.0, "2026-03-15T08:00:00Z"),
        make_record("b", 90.0, -180.0, "2026-03-15T08:30:00Z"),
        make_record("c", -90.0, 180.0, "2026-03-15T08:00:00Z"),
        make_record("d", -90.0, 180.0, "2026-03-15T08:30:00Z"),
    ]
    response = client.post(
        ENDPOINT, json=payload(k=2, max_zoom=2, max_window_hours=1, records=records)
    )
    assert response.status_code == 200
    assert [(g["x"], g["y"]) for g in response.json()["groups"]] == [
        (0, 0), (3, 3),
    ]


def test_timezone_converted_before_grouping():
    # 本地 16:20+08:00 转 UTC 为 08:20，与 UTC 08 时记录同组。
    records = [
        make_record("a", 31.741, 110.682, "2026-03-15T08:12:00Z"),
        make_record("b", 31.744, 110.679, "2026-03-15T16:20:00+08:00"),
    ]
    response = client.post(
        ENDPOINT, json=payload(k=2, max_zoom=8, max_window_hours=1, records=records)
    )
    assert response.status_code == 200
    groups = response.json()["groups"]
    assert len(groups) == 1
    assert groups[0]["window_start"] == "2026-03-15T08:00:00Z"
    assert groups[0]["count"] == 2


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
    for bad_zoom in (True, "8", 8.5):
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


def test_422_when_id_invalid_or_duplicated():
    variants = [
        [{"latitude": 0, "longitude": 0, "observed_at": "2026-03-15T08:00:00Z"}],
        [make_record("", 0, 0, "2026-03-15T08:00:00Z")],
        [make_record("  ", 0, 0, "2026-03-15T08:00:00Z")],
        [{"id": 1, "latitude": 0, "longitude": 0,
          "observed_at": "2026-03-15T08:00:00Z"}],
    ]
    for records in variants:
        response = client.post(ENDPOINT, json=payload(k=1, records=records))
        assert response.status_code == 422
        assert "id" in response.json()["detail"]

    duplicated = RECORDS[:2]
    duplicated[1] = make_record("obs-001", 31.8, 110.7, "2026-03-15T09:00:00Z")
    response = client.post(ENDPOINT, json=payload(k=1, records=duplicated))
    assert response.status_code == 422
    assert "唯一" in response.json()["detail"]


def test_422_when_coordinate_invalid():
    variants = [
        [{"id": "a", "latitude": 90.1, "longitude": 0,
          "observed_at": "2026-03-15T08:00:00Z"}],
        [{"id": "a", "latitude": 0, "longitude": 180.1,
          "observed_at": "2026-03-15T08:00:00Z"}],
        [{"id": "a", "latitude": "0", "longitude": 0,
          "observed_at": "2026-03-15T08:00:00Z"}],
        [{"id": "a", "latitude": True, "longitude": 0,
          "observed_at": "2026-03-15T08:00:00Z"}],
    ]
    for records in variants:
        response = client.post(ENDPOINT, json=payload(k=1, records=records))
        assert response.status_code == 422


def test_422_when_coordinate_is_nan_or_infinity():
    # httpx 的 json= 无法序列化 NaN/Infinity，直接发送原始 JSON 文本。
    for bad_literal in ("NaN", "Infinity", "-Infinity"):
        for field in ("latitude", "longitude"):
            body = (
                '{"k": 1, "max_zoom": 1, "max_window_hours": 4, "records": ['
                '{"id": "obs-a", "latitude": %s, "longitude": %s,'
                ' "observed_at": "2026-03-15T08:00:00Z"}]}'
                % (
                    bad_literal if field == "latitude" else "10.0",
                    bad_literal if field == "longitude" else "10.0",
                )
            )
            response = client.post(
                ENDPOINT,
                content=body,
                headers={"Content-Type": "application/json"},
            )
            assert response.status_code == 422, (field, bad_literal)
            assert "有限数值" in response.json()["detail"]


def test_422_when_observed_at_invalid():
    variants = [
        [{"id": "a", "latitude": 0, "longitude": 0}],
        [{"id": "a", "latitude": 0, "longitude": 0, "observed_at": "not-a-time"}],
        # 不带时区的时刻必须拒绝。
        [{"id": "a", "latitude": 0, "longitude": 0,
          "observed_at": "2026-03-15T08:00:00"}],
    ]
    for records in variants:
        response = client.post(ENDPOINT, json=payload(k=1, records=records))
        assert response.status_code == 422
        assert "observed_at" in response.json()["detail"]


def test_422_when_records_span_multiple_utc_dates():
    records = [
        make_record("a", 31.7, 110.6, "2026-03-15T08:00:00Z"),
        make_record("b", 31.8, 110.7, "2026-03-16T08:00:00Z"),
    ]
    response = client.post(ENDPOINT, json=payload(k=1, records=records))
    assert response.status_code == 422
    assert "同一 UTC 日期" in response.json()["detail"]


def test_422_when_no_feasible_z_width_combination():
    # 08 时与 20 时各 1 条且空间分离：宽度上限 8 时窗口无法合并
    # （08//8=1、20//8=2），任何 z 都存在单元素联合组。
    records = [
        make_record("a", 31.7, 110.6, "2026-03-15T08:10:00Z"),
        make_record("b", 10.2, 90.3, "2026-03-15T20:10:00Z"),
    ]
    response = client.post(
        ENDPOINT, json=payload(k=2, max_zoom=12, max_window_hours=8, records=records)
    )
    assert response.status_code == 422
    assert "时空联合组" in response.json()["detail"]


def test_422_when_merge_needs_non_divisor_width():
    # 06 时与 09 时：非整除宽度 5（[05,10)）本可合并，但宽度必须整除 24；
    # 上限 5 时可用的整除宽度 1/2/3/4 都无法使二者同窗，故无方案。
    records = [
        make_record("a", 31.7, 110.6, "2026-03-15T06:10:00Z"),
        make_record("b", 31.8, 110.7, "2026-03-15T09:10:00Z"),
    ]
    response = client.post(
        ENDPOINT, json=payload(k=2, max_zoom=6, max_window_hours=5, records=records)
    )
    assert response.status_code == 422
    assert "时空联合组" in response.json()["detail"]


def test_422_on_missing_or_extra_body_fields():
    response = client.post(
        ENDPOINT,
        json={"k": 2, "max_zoom": 8, "records": []},
    )
    assert response.status_code == 422

    response = client.post(ENDPOINT, json={**payload(), "extra": 1})
    assert response.status_code == 422


def test_422_when_record_is_not_object():
    response = client.post(
        ENDPOINT, json=payload(k=1, records=[["not", "a", "dict"]])
    )
    assert response.status_code == 422


# ---------------------------------------------------------------- 服务发现

def test_root_lists_spacetime_endpoint():
    body = client.get("/").json()
    assert body["spacetime_anonymize"] == "/api/v1/spacetime-anonymize"
