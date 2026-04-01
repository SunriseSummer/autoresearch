# autoresearch-skill：基于 autoresearch 框架的 Skill 优化方案

## 1. 项目背景

### 1.1 原始 autoresearch 项目

autoresearch 是 Karpathy 开发的自主 AI 研究框架，核心思路是：让 LLM Agent 自主、迭代地修改 GPT 训练代码（`train.py`），每次实验在固定 5 分钟时间预算内运行，根据验证集 bits-per-byte 指标（`val_bpb`，越低越好）决定保留还是回退改动。整个循环无需人工干预，可以在人类睡觉时持续运行约 100 次实验。

项目的三个核心文件：

| 文件 | 角色 | 谁来修改 |
|---|---|---|
| `prepare.py` | 数据准备、分词器、评估函数（只读） | 无人修改 |
| `train.py` | 模型架构、优化器、训练循环 | Agent 修改 |
| `program.md` | Agent 的行为指令 | 人类修改 |

核心实验循环：

```
永久循环：
  1. Agent 提出优化假设，修改 train.py
  2. git commit
  3. 运行 train.py（5 分钟时间预算）
  4. 提取指标（val_bpb、peak_vram_mb）
  5. 若 val_bpb 改善 → 保留 commit
     若 val_bpb 变差或崩溃 → git reset 回退
  6. 记录到 results.tsv
```

### 1.2 改造目标

将 autoresearch 框架改造为 **Skill 优化器**：Agent 自主迭代修改 Skill（即指导 Agent 完成任务的提示词/指令文件），目标是 **基于修改后的 Skill 完成相同任务、Token 消耗更少**。

## 2. 核心概念映射

下表展示了原始框架到 Skill 优化框架的概念映射关系：

| 原始 autoresearch | Skill 优化框架 | 说明 |
|---|---|---|
| `train.py`（被优化对象） | `skill.md`（被优化对象） | 从优化训练代码变为优化 Skill 指令 |
| `prepare.py`（只读基础设施） | `harness.py`（只读基础设施） | 从数据/评估变为任务定义/执行/评估 |
| `program.md`（人类编写的 Agent 指令） | `program.md`（人类编写的元指令） | 指导优化 Agent 如何迭代 Skill |
| `val_bpb`（越低越好） | `token_cost`（越低越好） | 从训练损失变为 Token 消耗量 |
| 5 分钟时间预算 | 任务集执行预算 | 从固定时间变为固定任务集 |
| `results.tsv` | `results.tsv` | 记录格式适配新指标 |
| `run.log` | `run.log` | 执行日志 |
| 模型架构/超参数的搜索空间 | Skill 文本/结构/策略的搜索空间 | 从代码空间变为自然语言空间 |

## 3. 整体架构设计

### 3.1 文件结构

```
autoresearch-skill/
├── harness.py          # 只读 — 任务加载、Skill 执行、评估（对应 prepare.py）
├── skill.md            # Agent 修改 — 被优化的 Skill 文件（对应 train.py）
├── program.md          # 人类修改 — 优化 Agent 的行为指令
├── tasks/              # 只读 — 任务定义集
│   ├── task_001.json
│   ├── task_002.json
│   └── ...
├── results.tsv         # 实验结果记录（不入 git）
├── pyproject.toml      # 依赖管理
└── README.md           # 项目说明
```

### 3.2 角色分工

改造后的系统涉及 **两层 Agent**：

```
┌─────────────────────────────────────────────────┐
│                  人类（Human）                    │
│  编写 program.md — 定义如何优化 Skill             │
└─────────────────┬───────────────────────────────┘
                  │ 指导
                  ▼
┌─────────────────────────────────────────────────┐
│            优化 Agent（Optimizer Agent）           │
│  读取 program.md，迭代修改 skill.md               │
│  运行 harness.py 评估，保留/回退改动               │
└─────────────────┬───────────────────────────────┘
                  │ 调用
                  ▼
┌─────────────────────────────────────────────────┐
│            执行 Agent（Execution Agent）           │
│  读取 skill.md，按指令完成 tasks/ 中的任务         │
│  由 harness.py 驱动，输出结果供评估                │
└─────────────────────────────────────────────────┘
```

- **优化 Agent**：对应原始项目中修改 `train.py` 的 Agent，现在改为修改 `skill.md`。
- **执行 Agent**：对应原始项目中的训练过程（`uv run train.py`），现在是一个根据 `skill.md` 指令完成任务的 Agent。

### 3.3 核心流程

```
永久循环：
  1. 优化 Agent 分析当前 skill.md 和历史结果
  2. 提出优化假设（如"精简冗余指令"、"合并重复步骤"等）
  3. 修改 skill.md
  4. git commit
  5. 运行评估：uv run harness.py > run.log 2>&1
     ├── 加载 tasks/ 中的任务集
     ├── 对每个任务：用 skill.md + 任务输入调用执行 Agent
     ├── 记录每个任务的 Token 消耗和完成质量
     └── 输出汇总指标
  6. 提取指标：grep "^avg_token_cost:\|^pass_rate:" run.log
  7. 判断：
     若 pass_rate >= 阈值 且 avg_token_cost 降低 → 保留
     若 pass_rate < 阈值 或 avg_token_cost 未改善 → git reset 回退
  8. 记录到 results.tsv
```

## 4. 各模块详细设计

### 4.1 harness.py — 评估框架（对应 prepare.py）

这是只读的基础设施文件，优化 Agent 不可修改，包含以下核心功能：

#### 4.1.1 常量定义

```python
# 评估配置（对应 prepare.py 中的 MAX_SEQ_LEN、EVAL_TOKENS 等）
TASK_DIR = "tasks"                    # 任务集目录
PASS_THRESHOLD = 0.9                  # 质量门槛: 通过率不低于 90%
MAX_RETRIES = 2                       # 单任务最大重试次数
MODEL_NAME = "gpt-4o-mini"            # 执行 Agent 使用的模型
TEMPERATURE = 0.0                     # 生成温度（保证可复现性）
SEED = 42                             # 随机种子
```

#### 4.1.2 任务加载器

```python
def load_tasks(task_dir: str) -> list[Task]:
    """
    从 tasks/ 目录加载任务集。

    每个任务是一个 JSON 文件，包含：
    - task_id: 唯一标识
    - input: 任务输入（用户请求）
    - expected: 期望输出或验证条件
    - evaluator: 评估方式（exact_match / contains / llm_judge / custom_func）
    """
```

#### 4.1.3 Skill 执行器

```python
def execute_skill(skill_path: str, task: Task) -> ExecutionResult:
    """
    用给定 Skill 完成一个任务。

    流程：
    1. 读取 skill.md 内容作为 system prompt
    2. 将 task.input 作为 user message
    3. 调用 LLM API（记录 token 用量）
    4. 返回 ExecutionResult（output, prompt_tokens, completion_tokens, total_tokens）

    对应原始项目中的 `uv run train.py`，但从"训练模型"变为"执行任务"。
    """
```

#### 4.1.4 质量评估器

```python
def evaluate_quality(task: Task, output: str) -> bool:
    """
    评估任务完成质量（通过/不通过）。

    对应原始项目中的 evaluate_bpb()，但从连续值指标变为二值质量门控。

    评估策略：
    - exact_match: 输出与期望完全一致
    - contains: 输出包含期望的关键内容
    - llm_judge: 用另一个 LLM 判断输出是否正确
    - custom_func: 自定义评估函数（如代码执行测试）
    """
```

#### 4.1.5 核心评估函数

```python
def evaluate_skill(skill_path: str) -> dict:
    """
    在完整任务集上评估一个 Skill 的效果。

    这是最核心的评估函数，对应 evaluate_bpb()。

    返回：
    - avg_token_cost: 平均每任务 Token 消耗（主指标，越低越好）
    - pass_rate: 任务通过率（质量门控）
    - total_tokens: 总 Token 消耗
    - per_task_results: 每个任务的详细结果

    Token 消耗 = prompt_tokens + completion_tokens
    （其中 prompt_tokens 包含 skill.md 内容 + 任务输入）
    """
```

#### 4.1.6 输出格式

```python
# 与原始项目保持一致的输出风格
print(f"---")
print(f"avg_token_cost:   {avg_token_cost:.1f}")
print(f"pass_rate:        {pass_rate:.4f}")
print(f"total_tokens:     {total_tokens}")
print(f"prompt_tokens:    {total_prompt_tokens}")
print(f"completion_tokens: {total_completion_tokens}")
print(f"num_tasks:        {num_tasks}")
print(f"num_passed:       {num_passed}")
print(f"skill_length:     {skill_char_count}")
```

### 4.2 skill.md — 被优化的 Skill（对应 train.py）

这是优化 Agent 唯一可以修改的文件。它是一份 Markdown 格式的指令文件，用于指导执行 Agent 完成任务。

**Skill 的优化维度**（对应原始项目中模型架构/超参数的搜索空间）：

| 维度 | 说明 | 示例 |
|---|---|---|
| **指令精简** | 删除冗余描述、合并重复步骤 | "请按以下步骤…" → 直接列出关键步骤 |
| **结构优化** | 调整指令的组织方式 | 平铺 → 层次化、条件化 |
| **措辞优化** | 用更精确的词汇减少歧义 | 模糊描述 → 具体约束 |
| **示例优化** | 调整 few-shot 示例的数量和选择 | 5 个示例 → 2 个更有代表性的示例 |
| **输出格式约束** | 限制输出的格式和长度 | "简要回答" → "用一句话回答" |
| **推理策略** | 调整思考链、分步推理的使用方式 | 完整 CoT → 关键步骤 CoT |
| **上下文管理** | 精简背景信息的提供方式 | 全量上下文 → 按需上下文 |

### 4.3 program.md — 优化 Agent 的指令（保持角色不变）

`program.md` 继续由人类编写，指导优化 Agent 如何迭代 Skill。结构与原始项目高度一致：

```markdown
# autoresearch-skill

## Setup
（初始化步骤，与原始项目类似）

## Experimentation

每次实验通过 `uv run harness.py` 运行完整任务集评估。

**可以修改的内容：**
- skill.md — 这是你唯一编辑的文件。指令内容、结构、措辞、示例都可以调整。

**不可修改的内容：**
- harness.py — 只读，包含任务加载、执行、评估逻辑。
- tasks/ — 只读，固定的任务集。
- pyproject.toml — 不可添加依赖。

**目标：在 pass_rate >= 0.9 的前提下，最小化 avg_token_cost。**

**质量门控：** pass_rate 是硬约束。如果一个修改降低了 Token 消耗但导致
pass_rate 低于阈值，必须回退。先保质量，再降消耗。

**简洁性原则：** 与原始项目一致 — 同等效果下，更简洁的 Skill 更好。
删减内容后效果不变甚至更好，是理想结果。

## Output format
（与原始项目格式保持一致，指标替换为新指标）

## Logging results
（results.tsv 格式适配新指标）

## The experiment loop
（与原始项目核心循环一致，NEVER STOP）
```

### 4.4 tasks/ — 任务定义集

每个任务是一个 JSON 文件，定义了一个具体的任务实例：

```json
{
  "task_id": "summarize_001",
  "input": "请将以下文章总结为 3 个要点：...",
  "expected": ["要点1的关键词", "要点2的关键词", "要点3的关键词"],
  "evaluator": "contains",
  "metadata": {
    "category": "summarization",
    "difficulty": "medium"
  }
}
```

任务集需要满足以下要求（对应原始项目中固定验证集的设计）：

- **固定不变**：与 `prepare.py` 中固定验证 shard 的设计一致，任务集在优化过程中不变，确保实验可比性。
- **覆盖面广**：包含 Skill 需要处理的各类场景。
- **数量适中**：太少则评估噪声大，太多则单次实验耗时过长。建议 20-50 个任务。

### 4.5 results.tsv — 结果记录

适配新指标的 TSV 格式：

```
commit	avg_token_cost	pass_rate	status	description
a1b2c3d	850.0	1.0000	keep	baseline
b2c3d4e	720.5	0.9500	keep	精简冗余指令段落
c3d4e5f	680.2	0.8500	discard	过度精简导致质量下降
d4e5f6g	710.0	0.9500	keep	优化 few-shot 示例
```

## 5. 评估指标设计

### 5.1 主指标：avg_token_cost（越低越好）

```
avg_token_cost = total_tokens / num_tasks
total_tokens = Σ (prompt_tokens_i + completion_tokens_i)   对每个任务 i
```

这对应原始项目中的 `val_bpb`，是优化目标。

**为什么用平均 Token 而非总 Token？** 与 `val_bpb` 采用归一化指标的理由一致 — 如果未来任务集规模调整，平均值仍可比。

### 5.2 质量门控：pass_rate（硬约束）

```
pass_rate = num_passed / num_tasks
```

这是原始项目中没有直接对应物的新概念。在原始项目中，`val_bpb` 是唯一指标，不存在"质量是否达标"的问题。但在 Skill 优化中，降低 Token 消耗不能以牺牲任务完成质量为代价，因此需要质量门控。

**判定规则：**
- `pass_rate >= PASS_THRESHOLD` 且 `avg_token_cost` 下降 → **保留**（keep）
- `pass_rate < PASS_THRESHOLD` → **回退**（discard），无论 Token 消耗如何
- `pass_rate >= PASS_THRESHOLD` 但 `avg_token_cost` 未下降 → **回退**（discard）

### 5.3 辅助指标

| 指标 | 说明 |
|---|---|
| `prompt_tokens` | 总输入 Token 数（反映 Skill 本身的长度） |
| `completion_tokens` | 总输出 Token 数（反映 Skill 引导的输出效率） |
| `skill_length` | Skill 文件字符数（间接反映复杂度） |
| `num_tasks` | 评估任务总数 |
| `num_passed` | 通过任务数 |

## 6. 关键设计决策

### 6.1 保留的设计原则

以下是从原始项目继承的核心设计理念，在改造中应当保留：

| 原则 | 原始项目体现 | Skill 优化体现 |
|---|---|---|
| **单文件修改** | Agent 只改 `train.py` | Agent 只改 `skill.md` |
| **固定评估基准** | 固定验证 shard | 固定任务集 |
| **自主循环** | NEVER STOP | NEVER STOP |
| **保留/回退** | val_bpb 改善则保留 | Token 下降且质量达标则保留 |
| **简洁性优先** | 简单方案优于复杂方案 | 精简指令优于冗长指令 |
| **结果可复现** | 固定种子、固定数据 | 固定种子、固定任务、temperature=0 |
| **指标提取** | grep 从 log 提取 | grep 从 log 提取 |

### 6.2 需要调整的设计

| 方面 | 原始项目 | Skill 优化 | 原因 |
|---|---|---|---|
| **优化对象** | Python 代码 | Markdown 文本 | Skill 是自然语言指令 |
| **执行方式** | 本地 GPU 训练 | LLM API 调用 | Skill 通过 API 执行 |
| **单一指标** | 只看 val_bpb | Token + 质量双指标 | 需要质量门控防止过度精简 |
| **时间预算** | 固定 5 分钟 | 固定任务集 | 可比性通过固定任务集保证 |
| **崩溃检测** | exit code / NaN | API 错误 / 超时 | 执行环境不同 |
| **VRAM 约束** | 软约束 | 无（API 调用） | 不涉及本地计算资源 |

### 6.3 新增设计：Token 消耗的分解优化

Token 消耗可分解为两个独立优化方向：

```
total_tokens = prompt_tokens + completion_tokens
             = (skill_tokens + task_input_tokens) + completion_tokens
```

- **skill_tokens**：Skill 指令本身的 Token 数。优化方向：精简指令。
- **task_input_tokens**：任务输入的 Token 数。不可优化（任务固定）。
- **completion_tokens**：Agent 生成的输出 Token 数。优化方向：通过 Skill 指令约束输出格式和长度。

优化 Agent 需要同时关注这两个可控维度。

## 7. 改造实施步骤

### 第一阶段：框架搭建

1. **创建 `harness.py`**：实现任务加载、Skill 执行、质量评估、指标输出。
2. **创建 `tasks/` 目录**：准备初始任务集（可以从一个具体 Skill 应用场景出发）。
3. **创建初始 `skill.md`**：作为 baseline 的 Skill 文件。
4. **改写 `program.md`**：调整 Agent 指令，从优化训练代码改为优化 Skill。

### 第二阶段：评估体系

5. **实现评估器**：支持 exact_match / contains / llm_judge 等多种评估方式。
6. **实现 Token 追踪**：精确记录 prompt_tokens 和 completion_tokens。
7. **实现质量门控逻辑**：pass_rate 达标才考虑 Token 优化。

### 第三阶段：优化循环

8. **验证完整循环**：手动运行一次完整的优化循环，确认 baseline。
9. **自主实验**：启动优化 Agent，让其自主迭代。
10. **分析结果**：检查 results.tsv，分析优化趋势。

## 8. 示例场景

假设要优化一个"代码审查 Skill"，该 Skill 指导 Agent 审查代码并给出改进建议：

**初始 skill.md（baseline）：**

```markdown
# Code Review Skill

你是一个资深的代码审查专家。请对用户提交的代码进行全面审查。

## 审查流程

1. 首先，仔细阅读整段代码，理解其功能和目的
2. 然后，从以下几个维度进行审查：
   - 代码正确性：是否有 bug 或逻辑错误
   - 代码风格：是否符合最佳实践
   - 性能：是否有性能问题
   - 安全性：是否有安全漏洞
   - 可读性：命名、注释、结构是否清晰
3. 最后，给出具体的改进建议

## 输出格式

请按以下格式输出审查结果：
- 问题列表：每个问题包含位置、描述、严重程度
- 改进建议：针对每个问题的具体修改方案
- 总体评价：代码质量的整体评估

## 注意事项

- 请关注关键问题，不要纠结于琐碎的风格问题
- 建议要具体可执行，不要给出模糊的建议
- 如果代码质量很好，也请明确指出优点
```

**优化后的 skill.md（经过多轮迭代）：**

```markdown
审查代码，列出 bug、安全、性能问题。每条：行号 + 问题 + 修复建议。无问题则回复"LGTM"。
```

**预期结果：**
- baseline avg_token_cost: ~800 tokens/task
- 优化后 avg_token_cost: ~350 tokens/task（↓56%）
- pass_rate 保持 >= 0.9

## 9. 与原始项目的代码对照

| 原始代码 | 新代码 | 变化说明 |
|---|---|---|
| `prepare.py` (390行) | `harness.py` (~200行) | 从数据/训练基础设施 → 任务/执行/评估基础设施 |
| `train.py` (630行) | `skill.md` (~几十行) | 从 Python 训练代码 → Markdown 指令文本 |
| `program.md` (115行) | `program.md` (~120行) | 结构不变，内容适配 Skill 优化场景 |
| `pyproject.toml` | `pyproject.toml` | 依赖从 PyTorch → openai / anthropic SDK |
| 无 | `tasks/*.json` | 新增：固定任务集（对应固定验证 shard） |

## 10. 风险与应对

| 风险 | 说明 | 应对 |
|---|---|---|
| **质量退化** | 过度精简导致任务完成质量下降 | 质量门控（pass_rate 硬约束） |
| **过拟合** | Skill 针对固定任务集过度优化 | 任务集要有足够覆盖面；可定期更换任务集验证泛化能力 |
| **局部最优** | 陷入无法进一步优化的状态 | 鼓励 Agent 尝试大幅重构（参考原始项目"更激进的架构变化"建议） |
| **评估噪声** | LLM 输出的随机性导致指标波动 | temperature=0 + 固定 seed；多次运行取平均 |
| **API 成本** | 大量 API 调用产生费用 | 使用低成本模型（如 gpt-4o-mini）；控制任务集规模 |
| **评估可靠性** | llm_judge 评估本身可能不准确 | 优先使用确定性评估（exact_match, contains）；llm_judge 作为补充 |

## 11. 总结

autoresearch 框架的核心抽象 — **"自主循环 × 单一可变文件 × 固定评估基准 × 保留/回退策略"** — 可以干净地迁移到 Skill 优化场景。关键改造点是：

1. **优化对象**从 Python 代码变为 Markdown 指令。
2. **评估方式**从本地 GPU 训练变为 LLM API 调用。
3. **评估指标**从单一 val_bpb 变为 "Token 消耗 + 质量门控" 的双指标体系。
4. **可比性保证**从固定时间预算变为固定任务集。

这种改造保持了原始框架的简洁性和自主性，同时将优化目标从"训练出更好的模型"转向"用更少的 Token 完成同样的任务"。
