"""两次发布关联风险审计接口 POST /api/v1/audit-intersection 的端到端测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

ENDPOINT = "/api/v1/audit-intersection"


def make_a(obs_id: str, reserve: str) -> dict[str, str]:
    return {"id": obs_id, "reserve": reserve}


def make_b(obs_id: str, weather: str) -> dict[str, str]:
    return {"id": obs_id, "weather": weather}


RECORDS_A = [
    make_a("obs-001", "神农架"),
    make_a("obs-002", "神农架"),
    make_a("obs-003", "卧龙"),
    make_a("obs-004", "卧龙"),
]

RECORDS_B = [
    make_b("obs-001", "晴"),
    make_b("obs-002", "阴"),
    make_b("obs-003", "晴"),
    make_b("obs-004", "阴"),
]


def payload(k: int = 2, **overrides) -> dict:
    body = {
        "records_a": RECORDS_A,
        "fields_a": ["reserve"],
        "records_b": RECORDS_B,
        "fields_b": ["weather"],
        "id_field": "id",
        "k": k,
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------- 基本功能

def test_intersection_basic_cells_and_risk_flags():
    # 两批各自每组 2 条（单独看满足 k=2），但 2x2 交叉后每个交集只有 1 条。
    response = client.post(ENDPOINT, json=payload(k=2))
    assert response.status_code == 200
    body = response.json()

    assert body["k"] == 2
    assert body["total_records"] == 4
    assert body["risk_count"] == 4

    intersections = body["intersections"]
    assert len(intersections) == 4
    assert [cell["ids"] for cell in intersections] == [
        ["obs-001"],
        ["obs-002"],
        ["obs-003"],
        ["obs-004"],
    ]
    assert intersections[0] == {
        "ids": ["obs-001"],
        "count": 1,
        "values_a": {"reserve": "神农架"},
        "values_b": {"weather": "晴"},
        "at_risk": True,
    }
    assert all(cell["count"] == 1 for cell in intersections)
    assert all(cell["at_risk"] for cell in intersections)


def test_safe_when_every_intersection_meets_k():
    # 第二次发布全部为同一值：交集即第一批分组，各 2 条，k=2 时无风险。
    records_b = [make_b(obs_id, "晴") for obs_id in ("obs-001", "obs-002",
                                                     "obs-003", "obs-004")]
    response = client.post(ENDPOINT, json=payload(k=2, records_b=records_b))
    assert response.status_code == 200
    body = response.json()

    assert body["risk_count"] == 0
    assert [cell["ids"] for cell in body["intersections"]] == [
        ["obs-001", "obs-002"],
        ["obs-003", "obs-004"],
    ]
    first = body["intersections"][0]
    assert first["count"] == 2
    assert first["values_a"] == {"reserve": "神农架"}
    assert first["values_b"] == {"weather": "晴"}
    assert first["at_risk"] is False


def test_mixed_risk_and_safe_cells():
    # 神农架的 2 人天气相同 -> 安全交集 2 条；卧龙 2 人天气不同 -> 两个 1 条风险交集。
    records_b = [
        make_b("obs-001", "晴"),
        make_b("obs-002", "晴"),
        make_b("obs-003", "晴"),
        make_b("obs-004", "阴"),
    ]
    response = client.post(ENDPOINT, json=payload(k=2, records_b=records_b))
    assert response.status_code == 200
    body = response.json()

    cells = body["intersections"]
    assert [cell["ids"] for cell in cells] == [
        ["obs-001", "obs-002"],
        ["obs-003"],
        ["obs-004"],
    ]
    assert [cell["at_risk"] for cell in cells] == [False, True, True]
    assert body["risk_count"] == 2


def test_multi_field_signatures():
    records_a = [
        {"id": "o1", "reserve": "神农架", "season": "春"},
        {"id": "o2", "reserve": "神农架", "season": "春"},
        {"id": "o3", "reserve": "神农架", "season": "秋"},
        {"id": "o4", "reserve": "卧龙",   "season": "秋"},
    ]
    records_b = [
        {"id": "o1", "weather": "晴"},
        {"id": "o2", "weather": "晴"},
        {"id": "o3", "weather": "晴"},
        {"id": "o4", "weather": "晴"},
    ]
    response = client.post(
        ENDPOINT,
        json={
            "records_a": records_a,
            "fields_a": ["reserve", "season"],
            "records_b": records_b,
            "fields_b": ["weather"],
            "id_field": "id",
            "k": 2,
        },
    )
    assert response.status_code == 200
    cells = response.json()["intersections"]
    assert [cell["ids"] for cell in cells] == [["o1", "o2"], ["o3"], ["o4"]]
    assert cells[0]["values_a"] == {"reserve": "神农架", "season": "春"}
    assert cells[0]["values_b"] == {"weather": "晴"}
    assert cells[1]["values_a"] == {"reserve": "神农架", "season": "秋"}


def test_cell_order_follows_first_batch_first_occurrence():
    # 第二批乱序排列不应影响输出顺序；交集顺序与单元内 id 顺序都由第一批决定。
    records_b_shuffled = [RECORDS_B[i] for i in (3, 1, 0, 2)]
    response = client.post(
        ENDPOINT, json=payload(k=2, records_b=records_b_shuffled)
    )
    assert response.status_code == 200
    assert [cell["ids"] for cell in response.json()["intersections"]] == [
        ["obs-001"],
        ["obs-002"],
        ["obs-003"],
        ["obs-004"],
    ]


def test_ids_within_cell_follow_first_batch_order():
    records_a = [
        make_a("zeta", "神农架"),
        make_a("alpha", "神农架"),
    ]
    records_b = [
        make_b("alpha", "晴"),
        make_b("zeta", "晴"),
    ]
    response = client.post(
        ENDPOINT,
        json={
            "records_a": records_a,
            "fields_a": ["reserve"],
            "records_b": records_b,
            "fields_b": ["weather"],
            "id_field": "id",
            "k": 2,
        },
    )
    assert response.status_code == 200
    cells = response.json()["intersections"]
    assert len(cells) == 1
    # 单元内 id 顺序按第一批记录的出现顺序，而非第二批或字典序。
    assert cells[0]["ids"] == ["zeta", "alpha"]


def test_empty_field_lists_put_all_in_one_cell():
    response = client.post(
        ENDPOINT, json=payload(k=4, fields_a=[], fields_b=[])
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["intersections"]) == 1
    cell = body["intersections"][0]
    assert cell["ids"] == ["obs-001", "obs-002", "obs-003", "obs-004"]
    assert cell["count"] == 4
    assert cell["values_a"] == {}
    assert cell["values_b"] == {}
    assert cell["at_risk"] is False
    assert body["risk_count"] == 0


def test_k_one_never_at_risk():
    response = client.post(ENDPOINT, json=payload(k=1))
    assert response.status_code == 200
    body = response.json()
    assert body["risk_count"] == 0
    assert all(not cell["at_risk"] for cell in body["intersections"])


def test_k_larger_than_all_cells_marks_everything():
    # k 大于记录总数不是参数错误：所有交集必然不足 k，全部报险。
    response = client.post(ENDPOINT, json=payload(k=99))
    assert response.status_code == 200
    body = response.json()
    assert body["risk_count"] == 4
    assert all(cell["at_risk"] for cell in body["intersections"])


def test_id_field_may_have_any_name_and_extra_fields_ignored():
    records_a = [
        {"uuid": "x1", "reserve": "神农架", "note": "a"},
        {"uuid": "x2", "reserve": "神农架", "note": "b"},
    ]
    records_b = [
        {"uuid": "x1", "weather": "晴", "note": "c"},
        {"uuid": "x2", "weather": "晴", "note": "d"},
    ]
    response = client.post(
        ENDPOINT,
        json={
            "records_a": records_a,
            "fields_a": ["reserve"],
            "records_b": records_b,
            "fields_b": ["weather"],
            "id_field": "uuid",
            "k": 2,
        },
    )
    assert response.status_code == 200
    cells = response.json()["intersections"]
    assert len(cells) == 1
    assert cells[0]["ids"] == ["x1", "x2"]
    assert cells[0]["at_risk"] is False


def test_id_field_may_also_be_used_as_group_field():
    # id 同时作为公开分组字段时每个签名唯一，交集必然为单条 -> k=2 全部报险。
    response = client.post(
        ENDPOINT,
        json=payload(k=2, fields_a=["id"], fields_b=["id"]),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["risk_count"] == 4
    assert body["intersections"][0]["values_a"] == {"id": "obs-001"}
    assert body["intersections"][0]["values_b"] == {"id": "obs-001"}


def test_empty_string_group_value_is_allowed():
    # 分组字段值只要求存在且为字符串，空字符串也是合法值。
    records_a = [
        {"id": "o1", "reserve": ""},
        {"id": "o2", "reserve": ""},
    ]
    records_b = [
        {"id": "o1", "weather": "晴"},
        {"id": "o2", "weather": "晴"},
    ]
    response = client.post(
        ENDPOINT,
        json={
            "records_a": records_a,
            "fields_a": ["reserve"],
            "records_b": records_b,
            "fields_b": ["weather"],
            "id_field": "id",
            "k": 2,
        },
    )
    assert response.status_code == 200
    cells = response.json()["intersections"]
    assert cells[0]["values_a"] == {"reserve": ""}
    assert cells[0]["count"] == 2


# ---------------------------------------------------------------- 422 校验

def assert_422(body_overrides: dict) -> None:
    response = client.post(ENDPOINT, json=payload(**body_overrides))
    assert response.status_code == 422
    assert "detail" in response.json()


def test_422_when_k_not_positive():
    for bad_k in (0, -1):
        assert_422({"k": bad_k})


def test_422_when_k_is_bool_or_wrong_type():
    for bad_k in (True, "2", 2.5, None):
        response = client.post(ENDPOINT, json=payload(k=bad_k))
        assert response.status_code == 422


def test_422_when_id_missing():
    records_a = [dict(record) for record in RECORDS_A]
    del records_a[0]["id"]
    assert_422({"records_a": records_a})


def test_422_when_id_not_string():
    for bad_id in (123, None, ["obs-001"], {"x": 1}, True):
        records_a = [dict(record) for record in RECORDS_A]
        records_a[0]["id"] = bad_id
        assert_422({"records_a": records_a})


def test_422_when_id_empty_or_blank():
    for bad_id in ("", "   "):
        records_a = [dict(record) for record in RECORDS_A]
        records_a[0]["id"] = bad_id
        assert_422({"records_a": records_a})


def test_422_when_id_not_unique_within_batch():
    records_a = [dict(record) for record in RECORDS_A]
    records_a[2]["id"] = "obs-001"
    response = client.post(ENDPOINT, json=payload(records_a=records_a))
    assert response.status_code == 422
    assert "不唯一" in response.json()["detail"]


def test_422_when_id_sets_differ():
    records_a = [dict(record) for record in RECORDS_A]
    records_a[0]["id"] = "obs-999"
    response = client.post(ENDPOINT, json=payload(records_a=records_a))
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "id 集合" in detail
    assert "obs-999" in detail


def test_422_when_second_batch_has_extra_id():
    records_b = RECORDS_B + [make_b("obs-009", "晴")]
    response = client.post(
        ENDPOINT, json=payload(records_b=records_b)
    )
    assert response.status_code == 422
    assert "obs-009" in response.json()["detail"]


def test_422_when_group_field_missing():
    records_a = [dict(record) for record in RECORDS_A]
    del records_a[1]["reserve"]
    response = client.post(ENDPOINT, json=payload(records_a=records_a))
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "缺少分组字段" in detail
    assert "reserve" in detail


def test_422_when_group_field_not_string():
    for bad_value in (123, None, ["神农架"], {"v": 1}, True):
        records_a = [dict(record) for record in RECORDS_A]
        records_a[0]["reserve"] = bad_value
        response = client.post(ENDPOINT, json=payload(records_a=records_a))
        assert response.status_code == 422
        assert "必须是字符串" in response.json()["detail"]


def test_422_when_second_batch_group_field_missing():
    records_b = [dict(record) for record in RECORDS_B]
    del records_b[0]["weather"]
    response = client.post(ENDPOINT, json=payload(records_b=records_b))
    assert response.status_code == 422
    assert "缺少分组字段" in response.json()["detail"]


def test_422_when_id_field_empty():
    assert_422({"id_field": ""})


def test_422_when_id_field_blank():
    response = client.post(ENDPOINT, json=payload(id_field="   "))
    assert response.status_code == 422


def test_422_when_group_field_name_empty():
    assert_422({"fields_a": [""]})
    assert_422({"fields_b": ["", "weather"]})


def test_422_when_group_fields_repeated():
    response = client.post(
        ENDPOINT, json=payload(fields_a=["reserve", "reserve"])
    )
    assert response.status_code == 422
    assert "不能重复" in response.json()["detail"]


def test_422_when_batch_empty():
    assert_422({"records_a": []})
    assert_422({"records_b": []})


def test_422_when_field_list_wrong_element_type():
    response = client.post(ENDPOINT, json=payload(fields_a=["reserve", 1]))
    assert response.status_code == 422


def test_422_when_records_wrong_type():
    response = client.post(
        ENDPOINT, json=payload(records_a="not-a-list")
    )
    assert response.status_code == 422


def test_422_on_missing_body_fields():
    response = client.post(ENDPOINT, json={"k": 2})
    assert response.status_code == 422


def test_422_on_extra_body_field():
    response = client.post(ENDPOINT, json=payload(extra=1))
    assert response.status_code == 422


# ---------------------------------------------------------------- 既有服务不受影响

def test_existing_endpoints_still_available():
    assert client.get("/api/v1/health").status_code == 200
    root = client.get("/").json()
    assert root["audit_intersection"] == "/api/v1/audit-intersection"
