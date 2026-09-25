"""时间窗口匿名化：把同一 UTC 日期内的观测时间归入满足 k-匿名的连续小时窗口。

所有记录先转换到 UTC 并按小时归槽；有记录的小时槽按时间升序划分为连续
窗口：每个窗口至少包含 k 条记录，首末小时覆盖的跨度（末小时 - 首小时 + 1，
即半开区间的小时数）不超过 max_window_hours，且同一小时槽内的记录不可
拆分到不同窗口。在所有可行划分中用动态规划求全局最优：最小化各窗口
“记录数 × 跨度小时”之和；代价相同时按窗口结束小时序列的字典序选最早者。
响应只回传各 id 所属窗口的半开 UTC 小时区间与各窗口计数，不回传原始时间。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from .anonymizer import unprocessable

# max_window_hours 允许的最大跨度。
MAX_WINDOW_HOURS_LIMIT = 24


def prepare_records(
    records: list[dict[str, Any]],
) -> tuple[list[str], list[int], date]:
    """校验记录并返回 (id 列表, 各记录的 UTC 小时槽, 共同的 UTC 日期)。

    每条记录必须有：唯一非空字符串 id、带时区信息的 ISO 8601 字符串
    observed_at；全部记录转换到 UTC 后必须落在同一日历日期。
    其余字段不参与处理，也不回传。
    """
    ids: list[str] = []
    seen_ids: set[str] = set()
    hours: list[int] = []
    common_date: date | None = None

    for index, record in enumerate(records):
        label = f"第 {index + 1} 条记录"

        obs_id = record.get("id")
        if not isinstance(obs_id, str) or not obs_id.strip():
            raise unprocessable(f"{label}的 id 必须是非空字符串")
        if obs_id in seen_ids:
            raise unprocessable(f"id {obs_id!r} 重复出现，每条记录的 id 必须唯一")
        seen_ids.add(obs_id)
        ids.append(obs_id)

        observed = record.get("observed_at")
        if not isinstance(observed, str) or not observed.strip():
            raise unprocessable(
                f"{label}的 observed_at 必须是带时区的 ISO 8601 字符串"
            )
        try:
            parsed = datetime.fromisoformat(observed)
        except ValueError:
            raise unprocessable(
                f"{label}的 observed_at 不是合法的 ISO 8601 时间: {observed!r}"
            )
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise unprocessable(f"{label}的 observed_at 必须携带时区信息")

        utc_moment = parsed.astimezone(timezone.utc)
        if common_date is None:
            common_date = utc_moment.date()
        elif utc_moment.date() != common_date:
            raise unprocessable("所有记录转换到 UTC 后必须落在同一 UTC 日期")
        hours.append(utc_moment.hour)

    # 调用方保证 records 非空。
    assert common_date is not None
    return ids, hours, common_date


def assign_windows(
    hours: list[int],
    common_date: date,
    k: int,
    max_window_hours: int,
) -> tuple[list[int], list[dict[str, Any]]]:
    """求全局最优窗口划分，返回 (各记录下标对应的窗口序号, 按时间升序的窗口列表)。

    有记录的小时槽升序排列后划分为连续段：每段记录数 >= k 且首末小时跨度
    （末小时 - 首小时 + 1）<= max_window_hours。动态规划最小化各段
    “记录数 × 跨度”之和，代价相同时按窗口结束小时序列的字典序取最小。
    每个窗口给出半开 UTC 小时区间 [window_start, window_end) 与记录数。
    """
    counts_by_hour: dict[int, int] = {}
    for hour in hours:
        counts_by_hour[hour] = counts_by_hour.get(hour, 0) + 1
    slot_hours = sorted(counts_by_hour)
    slot_counts = [counts_by_hour[hour] for hour in slot_hours]
    n = len(slot_hours)

    # dp[right]：前 right 个小时槽的最优划分，值为 (总代价, 窗口结束小时序列)；
    # 元组比较即“先比总代价，代价相同再比结束小时序列字典序”。
    dp: list[tuple[int, tuple[int, ...]] | None] = [None] * (n + 1)
    dp[0] = (0, ())
    for right in range(1, n + 1):
        best: tuple[int, tuple[int, ...]] | None = None
        count = 0
        for left in range(right - 1, -1, -1):
            count += slot_counts[left]
            span = slot_hours[right - 1] - slot_hours[left] + 1
            if span > max_window_hours:
                break  # left 继续前移跨度只会更大
            prefix = dp[left]
            if count < k or prefix is None:
                continue
            candidate = (
                prefix[0] + count * span,
                prefix[1] + (slot_hours[right - 1],),
            )
            if best is None or candidate < best:
                best = candidate
        dp[right] = best

    final = dp[n]
    if final is None:
        raise unprocessable(
            "不存在同时满足每窗至少 k 条且跨度不超过上限的时间窗口划分"
        )

    # 由结束小时序列还原各窗口覆盖的小时槽区间，并建立 小时槽 -> 窗口序号 映射。
    index_of = {hour: idx for idx, hour in enumerate(slot_hours)}
    window_of_hour: dict[int, int] = {}
    windows: list[dict[str, Any]] = []
    begin = 0
    midnight = datetime.combine(common_date, time.min, tzinfo=timezone.utc)
    for end_hour in final[1]:
        end_idx = index_of[end_hour]
        windows.append(
            {
                "window_start": _iso_z(
                    midnight + timedelta(hours=slot_hours[begin])
                ),
                "window_end": _iso_z(
                    midnight + timedelta(hours=slot_hours[end_idx] + 1)
                ),
                "count": sum(slot_counts[begin : end_idx + 1]),
            }
        )
        for idx in range(begin, end_idx + 1):
            window_of_hour[slot_hours[idx]] = len(windows) - 1
        begin = end_idx + 1

    return [window_of_hour[hour] for hour in hours], windows


def _iso_z(moment: datetime) -> str:
    """把 UTC 时刻格式化为带 Z 后缀的 ISO 8601 字符串。"""
    return moment.isoformat().replace("+00:00", "Z")
