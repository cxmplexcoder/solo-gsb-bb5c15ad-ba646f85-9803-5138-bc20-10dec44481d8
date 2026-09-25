"""自适应网格匿名化：把野外观测坐标归入满足 k-匿名的递归四分网格。

以全球经纬范围为根格（z=0，即 z/x/y = 0/0/0），按经纬中点递归四分；
仅当当前格的每个非空子格都至少有 k 条记录时才拆分，否则整格停止，
最多拆到 max_zoom。落在子格边界上的点归入最北或最东的子格。
"""

from __future__ import annotations

import math
from typing import Any, cast

from .anonymizer import unprocessable

# 根格（z=0）覆盖全球经纬范围。
ROOT_LAT_MIN, ROOT_LAT_MAX = -90.0, 90.0
ROOT_LON_MIN, ROOT_LON_MAX = -180.0, 180.0

# max_zoom 允许的最大层级。
MAX_ZOOM_LIMIT = 12


def prepare_points(
    records: list[dict[str, Any]],
) -> tuple[list[str], list[float], list[float]]:
    """校验记录并返回 (id 列表, 纬度列表, 经度列表)，三者等长且与输入同序。

    每条记录必须有：唯一非空字符串 id、有限数值 latitude（-90～90）、
    有限数值 longitude（-180～180）。其余字段不参与处理，也不回传。
    """
    ids: list[str] = []
    seen_ids: set[str] = set()
    lats: list[float] = []
    lons: list[float] = []

    for index, record in enumerate(records):
        label = f"第 {index + 1} 条记录"

        obs_id = record.get("id")
        if not isinstance(obs_id, str) or not obs_id.strip():
            raise unprocessable(f"{label}的 id 必须是非空字符串")
        if obs_id in seen_ids:
            raise unprocessable(f"id {obs_id!r} 重复出现，每条记录的 id 必须唯一")
        seen_ids.add(obs_id)
        ids.append(obs_id)

        lat = _coordinate(record.get("latitude"), "latitude", label)
        if not -90.0 <= lat <= 90.0:
            raise unprocessable(f"{label}的 latitude 必须在 -90 到 90 之间")
        lats.append(lat)

        lon = _coordinate(record.get("longitude"), "longitude", label)
        if not -180.0 <= lon <= 180.0:
            raise unprocessable(f"{label}的 longitude 必须在 -180 到 180 之间")
        lons.append(lon)

    return ids, lats, lons


def _coordinate(value: Any, field: str, label: str) -> float:
    """校验单个坐标为有限数值（拒绝布尔、字符串与 NaN/无穷），返回 float。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise unprocessable(f"{label}的 {field} 必须是数值")
    result = float(value)
    if not math.isfinite(result):
        raise unprocessable(f"{label}的 {field} 必须是有限数值")
    return result


def assign_cells(
    lats: list[float],
    lons: list[float],
    k: int,
    max_zoom: int,
) -> tuple[list[tuple[int, int, int]], list[dict[str, Any]]]:
    """递归四分并返回 (每条记录下标对应的最终格 z/x/y, 发布格列表)。

    仅当当前格的每个非空子格都至少有 k 条记录时才拆分，否则整格停止；
    最多拆到 max_zoom。边界点归入最北或最东子格。发布格按格内记录在
    输入中首次出现的顺序排列。
    """
    final: list[tuple[int, int, int] | None] = [None] * len(lats)
    leaves: list[tuple[int, dict[str, Any]]] = []

    def recurse(
        z: int,
        x: int,
        y: int,
        lat_min: float,
        lat_max: float,
        lon_min: float,
        lon_max: float,
        indices: list[int],
    ) -> None:
        if z < max_zoom:
            lat_mid = (lat_min + lat_max) / 2.0
            lon_mid = (lon_min + lon_max) / 2.0
            # 边界归北（lat >= 中点）、归东（lon >= 中点）。
            northwest: list[int] = []
            northeast: list[int] = []
            southwest: list[int] = []
            southeast: list[int] = []
            for i in indices:
                if lats[i] >= lat_mid:
                    (northeast if lons[i] >= lon_mid else northwest).append(i)
                else:
                    (southeast if lons[i] >= lon_mid else southwest).append(i)
            # y 向北为 0（与通用瓦片编号一致）：西北/东北/西南/东南。
            children = [
                (northwest, x * 2, y * 2, lat_mid, lat_max, lon_min, lon_mid),
                (northeast, x * 2 + 1, y * 2, lat_mid, lat_max, lon_mid, lon_max),
                (southwest, x * 2, y * 2 + 1, lat_min, lat_mid, lon_min, lon_mid),
                (southeast, x * 2 + 1, y * 2 + 1, lat_min, lat_mid, lon_mid, lon_max),
            ]
            non_empty = [child for child in children if child[0]]
            if all(len(child[0]) >= k for child in non_empty):
                for bucket, cx, cy, c_lat_min, c_lat_max, c_lon_min, c_lon_max in non_empty:
                    recurse(
                        z + 1, cx, cy,
                        c_lat_min, c_lat_max, c_lon_min, c_lon_max,
                        bucket,
                    )
                return

        # 整格停止：成为发布格。
        for i in indices:
            final[i] = (z, x, y)
        leaves.append(
            (
                indices[0],
                {
                    "z": z,
                    "x": x,
                    "y": y,
                    "lat_min": lat_min,
                    "lat_max": lat_max,
                    "lon_min": lon_min,
                    "lon_max": lon_max,
                    "count": len(indices),
                },
            )
        )

    recurse(
        0, 0, 0,
        ROOT_LAT_MIN, ROOT_LAT_MAX, ROOT_LON_MIN, ROOT_LON_MAX,
        list(range(len(lats))),
    )

    # indices 始终按下标升序传递，leaves 首元素即格内最小下标；
    # 按它排序使发布格顺序与记录在输入中首次出现的顺序一致。
    leaves.sort(key=lambda item: item[0])
    cells = [cell for _, cell in leaves]

    # 每条记录都必然落入某个发布格。
    assert all(cell is not None for cell in final)
    return cast(list[tuple[int, int, int]], final), cells
