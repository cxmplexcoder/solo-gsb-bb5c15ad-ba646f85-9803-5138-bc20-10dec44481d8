"""时空联合匿名化：统一层级全球四分格 × 午夜对齐等宽小时窗的联合 k-匿名。

空间维度使用全球统一层级 z 的四分网格（编号与边界归属规则与
``geo.assign_cells`` 一致：x 向东递增、y 向北为 0，网格线上的点归入最北或
最东侧格），时间维度使用从 UTC 午夜起对齐、宽度整除 24 且不超过
max_window_hours 的等宽小时窗。网格与小时窗的交叉单元即联合组。

枚举全部可行的 (z, 窗口宽度)：z 取 0～max_zoom，宽度取 1～max_window_hours
中能整除 24 的值；仅保留每个非空联合组都至少含 k 条记录的方案，最大化
``4^z × 24/宽度``（全球格数 × 全天窗数），平局先取较短窗（宽度较小），
再取较大 z。无可行方案时抛出 422。

响应只回传 id、z/x/y 与半开 UTC 窗口，不回传原坐标和原时刻。
"""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from .anonymizer import unprocessable
from .geo import prepare_points
from .time_windows import prepare_records

# 全天小时数；窗口宽度必须整除该值。
HOURS_PER_DAY = 24


def prepare_spacetime_records(
    records: list[dict[str, Any]],
) -> tuple[list[str], list[float], list[float], list[int], date]:
    """校验记录并返回 (id, 纬度, 经度, UTC 小时槽, 共同 UTC 日期)。

    空间校验复用坐标网格接口的规则（唯一非空字符串 id、合法经纬度），
    时间校验复用时间窗口接口的规则（带时区 observed_at、同一 UTC 日期）。
    各列表等长且与输入同序；其余字段不参与处理，也不回传。
    """
    ids, lats, lons = prepare_points(records)
    time_ids, hours, common_date = prepare_records(records)
    # 两处 id 校验规则一致，返回的 id 序列必然相同。
    assert ids == time_ids
    return ids, lats, lons, hours, common_date


def cell_xy(lat: float, lon: float, z: int) -> tuple[int, int]:
    """返回全球统一层级 z 下坐标所属格的 (x, y)。

    x 向东递增、y 向北为 0；落在网格线上的点归入最北或最东侧格
    （与自适应四分接口的递归边界规则一致），180° 经线与 -90° 纬线
    分别夹取到最东、最南侧格。
    """
    n = 2**z
    # 边界归东：floor((lon + 180) / 360 * n)；lon=180 时结果为 n，夹取 n-1。
    x = min(n - 1, math.floor((lon + 180.0) / 360.0 * n))
    # 网格线归北侧格：对整数网格线 q 取 ceil(q) - 1（北格），非网格线时
    # 与 floor(q) 等价；lat=90 与 lat=-90 分别夹取 0 与 n-1。
    y = max(0, min(n - 1, math.ceil((90.0 - lat) / 180.0 * n) - 1))
    return x, y


def solve_spacetime(
    lats: list[float],
    lons: list[float],
    hours: list[int],
    common_date: date,
    k: int,
    max_zoom: int,
    max_window_hours: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """枚举最优 (z, 窗口宽度) 并发布联合组。

    返回 (与输入等长等序的逐条发布信息, 按联合组首次出现顺序的发布组列表)。
    无任何可行方案时抛出 422。
    """
    # 宽度必须整除 24（全天被等宽窗口无剩余切分），且不超过上限。
    widths = [w for w in range(1, max_window_hours + 1) if HOURS_PER_DAY % w == 0]

    # 选择键：(4^z × 24/宽度, -宽度, z)，取字典序最大；
    # 即先最大化联合划分粒度，平局优先较短窗、再较大 z。
    best_key: tuple[int, int, int] | None = None
    best_z = 0
    best_width = 0

    for z in range(max_zoom + 1):
        cells = [cell_xy(lat, lon, z) for lat, lon in zip(lats, lons)]
        for width in widths:
            counts: dict[tuple[int, int, int], int] = {}
            for (x, y), hour in zip(cells, hours):
                key = (x, y, hour // width)
                counts[key] = counts.get(key, 0) + 1
            if any(count < k for count in counts.values()):
                continue
            choice_key = (
                4**z * (HOURS_PER_DAY // width),
                -width,
                z,
            )
            if best_key is None or choice_key > best_key:
                best_key = choice_key
                best_z, best_width = z, width

    if best_key is None:
        raise unprocessable(
            "不存在每个非空时空联合组都至少含 k 条记录的"
            "（网格层级 z，窗口宽度）组合"
        )

    return _build_publication(
        lats, lons, hours, common_date, best_z, best_width
    )


def _build_publication(
    lats: list[float],
    lons: list[float],
    hours: list[int],
    common_date: date,
    z: int,
    width: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """按选定的 (z, width) 生成逐条发布信息与联合组（组按首次出现顺序）。"""
    midnight = datetime.combine(common_date, time.min, tzinfo=timezone.utc)

    # key = (x, y, 窗口序号)；group_order 保留联合组在输入中的首次出现顺序。
    group_order: list[tuple[int, int, int]] = []
    group_counts: dict[tuple[int, int, int], int] = {}
    record_keys: list[tuple[int, int, int]] = []
    for lat, lon, hour in zip(lats, lons, hours):
        x, y = cell_xy(lat, lon, z)
        key = (x, y, hour // width)
        record_keys.append(key)
        if key not in group_counts:
            group_counts[key] = 0
            group_order.append(key)
        group_counts[key] += 1

    window_of_group: dict[tuple[int, int, int], tuple[str, str]] = {}
    groups: list[dict[str, Any]] = []
    for key in group_order:
        x, y, slot = key
        start = midnight + timedelta(hours=slot * width)
        end = midnight + timedelta(hours=slot * width + width)
        window = (_iso_z(start), _iso_z(end))
        window_of_group[key] = window
        groups.append(
            {
                "z": z,
                "x": x,
                "y": y,
                "window_start": window[0],
                "window_end": window[1],
                "count": group_counts[key],
            }
        )

    assignments = [
        {
            "z": z,
            "x": key[0],
            "y": key[1],
            "window_start": window_of_group[key][0],
            "window_end": window_of_group[key][1],
        }
        for key in record_keys
    ]
    return assignments, groups


def _iso_z(moment: datetime) -> str:
    """把 UTC 时刻格式化为带 Z 后缀的 ISO 8601 字符串。"""
    return moment.isoformat().replace("+00:00", "Z")
