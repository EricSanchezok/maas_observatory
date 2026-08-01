# MaaS Observatory

这是一个面向 OpenAI-compatible MaaS 部署的主动响应观测与可复现工具调用评测
项目。实时观测服务只使用 `/v1/models` 轻量连通检查，以及从 Observatory 节点
发出的真实 streaming 请求；不采集统计边界不明确的 serving metrics。实时观测
服务与离线评测 runner 相互独立。离线评测分别记录：

- endpoint、鉴权或超时等传输问题；
- OpenAI `message.tool_calls` 协议不兼容；
- 工具选择、参数、并行调用等 function-calling 能力；
- 长链 agent 中的规划、工具结果整合和可靠性。

首版包含 5 项轻量协议探针、BFCL V4 和 Toolathlon-Verified。协议探针与长链任务
每个任务重复 3 次；用于官方口径对照的 BFCL 主结果执行一次完整评测，重复性另行
测量。报告按适用范围给出 Pass@1、Pass@3、Pass^3、置信区间、错误分类、延迟与
调用效率。不同 benchmark 的分数保持独立。

## 实时观测界面

```bash
npm --prefix frontend ci
npm --prefix frontend run build
uv run maas-observatory serve
```

打开 `http://127.0.0.1:8080/`。生产模式下 FastAPI 同源提供前端与公开只读 API；
开发前端时可另行运行 `npm --prefix frontend run dev`，Vite 会把 API 请求代理到
8080 端口。

仓库默认使用 `standard` 采集。`rapid` 模式需要设置
`MAAS_OBSERVATORY_RAPID_CONTEXT_TIER`（可选 `1k`、`16k` 或 `64k`），
并与 Standard 共享每部署每日预算上限，只适合有人值守的采集阶段；使用后必须
手动切回 Standard。准确的调度、指标公式、schema v4 迁移和 API v6 说明见
[运行与维护](docs/maas-observatory-operations.md)。

## 快速开始

```bash
cp .env.example .env
# 只在本地填写 URL 和 API key；.env 已被 Git 忽略。

uv sync --extra dev --extra docs --frozen
uv sync --project benchmark-envs/bfcl --frozen
uv sync --project benchmark-envs/toolathlon --frozen

uv run tooluse-bench models validate --require-endpoints
uv run tooluse-bench benchmarks validate
uv run tooluse-bench run --experiment config/experiments/release-v1.yaml
```

BFCL 全量 web-search 子集需要 `SERPAPI_API_KEY`。自托管 Toolathlon 服务还需按
`benchmark-envs/toolathlon/README.md` 固定上游提交与任务镜像 digest，并在环境
变量中声明这两个固定值。

私有运行记录写入 `runs/`。报告和公开发布包必须显式生成：

```bash
uv run tooluse-bench report build <run-id>
uv run tooluse-bench release build <run-id>
uv run tooluse-bench release validate <run-id>
uv run tooluse-bench publication build <run-id> --title "Release candidate"
uv run tooluse-bench publication validate
```

发布链路会递归脱敏 URL、密钥、鉴权头和本机路径，并生成 SHA-256 校验文件与确定性
压缩包。只有 benchmark 版本、评测 harness、模型精度、推理设置和部署身份都严格
对齐时，系统才允许计算相对官方分数的差值；其他官方数据只作为带来源的背景信息。
报告会从验证后的聚合数据自动生成 SVG 与高分辨率 PNG：同 benchmark 官方值以
菱形标记对照，不同版本、精度或 reasoning 设置的值明确标为 contextual，跨
benchmark 不计算差值。

更多内容见：

- [方法学](docs/methodology.md)
- [复现说明](docs/reproducibility.md)
- [benchmark 调研](docs/benchmark-research.md)
- [官方基线矩阵](docs/official-baseline-matrix.md)
- [新增模型与 benchmark](docs/adding-models-and-benchmarks.md)

代码采用 Apache-2.0；公开报告与评测数据采用 CC BY 4.0。
