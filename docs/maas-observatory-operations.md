# MaaS Observatory 运行与维护

本文描述当前仓库中 MaaS Observatory 的实际运行方式。服务是单进程、单副本，
同源提供 React 观测界面与公开只读 API；所有写操作仅由本地 CLI 和进程内后台任务
执行。

## 快速启动

复制环境变量模板并填写 9 个 deployment 的推理 URL、API key，以及每个实际
serving instance 的 metrics URL：

```bash
cp .env.example .env
uv sync --all-extras --frozen
npm --prefix frontend ci
npm --prefix frontend run build
uv run maas-observatory serve
```

服务监听 `0.0.0.0:8080`。在远程容器中运行时，通过 VS Code Ports 面板转发
`8080`，不需要在应用内配置 TLS。`/healthz` 表示 HTTP 进程存活；
`/readyz` 仅在 migration、SQLite `quick_check`、catalog 同步和 writer 启动成功后
返回 200。

前端构建产物位于 `frontend/dist/`，不提交到 Git。生产服务检测到该目录后在 `/`
提供界面，在 `/assets/` 提供带内容哈希的静态资源；未构建前端时 `/` 仅返回 API
服务信息。前端开发模式为：

```bash
# 终端 1：API 与采集任务
uv run maas-observatory serve

# 终端 2：Vite HMR，监听 5173 并代理 /api、/healthz、/readyz
npm --prefix frontend run dev
```

同源部署不需要 CORS。前后端分离时，只允许通过环境变量列出的 origin：

```bash
MAAS_OBSERVATORY_CORS_ORIGINS=https://status.example.edu
```

多个 origin 用逗号分隔。API 没有通配 CORS、认证、写端点或远程 probe 控制端点。

## 数据目录

运行数据固定在：

```text
var/maas-observatory/
├── observatory.sqlite3
├── backups/
└── exports/
```

整个 `var/` 被 Git 忽略。clone 目录必须可写且持久；删除 clone 目录会同时删除数据库。
SQLite 使用 WAL、`foreign_keys=ON`、5 秒 busy timeout、`synchronous=NORMAL` 和
incremental auto-vacuum。应用只允许一个 writer task，因此不得使用多个 Uvicorn
worker，也不得水平启动多个实例指向同一个数据库文件。

## 运维命令

```bash
# 只执行 migration
uv run maas-observatory db migrate

# 执行 PRAGMA quick_check
uv run maas-observatory db check

# 使用 SQLite online backup API 创建一致性备份
uv run maas-observatory db backup

# 导出不含 endpoint、credential、prompt 或 completion 的快照
uv run maas-observatory export --format json
uv run maas-observatory export --format csv

# 检查 metrics、route、profile；此模式不产生 generation
uv run maas-observatory inventory --no-generation

# inventory 默认还会对每个 deployment 人工执行一次短体验 probe
uv run maas-observatory inventory

# 人工执行单个 probe；不加 --force 时仍受 load gate 和预算约束
uv run maas-observatory probe run --model glm-5.2 --kind experience_short

# schema v1 → v2 必须显式备份、重建；serve 不会静默清空旧历史
uv run maas-observatory db backup
uv run maas-observatory db reset --confirm metrics-source-v2
uv run maas-observatory db migrate
```

`inventory` 的 generation 模式和 `probe run` 是明确的操作员动作。常驻服务不提供
对应 HTTP 写接口。

## 采集与探测

- 每 15 秒并发读取逐实例 `/metrics`，最大并发 4、超时 12 秒、响应上限
  8 MiB，并且不 retry。负载均衡地址不能作为 metrics source。
- route liveness 每 60 秒调用 `/v1/models`，不产生推理。
- `interactive-short-v1` 每 90 秒最多调度一个 deployment，每个 deployment
  至少间隔 30 分钟；`context-16k-v1` 每个 deployment 至少间隔 6 小时。
- Running 大于零不会阻止短体验采样；Waiting 非零、telemetry 不完整、KV peak
  超限或 maintenance 会阻止采样。长上下文还要求 KV peak 小于 50% 且近期没有
  preemption。
- 所有推理 probe 的全局并发为 1。每个 deployment 每日最多 48 次短体验、
  4 次长上下文、合计 52 次；配置 input/output token 预算分别为 25,000/3,584。
  预算与 round-robin 位置保存在 SQLite，重启不会清零。

体验 TPS 仅在 streaming usage 含 completion token 数且存在有效 token 事件时
产生。缺少 usage、token 不足、profile 不明确或 stream 无有效事件时返回
`unavailable`；实现不会用字符数或 chunk 数估算 token。

服务端 `aggregate_output_tps` 先在 `(deployment_id, source_id)` 内计算
generation counter rate，再对 fresh source 求和。Running/Waiting 求和、KV 取
source peak；任一 source 缺失时质量为 `incomplete`，不会推算缺失流量。
`observed_decode_tps` 已从公开合同删除。

## 公开 API

```text
GET|HEAD /healthz
GET|HEAD /readyz
GET|HEAD /api/v1/catalog
GET|HEAD /api/v1/overview?window=24h
GET|HEAD /api/v1/deployments/{id}/series
GET|HEAD /api/v1/experience/overview
GET|HEAD /api/v1/deployments/{id}/experience/latest
GET|HEAD /api/v1/deployments/{id}/experience/series
GET|HEAD /api/v1/experience/profiles
GET|HEAD /api/v1/compare
GET|HEAD /api/v1/events
GET|HEAD /api/v1/meta
```

动态 API 使用 ETag，并返回
`Cache-Control: public, max-age=10, stale-while-revalidate=30`。无数据的数值是
`null` 并附带原因，不用 `0` 代替。`overview` 只包含 server-side telemetry；
experience API 只包含 observer-path 主动体验；`compare` 只读取固定
`interactive-short-v1`，并明确标记
`observation_scope=observer_path`。每个 comparison item 将窗口内最近一次
成功测量（`value`、`measured_at`、`sample_count`）与最近一次调度尝试
（`latest_attempt_outcome`、`latest_attempt_reason`、`latest_attempt_at`）
分开返回；较新的门禁跳过不会覆盖已有成功值。公开门禁原因只使用
`busy`、`telemetry_pending`、`recently_active`、`budget_deferred`、
`scheduled_interval`、`maintenance`、`awaiting_turn`、`deferred` 和
`attempt_failed` 等中立枚举。

公开响应不会包含 endpoint、API key、内部 IP、pod/node/GPU 标识、原始 label、
prompt 或 completion。错误统计分为：

- `service_failures`：有服务端或协议证据的失败；
- `transport_unconfirmed`：DNS、connect、TLS 或 timeout，尚未归因为 outage；
- `measurement_errors`：parser、usage 缺失、collector 或存储观测错误。

## 备份、保留与恢复

进程每天 03:00 创建 online backup，并保留 7 个 daily 与 4 个 weekly。原始 15 秒
scrape 保留 7 天，1 分钟 rollup 保留 30 天，5 分钟 rollup 和 probe 保留 365 天，
state transition 与 event 长期保留。

恢复前先停止服务，再将选定备份复制为
`var/maas-observatory/observatory.sqlite3`。保留原文件用于回滚，然后运行：

```bash
uv run maas-observatory db migrate
uv run maas-observatory db check
uv run maas-observatory serve
```

不要在服务运行时用文件复制代替 online backup API。

## 容器运行

仓库提供单进程 `Dockerfile`：

```bash
docker build -t maas-observatory .
docker run --rm \
  --env-file .env \
  -p 8080:8080 \
  -v "$PWD/var/maas-observatory:/app/var/maas-observatory" \
  maas-observatory
```

Docker 使用独立 Node build stage 编译前端，运行镜像中不包含 Node 或
`node_modules`。容器以非 root 用户运行。不得增加 `--workers`，也不得让多个
容器共享同一 SQLite 目录。TLS 和公网访问控制应由端口转发、反向代理或平台网络层
提供。

## 发布前检查

```bash
npm --prefix frontend ci
npm --prefix frontend run typecheck
npm --prefix frontend run build
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy
uv run pytest --cov --cov-report=term-missing
uv build
```

fixture 和 fake vLLM 测试覆盖 counter reset、低样本 quantile、malformed metrics、
多实例 counter 隔离与聚合、SSE usage 缺失、observer-path 计时、短/长体验
load gate、预算、WAL、migration、backup、状态转换、ETag、无数据语义和秘密字段
扫描。真实 endpoint 不属于单元测试依赖。
