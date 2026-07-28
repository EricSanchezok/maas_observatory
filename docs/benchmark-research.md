# Tool-use benchmark 调研

调研日期：2026-07-28

## 结论

不要只报一个总分。要证明“学院部署的模型难以接入 agent”，至少需要分开回答三个问题：

1. 服务是否真的实现了 OpenAI tool-calling 协议；
2. 模型能否正确选择工具、填写参数并处理多轮结果；
3. 模型放进 agent loop 后，能否稳定完成有状态的长任务。

最合适的主线是：

- 本仓库的 protocol probe：先定位服务层或 chat template 层的问题；
- Toolathlon-Verified：作为有官方同型号数据的主要 agent/tool-use 横评；
- BFCL V4：作为标准化 function-calling 协议诊断；
- ACEBench：补充中文、模糊指令和真实多轮交互；
- τ³-bench、Claw-Eval、MCP-Atlas/MCPMark：按各模型官方报告选择，计算学院部署与
  上游发布值的差距。

各模型对应的官方分数、来源、版本陷阱和 serving parser 风险，见
[官方可比基线矩阵](official-baseline-matrix.md)。

## Benchmark 对比

| Benchmark | 主要测什么 | 评测方式 | 对本项目的价值 | 局限与成本 | 建议 |
|---|---|---|---|---|---|
| [Toolathlon-Verified](https://toolathlon.xyz/docs/leaderboard) | 600+ 真实工具上的长链、多应用任务 | 108 个任务、程序化 verifier、Pass@1/Pass@3/Pass^3 | GLM 5.2、DeepSeek V4 Pro、Kimi K2.6 已有当前官方榜分；旧版还覆盖 Nex、MiniMax、DeepSeek Flash | 运行较重；2026-06-30 后是新分数序列，不能与旧 Toolathlon 混比 | **统一 agent 主榜** |
| [BFCL V4](https://gorilla.cs.berkeley.edu/leaderboard.html) | 单轮、多轮、并行调用、无关工具拒绝、缺失函数/参数、长上下文、搜索与记忆 | AST/可执行检查为主，官方支持 remote OpenAI-compatible endpoint | 标准度高，最适合拆解 function-calling 协议错误 | 这些确切上游型号没有官方 BFCL V4 发布值，不能计算官方差值 | **协议诊断主榜** |
| [ACEBench](https://github.com/chenchen0103/ACEBench) | Normal、Special、Agent 三类；中英双语；模糊或不完整指令 | 规则评测与模拟多轮；4,538 个 API，覆盖 8 个领域 | 很适合学院的中文使用场景，也能细分错误类型 | 接入代码需要适配自定义 endpoint；Agent 子集成本更高 | **第二优先级** |
| [ComplexFuncBench](https://github.com/zai-org/ComplexFuncBench) | 多步、约束、隐式参数推理、超长参数、128K 上下文 | 1,000 条复杂样本，含真实 API 响应和自动评测 | 对 GLM、长上下文及复杂参数能力特别有诊断价值 | 真实响应模式需要 RapidAPI；部分评分依赖额外模型 | **推荐补充** |
| [ToolSandbox](https://github.com/apple/ToolSandbox) | 有状态工具、工具间隐式依赖、信息不足、参数规范化、对话中动态评测 | 本地模拟世界状态，以中间和最终 milestone 评分 | 能回答“基础 function call 会了，为什么 agent 仍然失败” | 原生 model adapter 有限，需要写 OpenAI-compatible adapter | **端到端首选之一** |
| [τ³-bench](https://github.com/sierra-research/tau2-bench) | 用户—agent—工具动态交互、政策遵循、数据库结果；零售/航空/通信/银行知识库 | 多轮用户模拟，按最终数据库状态和 pass^k 评分 | 非常接近生产 agent，能测可靠性而不只是一次成功 | Python 3.12+；需要单独的 user simulator，完整运行较贵 | **端到端首选之一** |
| [MCPMark Verified](https://github.com/eval-sys/mcpmark) | Notion、GitHub、Filesystem、Postgres、Playwright 中的长链路 CRUD | 隔离环境、程序化 verifier；127 个高质量任务 | 如果最终要接 Codex/MCP，证据最贴近真实工作流 | 测到的是“模型 + agent harness + MCP server”；部署和授权较重 | **第三阶段** |
| [NESTFUL](https://github.com/IBM/NESTFUL) | 一个工具输出作为下一个工具输入的 nested sequence | 1,800+ 可执行序列，检查完整调用链 | 精准测试常见的链式调用崩溃 | 场景较窄，不能代表完整 agent | 定向补充 |
| [HammerBench](https://github.com/MadeAgents/HammerBench) | 移动助理多轮 slot filling、用户改口、代词、信息多给或少给 | 对话快照上的细粒度 function-call 指标 | 适合测真实用户表达扰动 | 生态和横向对比度不如 BFCL | 可选 |
| [StableToolBench](https://github.com/THUNLP-MT/StableToolBench) | 大规模真实工具选择与规划，MirrorAPI 模拟 7,000+ 工具 | 模拟 API server + StableToolEval | 适合大规模 tool retrieval/selection | 系统较重；基于较老 ToolBench 体系 | 有专项需求再跑 |
| [API-Bank](https://github.com/AlibabaResearch/DAMO-ConvAI/tree/main/api-bank) | API 检索、规划和调用 | 73 个可执行 API、314 段对话、753 次调用 | 轻量、容易理解，也有中文团队背景 | 2023 年基准，规模与难度已不足以当主结论 | 只作 sanity baseline |
| [ToolBench](https://github.com/OpenBMB/ToolBench) | 16,000+ 真实 API 上的检索、规划和调用 | RapidAPI、DFSDT、ToolEval | 历史影响大、覆盖广 | 原始真实 API 不稳定，评测常依赖 LLM judge；应优先用 StableToolBench | 不建议作为主表 |

## 为什么仍然要先跑 BFCL V4 小样本

BFCL 官方 runner 支持 `REMOTE_OPENAI_BASE_URL` 和
`REMOTE_OPENAI_API_KEY`，因此这些学院 endpoint 不需要重新部署；不过官方 runner
仍然要求模型出现在它的 model handler 配置中。对本项目这些自定义 model ID，
使用 EvalScope 的 BFCL adapter 更直接。它能把以下失败拆开：

- API 返回了普通文本，而不是 `message.tool_calls`；
- function name 正确但 arguments 不是合法 JSON；
- 参数类型、枚举或必填字段错误；
- 不该调用工具时仍然调用；
- 多个独立工具不能并行调用；
- 多轮中找不到函数、缺少参数或上下文过长后失败。

这比直接跑完整 agent benchmark 更容易定位故障。但它回答的是“OpenAI 标准
tool calling 是否可用”，不是“学院部署比官方模型退化了多少”。后一个问题应优先用
官方模型卡报告过的 Toolathlon、τ³-bench、Claw-Eval、MCP-Atlas 或 MCPMark。

官方复现实验给出的版本是 `bfcl-eval==2025.12.17`。若要与官网榜单比较，应固定该版本或官网指定 commit，并把版本写进报告。本仓库已经封装了
EvalScope adapter：

```bash
uv sync --project benchmark-envs/bfcl --frozen

tooluse-bench bfcl --model glm-5.2 --limit 10
```

默认先跑 8 个单轮/多轮核心子集；可以重复传 `--subset` 精确选择。每个 endpoint
使用独立缓存目录，避免结果串模型。命令参数可能随 BFCL 或 EvalScope 版本变化；
正式报告要记录 `pip freeze`，并以固定版本的
[BFCL 官方 README](https://github.com/ShishirPatil/gorilla/blob/main/berkeley-function-call-leaderboard/README.md)
与 [EvalScope BFCL V4 文档](https://evalscope.readthedocs.io/en/v1.2.0/third_party/bfcl_v4.html)
为准。

如果改用 BFCL 官方 CLI，自定义模型需要增加 model handler/config；不能只把任意
model ID 写成 `remote-openai`。

## 推荐实验设计

### 阶段 0：协议探针

九个模型各跑 3 次本仓库的 5 个用例，记录：

- HTTP 成功率；
- 原生 `tool_calls` 返回率；
- arguments JSON 可解析率；
- 工具选择准确率；
- 并行调用成功率；
- 缺失信息时的澄清率；
- p50/p95 延迟与 token 数。

这一步只用 135 次请求，能快速区分：

- 全部用例 HTTP/格式错误：endpoint 或 serving/template 问题；
- 简单调用通过、并行/澄清失败：模型或 chat template 能力问题；
- probe 通过、正式 agent 失败：多轮 tool-result、harness 或长链规划问题。

### 阶段 1：BFCL V4 协议诊断

先跑便宜且确定性的子集：

1. `simple_python`、`multiple`、`parallel`、`parallel_multiple`；
2. `irrelevance`；
3. `multi_turn_base`、`multi_turn_miss_func`、`multi_turn_miss_param`；
4. 最后再跑 live、long-context、web-search 和 memory。

主表应同时报告各子集分数，不能只报 overall。

### 阶段 2：Toolathlon-Verified 统一横评

- 九个 endpoint 使用同一 Verified release、默认 agent 和相同资源限制；
- 报告 Pass@1、Pass@3、Pass^3、平均 turns 与 tool calls；
- GLM 5.2、DeepSeek V4 Pro、Kimi K2.6 与当前官方 Verified 分数对比；
- Nex BF16 与 W8A8 直接做量化 A/B；
- 其余模型只进行学院内部横评，不借用旧版厂商分数。

### 阶段 3：逐模型复现官方基线

- GLM 5.2、DeepSeek V4：MCP-Atlas Public 与 HLE w/ Tools；
- Nex N2 Pro、MiMo V2.5 Pro：τ³-bench；
- Kimi K2.6、Qwen3.6-27B、MiMo V2.5 Pro：Claw-Eval；
- Kimi K2.6：MCPMark；注意它不是 MCP-Atlas；
- MiniMax M2.7：旧版 Toolathlon 或 MM-Claw。

只有版本、agent、reasoning 和采样参数对齐的结果才计算 `academy - official`。

### 阶段 4：中文与复杂场景

- ACEBench 分别报告中文/英文、Normal/Special/Agent；
- ComplexFuncBench 分别报告 multi-step、constraints、implicit parameter、
  long parameter、long context；
- 至少选择一个公开的强模型，在完全相同 harness 下作 control。

### 阶段 5：其他真实 agent

ToolSandbox 和 τ³-bench 二选一先做：

- 想定位状态依赖、信息不足、参数规范化：ToolSandbox；
- 想证明真实客服 agent 的成功率与可靠性：τ³-bench；
- 最终接入目标是 MCP/Codex：再加 MCPMark Verified。

每项至少重复 3 次，并同时报告 `pass@1` 和 `pass^3`。一次偶然成功不能说明模型可用于 agent。

## 对照组与归因

为了让结论能说明“学院部署有问题”，而不只是“这个模型本来就弱”，建议至少包含：

1. 同一上游模型的官方 API（如果存在）；
2. 一个已知 tool-use 较强的 frontier model；
3. 学院部署的 BF16 与量化版本对照，例如 Nex N2 Pro BF16 vs W8A8；
4. 同一模型 thinking on/off 的对照；
5. 相同 prompt、tool schema、temperature、max tokens、重试策略与 agent harness。

报告中把错误分为 transport、protocol、selection、arguments、planning、
tool-result integration、policy、timeout 八类，并保留原始 JSONL trajectory。这样才能把
serving 层、模型层和 agent 层的问题分开。
