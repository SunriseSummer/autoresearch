"""
harness.py — 任务评估框架

通过 opencode（或其他 agent 工具）执行任务目录中的任务，
评估任务的 token 消耗和完成质量。

harness.py 只负责评估任务执行效果（token 消耗、通过率），
不涉及 Skill 文件的统计和分析 — 这部分由上层 agent 自行处理。

用法：
    uv run harness.py --task-dir ./example
    uv run harness.py --task-dir ./example --token-limit 5000
    uv run harness.py --task-dir ./example --agent opencode --timeout 300
    uv run harness.py --task-dir ./example --runs 3

任务目录结构：
    <task-dir>/
    ├── task.md                    # 任务描述（必须）
    └── .agents/
        └── skills/                # opencode 自动加载的 Skill 文件
            └── ...                # 由上层 agent 管理和优化

环境变量：
    HARNESS_AGENT           — Agent 命令（默认：opencode）
    HARNESS_TOKEN_LIMIT     — 每次任务的 Token 上限（0 = 无限制）
    HARNESS_TIMEOUT         — 执行超时秒数（默认：300）

注意：
    本框架配置的 API KEY 只用于框架自身（如 LLM 评估）。
    opencode 使用的 AI 服务由用户自行配置，框架只管调用 opencode 干活。
"""

import os
import re
import shutil
import subprocess
import sys
import time
import argparse
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

DEFAULT_AGENT = "opencode"
DEFAULT_TIMEOUT = 300
DEFAULT_TOKEN_LIMIT = 0
PASS_THRESHOLD = 0.9

# Token 使用量匹配模式（按优先级排列，适配多种 agent 输出格式）
TOKEN_PATTERNS = [
    r"[Tt]otal\s*[Tt]okens?[:\s]+(\d+)",
    r"[Tt]okens?\s*(?:used|usage|consumed)[:\s]+(\d+)",
    r"(\d+)\s+tokens?\s+total",
    r"tokens_used[:\s]+(\d+)",
    r"token[_\s]?count[:\s]+(\d+)",
]

# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass
class TaskResult:
    """单次任务执行结果。"""
    success: bool
    tokens_used: int
    duration_seconds: float
    exit_code: int
    output: str
    error: Optional[str] = None
    token_limit_exceeded: bool = False


@dataclass
class EvaluationResult:
    """完整评估结果。"""
    task_dir: str
    runs: list
    avg_token_cost: float
    pass_rate: float
    total_tokens: int
    num_runs: int
    num_passed: int

# ---------------------------------------------------------------------------
# 任务加载
# ---------------------------------------------------------------------------


def load_task(task_dir: str) -> str:
    """加载任务目录中的 task.md。"""
    task_file = os.path.join(task_dir, "task.md")
    if not os.path.isfile(task_file):
        print(f"错误：任务文件 '{task_file}' 不存在。", file=sys.stderr)
        sys.exit(1)
    with open(task_file, "r", encoding="utf-8") as f:
        return f.read()

# ---------------------------------------------------------------------------
# Token 解析
# ---------------------------------------------------------------------------


def parse_token_usage(output: str) -> int:
    """从 agent 输出中解析 token 使用量。返回 0 如果未找到。"""
    for pattern in TOKEN_PATTERNS:
        matches = re.findall(pattern, output)
        if matches:
            return int(matches[-1])
    return 0

# ---------------------------------------------------------------------------
# Agent 可用性检查
# ---------------------------------------------------------------------------


def check_agent_available(agent_cmd: str) -> bool:
    """检查 agent 命令是否在 PATH 中可用。"""
    return shutil.which(agent_cmd) is not None

# ---------------------------------------------------------------------------
# 任务执行
# ---------------------------------------------------------------------------


def run_task(
    task_dir: str,
    task_content: str,
    agent_cmd: str = DEFAULT_AGENT,
    token_limit: int = DEFAULT_TOKEN_LIMIT,
    timeout: int = DEFAULT_TIMEOUT,
) -> TaskResult:
    """
    使用 agent 工具执行一次任务。

    agent 在 task_dir 中运行，自动加载 .agents/skills/ 下的 Skill 文件。
    """
    start_time = time.time()

    try:
        process = subprocess.run(
            [agent_cmd, "-m", task_content],
            cwd=task_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        duration = time.time() - start_time
        combined = (process.stdout or "") + "\n" + (process.stderr or "")
        tokens = parse_token_usage(combined)

        success = process.returncode == 0
        exceeded = token_limit > 0 and tokens > token_limit
        if exceeded:
            success = False

        return TaskResult(
            success=success,
            tokens_used=tokens,
            duration_seconds=duration,
            exit_code=process.returncode,
            output=process.stdout or "",
            error=process.stderr if process.returncode != 0 else None,
            token_limit_exceeded=exceeded,
        )

    except subprocess.TimeoutExpired:
        return TaskResult(
            success=False, tokens_used=0,
            duration_seconds=time.time() - start_time,
            exit_code=-1, output="",
            error=f"超时：执行超过 {timeout} 秒",
        )

    except FileNotFoundError:
        return TaskResult(
            success=False, tokens_used=0,
            duration_seconds=time.time() - start_time,
            exit_code=-1, output="",
            error=f"agent 命令 '{agent_cmd}' 未找到，请确认已安装",
        )

    except Exception as exc:
        return TaskResult(
            success=False, tokens_used=0,
            duration_seconds=time.time() - start_time,
            exit_code=-1, output="",
            error=str(exc),
        )

# ---------------------------------------------------------------------------
# 核心评估
# ---------------------------------------------------------------------------


def evaluate(
    task_dir: str,
    agent_cmd: str = DEFAULT_AGENT,
    token_limit: int = DEFAULT_TOKEN_LIMIT,
    timeout: int = DEFAULT_TIMEOUT,
    num_runs: int = 1,
) -> EvaluationResult:
    """在任务目录上评估任务执行效果，返回评估结果。"""
    task_content = load_task(task_dir)

    runs = []
    for _ in range(num_runs):
        result = run_task(task_dir, task_content, agent_cmd, token_limit, timeout)
        runs.append(result)

    num_passed = sum(1 for r in runs if r.success)
    total_tokens = sum(r.tokens_used for r in runs)
    avg_cost = total_tokens / num_runs if num_runs > 0 else 0
    pass_rate = num_passed / num_runs if num_runs > 0 else 0

    return EvaluationResult(
        task_dir=task_dir,
        runs=runs,
        avg_token_cost=avg_cost,
        pass_rate=pass_rate,
        total_tokens=total_tokens,
        num_runs=num_runs,
        num_passed=num_passed,
    )

# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="任务评估框架 — 通过 opencode 执行任务")
    parser.add_argument(
        "--task-dir", default=".",
        help="任务目录路径（包含 task.md 和 .agents/skills/）",
    )
    parser.add_argument(
        "--agent", default=None,
        help=f"Agent 命令（默认：{DEFAULT_AGENT}，可通过 HARNESS_AGENT 环境变量设置）",
    )
    parser.add_argument(
        "--token-limit", type=int, default=None,
        help="每次任务的 Token 上限，超过判定失败（0=无限制）",
    )
    parser.add_argument(
        "--timeout", type=int, default=None,
        help=f"执行超时秒数（默认：{DEFAULT_TIMEOUT}）",
    )
    parser.add_argument(
        "--runs", type=int, default=1,
        help="评估运行次数（默认：1）",
    )
    args = parser.parse_args()

    # 配置优先级：CLI > 环境变量 > 默认值
    agent_cmd = (
        args.agent
        or os.environ.get("HARNESS_AGENT", DEFAULT_AGENT)
    )
    token_limit = (
        args.token_limit if args.token_limit is not None
        else int(os.environ.get("HARNESS_TOKEN_LIMIT", DEFAULT_TOKEN_LIMIT))
    )
    timeout = (
        args.timeout if args.timeout is not None
        else int(os.environ.get("HARNESS_TIMEOUT", DEFAULT_TIMEOUT))
    )
    task_dir = os.path.abspath(args.task_dir)

    # 验证任务目录
    if not os.path.isdir(task_dir):
        print(f"错误：任务目录 '{task_dir}' 不存在。", file=sys.stderr)
        sys.exit(1)
    if not os.path.isfile(os.path.join(task_dir, "task.md")):
        print(f"错误：'{task_dir}/task.md' 不存在。", file=sys.stderr)
        sys.exit(1)

    # 信息提示
    if not check_agent_available(agent_cmd):
        print(f"警告：agent 命令 '{agent_cmd}' 未在 PATH 中找到。", file=sys.stderr)

    # 执行评估
    result = evaluate(task_dir, agent_cmd, token_limit, timeout, args.runs)

    # 输出指标
    print("---")
    print(f"task_dir:           {result.task_dir}")
    print(f"agent:              {agent_cmd}")
    print(f"avg_token_cost:     {result.avg_token_cost:.1f}")
    print(f"pass_rate:          {result.pass_rate:.4f}")
    print(f"total_tokens:       {result.total_tokens}")
    print(f"num_runs:           {result.num_runs}")
    print(f"num_passed:         {result.num_passed}")
    print(f"token_limit:        {token_limit}")
    print(f"timeout:            {timeout}")

    for i, run in enumerate(result.runs):
        status = "PASS" if run.success else "FAIL"
        extra = ""
        if run.token_limit_exceeded:
            extra = " [TOKEN_LIMIT_EXCEEDED]"
        if run.error:
            extra += f" [{run.error[:80]}]"
        print(
            f"  run_{i+1}: {status}"
            f" tokens={run.tokens_used}"
            f" duration={run.duration_seconds:.1f}s{extra}"
        )


if __name__ == "__main__":
    main()
