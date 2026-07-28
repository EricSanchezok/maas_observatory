# SII Holos Tool-use Bench

这是一个面向 SII Holos 学院自部署模型的可复现工具调用评测框架。项目的核心目的
不是只给模型排一个总榜，而是区分：

- endpoint、鉴权或超时等传输问题；
- OpenAI `message.tool_calls` 协议不兼容；
- 工具选择、参数、并行调用等 function-calling 能力；
- 长链 agent 中的规划、工具结果整合和可靠性。

首版包含 5 项轻量协议探针、BFCL V4 和 Toolathlon-Verified。发布计划中每个任务
重复 3 次，报告 Pass@1、Pass@3、Pass^3、置信区间、错误分类、延迟与调用效率；
不同 benchmark 的分数不会合并成一个缺乏解释性的总分。

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

私有运行记录写入 `runs/`。报告和公开发布包必须显式生成：

```bash
uv run tooluse-bench report build <run-id>
uv run tooluse-bench release build <run-id>
uv run tooluse-bench release validate <run-id>
```

发布链路会递归脱敏 URL、密钥、鉴权头和本机路径，并生成 SHA-256 校验文件与确定性
压缩包。只有 benchmark 版本、评测 harness、模型精度、推理设置和部署身份都严格
对齐时，系统才允许计算相对官方分数的差值；其他官方数据只作为带来源的背景信息。

更多内容见：

- [方法学](docs/methodology.md)
- [复现说明](docs/reproducibility.md)
- [benchmark 调研](docs/benchmark-research.md)
- [官方基线矩阵](docs/official-baseline-matrix.md)
- [新增模型与 benchmark](docs/adding-models-and-benchmarks.md)

代码采用 Apache-2.0；公开报告与评测数据采用 CC BY 4.0。
