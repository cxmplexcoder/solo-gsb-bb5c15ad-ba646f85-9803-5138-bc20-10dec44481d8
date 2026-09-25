"""FastAPI 应用入口：野外观测记录匿名化发布服务。

服务为纯内存、无状态处理：不写日志正文、不落盘任何原始记录。
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from .anonymizer import (
    apply_levels,
    prepare_hierarchies,
    prepare_sensitive,
    solve_levels,
    unprocessable,
)
from .geo import MAX_ZOOM_LIMIT, assign_cells, prepare_points
from .intersection import audit_intersections
from .time_windows import MAX_WINDOW_HOURS_LIMIT, assign_windows, prepare_records

API_PREFIX = "/api/v1"

app = FastAPI(
    title="野外观测记录匿名化 API",
    description=(
        "对自然保护站公开野外观测记录进行统一层级泛化，"
        "使发布后的每种字段值组合至少出现 k 次；"
        "可选地要求每组包含至少 l 种不同的敏感属性值。"
        "另提供两次发布的关联风险审计：对同一批记录两次公开的"
        "分组按 id 求交集，标记成员数不足 k 的交集。"
        "另提供坐标自适应网格匿名化：以全球经纬范围为根格递归四分，"
        "仅当每个非空子格都至少有 k 条记录时才拆分，输出可发布的 z/x/y 格。"
        "另提供观测时间窗口匿名化：同一 UTC 日期内的记录按小时归槽后"
        "划分为连续时间窗，每窗至少 k 条且首末小时跨度不超过上限，"
        "全局最优最小化各窗“记录数×跨度小时”之和，"
        "输出各 id 所属的半开 UTC 小时区间。"
    ),
    version="1.3.0",
)


class AnonymizeRequest(BaseModel):
    """匿名化请求体。"""

    # 拒绝 bool 冒充整数；k 必须为正整数。
    k: StrictInt = Field(..., description="每种字段值组合发布后至少出现的次数")
    records: list[dict[str, Any]] = Field(..., description="待匿名化的原始记录")
    # 每个字段：原值 -> 从原值（含）到统一通配符（含）的层级路径，至多 4 级。
    fields: dict[str, dict[str, list[str]]] = Field(
        ..., description="最多 3 个待匿名字段的泛化层级映射"
    )
    # 可选的 l-多样性约束：两者必须同时提供；
    # sensitive_field 不得与待匿名字段重合，且每条记录的敏感值都必须是非空字符串。
    sensitive_field: str | None = Field(
        default=None,
        description="敏感属性字段名（非待匿名字段），需与 l 同时提供",
    )
    l: StrictInt | None = Field(
        default=None,
        description="每个发布分组至少包含的不同敏感值种数，需与 sensitive_field 同时提供",
    )

    model_config = ConfigDict(extra="forbid")


class Group(BaseModel):
    """一个发布分组：泛化后的字段值组合与记录数量。"""

    values: dict[str, str]
    count: int


class AnonymizeResponse(BaseModel):
    """匿名化结果。"""

    generalized_records: list[dict[str, Any]]
    selected_levels: dict[str, int]
    groups: list[Group]
    k: int
    total_records: int


class AuditIntersectionRequest(BaseModel):
    """两次发布关联风险审计请求体。"""

    records_a: list[dict[str, Any]] = Field(..., description="第一次公开发布的记录")
    fields_a: list[str] = Field(
        ..., description="第一次发布用于分组的公开字段名列表（可为空列表）"
    )
    records_b: list[dict[str, Any]] = Field(..., description="第二次公开发布的记录")
    fields_b: list[str] = Field(
        ..., description="第二次发布用于分组的公开字段名列表（可为空列表）"
    )
    id_field: str = Field(..., description="两批记录共同的 id 字段名")
    # 拒绝 bool 冒充整数；k 必须为正整数。
    k: StrictInt = Field(..., description="交集成员数的风险阈值")

    model_config = ConfigDict(extra="forbid")


class Intersection(BaseModel):
    """一个交集单元：两次分组的一对等价组按 id 求交的结果。"""

    ids: list[str]
    count: int
    values_a: dict[str, str]
    values_b: dict[str, str]
    at_risk: bool


class AuditIntersectionResponse(BaseModel):
    """两次发布关联风险审计结果。"""

    intersections: list[Intersection]
    k: int
    total_records: int
    risk_count: int


class GeoAnonymizeRequest(BaseModel):
    """坐标自适应网格匿名化请求体。"""

    # 拒绝 bool 冒充整数；k 必须为正整数。
    k: StrictInt = Field(..., description="每个发布格至少包含的记录数")
    # 拒绝 bool 冒充整数；max_zoom 必须在 0～12 之间。
    max_zoom: StrictInt = Field(..., description="最大拆分层级（0～12）")
    records: list[dict[str, Any]] = Field(
        ..., description="含 id、latitude、longitude 的野外观测记录"
    )

    model_config = ConfigDict(extra="forbid")


class GeoAssignment(BaseModel):
    """一条记录的最终发布格（仅 id 与 z/x/y，不回传原坐标）。"""

    id: str
    z: int
    x: int
    y: int


class GeoCell(BaseModel):
    """一个发布格：z/x/y、经纬边界与人数。"""

    z: int
    x: int
    y: int
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float
    count: int


class GeoAnonymizeResponse(BaseModel):
    """坐标自适应网格匿名化结果。"""

    records: list[GeoAssignment]
    cells: list[GeoCell]
    k: int
    max_zoom: int
    total_records: int


class TimeAnonymizeRequest(BaseModel):
    """时间窗口匿名化请求体。"""

    # 拒绝 bool 冒充整数；k 必须为正整数。
    k: StrictInt = Field(..., description="每个时间窗口至少包含的记录数")
    # 拒绝 bool 冒充整数；max_window_hours 必须在 1～24 之间。
    max_window_hours: StrictInt = Field(
        ..., description="单个窗口首末小时覆盖的最大跨度小时数（1～24）"
    )
    records: list[dict[str, Any]] = Field(
        ..., description="同一 UTC 日期内含唯一 id 与带时区 observed_at 的观测记录"
    )

    model_config = ConfigDict(extra="forbid")


class TimeAssignment(BaseModel):
    """一条记录的最终时间窗口（仅 id 与半开 UTC 小时区间，不回传原时间）。"""

    id: str
    window_start: str
    window_end: str


class TimeWindow(BaseModel):
    """一个发布时间窗：半开 UTC 小时区间 [window_start, window_end) 与记录数。"""

    window_start: str
    window_end: str
    count: int


class TimeAnonymizeResponse(BaseModel):
    """时间窗口匿名化结果。"""

    records: list[TimeAssignment]
    windows: list[TimeWindow]
    k: int
    max_window_hours: int
    total_records: int


@app.get("/")
def root() -> dict[str, str]:
    """服务信息。"""
    return {
        "service": "wildlife-observation-anonymizer",
        "docs": "/docs",
        "health": f"{API_PREFIX}/health",
        "anonymize": f"{API_PREFIX}/anonymize",
        "audit_intersection": f"{API_PREFIX}/audit-intersection",
        "geo_anonymize": f"{API_PREFIX}/geo-anonymize",
        "time_anonymize": f"{API_PREFIX}/time-anonymize",
    }


@app.get(f"{API_PREFIX}/health")
def health() -> dict[str, str]:
    """健康检查。"""
    return {"status": "ok"}


@app.post(f"{API_PREFIX}/anonymize", response_model=AnonymizeResponse)
def anonymize(request: AnonymizeRequest) -> AnonymizeResponse:
    """对记录执行统一层级泛化，返回可公开发布的匿名化结果。"""
    if request.k < 1:
        raise unprocessable("k 必须是正整数（>= 1）")

    # 记录总数小于 k：任何分组（至少存在一个组）都不可能达到 k 条。
    if len(request.records) < request.k:
        raise unprocessable(
            f"记录总数 {len(request.records)} 小于 k={request.k}，无法满足 k-匿名"
        )

    sensitive_values = prepare_sensitive(
        request.records, request.fields, request.sensitive_field, request.l
    )

    names, hierarchies = prepare_hierarchies(request.records, request.fields)
    combo, _ = solve_levels(
        request.records,
        names,
        hierarchies,
        request.k,
        sensitive_values=sensitive_values,
        l=request.l,
    )
    selected_levels = dict(zip(names, combo))
    generalized_records, groups = apply_levels(
        request.records, names, hierarchies, combo
    )

    return AnonymizeResponse(
        generalized_records=generalized_records,
        selected_levels=selected_levels,
        groups=[Group(**group) for group in groups],
        k=request.k,
        total_records=len(request.records),
    )


@app.post(
    f"{API_PREFIX}/audit-intersection",
    response_model=AuditIntersectionResponse,
)
def audit_intersection(request: AuditIntersectionRequest) -> AuditIntersectionResponse:
    """审计同一批观测记录两次公开后的关联风险：对两次分组按 id 求交集。"""
    if request.k < 1:
        raise unprocessable("k 必须是正整数（>= 1）")

    intersections = audit_intersections(
        request.records_a,
        request.fields_a,
        request.records_b,
        request.fields_b,
        request.id_field,
        request.k,
    )

    return AuditIntersectionResponse(
        intersections=[Intersection(**item) for item in intersections],
        k=request.k,
        total_records=len(request.records_a),
        risk_count=sum(1 for item in intersections if item["at_risk"]),
    )


@app.post(
    f"{API_PREFIX}/geo-anonymize",
    response_model=GeoAnonymizeResponse,
)
def geo_anonymize(request: GeoAnonymizeRequest) -> GeoAnonymizeResponse:
    """为观测坐标生成可发布的自适应网格：返回各 id 的最终格与发布格边界。"""
    if request.k < 1:
        raise unprocessable("k 必须是正整数（>= 1）")

    if not 0 <= request.max_zoom <= MAX_ZOOM_LIMIT:
        raise unprocessable(
            f"max_zoom 必须是 0 到 {MAX_ZOOM_LIMIT} 之间的整数"
        )

    # 记录总数小于 k：根格（必然存在的发布格）不可能达到 k 条。
    if len(request.records) < request.k:
        raise unprocessable(
            f"记录总数 {len(request.records)} 小于 k={request.k}，无法满足 k-匿名"
        )

    ids, lats, lons = prepare_points(request.records)
    assignments, cells = assign_cells(lats, lons, request.k, request.max_zoom)

    return GeoAnonymizeResponse(
        records=[
            GeoAssignment(id=obs_id, z=z, x=x, y=y)
            for obs_id, (z, x, y) in zip(ids, assignments)
        ],
        cells=[GeoCell(**cell) for cell in cells],
        k=request.k,
        max_zoom=request.max_zoom,
        total_records=len(request.records),
    )


@app.post(
    f"{API_PREFIX}/time-anonymize",
    response_model=TimeAnonymizeResponse,
)
def time_anonymize(request: TimeAnonymizeRequest) -> TimeAnonymizeResponse:
    """为观测时间生成可发布的连续小时窗口：返回各 id 所属窗口与各窗口计数。"""
    if request.k < 1:
        raise unprocessable("k 必须是正整数（>= 1）")

    if not 1 <= request.max_window_hours <= MAX_WINDOW_HOURS_LIMIT:
        raise unprocessable(
            f"max_window_hours 必须是 1 到 {MAX_WINDOW_HOURS_LIMIT} 之间的整数"
        )

    # 记录总数小于 k：任何窗口（至少存在一个）都不可能达到 k 条。
    if len(request.records) < request.k:
        raise unprocessable(
            f"记录总数 {len(request.records)} 小于 k={request.k}，无法满足 k-匿名"
        )

    ids, hours, common_date = prepare_records(request.records)
    assignments, windows = assign_windows(
        hours, common_date, request.k, request.max_window_hours
    )

    return TimeAnonymizeResponse(
        records=[
            TimeAssignment(
                id=obs_id,
                window_start=windows[window_index]["window_start"],
                window_end=windows[window_index]["window_end"],
            )
            for obs_id, window_index in zip(ids, assignments)
        ],
        windows=[TimeWindow(**window) for window in windows],
        k=request.k,
        max_window_hours=request.max_window_hours,
        total_records=len(request.records),
    )
