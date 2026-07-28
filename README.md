# SII Holos Tool-use Bench

学院自部署 OpenAI-compatible 模型的 tool-calling 配置、协议探针和 benchmark
调研。

## 已配置模型

- DeepSeek V4 Pro / Flash
- GLM 5.2
- Qwen 3.6 27B
- Kimi K2.6
- MiniMax M2.7
- MiMo V2.5 Pro
- Nex N2 Pro W8A8 / BF16

公开模型元数据位于 [`config/models.yaml`](config/models.yaml)。endpoint 和 API key
从 `.env` 读取；`.env` 已被 Git 忽略，模板见 [`.env.example`](.env.example)。

## 快速开始

不安装也可以直接运行：

```bash
PYTHONPATH=src python -m tooluse_bench list
PYTHONPATH=src python -m tooluse_bench validate
PYTHONPATH=src python -m unittest discover -s tests
```

也可以安装为本地命令：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .

tooluse-bench list
tooluse-bench validate
```

## 协议探针

先对一个模型运行 5 个低成本用例：

```bash
PYTHONPATH=src python -m tooluse_bench probe --model glm-5.2
```

运行所有模型：

```bash
PYTHONPATH=src python -m tooluse_bench probe --all
```

只运行指定用例：

```bash
PYTHONPATH=src python -m tooluse_bench probe \
  --model nex-n2-pro-bf16 \
  --case exact_arguments \
  --case parallel_calls
```

结果写入 `results/probe-<timestamp>.jsonl`，该目录不会提交到 Git。探针只接受
OpenAI 标准的 `message.tool_calls`；模型把调用写成普通文本时会判失败，因为这种输出
正是大多数 agent 框架无法接入的原因。

用例覆盖：

- 精确函数名和参数；
- 无关请求不调用工具；
- 多工具选择；
- 并行调用；
- 缺少必填信息时先询问，而不是编造参数。

## Benchmark 方案

完整调研、取舍和分阶段实验设计见
[`docs/benchmark-research.md`](docs/benchmark-research.md)，逐模型的官方分数与
可比性限制见
[`docs/official-baseline-matrix.md`](docs/official-baseline-matrix.md)。

当前建议：

1. protocol probe 与 BFCL V4 小样本，定位协议/parser 问题；
2. Toolathlon-Verified 统一横评九个学院 endpoint；
3. 按模型复现其官方真正发布过的 benchmark，计算 official delta；
4. 再用 ACEBench、τ³-bench、Claw-Eval 或 MCP benchmark 扩充证据。

BFCL V4 不再被当作唯一主榜：这些确切上游模型的官方材料并没有提供 BFCL V4
分数，而 Toolathlon 对 GLM 5.2、DeepSeek V4、Nex N2 Pro、Kimi K2.6 和
MiniMax M2.7 的官方覆盖更好。Toolathlon-Verified 与 2026-06-30 前的旧版分数是
两个不可直接比较的序列。

## BFCL V4 快速子集

BFCL 依赖锁定在独立运行环境中，避免污染核心包：

```bash
uv sync --project benchmark-envs/bfcl --frozen
tooluse-bench bfcl --model glm-5.2 --limit 10
```

默认选择 8 个确定性较强的单轮/多轮子集。也可以重复传入 `--subset`：

```bash
tooluse-bench bfcl \
  --model glm-5.2 \
  --subset simple_python \
  --subset parallel \
  --subset irrelevance \
  --limit 20
```
