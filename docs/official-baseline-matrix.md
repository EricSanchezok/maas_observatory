# 官方可比基线矩阵

调研日期：2026-07-28

## 先说结论

本项目不应把 BFCL V4 作为唯一主榜。检查这些**确切上游模型**的官方模型卡、
技术报告与当前公开榜单后，覆盖面最好的公共交集是 Toolathlon：

- GLM 5.2、DeepSeek V4 Pro/Flash、Nex N2 Pro、Kimi K2.6、
  MiniMax M2.7 都有官方发布的旧版 Toolathlon/Tool-Decathlon 分数；
- 当前 [Toolathlon-Verified 榜单](https://toolathlon.xyz/docs/leaderboard)
  又直接包含 GLM 5.2、DeepSeek V4 Pro 和 Kimi K2.6；
- MiMo V2.5 Pro 和 Qwen3.6-27B 没有同口径 Toolathlon 官方分数，需要分别用
  τ³-bench 和 Claw-Eval 对齐官方数据；
- 这些确切模型的官方材料没有给出 BFCL V4 分数。因此 BFCL 很适合做统一的
  function-calling 协议诊断，但不能单靠它计算“学院部署相对官方掉了多少分”。

## 每个学院端点对应什么官方基线

表中的分数是官方模型卡、官方发布页或 benchmark 官方榜单公开值。`旧版` 与
`Verified` 是两个分数序列，绝对不能直接横比。

| 学院端点 | 最优先复现的官方同名基准 | 官方发布值 | 第二基准 | 重要限制 |
|---|---|---:|---|---|
| GLM 5.2 W4A8 | Toolathlon-Verified | 59.9 Pass@1 | MCP-Atlas Public 76.8；HLE w/ Tools 54.7 | [GLM-5.2 官方卡](https://huggingface.co/zai-org/GLM-5.2)中的旧 Tool-Decathlon 是 48.2，不是 Verified；官方上下文是 1M，学院配置只有 200K |
| DeepSeek V4 Pro W4A8 | Toolathlon-Verified | 55.9 Pass@1 | MCP-Atlas 73.6；HLE w/ Tools 48.2 | [官方卡](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro)旧版 Toolathlon Max 为 51.8；学院 W4A8 不是官方同精度 |
| DeepSeek V4 Flash W8A8 | 旧版 Toolathlon | 47.8 Max | MCP-Atlas 69.0 Max；HLE w/ Tools 45.1 Max | 必须复现 Max reasoning；当前 Verified 榜未提供 Flash 同名行 |
| Nex N2 Pro BF16 | 旧版 Toolathlon | 51.9 | τ³-bench 71.1 | [官方卡](https://huggingface.co/nex-agi/Nex-N2-Pro)是最接近学院 BF16 的对照，但仍要固定 harness 和 reasoning 设置 |
| Nex N2 Pro W8A8 | 旧版 Toolathlon | 51.9（上游模型） | τ³-bench 71.1（上游模型） | 官方没有给学院 W8A8 量化分数；应先与学院 BF16 做同 harness A/B，再与上游值比较 |
| Kimi K2.6 W4A8 | Toolathlon-Verified | 58.0 Pass@1 | Claw-Eval 62.3 Pass^3；MCPMark 55.9 | [官方卡](https://huggingface.co/moonshotai/Kimi-K2.6)旧版 Toolathlon 为 50.0；MCPMark 不是 MCP-Atlas |
| MiniMax M2.7 W8A8 | 旧版 Toolathlon | 46.3 | MM-Claw 62.7 | [官方仓库](https://github.com/MiniMax-AI/MiniMax-M2.7)没有当前 Verified 同名结果；MM-Claw 也不能写成 Claw-Eval |
| MiMo V2.5 Pro W8A8 | τ³-bench | 72.9 | Claw-Eval 63.8 Pass^3 | [官方卡](https://huggingface.co/XiaomiMiMo/MiMo-V2.5-Pro)发布的是 FP8 mixed precision，不是学院 W8A8 |
| Qwen3.6-27B BF16 | Claw-Eval | 60.6 Pass^3 / 72.4 Avg | QwenClawBench 53.4；SkillsBench Avg5 48.2 | [官方卡](https://huggingface.co/Qwen/Qwen3.6-27B)没有该 27B 型号的 Toolathlon/BFCL 分数 |

## Toolathlon 的两个口径

| 口径 | 可用于哪些模型 | 官方分数 | 怎么用 |
|---|---|---|---|
| 厂商发布时的旧版 Toolathlon/Tool-Decathlon | GLM 5.2、DeepSeek V4 Pro/Flash、Nex N2 Pro、Kimi K2.6、MiniMax M2.7 | 48.2 / 51.8 / 47.8 / 51.9 / 50.0 / 46.3 | 只有严格复现厂商当时的 commit、agent、reasoning 和采样参数时，才能作为 official delta |
| Toolathlon-Verified，2026-06-30 起的新序列 | GLM 5.2、DeepSeek V4 Pro、Kimi K2.6 有 benchmark 方复测 | 59.9 / 55.9 / 58.0 Pass@1 | 推荐作为当前统一主榜；学院模型也必须跑当前 Verified release |

[Toolathlon-Verified 官方说明](https://toolathlon.xyz/docs/leaderboard)明确写明它是新的
官方分数序列，不能与早期版本直接比较。它包含 108 个任务、32 个 MCP server、
604 个工具，并给出 Pass@1、Pass@3、Pass^3、平均轮数和平均 tool calls。其
[官方仓库](https://github.com/hkust-nlp/Toolathlon)提供公共评测服务，也能接
OpenAI-compatible base URL，适合当前九个端点。

## 正确的实验结构

要同时回答“谁在同一环境里更好”和“学院部署是否低于官方”，需要两套表：

1. **统一横评表**：九个学院端点全部使用当前 Toolathlon-Verified、BFCL V4 和同一
   agent harness。它比较学院端点之间的相对能力。
2. **官方差值表**：每个模型使用上表中官方真正报告过的同名 benchmark，并尽量复现
   官方版本、推理档位、采样参数和上下文。它比较学院部署与上游发布值。

不要把两套表的数字直接合成一个排名。即使 benchmark 名称相同，以下任一项不同，
分数就可能没有可比性：

- benchmark release/commit、任务 split 与 verifier；
- agent scaffold、system prompt、最大轮数和超时；
- thinking effort、temperature、top_p 和最大输出；
- tool schema、并行调用方式、上下文裁剪；
- user simulator 或 judge 模型；
- 模型精度与 serving 版本。

## Serving 层需要单独审计

这批 endpoint 的失败不一定都是模型能力问题。官方部署说明暴露出几个高风险点：

- **Qwen3.6-27B**：SGLang/vLLM 需要启用自动工具选择并使用
  `qwen3_coder` tool-call parser。未配置时，模型可能输出文本格式调用，agent 看不到
  `message.tool_calls`。
- **MiMo V2.5 Pro**：官方 SGLang 示例指定 `--reasoning-parser mimo` 和
  `--tool-call-parser mimo`。
- **DeepSeek V4**：官方卡明确使用专门的 encoding/decoding 脚本，而不是普通 Jinja
  chat template；学院 serving 若只套通用模板，工具协议可能在模型推理前后被破坏。
- **GLM 5.2**：官方能力按 1M 上下文发布，而学院端点声明 200K。长任务成绩下降时，
  不能直接归因于模型权重。
- **量化版本**：GLM W4A8、DeepSeek W4A8/W8A8、Kimi W4A8、MiniMax W8A8、
  MiMo W8A8 都不是官方表中完全相同的精度配置。Nex 的 BF16/W8A8 两个学院端点因此
  是最有价值的量化损失对照。

所以正式跑大 benchmark 前，应先保存协议探针的原始 request/response，并让运维确认
serving engine 版本、chat template、reasoning parser、tool-call parser 和上下文参数。

## 建议最终报告的核心指标

- `official_score`：来源、日期、benchmark release、模型精度和 reasoning 档位；
- `academy_score`：同一 release 下的 Pass@1 / Pass^3；
- `absolute_gap = academy_score - official_score`；
- 协议成功率：HTTP、`tool_calls`、arguments JSON、tool result 回填；
- 任务效率：平均 turns、tool calls、token、p50/p95 latency；
- 八类错误：transport、protocol、selection、arguments、planning、
  tool-result integration、policy、timeout。

如果官方部署无法严格复现，差值必须标为 `directional`，不能写成精确的模型退化量。
