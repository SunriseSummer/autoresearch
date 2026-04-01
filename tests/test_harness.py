"""harness.py 单元测试。"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from harness import (
    SkillInfo,
    TaskResult,
    EvaluationResult,
    discover_skills,
    load_task,
    parse_token_usage,
    check_agent_available,
    run_task,
    evaluate,
    PASS_THRESHOLD,
    DEFAULT_AGENT,
    DEFAULT_TIMEOUT,
    DEFAULT_TOKEN_LIMIT,
)


# ---------------------------------------------------------------------------
# discover_skills 测试
# ---------------------------------------------------------------------------


class TestDiscoverSkills:
    def test_finds_skills(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = os.path.join(tmpdir, ".agents", "skills")
            os.makedirs(skills_dir)
            for name in ["a.md", "b.md"]:
                with open(os.path.join(skills_dir, name), "w") as f:
                    f.write(f"# Skill {name}")

            skills = discover_skills(tmpdir)
            assert len(skills) == 2
            assert skills[0].name == "a.md"
            assert skills[1].name == "b.md"
            assert skills[0].char_count > 0

    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = os.path.join(tmpdir, ".agents", "skills")
            os.makedirs(skills_dir)
            assert discover_skills(tmpdir) == []

    def test_no_skills_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            assert discover_skills(tmpdir) == []

    def test_ignores_non_md(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = os.path.join(tmpdir, ".agents", "skills")
            os.makedirs(skills_dir)
            with open(os.path.join(skills_dir, "a.md"), "w") as f:
                f.write("skill")
            with open(os.path.join(skills_dir, "b.txt"), "w") as f:
                f.write("not a skill")

            skills = discover_skills(tmpdir)
            assert len(skills) == 1
            assert skills[0].name == "a.md"


# ---------------------------------------------------------------------------
# load_task 测试
# ---------------------------------------------------------------------------


class TestLoadTask:
    def test_loads_task_md(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "task.md"), "w") as f:
                f.write("# Test Task\nDo something.")

            content = load_task(tmpdir)
            assert "Test Task" in content
            assert "Do something." in content

    def test_missing_task_md(self):
        import pytest
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(SystemExit):
                load_task(tmpdir)


# ---------------------------------------------------------------------------
# parse_token_usage 测试
# ---------------------------------------------------------------------------


class TestParseTokenUsage:
    def test_total_tokens_format(self):
        assert parse_token_usage("Total Tokens: 1234") == 1234

    def test_tokens_used_format(self):
        assert parse_token_usage("tokens used: 5678") == 5678

    def test_n_tokens_total(self):
        assert parse_token_usage("Used 999 tokens total") == 999

    def test_token_count_format(self):
        assert parse_token_usage("token_count: 42") == 42

    def test_no_match(self):
        assert parse_token_usage("no token info here") == 0

    def test_empty_string(self):
        assert parse_token_usage("") == 0

    def test_last_match_wins(self):
        output = "Total Tokens: 100\nSome text\nTotal Tokens: 200"
        assert parse_token_usage(output) == 200

    def test_tokens_consumed(self):
        assert parse_token_usage("Tokens consumed: 3456") == 3456


# ---------------------------------------------------------------------------
# check_agent_available 测试
# ---------------------------------------------------------------------------


class TestCheckAgentAvailable:
    def test_python_available(self):
        assert (check_agent_available("python3") is True
                or check_agent_available("python") is True)

    def test_nonexistent_unavailable(self):
        assert check_agent_available("nonexistent_cmd_xyz_123") is False


# ---------------------------------------------------------------------------
# run_task 测试
# ---------------------------------------------------------------------------


class TestRunTask:
    def test_agent_not_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_task(tmpdir, "test", agent_cmd="nonexistent_cmd_xyz")
            assert result.success is False
            assert "未找到" in result.error

    def test_successful_run(self):
        """使用 echo 作为 agent 模拟成功执行。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_task(tmpdir, "hello", agent_cmd="echo", timeout=10)
            assert result.exit_code == 0
            assert result.success is True
            assert result.duration_seconds > 0

    def test_token_limit_exceeded(self):
        """Token limit 超出时应判定为失败。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # echo outputs "Total Tokens: 100" which will be parsed
            result = run_task(
                tmpdir,
                "Total Tokens: 100",
                agent_cmd="echo",
                token_limit=50,
                timeout=10,
            )
            assert result.token_limit_exceeded is True
            assert result.success is False


# ---------------------------------------------------------------------------
# TaskResult 数据类测试
# ---------------------------------------------------------------------------


class TestTaskResult:
    def test_default_fields(self):
        r = TaskResult(True, 100, 1.0, 0, "output")
        assert r.error is None
        assert r.token_limit_exceeded is False

    def test_with_error(self):
        r = TaskResult(False, 0, 1.0, 1, "", error="timeout")
        assert r.error == "timeout"


# ---------------------------------------------------------------------------
# evaluate 测试
# ---------------------------------------------------------------------------


class TestEvaluate:
    def test_evaluate_with_echo(self):
        """使用 echo 模拟评估。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create task.md
            with open(os.path.join(tmpdir, "task.md"), "w") as f:
                f.write("test task")
            # Create a skill
            skills_dir = os.path.join(tmpdir, ".agents", "skills")
            os.makedirs(skills_dir)
            with open(os.path.join(skills_dir, "test.md"), "w") as f:
                f.write("# Test Skill")

            result = evaluate(tmpdir, agent_cmd="echo", timeout=10, num_runs=2)
            assert result.num_runs == 2
            assert len(result.runs) == 2
            assert len(result.skills) == 1
            assert result.skills[0].name == "test.md"


# ---------------------------------------------------------------------------
# 示例目录完整性测试
# ---------------------------------------------------------------------------


class TestExampleDirectory:
    def test_example_task_exists(self):
        example_dir = os.path.join(os.path.dirname(__file__), "..", "example")
        if not os.path.isdir(example_dir):
            return  # 跳过

        assert os.path.isfile(os.path.join(example_dir, "task.md"))
        skills = discover_skills(example_dir)
        assert len(skills) >= 1

    def test_example_task_loadable(self):
        example_dir = os.path.join(os.path.dirname(__file__), "..", "example")
        if not os.path.isdir(example_dir):
            return

        content = load_task(example_dir)
        assert len(content) > 0


# ---------------------------------------------------------------------------
# 常量测试
# ---------------------------------------------------------------------------


class TestConstants:
    def test_pass_threshold(self):
        assert PASS_THRESHOLD == 0.9

    def test_default_agent(self):
        assert DEFAULT_AGENT == "opencode"

    def test_default_timeout(self):
        assert DEFAULT_TIMEOUT == 300

    def test_default_token_limit(self):
        assert DEFAULT_TOKEN_LIMIT == 0
