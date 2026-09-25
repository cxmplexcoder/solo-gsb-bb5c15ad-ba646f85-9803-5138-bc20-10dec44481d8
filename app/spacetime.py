"""时空联合匿名发布：统一层级的全球四分格 × 从午夜起对齐的等宽小时窗。

所有记录使用同一层级 z 的全球四分格（z/x/y 归属规则与 geo 模块一致，
边界上的点归入最北或最东的子格）与同一宽度的小时窗：窗口从当日 UTC
午夜起对齐，宽度整除 24 且不超过 max_window_hours。每个“格 × 窗”组成
一个时空联合组。枚举全部候选 (z, 宽度)，仅保留每个非空联合组都至少有
k 条记录的方案；在其中最大化 4^z × 24/宽度（联合组总数，即时空分辨
率），并列时优先较短的窗口宽度，再优先较大的 z；无可行方案时返回 422。
响应只回传各 id 的 z/x/y 与半开 UTC 窗口以及各联合组人数，
不回传原始坐标与时刻。
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from .anonymizer import unprocessable
from .geo import ROOT_LAT_MAX, ROOT_LAT_MIN, ROOT_LON_MAX, ROOT_LON_MIN

# 整除 24 的候选窗口宽度（小时），窗口从午夜起对齐。
WINDOW_WIDTHS = (1, 2, 3, 4, 6, 8, 12, 24)


def cell_of_point(lat: float, lon: float, z: int) -> tuple[int, int]:
    """求坐标在统一层级 z 下的格编号 (x, y)。

    从根格起按经纬中点逐层对分，与 geo 模块的归属规则一致：
    边界上的点归入最北或最东的子格；y 向北为 0。
    """
    lat_min, lat_max = ROOT_LAT_MIN, ROOT_LAT_MAX
    lon_min, lon_max = ROOT_LON_MIN, ROOT_LON_MAX
    x = y = 0
    for _ in range(z):
        lat_mid = (lat_min + lat_max) / 2.0
        lon_mid = (lon_min + lon_max) / 2.0
        if lat >= lat_mid:
            y *= 2
            lat_min = lat_mid
        else:
            y = y * 2 + 1
            lat_max = lat_mid
        if lon >= lon_mid:
            x = x * 2 + 1
            lon_min = lon_mid
        else:
            x *= 2
            lon_max = lon_mid
    return x, y


def assign_joint(
    lats: list[float],
    lons: list[float],
    hours: list[int],
    common_date: date,
    k: int,
    max_zoom: int,
    max_window_hours: int,
) -> tuple[list[tuple[int, int, int, str, str]], list[dict[str, Any]]]:
    """选出最优 (z, 宽度) 并返回 (各记录的 (z, x, y, 窗口起点, 窗口终点), 联合组列表)。

    枚举 z（0～max_zoom）与宽度（整除 24 且不超过 max_window_hours）的
    全部组合，仅保留每个非空联合组都至少有 k 条记录的方案；最大化
    4^z × 24/宽度，并列时优先较短宽度、再较大 z。每个联合组给出 z/x/y、
    半开 UTC 小时区间 [window_start, window_end) 与人数，按组内记录在
    输入中首次出现的顺序排列。
    """
    widths = [width for width in WINDOW_WIDTHS if width <= max_window_hours]
    # 调用方保证 max_window_hours >= 1，候选宽度必然非空。
    assert widths

    # 各记录在每个候选层级下的格编号，避免随宽度重复计算。
    cells_by_z = [
        [cell_of_point(lat, lon, z) for lat, lon in zip(lats, lons)]
        for z in range(max_zoom + 1)
    ]

    best_key: tuple[int, int, int] | None = None
    best_scheme: tuple[int, int] | None = None
    for z in range(max_zoom + 1):
        cells = cells_by_z[z]
        for width in widths:
            counts = Counter(
                (cells[index], hours[index] // width)
                for index in range(len(hours))
            )
            # 记录非空，联合组必然非空；只约束非空组。
            if min(counts.values()) < k:
                continue
            # 先最大化联合组总数，并列时优先较短窗、再较大 z。
            key = (4**z * (24 // width), -width, z)
            if best_key is None or key > best_key:
                best_key = key
                best_scheme = (z, width)

    if best_scheme is None:
        raise unprocessable(
            "不存在每个非空联合组都不少于 k 条的 (z, 窗口宽度) 组合"
        )

    z, width = best_scheme
    midnight = datetime.combine(common_date, time.min, tzinfo=timezone.utc)
    windows = [
        (
            _iso_z(midnight + timedelta(hours=start)),
            _iso_z(midnight + timedelta(hours=start + width)),
        )
        for start in range(0, 24, width)
    ]

    assignments: list[tuple[int, int, int, str, str]] = []
    groups: list[dict[str, Any]] = []
    index_of: dict[tuple[int, int, int], int] = {}
    for index in range(len(hours)):
        x, y = cells_by_z[z][index]
        window_index = hours[index] // width
        window_start, window_end = windows[window_index]
        assignments.append((z, x, y, window_start, window_end))
        group_key = (x, y, window_index)
        if group_key not in index_of:
            index_of[group_key] = len(groups)
            groups.append(
                {
                    "z": z,
                    "x": x,
                    "y": y,
                    "window_start": window_start,
                    "window_end": window_end,
                    "count": 0,
                }
            )
        groups[index_of[group_key]]["count"] += 1

    return assignments, groups


def _iso_z(moment: datetime) -> str:
    """把 UTC 时刻格式化为带 Z 后缀的 ISO 8601 字符串。"""
    return moment.isoformat().replace("+00:00", "Z")
