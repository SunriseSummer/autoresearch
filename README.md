# autoresearch-skill

基于 [autoresearch](https://github.com/karpathy/autoresearch) 框架改造的 **自主 Skill 优化工具**。

让 LLM Agent 自主、迭代地优化 Skill（提示词/指令文件），在保证任务通过率（`pass_rate >= 0.9`）的前提下，最小化每任务平均 Token 消耗（`avg_token_cost`）。

## 快速开始

```bash
# 1. 安装依赖
uv sync

# 2. 设置 API 密钥
export OPENAI_API_KEY="sk-..."

# 3. 运行评估
uv run harness.py
```

## 项目结构

| 文件 | 角色 | 修改者 |
|---|---|---|
| `harness.py` | 任务加载、Skill 执行、质量评估（只读） | 无 |
| `skill.md` | 被优化的 Skill 指令文件 | Agent |
| `program.md` | 优化 Agent 的行为指令 | 人类 |
| `tasks/` | 固定任务集（只读） | 无 |

## 核心概念

### 与原始 autoresearch 的映射

| 原始 autoresearch | Skill 优化框架 |
|---|---|
| `train.py`（被优化对象） | `skill.md`（被优化对象） |
| `prepare.py`（只读基础设施） | `harness.py`（只读基础设施） |
| `val_bpb`（越低越好） | `avg_token_cost`（越低越好） |
| 5 分钟时间预算 | 固定任务集 |

### 实验循环

```
永久循环：
  1. 优化 Agent 分析当前 skill.md 和历史结果
  2. 提出优化假设，修改 skill.md
  3. git commit
  4. 运行评估：uv run harness.py > run.log 2>&1
  5. 提取指标：grep "^avg_token_cost:\|^pass_rate:" run.log
  6. pass_rate >= 0.9 且 avg_token_cost 降低 → 保留
     否则 → git reset 回退
  7. 记录到 results.tsv
```

## 多模型支持

```bash
# OpenAI（默认）
export OPENAI_API_KEY="sk-..."
uv run harness.py --provider openai

# Kimi（Moonshot）
export KIMI_API_KEY="sk-..."
uv run harness.py --provider kimi

# 自定义提供商
export SKILL_API_KEY="..."
export SKILL_API_BASE="https://your-api.com/v1"
export SKILL_MODEL_NAME="your-model"
uv run harness.py
```

## 测试

```bash
uv run pytest tests/
```
