# 野外观测记录匿名化 API

为自然保护站公开发布野外观测记录而设计的匿名化服务（Python + FastAPI）。
调用方提交一批 JSON 观测记录、正整数 **k**，以及最多 **3 个**待匿名字段的
泛化层级映射；服务对**全部记录统一**选择每个字段的泛化层级，使发布后每一种
字段值组合（等价组）都至少出现 **k** 次，并在所有可行方案中最小化层级序号之和。
调用方还可可选地同时提供敏感属性字段名与正整数 **l**，追加 **l-多样性**约束：
每个发布分组中不同的敏感值种数至少为 **l**。
此外，服务还提供**坐标自适应网格匿名化**：以全球经纬范围为根格递归四分，
仅当每个非空子格都至少有 **k** 条记录时才拆分，输出可发布的 `z/x/y` 网格，
替代直接公开精确坐标。
以及**观测时间窗口匿名化**：把同一 UTC 日期内的观测时间按小时归槽后划分为
连续时间窗，每窗至少 **k** 条且首末小时跨度不超过上限，全局最优最小化各窗
“记录数 × 跨度小时”之和，输出各 id 所属的半开 UTC 小时区间，替代直接公开
精确观测时刻。

服务为纯内存、无状态处理：**原始记录不写日志、不落盘、不挂载数据卷**。

## 一条命令启动

```bash
docker compose up --build
```

启动后：

- API 地址与端口：`http://localhost:8000`
- 匿名化接口：`POST http://localhost:8000/api/v1/anonymize`
- 两次发布关联风险审计接口：`POST http://localhost:8000/api/v1/audit-intersection`
- 坐标自适应网格匿名化接口：`POST http://localhost:8000/api/v1/geo-anonymize`
- 观测时间窗口匿名化接口：`POST http://localhost:8000/api/v1/time-anonymize`
- 健康检查：`GET http://localhost:8000/api/v1/health`
- 交互式文档（Swagger UI）：`http://localhost:8000/docs`

如需改用其他宿主机端口（容器内固定监听 8000）：

```bash
PORT=9000 docker compose up --build
# 此时 API 地址为 http://localhost:9000
```

也可以不使用 Compose：

```bash
docker build -t wildlife-anonymizer .
docker run --rm -p 8000:8000 wildlife-anonymizer
```

## 必要配置

| 配置 | 位置 | 默认值 | 说明 |
| --- | --- | --- | --- |
| 宿主机端口 | Compose 环境变量 `PORT` | `8000` | `PORT=9000 docker compose up --build` 映射到 9000 |
| 容器内端口 | 容器环境变量 `PORT` | `8000` | 一般无需修改；容器以非 root 用户 `nobody` 运行 |

无数据库、无密钥、无其他依赖。容器以只读根文件系统运行，不挂载任何卷。

## 请求格式

`POST /api/v1/anonymize`，`Content-Type: application/json`：

```json
{
  "k": 3,
  "records": [
    {"id": "obs-001", "species": "金丝猴", "reserve": "神农架-老君山", "observer": "志愿者A"},
    {"id": "obs-002", "species": "金丝猴", "reserve": "神农架-老君山", "observer": "志愿者B"}
  ],
  "fields": {
    "species": {
      "金丝猴": ["金丝猴", "灵长类", "哺乳动物", "*"],
      "林麝":   ["林麝",   "有蹄类", "哺乳动物", "*"],
      "云豹":   ["云豹",   "食肉目", "哺乳动物", "*"]
    },
    "reserve": {
      "神农架-老君山": ["神农架-老君山", "神农架", "湖北", "*"],
      "神农架-大九湖": ["神农架-大九湖", "神农架", "湖北", "*"],
      "卧龙-邓生":     ["卧龙-邓生",     "卧龙",   "四川", "*"],
      "卧龙-耿达":     ["卧龙-耿达",     "卧龙",   "四川", "*"]
    }
  }
}
```

### 字段说明

- `k`：正整数，发布后每种匿名字段值组合至少出现的记录数。
- `records`：原始观测记录数组，每条为任意 JSON 对象。未列入 `fields` 的字段
  （如示例中的 `id`、`observer`）原样返回，由调用方自行决定是否提交。
- `fields`：最多 3 个待匿名字段。每个字段给出“原值 → 层级路径”的映射：
  - 路径为字符串数组，**1～4 级**，同一字段内所有路径长度必须一致；
  - 路径首项必须等于原值本身（即 **0 级 = 原值**，不泛化）；
  - 路径末项必须是该字段**统一的通配符**（如上例的 `"*"`，即最高 1 级泛化）；
  - 中间各级为该原值在对应层级的泛化值。
  - 层级序号：`0` 为原值，`1` 起逐级泛化，例如 4 级路径的层级为 `0/1/2/3`。
- `sensitive_field`（**可选**）：敏感属性字段名，**不得**与 `fields` 中的待匿名
  字段重合；该字段不会被泛化，按原值随记录返回。
- `l`（**可选**）：正整数，每个发布分组至少包含的**不同敏感值种数**。
  - `sensitive_field` 与 `l` 必须**同时提供**（只提供其中一个返回 422），
    两者均不提供时仅做 k-匿名，行为与不传这两个字段时完全一致；
  - 启用时每条记录的 `sensitive_field` 值都必须是**非空字符串**
    （缺失、为空或非字符串均返回 422）。

### 可选的 l-多样性约束

同时提供 `sensitive_field` 与 `l` 后，可行性条件变为“每个等价组记录数
`>= k` **且**不同敏感值种数 `>= l`”。例如（完整文件见
`examples/request-l-diversity.json`）：

```json
{
  "k": 3,
  "sensitive_field": "health",
  "l": 2,
  "records": [
    {"id": "obs-001", "species": "金丝猴", "reserve": "神农架-老君山", "health": "健康"},
    {"id": "obs-006", "species": "林麝",   "reserve": "卧龙-邓生",     "health": "患病"}
  ],
  "fields": { "...": "与上例相同" }
}
```

```bash
curl -s http://localhost:8000/api/v1/anonymize \
  -H 'Content-Type: application/json' \
  --data @examples/request-l-diversity.json
```

注意：多样性只通过“把组继续合并”实现（泛化层级越高，分组只会变少、组内
记录只会变多），服务不会改写或删除任何敏感值。

### 选层规则

对所有记录使用同一组层级：

1. 枚举每个字段的全部层级组合（最多 `4³ = 64` 种）；
2. 只保留可行组合：泛化后每个等价组记录数 `>= k`；启用 l-多样性时，
   还要求每个等价组内不同敏感值种数 `>= l`；
3. 最小化层级序号之和；
4. 和相同时，按**字段名排序后**的层级序号做**字典序**裁决
   （例如字段排序后为 `[reserve, species]`，`(2,3)` 优先于 `(3,2)`）。

## 响应格式

```json
{
  "generalized_records": [
    {"id": "obs-001", "species": "哺乳动物", "reserve": "神农架", "observer": "志愿者A"}
  ],
  "selected_levels": {"reserve": 1, "species": 2},
  "groups": [
    {"values": {"reserve": "神农架", "species": "哺乳动物"}, "count": 4},
    {"values": {"reserve": "卧龙",   "species": "哺乳动物"}, "count": 4}
  ],
  "k": 3,
  "total_records": 8
}
```

- `generalized_records`：泛化后的记录（与输入等长、等序；非匿名字段原样保留）。
- `selected_levels`：每个字段选中的层级序号。
- `groups`：发布后的各字段值组合及数量，`count` 均 `>= k`；按组在记录中
  首次出现的顺序返回。

仓库内 `examples/request.json` 为可直接使用的完整示例：

```bash
curl -s http://localhost:8000/api/v1/anonymize \
  -H 'Content-Type: application/json' \
  --data @examples/request.json
```

## 两次发布的关联风险审计

`POST /api/v1/audit-intersection`，`Content-Type: application/json`。

同一批观测记录若以**不同的公开字段**发布过两次，即使每次发布单独都满足
k-匿名，把两次发布按 id 交叉比对，等价组的**交集**仍可能不足 k 条，从而
把个体重新识别出来。本接口对这种关联风险做事后审计：

1. 分别按两批记录各自的公开字段值形成等价组（字段值组合相同的记录同组）；
2. 按共同 id 求两次分组的交集；
3. 返回每个非空交集的 id 列表、成员数、两侧字段值组合，以及
   `count < k` 的风险标记 `at_risk`；交集按**第一批记录的首次出现顺序**
   排列，交集内 id 同序。

### 请求格式

```json
{
  "id_field": "id",
  "k": 2,
  "records_a": [
    {"id": "obs-001", "reserve": "神农架", "observer": "志愿者A"},
    {"id": "obs-002", "reserve": "神农架", "observer": "志愿者B"},
    {"id": "obs-003", "reserve": "卧龙",   "observer": "志愿者C"},
    {"id": "obs-004", "reserve": "卧龙",   "observer": "志愿者D"}
  ],
  "fields_a": ["reserve"],
  "records_b": [
    {"id": "obs-001", "weather": "晴"},
    {"id": "obs-002", "weather": "阴"},
    {"id": "obs-003", "weather": "晴"},
    {"id": "obs-004", "weather": "阴"}
  ],
  "fields_b": ["weather"]
}
```

- `records_a` / `records_b`：两次已发布的记录（每条为 JSON 对象）。
- `fields_a` / `fields_b`：两批各自用于分组的公开字段名列表；字段必须
  在该批每条记录中存在且值为字符串（空字符串也是合法值）；字段名不能
  为空或重复。列表可为空，空列表表示该批所有记录同属一个组。
- `id_field`：两批共同的 id 字段名；id 必须是**非空字符串**且在**各批内
  唯一**，并且两批的 id 集合必须**完全相同**。
- `k`：正整数，交集成员数的风险阈值；`count < k` 即标记 `at_risk: true`。

### 响应格式

上例中两次发布各自的每个组都有 2 条（单独看满足 k=2），但 2×2 交叉后
每个交集只有 1 条，因此 4 个交集全部报险——这正是仅检查单次发布
无法发现的关联风险：

```json
{
  "intersections": [
    {"ids": ["obs-001"], "count": 1,
     "values_a": {"reserve": "神农架"}, "values_b": {"weather": "晴"},
     "at_risk": true},
    {"ids": ["obs-002"], "count": 1,
     "values_a": {"reserve": "神农架"}, "values_b": {"weather": "阴"},
     "at_risk": true},
    {"ids": ["obs-003"], "count": 1,
     "values_a": {"reserve": "卧龙"}, "values_b": {"weather": "晴"},
     "at_risk": true},
    {"ids": ["obs-004"], "count": 1,
     "values_a": {"reserve": "卧龙"}, "values_b": {"weather": "阴"},
     "at_risk": true}
  ],
  "k": 2,
  "total_records": 4,
  "risk_count": 4
}
```

- `intersections`：各交集单元，按第一批记录的首次出现顺序排列：
  - `ids`：属于该交集的 id 列表（同按第一批出现顺序）；
  - `count`：交集成员数；
  - `values_a` / `values_b`：交集两侧各自的字段值组合（键即
    `fields_a` / `fields_b`）；
  - `at_risk`：`count < k` 时为 `true`。
- `risk_count`：报险交集的数量；`k=1` 时恒为 0。

注意：**即使两批各自的所有组都达到 k，交集不足 k 仍会报险**（如上例）。
`k` 大于记录总数不是参数错误，此时所有交集都必然报险。

仓库内 `examples/request-audit-intersection.json` 为可直接使用的示例：

```bash
curl -s http://localhost:8000/api/v1/audit-intersection \
  -H 'Content-Type: application/json' \
  --data @examples/request-audit-intersection.json
```

### 审计接口的 422 校验

以下情况返回 HTTP `422 Unprocessable Entity`（请求体结构错误同样为
422），错误说明在响应体 `detail` 中：

- `k` 不是正整数（含 `0`、负数、布尔值、非整数）；
- `id_field` 为空字符串；
- 任一批记录为空，或某条记录缺少 id、id 不是非空字符串；
- id 在批内不唯一，或两批 id 集合不一致（错误信息列出差异 id）；
- 分组字段名为空或在同一列表中重复；
- 某条记录缺少分组字段，或分组字段值不是字符串；
- 请求体缺少必填字段或出现未声明字段。

## 坐标自适应网格匿名化

`POST /api/v1/geo-anonymize`，`Content-Type: application/json`。

直接发布精确观测坐标会暴露物种栖息地。本接口把坐标归入**自适应四分网格**
后再发布：以全球经纬范围为根格（`z/x/y = 0/0/0`，纬度 -90～90、经度
-180～180），按经纬中点递归四分；**仅当当前格的每个非空子格都至少有
k 条记录时才拆分，否则整格停止**，最多拆到 `max_zoom` 层。落在子格边界上
的点归入**最北或最东**的子格。最终每个发布格都至少含 k 条记录，格越拆分
越细的地方表示观测越密集。

### 请求格式

```json
{
  "k": 2,
  "max_zoom": 8,
  "records": [
    {"id": "obs-001", "latitude": 31.741, "longitude": 110.682},
    {"id": "obs-002", "latitude": 31.744, "longitude": 110.679},
    {"id": "obs-003", "latitude": 30.912, "longitude": 102.971},
    {"id": "obs-004", "latitude": 30.908, "longitude": 102.975}
  ]
}
```

- `k`：正整数，每个发布格至少包含的记录数。
- `max_zoom`：0～12 的整数，最大拆分层级；`0` 表示所有记录只落入根格。
- `records`：观测记录数组，每条必须包含：
  - `id`：**非空字符串**，且在整批记录中**唯一**；
  - `latitude`：**有限数值**，范围 -90～90；
  - `longitude`：**有限数值**，范围 -180～180；
  - 其余字段（如 `species`）不参与处理，也**不会**在响应中回传。

### 响应格式

```json
{
  "records": [
    {"id": "obs-001", "z": 8, "x": 206, "y": 82},
    {"id": "obs-002", "z": 8, "x": 206, "y": 82},
    {"id": "obs-003", "z": 8, "x": 201, "y": 84},
    {"id": "obs-004", "z": 8, "x": 201, "y": 84}
  ],
  "cells": [
    {"z": 8, "x": 206, "y": 82,
     "lat_min": 31.640625, "lat_max": 32.34375,
     "lon_min": 109.6875, "lon_max": 111.09375, "count": 2},
    {"z": 8, "x": 201, "y": 84,
     "lat_min": 30.234375, "lat_max": 30.9375,
     "lon_min": 102.65625, "lon_max": 104.0625, "count": 2}
  ],
  "k": 2,
  "max_zoom": 8,
  "total_records": 4
}
```

- `records`：与输入**等长、等序**，每项只含 `id` 与该记录最终格的
  `z/x/y`；**不回传原坐标或其他原字段**。
- `cells`：各发布格的 `z/x/y`、经纬边界（`lat_min/lat_max/lon_min/lon_max`）
  与人数 `count`（均 `>= k`）；按格内记录在输入中首次出现的顺序排列。
  `z` 每增加 1，格子经纬跨度减半（`z` 层格宽 `360/2^z` 度、格高
  `180/2^z` 度）；`y` 向北为 0，与通用瓦片编号一致。

仓库内 `examples/request-geo-anonymize.json` 为可直接使用的示例：

```bash
curl -s http://localhost:8000/api/v1/geo-anonymize \
  -H 'Content-Type: application/json' \
  --data @examples/request-geo-anonymize.json
```

### 网格接口的 422 校验

以下情况返回 HTTP `422 Unprocessable Entity`（请求体结构错误同样为
422），错误说明在响应体 `detail` 中：

- `k` 不是正整数（含 `0`、负数、布尔值、非整数）；
- `max_zoom` 不是 0～12 的整数（含负数、超过 12、布尔值、非整数）；
- 记录总数小于 `k`（含空记录数组）；
- 某条记录缺少 id、id 不是非空字符串，或 id 在批内重复；
- 某条记录缺少 `latitude`/`longitude`，或值不是有限数值
  （含字符串、布尔值、`NaN`、无穷）；
- 坐标越界：纬度超出 -90～90 或经度超出 -180～180；
- 请求体缺少必填字段或出现未声明字段。

## 观测时间窗口匿名化

`POST /api/v1/time-anonymize`，`Content-Type: application/json`。

直接发布精确观测时刻会暴露物种的活动节律。本接口把**同一 UTC 日期**内的
观测时间归入**连续小时窗口**后再发布：所有记录先按各自时区转换到 UTC 并
按小时归槽，有记录的小时槽按时间升序划分为连续窗口——**每个窗口至少包含
k 条记录、首末小时覆盖的跨度（末小时 − 首小时 + 1 小时）不超过
`max_window_hours`，且同一小时槽内的记录不可拆分到不同窗口**。在所有可行
划分中取**全局最优**：最小化各窗口“记录数 × 跨度小时”之和；代价相同时，
按**窗口结束小时序列的字典序**选最早者。

### 请求格式

```json
{
  "k": 2,
  "max_window_hours": 4,
  "records": [
    {"id": "obs-001", "observed_at": "2026-03-15T08:12:00+00:00", "species": "金丝猴"},
    {"id": "obs-002", "observed_at": "2026-03-15T08:45:00+00:00", "species": "金丝猴"},
    {"id": "obs-003", "observed_at": "2026-03-15T09:05:00+00:00", "species": "林麝"},
    {"id": "obs-004", "observed_at": "2026-03-15T09:30:00+00:00", "species": "林麝"},
    {"id": "obs-005", "observed_at": "2026-03-15T16:20:00+08:00", "species": "云豹"},
    {"id": "obs-006", "observed_at": "2026-03-15T16:50:00+08:00", "species": "云豹"}
  ]
}
```

- `k`：正整数，每个时间窗口至少包含的记录数。
- `max_window_hours`：1～24 的整数，单个窗口首末小时覆盖的最大跨度小时数
  （`1` 表示每个窗口只覆盖一个小时槽）。
- `records`：观测记录数组，每条必须包含：
  - `id`：**非空字符串**，且在整批记录中**唯一**；
  - `observed_at`：**带时区信息**的 ISO 8601 字符串（如
    `2026-03-15T08:12:00+00:00` 或 `2026-03-15T16:20:00+08:00`，`Z` 后缀
    同样接受）；不带时区的日期时间（如 `2026-03-15T08:12:00`）会被拒绝；
  - 全部记录转换到 UTC 后必须落在**同一 UTC 日历日期**（本地日期可以不同）；
  - 其余字段（如 `species`）不参与处理，也**不会**在响应中回传。

### 响应格式

上例中 `obs-005`/`obs-006` 的本地时刻 `16:20+08:00`、`16:50+08:00` 转换到
UTC 后为 `08:20`、`08:50`，因此 UTC 08 时共 4 条、09 时共 2 条；k=2 时每个
小时槽单独成窗即最优（总代价 `4×1 + 2×1 = 6`）：

```json
{
  "records": [
    {"id": "obs-001", "window_start": "2026-03-15T08:00:00Z", "window_end": "2026-03-15T09:00:00Z"},
    {"id": "obs-002", "window_start": "2026-03-15T08:00:00Z", "window_end": "2026-03-15T09:00:00Z"},
    {"id": "obs-003", "window_start": "2026-03-15T09:00:00Z", "window_end": "2026-03-15T10:00:00Z"},
    {"id": "obs-004", "window_start": "2026-03-15T09:00:00Z", "window_end": "2026-03-15T10:00:00Z"},
    {"id": "obs-005", "window_start": "2026-03-15T08:00:00Z", "window_end": "2026-03-15T09:00:00Z"},
    {"id": "obs-006", "window_start": "2026-03-15T08:00:00Z", "window_end": "2026-03-15T09:00:00Z"}
  ],
  "windows": [
    {"window_start": "2026-03-15T08:00:00Z", "window_end": "2026-03-15T09:00:00Z", "count": 4},
    {"window_start": "2026-03-15T09:00:00Z", "window_end": "2026-03-15T10:00:00Z", "count": 2}
  ],
  "k": 2,
  "max_window_hours": 4,
  "total_records": 6
}
```

- `records`：与输入**等长、等序**，每项只含 `id` 与该记录所属窗口的
  **半开 UTC 小时区间** `[window_start, window_end)`（整点对齐，右端不含；
  覆盖 23 时的窗口右端为次日 `00:00`）；**不回传原时间或其他原字段**。
- `windows`：各发布窗口的半开区间与记录数 `count`（均 `>= k`），按时间
  升序排列；窗口跨度 = `window_end - window_start` 的小时数，均
  `<= max_window_hours`。

仓库内 `examples/request-time-anonymize.json` 为可直接使用的示例：

```bash
curl -s http://localhost:8000/api/v1/time-anonymize \
  -H 'Content-Type: application/json' \
  --data @examples/request-time-anonymize.json
```

### 时间窗口接口的 422 校验

以下情况返回 HTTP `422 Unprocessable Entity`（请求体结构错误同样为
422），错误说明在响应体 `detail` 中：

- `k` 不是正整数（含 `0`、负数、布尔值、非整数）；
- `max_window_hours` 不是 1～24 的整数（含 `0`、超过 24、布尔值、非整数）；
- 记录总数小于 `k`（含空记录数组）；
- 某条记录缺少 id、id 不是非空字符串，或 id 在批内重复；
- 某条记录缺少 `observed_at`、不是字符串、无法解析为 ISO 8601 时间，
  或不携带时区信息；
- 全部记录转换到 UTC 后不在同一日历日期；
- 不存在可行划分（某个小时槽记录不足 k 条，而与相邻槽合并又会超出
  `max_window_hours`）；
- 请求体缺少必填字段或出现未声明字段。

## 422 校验错误

以下情况返回 HTTP `422 Unprocessable Entity`（FastAPI 的请求体结构错误同样为
422），错误说明在响应体 `detail` 中：

- 记录总数小于 `k`；
- `k` 不是正整数（含 `0`、负数、布尔值）；
- 待匿名字段超过 3 个；
- 映射缺值：记录中出现的某原值（或非字符串值）在字段映射中不存在；
- 层级路径为空、超过 4 级、同字段路径不等长；
- 路径首项不等于原值，或最高级没有收敛为同一个统一通配符；
- 泛化值或通配符为空字符串；
- `sensitive_field` 与 `l` 只提供了其中一个，或 `l` 不是正整数
  （含 `0`、负数、布尔值）；
- `sensitive_field` 为空字符串，或与某个待匿名字段重合；
- 启用 l-多样性时，某条记录缺少敏感字段、敏感值不是非空字符串；
- 启用 l-多样性时，不存在任何同时满足“每组 `>= k` 条”与“每组
  `>= l` 种不同敏感值”的层级组合（即使全部字段选到统一通配符级
  把全部记录并入一组也无法满足）。

注：未启用 l-多样性时，记录总数 `>= k` 即必然存在可行方案（所有字段选到
统一通配符级后全部记录并入同一组）；启用 l-多样性后该保证不再成立。

## 数据安全

- 匿名化完全在请求处理过程中于内存完成，服务不保存任何请求内容；
- 不配置 Docker 数据卷，容器根文件系统只读；
- 请勿在反向代理/网关层开启会记录请求体的访问日志。

## 本地开发（不使用 Docker）

需要 Python 3.11+：

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
pytest
```
