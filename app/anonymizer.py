"""匿名化核心逻辑：在统一层级泛化下满足 k-匿名（可选 l-多样性），最小化层级序号之和。"""

from __future__ import annotations

from collections import Counter
from itertools import product
from typing import Any

from fastapi import HTTPException


def unprocessable(detail: str) -> HTTPException:
    """构造 422 异常。"""
    return HTTPException(status_code=422, detail=detail)


def prepare_sensitive(
    records: list[dict[str, Any]],
    fields: dict[str, dict[str, list[str]]],
    sensitive_field: str | None,
    l: int | None,
) -> list[str] | None:
    """校验可选的敏感属性多样性（l-多样性）参数。

    ``sensitive_field`` 与 ``l`` 必须同时提供或同时省略。启用时返回与
    ``records`` 等长、逐条对应的敏感值列表；未启用时返回 ``None``。
    """
    if (sensitive_field is None) != (l is None):
        raise unprocessable("sensitive_field 与 l 必须同时提供，或同时省略")
    if sensitive_field is None or l is None:
        return None

    if l < 1:
        raise unprocessable("l 必须是正整数（>= 1）")
    if not sensitive_field.strip():
        raise unprocessable("sensitive_field 不能为空字符串")
    if sensitive_field in fields:
        raise unprocessable(
            f"敏感字段 {sensitive_field!r} 不得与待匿名字段重合"
        )

    values: list[str] = []
    for record in records:
        value = record.get(sensitive_field)
        if not isinstance(value, str) or not value.strip():
            raise unprocessable(
                f"每条记录的敏感字段 {sensitive_field!r} 都必须是非空字符串"
            )
        values.append(value)
    return values


def prepare_hierarchies(
    records: list[dict[str, Any]],
    fields: dict[str, dict[str, list[str]]],
) -> tuple[list[str], dict[str, dict[int, dict[str, str]]]]:
    """校验请求并返回 (按字段名排序的字段列表, 泛化查表结构)。

    ``hierarchies[field][level][value]`` 给出某字段在某层级下原值对应的
    泛化值：0 级为原值本身，``depth - 1`` 级为该字段的统一通配符。
    """
    if len(records) < 1:
        raise unprocessable("records 至少需要包含 1 条记录")

    if len(fields) > 3:
        raise unprocessable("fields 最多包含 3 个待匿名字段")

    names = sorted(fields)
    hierarchies: dict[str, dict[int, dict[str, str]]] = {}

    for name in names:
        mapping = fields[name]
        if not name.strip():
            raise unprocessable("字段名不能为空")
        if not mapping:
            raise unprocessable(f"字段 {name!r} 的映射不能为空")

        depths = {len(path) for path in mapping.values()}
        if len(depths) != 1:
            raise unprocessable(
                f"字段 {name!r} 的每个原值必须映射为等长的层级路径"
            )
        depth = depths.pop()
        if not 1 <= depth <= 4:
            raise unprocessable(
                f"字段 {name!r} 的层级路径长度必须在 1 到 4 之间"
            )

        wildcard_values = {path[-1] for path in mapping.values()}
        if len(wildcard_values) != 1:
            raise unprocessable(
                f"字段 {name!r} 的最高级（通配符级）必须统一为同一个通配符"
            )
        wildcard = wildcard_values.pop()
        if not wildcard.strip():
            raise unprocessable(f"字段 {name!r} 的通配符不能为空字符串")

        field_levels: dict[int, dict[str, str]] = {}
        for level in range(depth):
            table: dict[str, str] = {}
            for original, path in mapping.items():
                if not original.strip():
                    raise unprocessable(f"字段 {name!r} 中不允许空原值")
                if level == 0 and path[0] != original:
                    raise unprocessable(
                        f"字段 {name!r} 的映射路径首项必须等于原值 {original!r}"
                    )
                generalized = path[level]
                if not isinstance(generalized, str) or not generalized.strip():
                    raise unprocessable(
                        f"字段 {name!r} 的层级泛化值不能为空字符串"
                    )
                table[original] = generalized
            field_levels[level] = table
        hierarchies[name] = field_levels

        # 映射缺值：记录中出现的任何原值都必须能在映射里找到。
        missing: list[str] = []
        seen_missing: set[str] = set()
        for record in records:
            value = record.get(name)
            if not isinstance(value, str) or value not in mapping:
                marker = repr(value)
                if marker not in seen_missing:
                    seen_missing.add(marker)
                    missing.append(marker)
        if missing:
            raise unprocessable(
                f"字段 {name!r} 的映射缺少记录中出现的值: {missing}"
            )

    return names, hierarchies


def solve_levels(
    records: list[dict[str, Any]],
    names: list[str],
    hierarchies: dict[str, dict[int, dict[str, str]]],
    k: int,
    sensitive_values: list[str] | None = None,
    l: int | None = None,
) -> tuple[tuple[int, ...], list[str]]:
    """选出最优统一层级组合。

    遍历所有层级组合，保留满足“每组至少 k 条”的方案；提供
    ``sensitive_values`` 时还要求每组包含至少 ``l`` 种不同的敏感值。
    先最小化层级序号之和，再按字段名排序后的层级序号字典序裁决。
    返回 (选中层级组合, 按字段名排序后的键名列表)。
    """
    levels_range = [range(len(hierarchies[name])) for name in names]
    keys = names

    best_combo: tuple[int, ...] | None = None
    best_sum: int | None = None

    for combo in product(*levels_range):
        signatures = [
            tuple(
                hierarchies[name][level][record[name]]
                for name, level in zip(keys, combo)
            )
            for record in records
        ]
        counts = Counter(signatures)
        if signatures:
            smallest_group = min(counts.values())
        else:
            smallest_group = 0
        if smallest_group < k:
            continue

        if sensitive_values is not None:
            # 调用方保证 sensitive_values 与 l 同时提供且与 records 等长。
            assert l is not None
            distinct: dict[tuple[str, ...], set[str]] = {}
            for signature, sensitive_value in zip(signatures, sensitive_values):
                distinct.setdefault(signature, set()).add(sensitive_value)
            if any(len(values) < l for values in distinct.values()):
                continue

        total = sum(combo)
        if (
            best_combo is None
            or total < best_sum  # type: ignore[operator]
            or (total == best_sum and list(combo) < list(best_combo))
        ):
            best_combo = combo
            best_sum = total

    if best_combo is None:
        if sensitive_values is not None:
            # 启用多样性约束后，全字段选通配符级也不再保证可行。
            raise unprocessable("不存在同时满足 k-匿名与 l-多样性约束的泛化方案")
        # 结构合法时，全字段选通配符级必然可行，这里仅作防御性兜底。
        raise unprocessable("不存在满足 k-匿名的泛化方案")

    return best_combo, keys


def apply_levels(
    records: list[dict[str, Any]],
    names: list[str],
    hierarchies: dict[str, dict[int, dict[str, str]]],
    combo: tuple[int, ...],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """按选中层级生成泛化记录与分组计数（组按首次出现顺序输出）。"""
    signatures: list[tuple[str, ...]] = []
    generalized_records: list[dict[str, Any]] = []

    for record in records:
        generalized_values = {
            name: hierarchies[name][level][record[name]]
            for name, level in zip(names, combo)
        }
        signature = tuple(generalized_values[name] for name in names)
        signatures.append(signature)

        generalized_record = dict(record)
        generalized_record.update(generalized_values)
        generalized_records.append(generalized_record)

    counts = Counter(signatures)
    groups: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for signature in signatures:
        if signature in seen:
            continue
        seen.add(signature)
        groups.append(
            {
                "values": dict(zip(names, signature)),
                "count": counts[signature],
            }
        )

    return generalized_records, groups
