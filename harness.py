"""
harness.py — Skill 评估框架（对应原始项目的 prepare.py）

加载任务集，通过 LLM API 执行 Skill，评估质量，输出指标。

用法：
    uv run harness.py                    # 使用默认配置评估 skill.md
    uv run harness.py --skill my.md      # 评估指定 Skill 文件
    uv run harness.py --provider kimi    # 使用 Kimi 模型

环境变量：
    OPENAI_API_KEY      — OpenAI API 密钥
    KIMI_API_KEY        — Kimi（Moonshot）API 密钥
    MINIMAX_API_KEY     — Minimax API 密钥
    SKILL_API_KEY       — 覆盖任意提供商的 API 密钥
    SKILL_API_BASE      — 覆盖 API 基础地址
    SKILL_MODEL_NAME    — 覆盖模型名称
"""

import json
import os
import sys
import argparse
from dataclasses import dataclass, field
from typing import Optional

from openai import OpenAI

# ---------------------------------------------------------------------------
# 常量定义（固定值，请勿修改）
# ---------------------------------------------------------------------------

TASK_DIR = "tasks"
PASS_THRESHOLD = 0.9
MAX_RETRIES = 2
TEMPERATURE = 0.0
SEED = 42

# ---------------------------------------------------------------------------
# 模型提供商配置
# ---------------------------------------------------------------------------

PROVIDER_CONFIG = {
    "openai": {
        "api_base": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
        "default_model": "gpt-4o-mini",
    },
    "kimi": {
        "api_base": "https://api.moonshot.cn/v1",
        "api_key_env": "KIMI_API_KEY",
        "default_model": "moonshot-v1-8k",
    },
    "minimax": {
        "api_base": "https://api.minimax.chat/v1",
        "api_key_env": "MINIMAX_API_KEY",
        "default_model": "MiniMax-Text-01",
    },
}

# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass
class Task:
    """任务定义。"""
    task_id: str
    input: str
    expected: object          # str | list[str] | dict
    evaluator: str            # exact_match | contains | llm_judge
    metadata: dict = field(default_factory=dict)


@dataclass
class ExecutionResult:
    """单任务执行结果。"""
    output: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    error: Optional[str] = None

# ---------------------------------------------------------------------------
# 客户端创建
# ---------------------------------------------------------------------------


def create_client(provider: str = "openai") -> tuple:
    """为指定提供商创建 OpenAI 兼容客户端，返回 (client, model_name)。"""
    api_key = os.environ.get("SKILL_API_KEY")
    api_base = os.environ.get("SKILL_API_BASE")
    model_name = os.environ.get("SKILL_MODEL_NAME")

    config = PROVIDER_CONFIG.get(provider)
    if config is None:
        if not api_key or not api_base or not model_name:
            print(
                f"错误：未知提供商 '{provider}'，"
                "请设置 SKILL_API_KEY、SKILL_API_BASE、SKILL_MODEL_NAME。",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        if api_key is None:
            api_key = os.environ.get(config["api_key_env"], "")
        if api_base is None:
            api_base = config["api_base"]
        if model_name is None:
            model_name = config["default_model"]

    client = OpenAI(api_key=api_key, base_url=api_base)
    return client, model_name

# ---------------------------------------------------------------------------
# 任务加载器
# ---------------------------------------------------------------------------


def load_tasks(task_dir: str = TASK_DIR) -> list:
    """从 tasks/ 目录加载全部 JSON 任务。"""
    if not os.path.isdir(task_dir):
        print(f"错误：任务目录 '{task_dir}' 不存在。", file=sys.stderr)
        sys.exit(1)

    files = sorted(f for f in os.listdir(task_dir) if f.endswith(".json"))
    if not files:
        print(f"错误：任务目录 '{task_dir}' 中没有任务文件。", file=sys.stderr)
        sys.exit(1)

    tasks = []
    for filename in files:
        with open(os.path.join(task_dir, filename), "r", encoding="utf-8") as f:
            d = json.load(f)
        tasks.append(Task(
            task_id=d["task_id"],
            input=d["input"],
            expected=d["expected"],
            evaluator=d.get("evaluator", "contains"),
            metadata=d.get("metadata", {}),
        ))
    return tasks

# ---------------------------------------------------------------------------
# Skill 执行器
# ---------------------------------------------------------------------------


def execute_skill(
    client: OpenAI,
    model_name: str,
    skill_content: str,
    task: Task,
) -> ExecutionResult:
    """用 Skill 完成单个任务，返回 ExecutionResult。"""
    try:
        resp = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": skill_content},
                {"role": "user", "content": task.input},
            ],
            temperature=TEMPERATURE,
            seed=SEED,
        )
        usage = resp.usage
        return ExecutionResult(
            output=resp.choices[0].message.content or "",
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
        )
    except Exception as exc:
        return ExecutionResult(
            output="", prompt_tokens=0,
            completion_tokens=0, total_tokens=0,
            error=str(exc),
        )

# ---------------------------------------------------------------------------
# 质量评估器
# ---------------------------------------------------------------------------


def evaluate_quality(
    task: Task,
    output: str,
    client: Optional[OpenAI] = None,
    model_name: Optional[str] = None,
) -> bool:
    """评估任务完成质量（通过/不通过）。"""
    ev = task.evaluator

    if ev == "exact_match":
        return isinstance(task.expected, str) and output.strip() == task.expected.strip()

    if ev == "contains":
        keywords = (
            [task.expected] if isinstance(task.expected, str) else
            task.expected if isinstance(task.expected, list) else []
        )
        low = output.lower()
        return all(k.lower() in low for k in keywords)

    if ev == "llm_judge":
        if client is None or model_name is None:
            # 降级
            return evaluate_quality(
                Task(task.task_id, task.input, task.expected,
                     "contains", task.metadata),
                output,
            )
        judge_prompt = (
            "You are an evaluation judge. Determine if the response "
            "correctly addresses the task.\n\n"
            f"Task: {task.input}\n\nExpected: {task.expected}\n\n"
            f"Response: {output}\n\n"
            "Reply with exactly 'PASS' or 'FAIL'."
        )
        try:
            r = client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": judge_prompt}],
                temperature=0.0, seed=SEED,
            )
            return "PASS" in (r.choices[0].message.content or "").upper()
        except Exception:
            return False

    # 未知评估器 → 降级
    return evaluate_quality(
        Task(task.task_id, task.input, task.expected,
             "contains", task.metadata),
        output,
    )

# ---------------------------------------------------------------------------
# 核心评估函数
# ---------------------------------------------------------------------------


def evaluate_skill(
    skill_path: str,
    client: OpenAI,
    model_name: str,
    task_dir: str = TASK_DIR,
) -> dict:
    """在完整任务集上评估 Skill，返回指标字典。"""
    with open(skill_path, "r", encoding="utf-8") as f:
        skill_content = f.read()

    tasks = load_tasks(task_dir)
    num_tasks = len(tasks)

    per_task_results = []
    tot_prompt = tot_comp = tot_all = num_passed = 0

    for task in tasks:
        result = None
        passed = False
        for attempt in range(MAX_RETRIES + 1):
            result = execute_skill(client, model_name, skill_content, task)
            if result.error is None:
                passed = evaluate_quality(task, result.output, client, model_name)
                if passed:
                    break
            elif attempt >= MAX_RETRIES:
                break

        if result is None:
            result = ExecutionResult("", 0, 0, 0, "max retries exceeded")

        tot_prompt += result.prompt_tokens
        tot_comp += result.completion_tokens
        tot_all += result.total_tokens
        if passed:
            num_passed += 1
        per_task_results.append({
            "task_id": task.task_id,
            "passed": passed,
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "total_tokens": result.total_tokens,
            "error": result.error,
        })

    avg_cost = tot_all / num_tasks if num_tasks else 0
    pass_rate = num_passed / num_tasks if num_tasks else 0

    return {
        "avg_token_cost": avg_cost,
        "pass_rate": pass_rate,
        "total_tokens": tot_all,
        "total_prompt_tokens": tot_prompt,
        "total_completion_tokens": tot_comp,
        "num_tasks": num_tasks,
        "num_passed": num_passed,
        "skill_length": len(skill_content),
        "per_task_results": per_task_results,
    }

# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="评估 Skill 效果")
    parser.add_argument("--skill", default="skill.md", help="Skill 文件路径")
    parser.add_argument("--task-dir", default=TASK_DIR, help="任务目录")
    parser.add_argument("--provider", default="openai",
                        help="模型提供商: openai / kimi / minimax")
    args = parser.parse_args()

    if not os.path.isfile(args.skill):
        print(f"错误：Skill 文件 '{args.skill}' 不存在。", file=sys.stderr)
        sys.exit(1)

    client, model_name = create_client(args.provider)
    r = evaluate_skill(args.skill, client, model_name, args.task_dir)

    print("---")
    print(f"avg_token_cost:    {r['avg_token_cost']:.1f}")
    print(f"pass_rate:         {r['pass_rate']:.4f}")
    print(f"total_tokens:      {r['total_tokens']}")
    print(f"prompt_tokens:     {r['total_prompt_tokens']}")
    print(f"completion_tokens: {r['total_completion_tokens']}")
    print(f"num_tasks:         {r['num_tasks']}")
    print(f"num_passed:        {r['num_passed']}")
    print(f"skill_length:      {r['skill_length']}")


if __name__ == "__main__":
    main()
