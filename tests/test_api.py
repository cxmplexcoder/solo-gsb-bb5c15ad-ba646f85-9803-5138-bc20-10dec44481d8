"""API 与匿名化算法的端到端测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


# ---------------------------------------------------------------- 请求样例

SPECIES_HIERARCHY = {
    "金丝猴": ["金丝猴", "灵长类", "哺乳动物", "*"],
    "林麝": ["林麝", "有蹄类", "哺乳动物", "*"],
    "云豹": ["云豹", "食肉目", "哺乳动物", "*"],
}

RESERVE_HIERARCHY = {
    "神农架-老君山": ["神农架-老君山", "神农架", "湖北", "*"],
    "神农架-大九湖": ["神农架-大九湖", "神农架", "湖北", "*"],
    "卧龙-邓生": ["卧龙-邓生", "卧龙", "四川", "*"],
    "卧龙-耿达": ["卧龙-耿达", "卧龙", "四川", "*"],
}


def make_record(obs_id: str, species: str, reserve: str) -> dict[str, str]:
    return {"id": obs_id, "species": species, "reserve": reserve}


RECORDS = [
    make_record("obs-001", "金丝猴", "神农架-老君山"),
    make_record("obs-002", "金丝猴", "神农架-老君山"),
    make_record("obs-003", "金丝猴", "神农架-大九湖"),
    make_record("obs-004", "林麝", "神农架-大九湖"),
    make_record("obs-005", "林麝", "卧龙-邓生"),
    make_record("obs-006", "林麝", "卧龙-邓生"),
    make_record("obs-007", "云豹", "卧龙-耿达"),
    make_record("obs-008", "云豹", "卧龙-耿达"),
]

FIELDS = {
    "species": SPECIES_HIERARCHY,
    "reserve": RESERVE_HIERARCHY,
}


def payload(k: int = 3, records=None, fields=None) -> dict:
    return {"k": k, "records": records if records is not None else RECORDS,
            "fields": fields if fields is not None else FIELDS}


# ---------------------------------------------------------------- 基本功能

def test_health():
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_selects_minimal_levels_and_generalizes():
    response = client.post("/api/v1/anonymize", json=payload(k=3))
    assert response.status_code == 200
    body = response.json()

    # (species=2, reserve=1) 是唯一的和最小可行方案：哺乳动物|神农架、哺乳动物|卧龙 各 4 条。
    assert body["selected_levels"] == {"reserve": 1, "species": 2}
    assert body["k"] == 3
    assert body["total_records"] == 8

    for group in body["groups"]:
        assert group["count"] >= 3
    counts = sorted(group["count"] for group in body["groups"])
    assert counts == [4, 4]

    # 泛化记录数与输入一致，非匿名字段原样保留。
    assert len(body["generalized_records"]) == len(RECORDS)
    first = body["generalized_records"][0]
    assert first["id"] == "obs-001"
    assert first["species"] == "哺乳动物"
    assert first["reserve"] == "神农架"

    # 原始值不应再出现在匿名字段中（该方案下）。
    for record in body["generalized_records"]:
        assert record["species"] not in {"金丝猴", "林麝", "云豹"}
        assert record["reserve"] in {"神农架", "卧龙"}


def test_k_equals_record_count_uses_wildcards():
    response = client.post("/api/v1/anonymize", json=payload(k=8))
    assert response.status_code == 200
    body = response.json()
    # 所有 8 条必须并入同一组。最小和方案是 (reserve=3, species=2)：species 2 级
    # “哺乳动物”已覆盖全部物种，reserve 升到通配符即可，和为 5；其余合并为单组
    # 的方案（如 reserve=2、species=3，或双方都到 3 级）层级和都更大。
    assert body["selected_levels"] == {"reserve": 3, "species": 2}
    assert len(body["groups"]) == 1
    assert body["groups"][0]["values"] == {"reserve": "*", "species": "哺乳动物"}
    assert body["groups"][0]["count"] == 8


def test_level_zero_when_already_k_anonymous():
    # 每种 (species, reserve) 组合都恰好出现 2 次，k=2 时无需泛化。
    balanced = [
        make_record("obs-001", "金丝猴", "神农架-老君山"),
        make_record("obs-002", "金丝猴", "神农架-老君山"),
        make_record("obs-003", "林麝", "卧龙-邓生"),
        make_record("obs-004", "林麝", "卧龙-邓生"),
        make_record("obs-005", "云豹", "卧龙-耿达"),
        make_record("obs-006", "云豹", "卧龙-耿达"),
    ]
    response = client.post(
        "/api/v1/anonymize", json=payload(k=2, records=balanced)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["selected_levels"] == {"reserve": 0, "species": 0}
    assert body["generalized_records"][0]["species"] == "金丝猴"
    assert body["generalized_records"][0]["reserve"] == "神农架-老君山"


def test_no_fields_passes_records_through():
    response = client.post(
        "/api/v1/anonymize",
        json={"k": 1, "records": RECORDS, "fields": {}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["selected_levels"] == {}
    assert len(body["groups"]) == 1
    assert body["groups"][0]["values"] == {}
    assert body["groups"][0]["count"] == 8


def test_single_field_anonymous():
    fields = {"species": SPECIES_HIERARCHY}
    response = client.post(
        "/api/v1/anonymize",
        json={"k": 3, "records": RECORDS, "fields": fields},
    )
    assert response.status_code == 200
    body = response.json()
    # 灵长类 3、有蹄类 3、食肉目 2 -> 需泛化到 2 级：哺乳动物 8。
    assert body["selected_levels"] == {"species": 2}
    assert body["groups"] == [{"values": {"species": "哺乳动物"}, "count": 8}]


# ---------------------------------------------------------------- 同和字典序裁决

def test_tie_break_lexicographic_by_field_name():
    """构造 (1,2) 与 (2,1) 同和且均可行、更小和均不可行的场景。

    两个字段各有 4 个原值，1 级两两合并：
        loc:  {a1,a2}->X, {a3,a4}->Y, 2 级为统一通配符 *
        area: {b1,b2}->B, {b3,b4}->C, 2 级为统一通配符 *
    4 条记录（k=2）：(a1,b1) (a2,b3) (a3,b2) (a4,b4)。

    - (1,1) 的四个交叉块 X/B=1、X/C=1、Y/B=1、Y/C=1，均不足 2；
    - (0,1)/(0,2) 因 a1、a4 原行只有 1 条而失败；
    - (1,0)/(2,0) 因 b1、b4 原列只有 1 条而失败；
    - (1,2) 按 loc 通配符合并：B 组 2 条、C 组 2 条，可行（和 3）；
    - (2,1) 按 area 通配符合并：X 组 2 条、Y 组 2 条，可行（和 3）。
    按字段名排序 [area, loc] 后字典序 (1,2) < (2,1)，
    即 area 取 1 级、loc 取 2 级。
    """
    records = [
        {"loc": "a1", "area": "b1"},
        {"loc": "a2", "area": "b3"},
        {"loc": "a3", "area": "b2"},
        {"loc": "a4", "area": "b4"},
    ]
    fields = {
        "area": {
            "b1": ["b1", "B", "*"],
            "b2": ["b2", "B", "*"],
            "b3": ["b3", "C", "*"],
            "b4": ["b4", "C", "*"],
        },
        "loc": {
            "a1": ["a1", "X", "*"],
            "a2": ["a2", "X", "*"],
            "a3": ["a3", "Y", "*"],
            "a4": ["a4", "Y", "*"],
        },
    }

    response = client.post(
        "/api/v1/anonymize",
        json={"k": 2, "records": records, "fields": fields},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["selected_levels"] == {"area": 1, "loc": 2}
    for group in body["groups"]:
        assert group["count"] >= 2


# ---------------------------------------------------------------- 422 校验

def test_422_when_total_less_than_k():
    response = client.post("/api/v1/anonymize", json=payload(k=9))
    assert response.status_code == 422
    assert "小于 k" in response.json()["detail"]


def test_422_when_k_not_positive():
    response = client.post("/api/v1/anonymize", json=payload(k=0))
    assert response.status_code == 422


def test_422_when_k_is_bool():
    response = client.post(
        "/api/v1/anonymize",
        json={"k": True, "records": RECORDS, "fields": FIELDS},
    )
    assert response.status_code == 422


def test_422_when_mapping_missing_value():
    records = RECORDS + [make_record("obs-009", "大熊猫", "卧龙-耿达")]
    response = client.post(
        "/api/v1/anonymize", json=payload(k=2, records=records)
    )
    assert response.status_code == 422
    assert "映射缺少" in response.json()["detail"]


def test_422_when_value_not_string():
    records = [dict(record) for record in RECORDS]
    records[0]["species"] = 123
    response = client.post(
        "/api/v1/anonymize", json=payload(k=2, records=records)
    )
    assert response.status_code == 422
    assert "映射缺少" in response.json()["detail"]


def test_422_when_value_is_unhashable_object():
    # list / dict 等非原子原值也必须返回 422，而不是 500。
    for bad_value in (["金丝猴"], {"nested": 1}):
        response = client.post(
            "/api/v1/anonymize",
            json={
                "k": 1,
                "records": [{"species": bad_value}, {"species": "金丝猴"}],
                "fields": {"species": {"金丝猴": ["金丝猴", "*"]}},
            },
        )
        assert response.status_code == 422
        assert "映射缺少" in response.json()["detail"]


def test_422_when_more_than_three_fields():
    fields = dict(FIELDS)
    fields["extra1"] = {"x": ["x", "*"]}
    fields["extra2"] = {"y": ["y", "*"]}
    response = client.post(
        "/api/v1/anonymize",
        json={"k": 1, "records": [{"f": "v"}], "fields": fields},
    )
    assert response.status_code == 422
    assert "最多包含 3 个" in response.json()["detail"]


def test_422_when_wildcard_not_unified():
    fields = {
        "species": {
            "金丝猴": ["金丝猴", "灵长类", "哺乳动物", "*"],
            "林麝": ["林麝", "有蹄类", "哺乳动物", "其他"],
            "云豹": ["云豹", "食肉目", "哺乳动物", "*"],
        }
    }
    response = client.post(
        "/api/v1/anonymize", json=payload(k=2, fields=fields)
    )
    assert response.status_code == 422
    assert "通配符" in response.json()["detail"]


def test_422_when_paths_have_different_lengths():
    fields = {
        "species": {
            "金丝猴": ["金丝猴", "灵长类", "哺乳动物", "*"],
            "林麝": ["林麝", "哺乳动物", "*"],
            "云豹": ["云豹", "食肉目", "哺乳动物", "*"],
        }
    }
    response = client.post(
        "/api/v1/anonymize", json=payload(k=2, fields=fields)
    )
    assert response.status_code == 422
    assert "等长" in response.json()["detail"]


def test_422_when_more_than_four_levels():
    fields = {"species": {"金丝猴": ["金丝猴", "猴", "灵长类", "哺乳动物", "*"]}}
    response = client.post(
        "/api/v1/anonymize",
        json={"k": 1, "records": [RECORDS[0]], "fields": fields},
    )
    assert response.status_code == 422
    assert "1 到 4" in response.json()["detail"]


def test_422_when_first_path_item_differs_from_key():
    fields = {
        "species": {
            "金丝猴": ["川金丝猴", "灵长类", "哺乳动物", "*"],
            "林麝": ["林麝", "有蹄类", "哺乳动物", "*"],
            "云豹": ["云豹", "食肉目", "哺乳动物", "*"],
        }
    }
    response = client.post(
        "/api/v1/anonymize", json=payload(k=2, fields=fields)
    )
    assert response.status_code == 422
    assert "首项必须等于原值" in response.json()["detail"]


def test_422_on_empty_records():
    response = client.post(
        "/api/v1/anonymize", json={"k": 1, "records": [], "fields": {}}
    )
    assert response.status_code == 422


def test_422_on_missing_body_fields():
    response = client.post("/api/v1/anonymize", json={"k": 2})
    assert response.status_code == 422


# ---------------------------------------------------------------- l-多样性

# 在 RECORDS 基础上追加敏感字段 health：神农架组（obs-001~004）敏感值单一，
# 卧龙组（obs-005~008）含两种敏感值。
HEALTH_RECORDS = [
    {**record, "health": health}
    for record, health in zip(
        RECORDS,
        ["健康", "健康", "健康", "健康", "健康", "患病", "健康", "患病"],
    )
]


def diversity_payload(k: int = 3, l: int = 2, records=None) -> dict:
    body = payload(k=k, records=records if records is not None else HEALTH_RECORDS)
    body["sensitive_field"] = "health"
    body["l"] = l
    return body


def test_without_diversity_params_behavior_unchanged():
    # 未提供 sensitive_field / l 时，即使记录带有敏感字段也保持原有行为。
    response = client.post(
        "/api/v1/anonymize", json=payload(k=3, records=HEALTH_RECORDS)
    )
    assert response.status_code == 200
    assert response.json()["selected_levels"] == {"reserve": 1, "species": 2}


def test_l_diversity_forces_higher_levels():
    """k-only 最优 (reserve=1, species=2) 的“神农架”组 4 条记录敏感值全为
    “健康”，不满足 l=2；满足两项约束的最小方案是 (reserve=3, species=2)：
    全部 8 条并入一组，含 2 种敏感值。"""
    response = client.post("/api/v1/anonymize", json=diversity_payload(k=3, l=2))
    assert response.status_code == 200
    body = response.json()
    assert body["selected_levels"] == {"reserve": 3, "species": 2}
    assert body["groups"] == [
        {"values": {"reserve": "*", "species": "哺乳动物"}, "count": 8}
    ]
    # 敏感字段不属于待匿名字段，原样保留。
    assert body["generalized_records"][0]["health"] == "健康"


def test_l_one_matches_k_only_result():
    # l=1 时多样性约束恒成立（敏感值均为非空字符串），结果与仅 k-匿名一致。
    only_k = client.post(
        "/api/v1/anonymize", json=payload(k=3, records=HEALTH_RECORDS)
    ).json()
    with_l = client.post(
        "/api/v1/anonymize", json=diversity_payload(k=3, l=1)
    ).json()
    assert with_l["selected_levels"] == {"reserve": 1, "species": 2}
    assert with_l["selected_levels"] == only_k["selected_levels"]
    assert with_l["groups"] == only_k["groups"]


def test_tie_break_lexicographic_with_diversity():
    """多样性约束启用后仍沿用“层级和最小、同和按字段名排序字典序”的裁决。

    结构同 test_tie_break_lexicographic_by_field_name，追加敏感值使
    (area=1, loc=2) 与 (area=2, loc=1) 均同时满足 k=2、l=2，
    字典序 (1,2) < (2,1)，应选 area=1、loc=2。
    """
    records = [
        {"loc": "a1", "area": "b1", "s": "s1"},
        {"loc": "a2", "area": "b3", "s": "s2"},
        {"loc": "a3", "area": "b2", "s": "s2"},
        {"loc": "a4", "area": "b4", "s": "s1"},
    ]
    fields = {
        "area": {
            "b1": ["b1", "B", "*"],
            "b2": ["b2", "B", "*"],
            "b3": ["b3", "C", "*"],
            "b4": ["b4", "C", "*"],
        },
        "loc": {
            "a1": ["a1", "X", "*"],
            "a2": ["a2", "X", "*"],
            "a3": ["a3", "Y", "*"],
            "a4": ["a4", "Y", "*"],
        },
    }

    response = client.post(
        "/api/v1/anonymize",
        json={
            "k": 2,
            "records": records,
            "fields": fields,
            "sensitive_field": "s",
            "l": 2,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["selected_levels"] == {"area": 1, "loc": 2}
    for group in body["groups"]:
        assert group["count"] >= 2


def test_422_when_diversity_infeasible():
    # 全部记录只有 2 种敏感值，l=3 时任何组合（含全通配符单组）都不可行。
    response = client.post("/api/v1/anonymize", json=diversity_payload(k=3, l=3))
    assert response.status_code == 422
    assert "不存在" in response.json()["detail"]


def test_422_when_diversity_params_not_paired():
    only_field = client.post(
        "/api/v1/anonymize",
        json={**payload(k=2), "sensitive_field": "health"},
    )
    assert only_field.status_code == 422
    assert "同时提供" in only_field.json()["detail"]

    only_l = client.post("/api/v1/anonymize", json={**payload(k=2), "l": 2})
    assert only_l.status_code == 422
    assert "同时提供" in only_l.json()["detail"]


def test_422_when_sensitive_field_overlaps_fields():
    response = client.post(
        "/api/v1/anonymize",
        json={**payload(k=2), "sensitive_field": "species", "l": 2},
    )
    assert response.status_code == 422
    assert "重合" in response.json()["detail"]


def test_422_when_sensitive_field_empty():
    response = client.post(
        "/api/v1/anonymize",
        json={**payload(k=2), "sensitive_field": "", "l": 2},
    )
    assert response.status_code == 422


def test_422_when_l_not_positive_integer():
    for bad_l in (0, -2, True, "2", 2.5):
        response = client.post(
            "/api/v1/anonymize",
            json=diversity_payload(k=2, l=bad_l),
        )
        assert response.status_code == 422


def test_422_when_sensitive_value_not_non_empty_string():
    variants = []
    # 缺失敏感字段
    missing = [dict(record) for record in HEALTH_RECORDS]
    del missing[0]["health"]
    variants.append(missing)
    # 各种非法敏感值
    for bad_value in ("", "   ", 123, None, ["健康"], {"v": 1}):
        records = [dict(record) for record in HEALTH_RECORDS]
        records[3]["health"] = bad_value
        variants.append(records)

    for records in variants:
        response = client.post(
            "/api/v1/anonymize",
            json=diversity_payload(k=2, records=records),
        )
        assert response.status_code == 422
        assert "非空字符串" in response.json()["detail"]


# ---------------------------------------------------------------- 持久化

def test_no_records_written_to_disk(tmp_path, monkeypatch):
    """处理请求前后工作目录下不应出现任何数据文件（服务纯内存）。"""
    monkeypatch.chdir(tmp_path)
    response = client.post("/api/v1/anonymize", json=payload(k=3))
    assert response.status_code == 200
    assert list(tmp_path.rglob("*")) == []
