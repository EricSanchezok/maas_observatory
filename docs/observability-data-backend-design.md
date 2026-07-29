# MaaS Observatory 数据后端服务设计

状态：设计稿
日期：2026-07-28
范围：9 个 OpenAI-compatible 模型部署的实时与近实时服务观测

## 1. 决策摘要

MaaS Observatory 只回答模型服务当前是否可用、速度如何、是否拥塞以及变化发生在何时。

本系统不展示：

- Benchmark 分数；
- 需要数小时或数十小时运行的评测结果；
- 峰值容量或最大 TPS；
- 无法由当前数据源可靠测得的阶段耗时；
- 用少量探测请求计算后看似精确的可用率或高分位数。

数据获取遵循以下顺序：

1. 优先读取 serving engine、gateway 和真实请求的被动遥测；
2. 没有新鲜被动数据时，才执行受预算约束的合成探测；
3. 合成性能探测仅在服务低负载时执行；
4. 所有数值携带来源、时间、窗口、样本量、条件和质量等级；
5. 无法可靠测量时返回 `unavailable`，不使用估算值填充界面。

面向用户的优先级为：

| 优先级 | 用户问题 | 主要指标 |
|---|---|---|
| P0 | 现在生成得快不快 | 单请求 decode tokens/s、TTFT、最近测量时间 |
| P0 | 9 个模型谁更快 | 同一 profile 下的最新值与 24 小时中位数 |
| P0 | 是否正在拥塞 | waiting/running requests、queue time、当前系统 output tokens/s |
| P1 | 服务是否还能正常生成 | 当前状态、最近成功生成、错误类型、数据新鲜度 |
| P1 | 速度是否正在变差 | 24 小时和 7 天趋势、相对自身历史基线的变化 |
| P2 | 为什么变慢 | prefill、decode、KV cache、preemption、GPU 信号，仅在来源可靠时 |

## 2. 对现有研究文档的审计

[`service-observability.md`](service-observability.md) 的基础定义总体合理，可以继续作为
技术研究和术语来源。

### 2.1 保留

- white-box、真实流量、black-box probe 和容量实验的边界；
- client、gateway、server、gpu 四种 observation scope；
- TTFT、first content chunk、TPOT、E2E 和 TPS 的定义；
- 失败请求与成功请求分开统计；
- HTTP 200 但输出无效仍属于失败；
- 服务失败与测量基础设施失败分开；
- `stale`、`unknown`、`maintenance` 等状态；
- histogram、sample count、freshness 和 definition version；
- probe 的 request、token、concurrency 硬预算；
- endpoint credential 不进入浏览器或公开数据。

### 2.2 调整

| 现有建议 | 问题 | 新决策 |
|---|---|---|
| Overview 首要展示 24 小时 generation-canary success ratio | 稀疏探测通常接近 100%，区分度低，容易制造虚假的精确感 | 首屏只显示当前状态、最后成功时间和异常事件；比例移至详情 |
| 单一 deployment 的性能作为主要展示 | 9 个模型无法快速比较 | 默认提供全部模型速度比较，并保留模型选择器查看单模型趋势 |
| controlled performance run 进入网站 | 与实时观测时效和负载模型不同 | 不进入 MaaS Observatory |
| Benchmark 页面或入口进入同一网站 | 静态评测与服务观测语义不同 | 完全移除 |
| 同时规划大量内部指标 | 信息优先级不清晰 | 按 P0/P1/P2 分级，前端默认只读取 P0/P1 |
| 固定频率持续执行所有 probe | 可能在服务繁忙时增加排队 | passive-first、idle-only、load-aware gating |
| request latency composition 总是可视化 | queue、network、prefill、decode 不一定都可测 | 只有 exact server/gateway spans 齐全时才展示阶段拆分 |

### 2.3 结论

现有文档适合作为广义研究记录，但不应直接作为实时观测站的产品规格。本文件是
MaaS Observatory 数据采集、公开 API 和前端信息层级的权威设计。

## 3. 观测边界

### 3.1 观测对象

`config/models.yaml` 中的 9 个 deployment 是唯一模型目录来源。每个 deployment 必须
具有稳定的：

- `deployment_id`；
- display name；
- upstream model；
- precision；
- serving engine 和 version，在已知时；
- supported profiles；
- endpoint credential environment references。

endpoint URL、API key、内部 IP、pod、node 和 GPU UUID 不进入公开 API。

### 3.2 数据类型

系统只处理四类数据：

| 类型 | 目的 | 是否增加推理负载 |
|---|---|---:|
| Engine metrics | 速度、队列、token rate、cache 和 server latency | 否 |
| Gateway telemetry | 用户可见 latency、错误、流量和 streaming | 否 |
| Infrastructure metrics | saturation 与故障解释 | 否 |
| Synthetic probes | 无真实流量时确认生成与测量单请求速度 | 是，受严格预算 |

Benchmark、agent evaluation 和受控容量测试不属于本数据服务。

## 4. 指标目录与价值

### 4.1 P0：生成速度

#### `single_request_decode_tps`

用户最关心的本地部署速度指标。

```text
single_request_decode_tps =
  visible_output_tokens_after_first /
  (last_content_time - first_content_time)
```

要求：

- streaming request；
- 至少 16 个可计数的 visible output tokens；
- first content 和 last content timestamp 有效；
- tokenizer 与 deployment/profile 版本固定；
- reasoning mode、input tokens、output tokens 和 cache profile 记录完整。

不能可靠得到 token boundary 时，可以使用服务端
`time_per_output_token` histogram 的倒数形成聚合 decode rate，但必须标记
`observation_scope=server`。SSE chunk 数不得当作 token 数。

前端展示：

- selected model 当前值；
- 24 小时 median；
- 24 小时 p10-p90 范围；
- 同 profile 的 9 模型横向比较；
- measurement age。

不展示没有样本量的 p95。

#### `time_to_first_content_seconds`

```text
request_sent_at -> first non-empty content/tool-call delta
```

客户端只能确定 chunk 时，公开名称为 “First response”，内部 metric name 保留
`time_to_first_content_seconds`，不声称是纯 prefill。

前端展示：

- latest；
- 24 小时 median；
- 与 selected model 自身 7 天 median 的变化。

#### `observed_system_output_tps`

```text
rate(server_generation_tokens_total[window])
```

表示当前所有真实流量与监控流量产生的总 output token rate。它用于判断系统是否忙，
不是单用户速度，也不是峰值容量。

前端名称为 “Current output rate”，并与
`single_request_decode_tps` 分开。

### 4.2 P0：拥塞

| 统一指标 | 首选来源 | 含义 |
|---|---|---|
| `requests_running` | engine gauge | 当前执行中的请求 |
| `requests_waiting` | engine gauge | 当前排队请求 |
| `queue_time_seconds` | engine histogram | 进入执行前等待时间 |
| `inflight_requests` | gateway gauge | gateway 观察到的未完成请求 |
| `request_rate` | gateway/server counter | 当前请求流量 |
| `output_token_rate` | engine counter | 当前 output token throughput |

`requests_waiting > 0` 是卡数紧张时的重要早期信号。首页速度图与 queue 图使用同一时间轴，
方便判断速度下降是否由排队导致。

### 4.3 P1：可用性与错误

可用性不以 36 个几乎全绿的格子作为首要图表。

公开状态由近期 evidence 计算：

| 状态 | 条件 |
|---|---|
| `operational` | 数据新鲜且最近生成/真实请求成功 |
| `slow` | 可生成，但速度或 queue 持续超过已声明阈值 |
| `degraded` | 错误、stream stall 或协议问题持续出现 |
| `unavailable` | 连续 generation evidence 失败并满足确认规则 |
| `maintenance` | 运维显式维护 |
| `stale` | 超过 freshness threshold |
| `unknown` | 观测系统不足以判断 |

首页只显示：

- 当前状态；
- `last_success_at`；
- `last_observed_at`；
- active event。

24 小时 success ratio 仅在详情或数据下载中提供，并必须同时展示：

- denominator；
- passive 与 synthetic sample 数；
- window；
- missing/deferred count；
- rule version。

### 4.4 P1：端到端体验

| 指标 | 价值 |
|---|---|
| `e2e_latency_seconds` | 用户等待完整回答的时间 |
| `stream_stall_seconds` | 输出过程中是否停顿 |
| `request_error_rate` | 显式、隐式和策略失败 |
| `cancellation_rate` | 用户取消或客户端断开 |
| `input_token_rate` | 当前 prefill 工作量 |
| `output_token_rate` | 当前 decode 工作量 |

成功与失败请求的 latency 分开聚合。

### 4.5 P2：原因与资源

以下指标用于解释 P0/P1 变化，不占据首屏：

- prefill duration；
- decode duration；
- prefix/KV cache hit ratio；
- KV cache usage；
- preemption/retraction；
- GPU utilization；
- GPU memory usage；
- temperature、power 和硬件错误；
- replica restart；
- engine/version change。

`prefill_duration` 只有在 engine request-level metric 或 server trace 存在时才发布。
客户端 TTFT 斜率不命名为 prefill。

## 5. 数据来源优先级

### 5.1 Engine metrics

采样间隔：15 秒，允许按部署调整为 15-30 秒。

对于 vLLM adapter，优先映射：

- generation/prompt token counters；
- running/waiting requests；
- TTFT、TPOT、E2E histograms；
- queue、prefill、decode histograms；
- KV cache usage；
- preemption；
- request success/finish reason。

每个 adapter 固定：

- engine name；
- engine version range；
- raw metric name；
- normalized metric name；
- unit conversion；
- labels allowlist；
- adapter version；
- fixture tests。

未知 engine 不假设使用 vLLM metric name。

### 5.2 Gateway/OpenTelemetry

Gateway 或 SDK 以被动方式记录：

- request start/end；
- first non-empty content；
- status/error type；
- streaming flag；
- input/output usage，在可靠时；
- cancellation；
- response model；
- production 与 monitoring caller。

默认不记录 prompt、completion、tool arguments 或 tool results。

OpenTelemetry GenAI semantic conventions 当前处于 Development。实现必须固定使用的
版本，并通过内部 mapping layer 转换，不能直接把上游字段作为稳定公开契约。

### 5.3 Infrastructure

GPU 和 node telemetry 采样间隔建议 15 秒。公开 API 只返回 deployment 级聚合。
具体节点、GPU、pod 和拓扑保留在内部系统。

### 5.4 Synthetic probe

Synthetic probe 是被动数据的补充，不是默认主数据源。

#### Profile A：`route-liveness`

| 项目 | 初始值 |
|---|---|
| 请求 | authenticated health 或 `/v1/models` |
| 频率 | 每模型 60 秒 |
| 推理 | 否 |
| 目的 | DNS、connect、TLS、auth、route |

#### Profile B：`generation-canary`

| 项目 | 初始值 |
|---|---|
| 启动条件 | 5 分钟内没有新鲜真实请求 evidence |
| 频率上限 | 每模型 15 分钟一次 |
| input | 约 64 tokens |
| output cap | 8 visible tokens |
| concurrency | 1 |
| 目的 | 实际生成成功、first content、E2E |

#### Profile C：`speed-microprobe`

| 项目 | 初始值 |
|---|---|
| 启动条件 | 15 分钟内没有可用速度样本且通过 load gate |
| 频率上限 | 每模型 30 分钟一次 |
| input | 固定约 128 tokens |
| output target | 64 visible tokens |
| streaming | true |
| concurrency | 1 |
| sampling | temperature 0 或等价确定性设置 |
| 目的 | decode tokens/s、first content、stream stalls |

9 模型理论上限：

```text
48 probes/day/model × 9 models = 432 requests/day
432 × 128 input tokens = 55,296 input tokens/day
432 × 64 output tokens = 27,648 visible output tokens/day
```

这是 hard ceiling，不是目标使用量。真实流量能够提供有效数据时，实际 probe 数应明显
低于该上限。

reasoning model 的 hidden token 无法可靠计量时：

- 记录服务端 reported usage；
- 记录 visible token count；
- 不把两者混为一个 throughput；
- 无法关闭 reasoning 的 deployment 使用独立 profile，不进入跨模型速度排序。

## 6. Probe 调度与服务保护

### 6.1 硬限制

- endpoint active inference probe：最多 1；
- 全局 active inference probe：默认最多 1；
- 9 个 endpoint 错峰；
- 禁止 SDK 自动 retry；
- request、input token、output token 按小时和按日限额；
- wall-clock deadline；
- max response bytes；
- probe caller 标签；
- client disconnect 后确认 server cancellation；
- 首次失败保留，确认请求不能覆盖首次 evidence。

### 6.2 Load gate

`speed-microprobe` 在任一条件成立时延后：

- `requests_waiting > 0`；
- production request 或 token rate 超过 deployment 的运营阈值；
- KV cache、GPU 或 memory saturation 超过运营阈值；
- 当前 P95 TTFT 已违反 SLO；
- maintenance；
- token/request budget 不足；
- engine telemetry stale，无法判断负载。

延后记录为：

```yaml
outcome: deferred
reason: queue_nonzero | load_high | saturation_high | maintenance |
  budget_exhausted | telemetry_stale
decided_at: RFC3339
next_eligible_at: RFC3339
```

`deferred` 不计入成功率或失败率。

## 7. 统一数据模型

### 7.1 Metric observation

```yaml
schema_version: 1
deployment_id: string
metric_name: string
value: number | null
unit: string
observed_at: RFC3339
collected_at: RFC3339
window_start: RFC3339 | null
window_end: RFC3339 | null
sample_count: integer
source_kind: engine | gateway | infrastructure | synthetic
observation_scope: client | gateway | server | gpu
quality: exact | estimated | incomplete | unavailable
profile_id: string | null
profile_version: string | null
reasoning_mode: string | null
input_tokens: integer | null
output_tokens: integer | null
cache_profile: warm | cold | unknown | null
collector_version: string
adapter_version: string
definition_version: string
```

### 7.2 Service state

```yaml
deployment_id: string
state: operational | slow | degraded | unavailable | maintenance | stale | unknown
effective_at: RFC3339
last_observed_at: RFC3339
last_success_at: RFC3339 | null
evidence_ids: [string]
rule_version: string
active_event_id: string | null
```

### 7.3 Probe run

```yaml
probe_id: string
deployment_id: string
profile_id: string
scheduled_at: RFC3339
started_at: RFC3339 | null
finished_at: RFC3339 | null
outcome: success | service_error | measurement_error | deferred | skipped
error_category: string | null
request_budget: object
measurement_ids: [string]
```

`0`、`null`、`unavailable` 和 `deferred` 具有不同语义，禁止互换。

## 8. 聚合与存储

### 8.1 存储

```text
engine /metrics ───────┐
gateway OTLP ──────────┤
GPU metrics ───────────┼─> collectors -> versioned adapters
synthetic probes ──────┘                       |
                                                v
                                  normalized metric/event stream
                                          /             \
                                         v               v
                              time-series storage   metadata store
                                         \               /
                                          v             v
                                     read aggregation layer
                                               |
                                               v
                                    sanitized public API
```

- time-series storage：counter、gauge、histogram、recording rules；
- metadata store：deployment、profile、state、event、rule 和 adapter version；
- object storage：必要的聚合快照，不存公开页面不需要的原始内容。

### 8.2 建议保留周期

| 粒度 | 保留 |
|---|---:|
| raw 15 秒 engine/gateway metrics | 7 天 |
| 1 分钟聚合 | 30 天 |
| 5 分钟聚合 | 1 年 |
| state transition 与 event | 长期 |
| synthetic probe summary | 1 年 |

具体周期由存储预算调整，但公开 API 的窗口语义保持稳定。

### 8.3 聚合规则

- counter 使用 `rate`/`increase`，处理 reset；
- gauge 直接读取或按时间聚合；
- latency 使用可聚合 histogram；
- 跨 replica 先聚合 bucket，再计算 quantile；
- 不平均各实例已计算的 p95；
- 少于 20 个样本不发布 p95；
- synthetic speed 默认发布 median 与 p10-p90；
- 成功和失败 latency 分开；
- 跨模型比较只使用相同 profile、reasoning mode 和质量等级。

## 9. 公开 API

### 9.1 Overview

```http
GET /api/v1/overview?window=24h
```

返回：

- 9 个 deployment 当前状态；
- 当前或最近可用 decode tokens/s；
- first content；
- waiting/running；
- data age；
- active event。

### 9.2 单模型时间序列

```http
GET /api/v1/deployments/{deployment_id}/series
  ?metrics=single_request_decode_tps,time_to_first_content_seconds,requests_waiting
  &window=24h
  &resolution=5m
```

### 9.3 跨模型比较

```http
GET /api/v1/compare
  ?metric=single_request_decode_tps
  &window=24h
  &profile=speed-microprobe-v1
  &aggregate=median
```

只返回可比较样本。不可比较 deployment 返回明确 reason。

### 9.4 事件

```http
GET /api/v1/events?window=30d
GET /api/v1/deployments/{deployment_id}/events?window=30d
```

### 9.5 Freshness

每个响应包含：

```yaml
generated_at: RFC3339
data_window: object
sample_count: integer
source_mix: object
quality: exact | estimated | incomplete | unavailable
freshness_seconds: integer
```

浏览器不直接访问 Prometheus、OTLP、engine metrics 或 endpoint。

## 10. 前端数据合同与阅读顺序

页面从上到下固定为：

1. 模型选择器与 9 模型当前状态；
2. selected model 当前速度；
3. selected model 24 小时 decode tokens/s 曲线；
4. 9 模型同 profile 速度比较；
5. selected model first response 与 E2E 趋势；
6. 当前 running、waiting、output token rate；
7. saturation 与诊断指标；
8. 最近状态变化和错误事件；
9. measurement age、sample count 和 data source。

界面不包含 Benchmark、容量实验或静态官方分数入口。

如果 selected model 没有某项可靠数据：

- 图表显示 `Unavailable`；
- 显示缺失原因；
- 不使用另一个模型或估算值填充；
- 不把无数据绘制为 0。

### 10.1 Availability 的展示规则

Availability 是异常检测信号，不是首屏主图。

- 全部正常时：一行 9 模型状态；
- 有异常时：异常模型提升到可见事件区域；
- 历史只突出 failure、slow、stale 和 maintenance 区间；
- 不用大量相同绿色格子占据主要视觉空间；
- 比例图必须显示 denominator 和 source mix。

### 10.2 阶段耗时的展示规则

只有以下条件同时满足，前端才展示 queue/network/prefill/decode composition：

- queue 来自 engine 或 trace；
- network 来自 gateway/client trace；
- prefill/decode 来自 engine；
- observation window 和 request population 对齐；
- adapter 将各阶段定义为互斥且可相加。

否则分别展示可用指标，不绘制伪精确的堆叠柱。

## 11. 状态计算与事件

状态规则必须：

- 简单；
- 可解释；
- versioned；
- 以用户可见症状为主；
- 对触发和恢复使用对称的持续条件；
- 不因单个稀疏 probe 抖动。

初始规则由一周内部观测数据校准后发布。未校准前不公开“慢”阈值。

事件至少记录：

- first observed；
- affected deployments；
- signal；
- current state；
- last update；
- resolved at；
- evidence IDs；
- rule version。

## 12. 安全与隐私

- secrets 只存在于 collector/probe runtime；
- public API 使用字段 allowlist；
- 禁止 raw prompt、completion、tool payload；
- trace ID 对外使用不可逆 public ID；
- internal endpoint、node、pod、GPU UUID 不公开；
- 高基数 user/tenant/request labels 不进入公共时序；
- public API read-only；
- 所有数据输出经过 freshness、quality 和 redaction validation。

## 13. 实施顺序

### Phase 0：Inventory

确认 9 个 deployment 的：

- engine/version；
- `/metrics`；
- gateway telemetry；
- tokenizer；
- streaming/usage 行为；
- reasoning profile；
- cancellation；
- probe budget。

### Phase 1：Passive-first

1. engine adapters；
2. gateway metrics；
3. unified schema；
4. speed、queue、error recording rules；
5. internal validation dashboard。

### Phase 2：Synthetic fallback

1. route liveness；
2. generation canary；
3. speed microprobe；
4. load gate；
5. token/request budget audit。

### Phase 3：Public API

1. overview；
2. deployment series；
3. comparison；
4. events；
5. freshness/quality metadata。

### Phase 4：Public UI

只接入本文件第 10 节定义的动态指标和事件。

## 14. 验收标准

- 9 模型均可独立选择；
- 首屏速度数据不依赖 Benchmark；
- 每个速度值可以追溯到 source、profile、time 和 sample count；
- synthetic probe 在高负载时自动延后；
- probe 不并发冲击多个 deployment；
- availability 不以稀疏样本制造高精度百分比；
- TTFT、first chunk 和 prefill 不混用；
- system TPS 与 single-request decode TPS 不混用；
- 没有 exact 阶段数据时不绘制 latency composition；
- stale、unknown、deferred 和 service failure 可区分；
- Benchmark 和容量实验不出现在 public API 或 UI；
- public API 不泄露 secret、endpoint 或 raw content。

## 15. 参考资料

1. [Google SRE: Monitoring Distributed Systems](https://sre.google/sre-book/monitoring-distributed-systems/)
2. [vLLM Production Metrics](https://docs.vllm.ai/en/stable/usage/metrics.html)
3. [OpenTelemetry GenAI Metrics](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-metrics.md)
4. [NVIDIA NIM LLM Benchmarking Metrics](https://docs.nvidia.com/nim/benchmarking/llm/latest/metrics.html)
5. [Prometheus Histograms and Summaries](https://prometheus.io/docs/practices/histograms/)
