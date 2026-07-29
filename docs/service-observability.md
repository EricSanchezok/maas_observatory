# 模型服务可观测性与低扰动性能测量

调研日期：2026-07-28

> 本文保留广义技术调研、术语和测量边界。MaaS Observatory 实时数据采集、
> 指标优先级、公开 API 和前端数据合同以
> [MaaS Observatory operations](maas-observatory-operations.md) 为准。当前实现
> 只使用 `/v1/models` 和 Observatory 节点发出的真实 streaming 请求，不采集
> 本文研究的 `/metrics` 或 Prometheus 数据。相关 white-box 内容仅是未来获得
> 稳定逐实例来源时的可选方案。实时观测站不包含 Benchmark、受控容量实验或对应入口。

## 范围

本文记录面向当前 9 个模型部署建设状态网站时采用的指标定义、数据来源、
采样策略和展示约束。目标是同时满足：

1. 持续观察端点可达性、生成可用性、延迟、错误和资源状态；
2. 限制监控系统自身产生的请求、输入 token、输出 token 和并发；
3. 区分实时业务观测、低频合成探测与受控容量实验；
4. 让网站上的每个数值都有时间、样本量、测量条件和来源；
5. 不把客户端可观测量标成服务端内部量，也不把单请求速度标成最大系统吞吐。

本文定义的是观测方法，不是 benchmark 分数规范。Tool-use benchmark 的结果仍按
[评测方法](methodology.md)独立生成和发布。

## 当前项目状态

`config/models.yaml` 声明了 9 个 deployment。当前 serving 元数据中：

- DeepSeek V4 Flash 记录为 `vLLM Ascend`；
- 其余 deployment 的 serving engine 仍为 `unknown`；
- 部分 deployment 已记录 reasoning parser、tool-call parser 或 chat template；
- endpoint URL 和 API key 继续只通过环境变量注入。

当前 `OpenAITransport.chat_completion` 使用非 streaming HTTP 请求，能够记录：

- 整次请求的 wall-clock latency；
- HTTP status；
- retry attempt 数；
- 响应中的 `usage`，如果服务端返回；
- transport、timeout 和 protocol failure。

它目前不能直接记录：

- first content chunk 时间；
- token 或 chunk 之间的到达间隔；
- 客户端观察到的 TTFT、TPOT 或 streaming stall；
- 服务端 queue、prefill、decode、KV cache 或 GPU 指标。

因此，现有 benchmark transport 不应直接作为完整实时观测采集器。后续可以共享
deployment catalog 和错误分类，但长驻 collector 与 benchmark runner 应保持独立。

## 监控与性能实验的边界

模型服务的持续观测和容量测试回答不同问题。

| 工作类型 | 主要问题 | 数据时效 | 对服务的额外负载 | 结果含义 |
|---|---|---:|---:|---|
| 服务端白盒监控 | 服务现在处理了多少请求，队列和资源是否接近饱和 | 秒级 | 不执行推理 | 当前内部状态 |
| 真实流量被动遥测 | 用户实际体验到什么延迟和错误 | 秒至分钟 | 不增加请求 | 当前业务体验 |
| 合成黑盒探测 | 没有真实请求时，端点是否仍能完成规定的短请求 | 分钟级 | 很低 | 固定条件下的外部可用性 |
| 受控性能实验 | 在给定输入、输出和并发下，吞吐—延迟曲线是什么 | 按运行发布 | 可显著增加 | 指定配置下的性能 |
| 最大容量实验 | 饱和点和峰值吞吐在哪里 | 低频人工安排 | 高 | 预约窗口内的容量 |

Google SRE 将 white-box monitoring 定义为读取服务内部指标，将 black-box
monitoring 定义为从用户视角测试外部行为，并建议组合使用大量白盒信号和少量关键
黑盒探测。其四个基础信号是 latency、traffic、errors 和 saturation。

持续状态网站应主要读取前两类数据。最大 TPS 不属于连续探测指标。

## 指标定义

### 请求时间线

一个 streaming 生成请求可以表示为：

```text
request submitted
    ├── network / gateway
    ├── queue
    ├── tokenization
    ├── prefill
    ├── first output token
    ├── decode and stream
    └── final output token / terminal event
```

客户端和服务端看到的边界不同。每项数据都必须携带 `observation_scope`：
`client`、`gateway`、`server` 或 `gpu`。

### TTFT

Time to First Token（TTFT）通常表示从客户端提交请求到收到第一个非空输出 token
的时间。

```text
client_ttft =
  network + gateway + queue + tokenization + prefill + first_token_generation
```

因此：

- client TTFT 不是纯 prefill time；
- server TTFT 通常不包含完整公网或跨网网络时间；
- TTFT 只能为实际产生非空输出的 streaming 请求定义；
- first SSE frame 如果只包含 role、metadata 或空 content，不应结束 TTFT 计时。

如果客户端只能识别 SSE chunk，指标名称应为 `time_to_first_content_chunk`。只有当
服务端保证一帧对应一个 token，或者有可靠 token boundary 时，才可命名为 TTFT。

### Prefill time

Prefill time 是服务端处理输入并构建所需 KV 状态的内部阶段时间。准确值需要：

- serving engine 的 request-level metric；
- server-side trace；
- 或等价的内部阶段 instrumentation。

vLLM 当前公开 `request_prefill_time_seconds`、
`request_prefill_kv_computed_tokens` 和 `request_queue_time_seconds`。没有这些
内部数据时，网站只能显示 TTFT。

使用两个输入长度在低并发下计算 TTFT 斜率，可以形成实验性的 client-side prefill
proxy，但它仍受网络、queue、cache 和调度影响，必须标为 `estimated`，不能命名为
prefill duration。

### E2E latency

End-to-end latency 是从请求提交到收到最后一个有效输出 token 或终止事件的时间：

```text
e2e_latency = ttft + generation_time
```

失败请求必须与成功请求分开统计。把快速 HTTP 失败混入成功请求 latency 会使总体
延迟看起来更低。

### ITL、TPOT 与 chunk cadence

Inter-token latency（ITL）或 Time Per Output Token（TPOT）描述第一个 token 之后
的生成阶段。NVIDIA AIPerf 使用：

```text
tpot = (e2e_latency - ttft) / (output_tokens - 1)
```

该值要求 `output_tokens >= 2` 且 token 计数可靠。

OpenAI-compatible streaming 的一个 SSE chunk 可能包含零个、一个或多个 token。
如果只能观测 chunk 到达时间，应报告：

- `time_to_first_content_chunk`；
- `time_per_output_chunk`；
- `stream_stall_seconds`；

而不是把 chunk 数当作 token 数。OpenTelemetry 的 GenAI client metric 也分别使用
first chunk 和 time per output chunk。

### TPS

“TPS”必须拆成至少三个名称明确的指标。

#### 当前系统 output throughput

```text
observed_system_output_tps =
  delta(server_generation_tokens_total) / window_seconds
```

它表示统计窗口内真实业务和探测流量合计产生的 output token rate。该值可从 vLLM、
SGLang 或网关 counter 获得，不需要额外发起推理。

它不是最大容量。没有请求时，该值为 0 只表示当前没有生成流量。

#### 单请求 decode rate

```text
single_request_decode_tps = 1 / tpot
```

或等价地：

```text
single_request_decode_tps =
  (output_tokens - 1) / (last_token_time - first_token_time)
```

这是固定 probe profile、并发 1 下的用户侧生成速度，不应显示为 server TPS。

#### 受控系统吞吐

```text
controlled_system_output_tps =
  total_output_tokens /
  (last_response_time - first_request_time)
```

它必须和 concurrency、输入长度、输出长度、持续时间、warmup、engine/version、
reasoning mode、sampling 参数及测试日期一起发布。只有专门的 load/capacity run
才能测量饱和点或峰值。

### RPS

```text
rps = completed_requests / observation_window_seconds
```

RPS 只有在输入和输出长度分布相近时才适合跨时间比较。对于 LLM 服务，RPS 必须和
prompt/output token rate 一起展示。

### 分位数

延迟应存为 histogram，并按窗口计算 p50、p95 和必要时的 p99。禁止：

- 平均多个实例各自计算出的 p95；
- 用很少的合成样本展示短窗口 p99；
- 在没有 sample count 的情况下展示 percentile。

Prometheus 建议在需要跨实例聚合时先聚合 histogram bucket，再使用
`histogram_quantile`。直接平均预计算 quantile 在统计上没有意义。

对于每 10 分钟一次的合成探测：

- 1 小时只有 6 个样本，不适合展示 p95；
- 可以展示最后一次值和 24 小时 median；
- 24 小时 p95 必须同时展示 `n=144`；
- 更高分辨率的 p95 应优先来自真实流量或 server histogram。

## 数据来源优先级

### 1. Serving engine metrics

如果运维允许从内网 scrape `/metrics`，这是实时指标的首选来源。

vLLM 可提供的常用指标包括：

- prompt/generation token counters；
- successful requests 和 finish reason；
- running/waiting requests；
- queue、prefill、decode 和 E2E latency；
- TTFT 和 inter-token latency；
- KV-cache usage；
- prefix-cache query/hit；
- preemption；
- request input/output token histogram。

SGLang 可提供对应的：

- prompt/generation/cached token counters；
- TTFT、E2E、ITL/TPOT histogram；
- running/queue requests；
- KV token usage 和 cache hit rate；
- current generation throughput；
- aborted/retracted requests；
- speculative decoding 和 disaggregated prefill/decode 指标，在相应模式启用时。

不同 engine/version 的 metric name 和语义可能变化。collector 应：

1. 保留 raw metric name；
2. 使用 versioned adapter 映射到统一 schema；
3. 记录 engine name、engine version 和 adapter version；
4. 未知或不支持的字段返回 `unavailable`，不填 0。

建议 scrape interval 为 15–30 秒。scrape 读取 counter/gauge/histogram，不执行模型
推理。Prometheus endpoint 必须保留在内网或受认证的监控网络中。

### 2. Gateway 和真实请求遥测

API gateway 或 SDK instrumentation 可以在不增加请求的情况下聚合：

- request count、in-flight requests 和 status code；
- client/gateway E2E latency；
- first content chunk 和 stream duration；
- input/output usage；
- 429、5xx、timeout 和 client cancellation；
- response model 和 finish reason。

默认不采集原始 prompt、completion、tool arguments 或 tool results。公开状态网站
只读取低基数、已聚合数据。

OpenTelemetry GenAI semantic conventions v1.41.0 包含 client token usage、
operation duration、time to first chunk、time per output chunk，以及 server TTFT、
TPOT 和 request duration。该规范当前标记为 Development，因此实现必须固定
semantic-convention 版本，不能静默改变字段含义。

### 3. GPU 和节点遥测

在 NVIDIA 环境中，DCGM Exporter 可以向 Prometheus 暴露：

- GPU utilization；
- framebuffer memory used/free；
- GPU/memory temperature；
- power 和 energy；
- SM/memory clock；
- XID、ECC、PCIe 和 NVLink 等错误或链路信号，取决于 collector 配置。

GPU 指标主要用于解释 saturation 和故障，不直接代表用户体验。公开页面通常只展示
聚合利用率或健康状态；GPU UUID、node name、pod name、内部 IP 和拓扑保留在内部
运维视图。

### 4. 合成黑盒探测

合成探测用于验证外部可见行为。它不替代真实流量或 serving metrics。

建议将探测拆成四种 profile：

| Profile | 默认频率/模型 | 建议请求 | 测量内容 |
|---|---:|---|---|
| `endpoint-liveness` | 1 分钟 | authenticated health 或 `/v1/models` | DNS、TCP、TLS、鉴权、API 路由 |
| `generation-canary` | 10 分钟 | 约 64 input、最多 8 output、concurrency 1 | 实际生成成功、TTFT、E2E |
| `stream-microprobe` | 30 分钟 | 约 128 input、最多 32 output、concurrency 1 | first chunk、chunk cadence、decode proxy、断流 |
| `tool-call-canary` | 6 小时 | 一个最小确定性工具 schema | native `message.tool_calls` 协议 |

这些数值是初始默认值，不是通用 SLO。执行频率应根据实际服务 SLO、容量、真实流量和
运维约束调整。

#### 初始 token 预算

在不计 tool-call canary、hidden reasoning token 和 tokenizer 差异时：

```text
generation-canary:
  144 requests/day/model × (64 input + 8 output)
  = 10,368 tokens/day/model

stream-microprobe:
  48 requests/day/model × (128 input + 32 output)
  = 7,680 tokens/day/model

combined:
  18,048 tokens/day/model
  162,432 tokens/day across 9 models
```

实际预算必须使用服务端 `usage` 或 server token counter 复核。对 reasoning model，
visible output cap 不一定等于全部计算 token。无法可靠读取 usage 时，使用固定
tokenizer 估计 input，按保守上界估计 output，并单独标记 completeness。

#### 负载控制

probe scheduler 至少实施：

- 每个 endpoint 最大 active inference probe 为 1；
- 全局 active inference probe 默认最多 1–2；
- 9 个 endpoint 错峰执行；
- 调度时间加入可复现的有限 jitter，避免固定整点突发；
- 分别限制每小时 request、input token 和 output token；
- 设置硬 wall-clock deadline 和最大响应字节；
- 默认不进行 SDK 自动重试；
- 首次失败保留为观测，确认探测不得覆盖首次结果；
- 验证 client disconnect 是否使服务端停止生成；
- 每次探测带稳定的 monitoring caller 标识，便于从真实业务流量中分离。

一旦能够读取真实服务负载，可以增加 load-aware gating：

- queue 已超过运营阈值时，延后 performance probe；
- KV cache 或 GPU saturation 接近阈值时，延后 performance probe；
- 真实请求 latency 已违反 SLO 时，不增加长输出 probe；
- 极短 generation health probe 可以保留，以维持黑盒可见性。

被 gating 延后的探测状态是 `deferred_due_to_load`，不是 pass、fail 或 missing。
网站应显示最近一次实际测量时间和延后原因。

建议同时实施绝对预算和相对预算：

```text
probe_request_share =
  probe_requests / (production_requests + probe_requests)

probe_input_token_share =
  probe_input_tokens / (production_input_tokens + probe_input_tokens)

probe_output_token_share =
  probe_output_tokens / (production_output_tokens + probe_output_tokens)
```

相对预算阈值需由运维根据服务规模设定。即使相对占比很低，绝对 concurrency 和 token
上限也不能取消。

#### Cache 条件

重复使用完全相同的 prompt 可能命中 prefix cache，使 TTFT 偏向 warm-cache 条件。
随机变化 prompt 又会改变 tokenization 和 cache 行为。

建议定义两个 profile：

- `warm-short`：固定 system/prompt prefix，测量稳定 canary 和共享前缀条件；
- `cold-short`：在 prompt 起始位置加入固定长度、记录在案的 nonce，降低 prefix
  reuse，低频运行。

两类结果分别展示，不合并平均。每个观测记录：

- input token 数；
- cache profile；
- 服务端 cached token 数，如果可用；
- prompt template version；
- nonce strategy version。

#### Reasoning 条件

当前 9 个模型对 thinking/no-thinking 的配置能力并不完全相同。状态探测与性能比较
需要区分：

- endpoint default behavior；
- explicitly disabled reasoning；
- explicitly enabled reasoning；
- unsupported 或 unknown。

实时可用性 canary 可以选择接近实际默认行为的 profile；跨模型性能图必须使用明确
记录的 reasoning mode，不能把不同 mode 的单请求速度直接排序。

### 5. 受控性能与容量实验

最大 system TPS、饱和点和 latency-throughput curve 仅通过受控运行生成。建议：

- 在预约低峰或维护窗口执行；
- deployment 或 serving configuration 变化后执行；
- 日常最多按月执行，除非有明确性能回归调查；
- 固定 input sequence length（ISL）和 output sequence length（OSL）；
- 记录 warmup、duration、request count 和 random seed；
- 从 concurrency 1 逐级增加；
- 到达预先声明的 queue、latency、error 或 saturation stop condition 后停止；
- 保留每个 concurrency 点，不只保留峰值；
- 发布 immutable run metadata 和原始聚合数据。

NVIDIA 的 load-control 指南指出，concurrency 是最常用的负载控制量；超过服务能力后，
排队会增加 TTFT，而 throughput 最终饱和。固定 request rate 如果高于处理能力，
outstanding request 可能无界增长，因此不能在未知容量时作为默认开放环压测方式。

容量实验结果的最小上下文：

- deployment、precision、engine 和 engine version；
- hardware/replica 配置，在可公开范围内；
- ISL/OSL distribution；
- concurrency 或 arrival process；
- reasoning、temperature、top-p 和 termination policy；
- streaming、timeout 和 retry；
- benchmark client/version；
- warmup 和测量窗口；
- p50/p95/p99 TTFT、TPOT 和 E2E；
- output TPS、prompt TPS、RPS 和 error rate；
- queue、KV cache 和 GPU saturation；
- run timestamp、Git commit 和 configuration hash。

## 探测结果和测量基础设施故障

服务失败和测量失败必须分开。

### 服务观测

以下是在预先声明的 deadline 和请求条件下得到的有效服务观测：

- DNS、TCP 或 TLS 无法建立；
- HTTP 401、403、404、408、429 或 5xx；
- first-byte、first-content 或整体请求 timeout；
- streaming 中途断开或 stall；
- invalid JSON、invalid SSE 或缺失终止事件；
- empty output；
- response model 不匹配；
- usage 缺失；
- tool call 不符合协议。

它们可以使 probe 失败或降级，但错误类型必须保留。

### 测量基础设施错误

以下不应被归到模型服务：

- probe scheduler 未运行；
- collector crash；
- 本地 DNS/网络环境整体异常且无法归因到 endpoint；
- 时钟回拨或计时器错误；
- 本地 tokenizer、parser 或 schema bug；
- Prometheus scrape/remote-write/storage failure；
- 网站查询或缓存失败。

这类记录使用 `measurement_error`，服务状态变为 `unknown` 或 `stale`，而不是
`unavailable`。

### 跳过

以下属于显式未执行：

- maintenance；
- load-aware gating；
- token budget exhausted；
- operator pause；
- profile 对该 deployment 不适用。

跳过记录必须包含 reason、decision timestamp 和 next eligible time。

## 状态模型

建议公开状态使用有限状态集：

| 状态 | 含义 |
|---|---|
| `operational` | 最近 generation canary 成功，数据新鲜，相关 SLI 未触发 degradation rule |
| `degraded` | 端点仍可生成，但 latency/error/protocol 指标持续违反预先声明的阈值 |
| `unavailable` | 连续 generation canary 失败，或被可靠黑盒/真实流量信号确认不可用 |
| `maintenance` | 运维显式声明的维护窗口 |
| `stale` | 最近成功采集已超过 freshness threshold |
| `unknown` | 测量基础设施不足以判断服务状态 |

建议默认：

- 不用单次失败直接切换为 unavailable；
- 两次连续 generation canary 失败后切换，首次失败立即显示在事件流；
- 一次成功不自动清除长期 degraded，恢复规则需与触发规则对称；
- maintenance 不能覆盖原始事件，只改变公开状态解释；
- 所有状态变化记录 rule version 和 evidence IDs。

Google Cloud synthetic monitoring 的默认提醒策略同样采用连续两次失败后通知。具体
次数和间隔仍应由本项目的 SLO 决定。

## 错误分类

状态网站建议采用以下低基数分类：

| Category | 示例 |
|---|---|
| `dns` | name resolution failure |
| `connect` | refused、reset、route unavailable |
| `tls` | certificate、handshake |
| `auth` | 401、403 |
| `rate_limit` | 429 |
| `server_4xx` | 其他请求拒绝 |
| `server_5xx` | 500–599 |
| `ttfb_timeout` | headers/first byte 未到达 |
| `ttfc_timeout` | first content chunk 未到达 |
| `request_timeout` | 整体 deadline |
| `stream_stall` | chunk 间隔超过阈值 |
| `stream_disconnect` | 未正常结束 |
| `invalid_http_body` | 非预期 body |
| `invalid_json` | JSON parse failure |
| `invalid_sse` | streaming framing failure |
| `empty_output` | 无有效 content/tool call |
| `model_mismatch` | response model 不符合允许规则 |
| `usage_missing` | 期望 usage 但未返回 |
| `tool_protocol` | 非 native tool call 或 arguments 无法解析 |
| `measurement_error` | collector、clock、storage 等观测基础设施错误 |

HTTP 200 但内容无效属于显式错误观测，不能只按 status code 统计为 success。

## 网站展示指标

### Overview

每个 deployment 卡片建议显示：

- display name、precision 和 modality；
- current status；
- `last_checked_at`；
- `last_success_at`；
- data age/freshness；
- 24 小时 generation-canary success ratio；
- 最近一次 client TTFT；
- 24 小时 synthetic TTFT median 和样本量；
- 最近一次 single-request decode rate，在 token 数据可靠时；
- observed system output TPS，在 server counter 可用时；
- current waiting/queue requests，在 server metric 可用时；
- last error category；
- maintenance 或 incident 标记；
- last controlled performance run timestamp。

### Deployment detail

详情页可按来源分区展示：

#### User-visible

- probe success/error time series；
- TTFT、TPOT/chunk cadence、E2E histogram；
- first-content 和 request timeout；
- streaming stall/disconnect；
- tool-call protocol status；
- request conditions 和 sample count。

#### Traffic and saturation

- production RPS；
- prompt/output token rate；
- running/waiting requests；
- queue time/depth；
- KV-cache usage；
- prefix-cache hit ratio；
- preemption/retraction；
- successful/failed finish reason。

#### Infrastructure

- replica health；
- GPU utilization 和 memory；
- temperature/power；
- restart count；
- engine/version；
- collector freshness。

#### Controlled results

- concurrency–TPS curve；
- concurrency–TTFT/TPOT/E2E curve；
- saturation/stop point；
- run configuration 和 immutable artifact link；
- 与上次同配置运行的差异。

## 每个公开数值的最小元数据

网站 API 返回的 metric point 或 aggregate 至少包含：

```yaml
schema_version: 1
deployment_id: string
metric_name: string
value: number
unit: string
observed_at: RFC3339 timestamp
window_start: RFC3339 timestamp | null
window_end: RFC3339 timestamp | null
sample_count: integer
source_kind: server | gateway | synthetic | controlled-test
observation_scope: client | gateway | server | gpu
collector_version: string
definition_version: string
quality: exact | estimated | incomplete | unavailable
freshness_seconds: number
conditions:
  profile_id: string | null
  input_tokens: integer | null
  output_tokens: integer | null
  max_output_tokens: integer | null
  concurrency: integer | null
  streaming: boolean | null
  reasoning_mode: string | null
  cache_profile: warm | cold | unknown | null
```

禁止用 null、0 和 unavailable 表达同一个含义：

- `0` 是真实测得的零；
- `null` 表示该字段不适用于当前记录；
- `unavailable` 表示适用但当前数据源不能提供；
- `incomplete` 表示只有部分 evidence。

## 时间与可追溯性

所有内部存储使用 UTC RFC 3339。网站可以同时显示：

- UTC；
- 用户选择的时区；
- 相对时间，例如“23 秒前”。

每个面板必须区分：

- observation time：事件实际发生时间；
- collection time：collector 接收到数据的时间；
- publication time：网站 API 提供该聚合的时间；
- window：统计覆盖范围。

配置、probe payload 和状态规则变更必须版本化。历史图不应在规则升级后静默重算为
新的语义；如需重算，保留原始版本并记录 migration。

## 公开视图与内部视图

| 可公开 | 建议仅内部 |
|---|---|
| deployment display name、precision | endpoint URL、internal IP、port |
| aggregate status 和时间 | API key 或 auth header |
|聚合 latency、error、token rate | raw prompt、completion、tool payload |
| profile、sample count、methodology | request/trace ID 原值 |
|公开的 engine family/version | node、pod、GPU UUID 和精确拓扑 |
|维护与 incident 摘要 | tenant/user 标识 |
|受控实验 artifact | 未聚合的真实业务流量 |

Prometheus、SGLang/vLLM metrics endpoint 和 DCGM endpoint 不直接暴露给浏览器。公开
网站只访问经过 allowlist、aggregation 和 redaction 的 read-only API。

## 推荐架构

```text
model endpoints
  ├── serving /metrics ───────────────┐
  ├── gateway OpenTelemetry ──────────┤
  ├── low-impact probe scheduler ─────┤
  └── controlled performance runner ──┤
                                      v
                           normalization adapters
                           + provenance validation
                                      |
                     ┌────────────────┴────────────────┐
                     v                                 v
             time-series storage               metadata/event store
             counters/histograms               config/incidents/runs
                     └────────────────┬────────────────┘
                                      v
                           sanitized read-only API
                                      |
                            public status website
```

职责边界：

- probe scheduler 只负责按预算生成黑盒观测；
- metrics collector 只负责 scrape/receive；
- normalization adapter 处理 engine/version 差异；
- time-series backend 保存可聚合数值；
- metadata store 保存 deployment、profile、规则、事件和 run provenance；
- public API 执行聚合、freshness、redaction 和 authorization；
- website 不直接持有 endpoint credential。

Prometheus 或兼容时序数据库适合 counter、gauge 和 histogram。事件、maintenance、
配置版本和 controlled run metadata 可以使用关系数据库。是否使用 Grafana 或自定义
前端不改变上述数据边界。

## 实施顺序

### Phase 0：Serving inventory

与运维确认每个 deployment 的：

- serving engine 和版本；
- metrics endpoint 是否存在；
- metrics authentication/network policy；
- replica 和 router 拓扑；
- tokenizer 和 usage 语义；
- reasoning 与 streaming 行为；
- client cancellation 是否传播；
- maintenance source；
- 允许的监控 request/token/concurrency budget。

该阶段不发送性能负载。

### Phase 1：Passive-first telemetry

1. 接入 serving `/metrics`；
2. 接入 gateway/OpenTelemetry 聚合；
3. 建立 engine adapter 和统一 schema；
4. 建立 freshness、missing 和 measurement-error 规则；
5. 先实现内部 dashboard 验证指标含义。

### Phase 2：Low-impact probes

1. 先启用 endpoint liveness；
2. 单 deployment 验证 generation canary；
3. 验证 token budget 和 cancellation；
4. 错峰扩展到 9 个 deployment；
5. 再启用 streaming 和 tool-call profile；
6. 观察至少一周后确定公开 SLO 和状态阈值。

### Phase 3：Public status site

1. 发布 sanitized read-only API；
2. 发布 overview、deployment detail、methodology 和 incident history；
3. 为每项数据展示 source、window、sample count 和 freshness；
4. 加入 stale/unknown/maintenance，不把 missing 显示为 healthy；
5. 加入 metric-definition changelog。

## 上线前检查清单

- [ ] 9 个 deployment 的 engine/version 已知；
- [ ] metric adapter 有 fixture 和 schema test；
- [ ] service failure 与 measurement failure 分开；
- [ ] probe request/input/output budget 有硬限制；
- [ ] endpoint 之间错峰且 concurrency 有界；
- [ ] timeout 后服务端 generation cancellation 已验证；
- [ ] TTFT 不包含空 metadata chunk；
- [ ] chunk cadence 未标成 token latency；
- [ ] prefill proxy 未标成真实 prefill time；
- [ ] observed throughput 未标成 peak capacity；
- [ ] p95 面板展示 window 和 sample count；
- [ ] percentile 由 histogram 正确聚合；
- [ ] retry 不覆盖初次失败；
- [ ] load-gated probe 记录为 deferred；
- [ ] stale、unknown 和 maintenance 状态可见；
- [ ] public API 不含 secret、endpoint 或 raw content；
- [ ] metric、profile、rule 和 collector 都有版本；
- [ ]受控 run 记录完整配置和时间；
- [ ] methodology 页面与实际实现一致。

## 主要来源

以下资料均于 2026-07-28 访问。

1. [Google SRE: Monitoring Distributed Systems](https://sre.google/sre-book/monitoring-distributed-systems/)：
   white-box/black-box、四个黄金信号、分位数和监控分辨率。
2. [Google Cloud: Create a synthetic monitor](https://docs.cloud.google.com/monitoring/synthetic-monitors/create)：
   合成监控频率、负载/成本权衡、执行历史与连续失败通知。
3. [NVIDIA NIM LLM Benchmarking: Metrics](https://docs.nvidia.com/nim/benchmarking/llm/latest/metrics.html)：
   TTFT、E2E、ITL/TPOT、system TPS、per-user TPS 和 RPS 定义。
4. [NVIDIA NIM LLM Benchmarking: Parameters and Best Practices](https://docs.nvidia.com/nim/benchmarking/llm/latest/parameters.html)：
   ISL/OSL、concurrency、request rate、queue 和 load sweep。
5. [vLLM Production Metrics](https://docs.vllm.ai/en/stable/usage/metrics.html)：
   serving `/metrics`、queue/prefill/decode、token、KV cache 和 latency
   指标。
6. [SGLang Production Metrics](https://docs.sglang.io/docs/references/production_metrics)：
   SGLang token、latency、queue、cache 和 generation throughput 指标。
7. [OpenTelemetry GenAI Metrics v1.41.0](https://github.com/open-telemetry/semantic-conventions/blob/v1.41.0/docs/gen-ai/gen-ai-metrics.md)：
   client/server GenAI metric naming and attributes；文档状态为 Development。
8. [Prometheus: Histograms and summaries](https://prometheus.io/docs/practices/histograms/)：
   histogram 聚合、quantile、bucket 和估计误差。
9. [NVIDIA DCGM Exporter](https://docs.nvidia.com/datacenter/cloud-native/gpu-telemetry/latest/dcgm-exporter.html)：
   GPU Prometheus telemetry。
10. [MLPerf Inference Rules](https://github.com/mlcommons/inference_policies/blob/master/inference_rules.adoc)：
    system under test、run、复现和公开结果的一般要求。
