# autoresearch-skill

自主 Skill 优化框架 — 使用 opencode 执行任务，迭代优化 .agents/skills/ 下的 Skill 文件。

## Setup

开始新一轮优化实验：

1. **确定运行标签**：根据日期提出标签（如 `apr1`）。分支名 `autoresearch-skill/<tag>`。
2. **创建分支**：`git checkout -b autoresearch-skill/<tag>`
3. **阅读上下文文件**：
   - `README.md` — 项目说明
   - `harness.py` — 只读评估框架（调用 opencode 执行任务，评估 token 消耗）。不可修改。
   - `<task-dir>/task.md` — 任务描述（只读）
   - `<task-dir>/.agents/skills/` — 你唯一修改的目标：Skill 文件（支持多个）
4. **确认 opencode 已配置**：用户应预先配置好 opencode 的 AI 服务。框架不管 opencode 的配置。
5. **初始化 results.tsv**：创建仅含表头的 `results.tsv`。Baseline 在首次运行后记录。
6. **确认并开始**。

## Experimentation

每次实验通过 `uv run harness.py --task-dir <path>` 运行评估。

**可以修改的内容：**
- `<task-dir>/.agents/skills/*.md` — 这是你唯一编辑的文件。指令内容、结构、措辞、示例都可以调整。支持多个 Skill 文件。

**不可修改的内容：**
- `harness.py` — 只读，包含任务执行和评估逻辑。
- `<task-dir>/task.md` — 只读，任务描述。
- `pyproject.toml` — 不可添加依赖。

**目标：在 pass_rate >= 0.9 的前提下，最小化 avg_token_cost。**

**Token 上限**：可通过 `--token-limit` 设置每次任务的 Token 上限。超过上限且未完成的任务判定为失败。

**质量门控**：pass_rate 是硬约束。如果修改 Skill 导致 pass_rate 低于阈值，必须回退。

**简洁性原则**：同等效果下，更简洁的 Skill 更好。删减内容后效果不变甚至更好，是理想结果。

**首次运行**：第一次运行应直接用当前 Skills 建立 baseline。

## Output format

评估完成后输出如下格式：

```
---
task_dir:           /path/to/task
agent:              opencode
num_skills:         2
skills_total_chars: 850
  skill: coding.md (500 chars)
  skill: review.md (350 chars)
avg_token_cost:     1200.0
pass_rate:          1.0000
total_tokens:       1200
num_runs:           1
num_passed:         1
token_limit:        5000
timeout:            300
  run_1: PASS tokens=1200 duration=45.3s
```

从日志提取关键指标：

```
grep "^avg_token_cost:\|^pass_rate:" run.log
```

## Logging results

实验结束后记录到 `results.tsv`（Tab 分隔，不要用逗号）。

表头和 5 列：

```
commitavg_token_costpass_ratestatusdescription
```

1. git commit hash（短格式，7 字符）
2. avg_token_cost（如 1200.0）— 崩溃时记 0.0
3. pass_rate（如 1.0000）— 崩溃时记 0.0000
4. status：`keep`、`discard` 或 `crash`
5. 简短描述本次实验的修改

## The experiment loop

LOOP FOREVER:

1. 查看当前 git 状态（分支/commit）
2. 分析当前 `.agents/skills/` 和历史 results.tsv，提出优化假设
3. 修改 Skill 文件（`.agents/skills/*.md`）
4. git commit
5. 运行评估：`uv run harness.py --task-dir <path> > run.log 2>&1`
6. 提取指标：`grep "^avg_token_cost:\|^pass_rate:" run.log`
7. 如果 grep 输出为空，说明运行崩溃。`tail -n 50 run.log` 查看错误信息
8. 记录到 results.tsv（不要 commit results.tsv）
9. 判断：
   - `pass_rate >= 0.9` 且 `avg_token_cost` 下降 → 保留 commit
   - `pass_rate < 0.9` → 回退（`git reset --hard HEAD~1`）
   - `avg_token_cost` 未下降 → 回退
10. 继续下一轮

**优化维度参考**：
- 指令精简：删除冗余描述
- 结构优化：调整 Skill 的组织方式
- 合并/拆分：将多个 Skill 合并，或拆分为更专注的 Skill
- 措辞优化：用更精确的词汇
- 示例优化：调整 few-shot 示例
- 输出格式约束：限制输出格式和长度

**NEVER STOP**：一旦实验循环开始，不要暂停问人类是否继续。人类可能在睡觉。你是自主的。如果没有想法，更用力地思考 — 尝试组合之前的方案、更激进的修改。循环持续到人类手动中断。
