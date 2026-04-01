# autoresearch-skill

基于 [autoresearch](https://github.com/karpathy/autoresearch) 框架改造的 **自主 Skill 优化工具**。

使用 `opencode` 作为 agent 工具执行任务，通过迭代优化 `.agents/skills/` 下的 Skill 文件，
在保证任务通过率（`pass_rate >= 0.9`）的前提下，最小化 Token 消耗（`avg_token_cost`）。

## 核心特性

- **默认使用 opencode** 作为 agent 工具执行任务
- **任务目录结构**：`task.md` + `.agents/skills/` — 用户提前准备好
- **支持多 Skills**：可同时优化多个 Skill 文件
- **Token 上限**：可设置每次任务的 Token 上限，超出即判定失败
- **API KEY 隔离**：框架自身 API KEY 与 opencode 使用的 AI 服务完全分离

## 快速开始

```bash
# 1. 安装依赖
uv sync

# 2. 确保 opencode 已安装并配置好 AI 服务
# opencode 的 AI 服务由用户自行配置，框架不管

# 3. 准备任务目录
mkdir -p my-task/.agents/skills
echo "# 你的任务描述" > my-task/task.md
echo "# 你的 Skill" > my-task/.agents/skills/coding.md

# 4. 运行评估
uv run harness.py --task-dir ./my-task

# 5. 设置 token 上限
uv run harness.py --task-dir ./my-task --token-limit 5000
```

## 任务目录结构

```
my-task/
├── task.md                    # 任务描述（必须，只读）
└── .agents/
    └── skills/
        ├── coding.md          # 顶层 Skill（被优化对象）
        ├── review/
        │   └── SKILL.md       # 子目录中的 Skill
        └── vendor/
            └── shared.md      # 可通过 --exclude-skills 排除
```

- `task.md`：描述要完成的任务
- `.agents/skills/` 下各层目录中的 `.md` 文件：opencode 自动加载的 Skill 文件，也是优化 Agent 唯一修改的目标
- 框架递归扫描 `.agents/skills/` 全部子目录，发现所有 `.md` 文件
- 可通过 `--exclude-skills` 排除不需要优化的文件/目录（不影响 opencode 的加载机制）

## 项目结构

| 文件 | 角色 | 修改者 |
|---|---|---|
| `harness.py` | 评估框架：调用 opencode 执行任务，度量 token（只读） | 无 |
| `program.md` | 优化 Agent 的行为指令 | 人类 |
| `<task-dir>/task.md` | 任务描述（只读） | 用户提前准备 |
| `<task-dir>/.agents/skills/` | Skill 文件（被优化对象） | 优化 Agent |

## 与原始 autoresearch 的映射

| 原始 autoresearch | Skill 优化框架 |
|---|---|
| `train.py`（被优化对象） | `.agents/skills/*.md`（被优化对象） |
| `prepare.py`（只读基础设施） | `harness.py`（只读基础设施） |
| `val_bpb` ↓ | `avg_token_cost` ↓ |
| 5 分钟 GPU 时间预算 | Token 上限 / 固定任务 |
| VRAM 约束 | `pass_rate >= 0.9` 质量门控 |

## API KEY 说明

- **框架 API KEY**（`OPENAI_API_KEY` 等）：仅用于框架自身（如 LLM 评估判断），与 opencode 无关
- **opencode AI 服务**：由用户自行在 opencode 中配置，框架只负责调用 `opencode` 命令

## 配置

| 参数 | CLI 参数 | 环境变量 | 默认值 |
|---|---|---|---|
| Agent 命令 | `--agent` | `HARNESS_AGENT` | `opencode` |
| Token 上限 | `--token-limit` | `HARNESS_TOKEN_LIMIT` | `0`（无限制） |
| 超时秒数 | `--timeout` | `HARNESS_TIMEOUT` | `300` |
| 任务目录 | `--task-dir` | — | `.`（当前目录） |
| 排除 Skills | `--exclude-skills` | `HARNESS_EXCLUDE_SKILLS`（逗号分隔） | 无 |

## 测试

```bash
uv run pytest tests/
```
