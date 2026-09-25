"""两次发布的关联风险审计：对同一批观测记录两次公开所用的分组求交集。

两批已发布记录各自按一组公开字段（准标识符）形成等价组；同一个体若在
两次发布中的等价组交集不足 ``k`` 条，则即便每次发布单独满足 k-匿名，
交叉比对两次发布仍可重新识别该个体。
"""

from __future__ import annotations

from typing import Any

from .anonymizer import unprocessable


def _group_records(
    records: list[dict[str, Any]],
    id_field: str,
    group_fields: list[str],
    batch_label: str,
) -> tuple[list[str], dict[str, tuple[str, ...]]]:
    """校验一批记录并返回 (按出现顺序去重的 id 列表, id -> 分组签名)。

    分组签名为按 ``group_fields`` 顺序取出的字段值元组；空字段列表时
    所有记录同属一个组（空元组签名）。
    """
    if not records:
        raise unprocessable(f"{batch_label} 至少需要包含 1 条记录")

    if any(not field.strip() for field in group_fields):
        raise unprocessable(f"{batch_label} 的分组字段名不能为空字符串")
    if len(set(group_fields)) != len(group_fields):
        raise unprocessable(f"{batch_label} 的分组字段不能重复")

    ordered_ids: list[str] = []
    seen_ids: set[str] = set()
    signatures: dict[str, tuple[str, ...]] = {}

    for index, record in enumerate(records):
        obs_id = record.get(id_field)
        if not isinstance(obs_id, str) or not obs_id.strip():
            raise unprocessable(
                f"{batch_label} 第 {index + 1} 条记录的 id 字段 "
                f"{id_field!r} 必须是非空字符串"
            )
        if obs_id in seen_ids:
            raise unprocessable(
                f"{batch_label} 的 id 字段 {id_field!r} 在批内不唯一："
                f"{obs_id!r} 重复出现"
            )
        seen_ids.add(obs_id)
        ordered_ids.append(obs_id)

        values: list[str] = []
        for field in group_fields:
            if field not in record:
                raise unprocessable(
                    f"{batch_label} 记录 {obs_id!r} 缺少分组字段 {field!r}"
                )
            value = record[field]
            if not isinstance(value, str):
                raise unprocessable(
                    f"{batch_label} 记录 {obs_id!r} 的分组字段 {field!r} "
                    "必须是字符串"
                )
            values.append(value)
        signatures[obs_id] = tuple(values)

    return ordered_ids, signatures


def audit_intersections(
    records_a: list[dict[str, Any]],
    fields_a: list[str],
    records_b: list[dict[str, Any]],
    fields_b: list[str],
    id_field: str,
    k: int,
) -> list[dict[str, Any]]:
    """求两次分组按 id 的交集并标记关联风险。

    返回各交集单元（第一批等价组 × 第二批等价组中的非空格）：
    单元按第一批记录的首次出现顺序排列，单元内 id 同序。每个单元给出
    两侧字段值组合、成员数与 ``count < k`` 的风险标记。
    """
    if not id_field.strip():
        raise unprocessable("id_field 不能为空字符串")

    ids_a, sig_a = _group_records(records_a, id_field, fields_a, "第一批记录")
    ids_b, sig_b = _group_records(records_b, id_field, fields_b, "第二批记录")

    set_a, set_b = set(ids_a), set(ids_b)
    if set_a != set_b:
        only_a = sorted(set_a - set_b)
        only_b = sorted(set_b - set_a)
        details = []
        if only_a:
            details.append(f"仅第一批出现: {only_a}")
        if only_b:
            details.append(f"仅第二批出现: {only_b}")
        raise unprocessable("两批记录的 id 集合必须相同（" + "；".join(details) + "）")

    # ids_a 去重且与 ids_b 同集合，以第一批顺序驱动全部输出顺序：
    # 单元顺序与单元内 id 顺序都只取决于第一批的首次出现顺序。
    members: dict[tuple[tuple[str, ...], tuple[str, ...]], list[str]] = {}
    order: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
    for obs_id in ids_a:
        cell = (sig_a[obs_id], sig_b[obs_id])
        if cell not in members:
            members[cell] = []
            order.append(cell)
        members[cell].append(obs_id)

    intersections: list[dict[str, Any]] = []
    for cell in order:
        signature_a, signature_b = cell
        ids = members[cell]
        intersections.append(
            {
                "ids": ids,
                "count": len(ids),
                "values_a": dict(zip(fields_a, signature_a)),
                "values_b": dict(zip(fields_b, signature_b)),
                "at_risk": len(ids) < k,
            }
        )

    return intersections
