"""
harness.py — Skill 评估框架

通过 opencode（或其他 agent 工具）执行任务目录中的任务，
评估 .agents/skills/ 下各层目录中 Skill 文件的 token 消耗和任务完成质量。

用法：
    uv run harness.py --task-dir ./example
    uv run harness.py --task-dir ./example --token-limit 5000
    uv run harness.py --task-dir ./example --agent opencode --timeout 300
    uv run harness.py --task-dir ./example --runs 3
    uv run harness.py --task-dir ./example --exclude-skills "vendor/*" "legacy.md"

任务目录结构：
    <task-dir>/
    ├── task.md                    # 任务描述（必须）
    └── .agents/
        └── skills/
            ├── coding.md          # Skill 文件（被优化对象）
            ├── review/
            │   └── SKILL.md       # 子目录中的 Skill
            └── vendor/
                └── shared.md      # 可通过 --exclude-skills 排除

环境变量：
    HARNESS_AGENT           — Agent 命令（默认：opencode）
    HARNESS_TOKEN_LIMIT     — 每次任务的 Token 上限（0 = 无限制）
    HARNESS_TIMEOUT         — 执行超时秒数（默认：300）
    HARNESS_EXCLUDE_SKILLS  — 排除的 Skill 路径模式（逗号分隔）

注意：
    本框架配置的 API KEY 只用于框架自身（如 LLM 评估）。
    opencode 使用的 AI 服务由用户自行配置，框架只管调用 opencode 干活。

    排除（--exclude-skills）仅影响本工具的优化跟踪，不影响 opencode 的加载机制。
"""

import fnmatch
import json
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
class SkillInfo:
    """Skill 文件信息。"""
    name: str
    path: str
    char_count: int


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
    skills: list
    runs: list
    avg_token_cost: float
    pass_rate: float
    total_tokens: int
    total_skills_chars: int
    num_runs: int
    num_passed: int

# ---------------------------------------------------------------------------
# Skill 发现
# ---------------------------------------------------------------------------


def discover_skills(
    task_dir: str,
    exclude_patterns: Optional[list] = None,
) -> list:
    """发现任务目录中 .agents/skills/ 下各层目录中的所有 Skill (.md) 文件。

    递归扫描 .agents/skills/ 的全部子目录。
    exclude_patterns 中的 glob 模式用于排除不需要优化的文件/目录
    （不影响 opencode 的加载机制，仅影响本工具的跟踪范围）。

    模式匹配基于相对于 .agents/skills/ 的路径，例如：
        "vendor/*"     — 排除 vendor 目录下所有文件
        "legacy.md"    — 排除名为 legacy.md 的文件
        "*/SKILL.md"   — 排除所有子目录中的 SKILL.md
    """
    skills_dir = os.path.join(task_dir, ".agents", "skills")
    if not os.path.isdir(skills_dir):
        return []

    if exclude_patterns is None:
        exclude_patterns = []

    skills = []
    for dirpath, _dirnames, filenames in os.walk(skills_dir):
        for filename in sorted(filenames):
            if not filename.endswith(".md"):
                continue
            filepath = os.path.join(dirpath, filename)
            # 计算相对于 skills_dir 的路径，用于排除匹配
            rel_path = os.path.relpath(filepath, skills_dir)
            if _is_excluded(rel_path, exclude_patterns):
                continue
            with open(filepath, "r", encoding="utf-8") as f:
                char_count = len(f.read())
            skills.append(SkillInfo(
                name=rel_path,
                path=filepath,
                char_count=char_count,
            ))
    # 按相对路径排序，确保输出稳定
    skills.sort(key=lambda s: s.name)
    return skills


def _is_excluded(rel_path: str, patterns: list) -> bool:
    """检查相对路径是否匹配任一排除模式。"""
    # 规范化路径分隔符为 /
    normalized = rel_path.replace(os.sep, "/")
    for pattern in patterns:
        pattern = pattern.replace(os.sep, "/")
        # 匹配完整路径
        if fnmatch.fnmatch(normalized, pattern):
            return True
        # 匹配文件名部分
        if fnmatch.fnmatch(os.path.basename(normalized), pattern):
            return True
        # 匹配路径中任一层级前缀（支持 "vendor/*" 匹配 "vendor/sub/file.md"）
        parts = normalized.split("/")
        for i in range(len(parts)):
            partial = "/".join(parts[:i + 1])
            if fnmatch.fnmatch(partial, pattern):
                return True
    return False

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
    exclude_patterns: Optional[list] = None,
) -> EvaluationResult:
    """在任务目录上评估当前 Skills，返回评估结果。"""
    task_content = load_task(task_dir)
    skills = discover_skills(task_dir, exclude_patterns=exclude_patterns)

    runs = []
    for _ in range(num_runs):
        result = run_task(task_dir, task_content, agent_cmd, token_limit, timeout)
        runs.append(result)

    num_passed = sum(1 for r in runs if r.success)
    total_tokens = sum(r.tokens_used for r in runs)
    avg_cost = total_tokens / num_runs if num_runs > 0 else 0
    pass_rate = num_passed / num_runs if num_runs > 0 else 0
    total_chars = sum(s.char_count for s in skills)

    return EvaluationResult(
        task_dir=task_dir,
        skills=skills,
        runs=runs,
        avg_token_cost=avg_cost,
        pass_rate=pass_rate,
        total_tokens=total_tokens,
        total_skills_chars=total_chars,
        num_runs=num_runs,
        num_passed=num_passed,
    )

# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Skill 评估框架 — 通过 opencode 执行任务")
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
    parser.add_argument(
        "--exclude-skills", nargs="*", default=None,
        help="排除的 Skill 路径模式（相对于 .agents/skills/，支持 glob）。"
             "仅影响本工具的跟踪，不影响 opencode 加载。"
             "例如：--exclude-skills 'vendor/*' 'legacy.md'",
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

    # 排除模式：CLI > 环境变量
    exclude_patterns = args.exclude_skills
    if exclude_patterns is None:
        env_exclude = os.environ.get("HARNESS_EXCLUDE_SKILLS", "")
        if env_exclude.strip():
            exclude_patterns = [p.strip() for p in env_exclude.split(",") if p.strip()]

    # 验证任务目录
    if not os.path.isdir(task_dir):
        print(f"错误：任务目录 '{task_dir}' 不存在。", file=sys.stderr)
        sys.exit(1)
    if not os.path.isfile(os.path.join(task_dir, "task.md")):
        print(f"错误：'{task_dir}/task.md' 不存在。", file=sys.stderr)
        sys.exit(1)

    # 信息提示
    skills_dir = os.path.join(task_dir, ".agents", "skills")
    if not os.path.isdir(skills_dir):
        print(f"警告：Skills 目录 '{skills_dir}' 不存在。", file=sys.stderr)
    if not check_agent_available(agent_cmd):
        print(f"警告：agent 命令 '{agent_cmd}' 未在 PATH 中找到。", file=sys.stderr)

    # 执行评估
    result = evaluate(
        task_dir, agent_cmd, token_limit, timeout, args.runs,
        exclude_patterns=exclude_patterns,
    )

    # 输出指标
    print("---")
    print(f"task_dir:           {result.task_dir}")
    print(f"agent:              {agent_cmd}")
    if exclude_patterns:
        print(f"exclude_skills:     {', '.join(exclude_patterns)}")
    print(f"num_skills:         {len(result.skills)}")
    print(f"skills_total_chars: {result.total_skills_chars}")
    for s in result.skills:
        print(f"  skill: {s.name} ({s.char_count} chars)")
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
