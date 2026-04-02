"""harness.py 单元测试。"""

import os
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from harness import (
    TaskResult,
    EvaluationResult,
    load_task,
    parse_token_usage,
    check_agent_available,
    run_task,
    evaluate,
    main,
    PASS_THRESHOLD,
    DEFAULT_AGENT,
    DEFAULT_TIMEOUT,
    DEFAULT_TOKEN_LIMIT,
    TOKEN_PATTERNS,
)


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
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(SystemExit):
                load_task(tmpdir)

    def test_loads_utf8_content(self):
        """能正确加载包含中文的 task.md。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "task.md"), "w", encoding="utf-8") as f:
                f.write("# 中文任务\n请编写代码。")

            content = load_task(tmpdir)
            assert "中文任务" in content
            assert "请编写代码" in content

    def test_loads_empty_task(self):
        """空的 task.md 也能正常加载。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "task.md"), "w") as f:
                f.write("")

            content = load_task(tmpdir)
            assert content == ""

    def test_loads_multiline_task(self):
        """多行 task.md 保持完整内容。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            lines = "Line 1\nLine 2\nLine 3\n"
            with open(os.path.join(tmpdir, "task.md"), "w") as f:
                f.write(lines)

            content = load_task(tmpdir)
            assert content == lines


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

    def test_tokens_used_key_value(self):
        assert parse_token_usage("tokens_used: 789") == 789

    def test_case_insensitive_total(self):
        assert parse_token_usage("total tokens: 500") == 500

    def test_token_count_underscore(self):
        assert parse_token_usage("token_count: 111") == 111

    def test_token_count_space(self):
        assert parse_token_usage("token count: 222") == 222

    def test_multiline_with_noise(self):
        """在包含大量无关文本的输出中正确提取 token。"""
        output = """
Starting task...
Processing files...
Total Tokens: 4500
Task complete.
"""
        assert parse_token_usage(output) == 4500

    def test_zero_tokens(self):
        assert parse_token_usage("Total Tokens: 0") == 0

    def test_large_token_number(self):
        assert parse_token_usage("Total Tokens: 1000000") == 1000000

    def test_patterns_list_not_empty(self):
        """TOKEN_PATTERNS 应包含至少一个模式。"""
        assert len(TOKEN_PATTERNS) > 0


# ---------------------------------------------------------------------------
# check_agent_available 测试
# ---------------------------------------------------------------------------


class TestCheckAgentAvailable:
    def test_python_available(self):
        assert (check_agent_available("python3") is True
                or check_agent_available("python") is True)

    def test_nonexistent_unavailable(self):
        assert check_agent_available("nonexistent_cmd_xyz_123") is False

    def test_echo_available(self):
        """echo 命令应当在 PATH 中可用。"""
        assert check_agent_available("echo") is True

    def test_empty_string(self):
        """空字符串命令不可用。"""
        assert check_agent_available("") is False


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
            result = run_task(
                tmpdir,
                "Total Tokens: 100",
                agent_cmd="echo",
                token_limit=50,
                timeout=10,
            )
            assert result.token_limit_exceeded is True
            assert result.success is False

    def test_token_limit_not_exceeded(self):
        """Token limit 未超出时应判定为成功。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_task(
                tmpdir,
                "Total Tokens: 100",
                agent_cmd="echo",
                token_limit=200,
                timeout=10,
            )
            assert result.token_limit_exceeded is False
            assert result.success is True
            assert result.tokens_used == 100

    def test_zero_token_limit_means_unlimited(self):
        """token_limit=0 表示无限制。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_task(
                tmpdir,
                "Total Tokens: 99999",
                agent_cmd="echo",
                token_limit=0,
                timeout=10,
            )
            assert result.token_limit_exceeded is False
            assert result.success is True

    def test_timeout(self):
        """超时时应返回失败结果。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建一个 shell 脚本模拟长时间运行
            script = os.path.join(tmpdir, "slow_agent.sh")
            with open(script, "w") as f:
                f.write("#!/bin/sh\nsleep 30\n")
            os.chmod(script, 0o755)

            result = run_task(
                tmpdir,
                "test",
                agent_cmd=script,
                timeout=1,
            )
            assert result.success is False
            assert result.exit_code == -1

    def test_failed_command(self):
        """命令执行失败（非零退出码）应报告失败。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_task(
                tmpdir,
                "",
                agent_cmd="false",
                timeout=10,
            )
            assert result.success is False
            assert result.exit_code != 0

    def test_output_captured(self):
        """echo 的输出应被捕获。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_task(tmpdir, "hello world", agent_cmd="echo", timeout=10)
            assert "hello world" in result.output

    def test_token_parsing_from_output(self):
        """从 agent 输出中正确解析 token 数。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_task(
                tmpdir,
                "Tokens used: 777",
                agent_cmd="echo",
                timeout=10,
            )
            assert result.tokens_used == 777

    def test_no_tokens_in_output(self):
        """输出中无 token 信息时返回 0。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_task(
                tmpdir,
                "just some text",
                agent_cmd="echo",
                timeout=10,
            )
            assert result.tokens_used == 0


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

    def test_all_fields(self):
        r = TaskResult(
            success=False,
            tokens_used=500,
            duration_seconds=2.5,
            exit_code=1,
            output="some output",
            error="some error",
            token_limit_exceeded=True,
        )
        assert r.success is False
        assert r.tokens_used == 500
        assert r.duration_seconds == 2.5
        assert r.exit_code == 1
        assert r.output == "some output"
        assert r.error == "some error"
        assert r.token_limit_exceeded is True


# ---------------------------------------------------------------------------
# EvaluationResult 数据类测试
# ---------------------------------------------------------------------------


class TestEvaluationResult:
    def test_basic_fields(self):
        r = EvaluationResult(
            task_dir="/tmp/test",
            runs=[],
            avg_token_cost=500.0,
            pass_rate=1.0,
            total_tokens=1000,
            num_runs=2,
            num_passed=2,
        )
        assert r.task_dir == "/tmp/test"
        assert r.avg_token_cost == 500.0
        assert r.pass_rate == 1.0
        assert r.total_tokens == 1000
        assert r.num_runs == 2
        assert r.num_passed == 2

    def test_zero_runs(self):
        r = EvaluationResult(
            task_dir="/tmp/test",
            runs=[],
            avg_token_cost=0,
            pass_rate=0,
            total_tokens=0,
            num_runs=0,
            num_passed=0,
        )
        assert r.num_runs == 0
        assert r.avg_token_cost == 0

    def test_partial_pass(self):
        r = EvaluationResult(
            task_dir="/tmp/test",
            runs=[],
            avg_token_cost=300.0,
            pass_rate=0.5,
            total_tokens=600,
            num_runs=2,
            num_passed=1,
        )
        assert r.pass_rate == 0.5
        assert r.num_passed == 1


# ---------------------------------------------------------------------------
# evaluate 测试
# ---------------------------------------------------------------------------


class TestEvaluate:
    def test_evaluate_with_echo(self):
        """使用 echo 模拟评估。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "task.md"), "w") as f:
                f.write("test task")

            result = evaluate(tmpdir, agent_cmd="echo", timeout=10, num_runs=2)
            assert result.num_runs == 2
            assert len(result.runs) == 2

    def test_evaluate_single_run(self):
        """单次运行评估。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "task.md"), "w") as f:
                f.write("test task")

            result = evaluate(tmpdir, agent_cmd="echo", timeout=10, num_runs=1)
            assert result.num_runs == 1
            assert len(result.runs) == 1
            assert result.task_dir == tmpdir

    def test_evaluate_pass_rate_all_pass(self):
        """echo 总是返回 0，所以全部通过。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "task.md"), "w") as f:
                f.write("test task")

            result = evaluate(tmpdir, agent_cmd="echo", timeout=10, num_runs=3)
            assert result.pass_rate == 1.0
            assert result.num_passed == 3

    def test_evaluate_with_token_info(self):
        """评估结果应包含从输出解析的 token 数据。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "task.md"), "w") as f:
                f.write("Total Tokens: 500")

            result = evaluate(tmpdir, agent_cmd="echo", timeout=10, num_runs=2)
            assert result.total_tokens == 1000
            assert result.avg_token_cost == 500.0

    def test_evaluate_pass_rate_with_failures(self):
        """使用 false 命令模拟全部失败。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "task.md"), "w") as f:
                f.write("test")

            result = evaluate(tmpdir, agent_cmd="false", timeout=10, num_runs=2)
            assert result.pass_rate == 0.0
            assert result.num_passed == 0

    def test_evaluate_token_limit(self):
        """超出 token limit 的运行应被标记为失败。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "task.md"), "w") as f:
                f.write("Total Tokens: 1000")

            result = evaluate(
                tmpdir,
                agent_cmd="echo",
                timeout=10,
                token_limit=500,
                num_runs=1,
            )
            assert result.pass_rate == 0.0
            assert result.runs[0].token_limit_exceeded is True


# ---------------------------------------------------------------------------
# main 函数测试
# ---------------------------------------------------------------------------


class TestMain:
    def test_main_missing_task_dir(self):
        """不存在的任务目录应导致退出。"""
        with pytest.raises(SystemExit):
            sys.argv = ["harness.py", "--task-dir", "/nonexistent/path/xyz"]
            main()

    def test_main_missing_task_md(self):
        """任务目录存在但缺少 task.md 应导致退出。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(SystemExit):
                sys.argv = ["harness.py", "--task-dir", tmpdir]
                main()

    def test_main_with_echo(self, capsys):
        """使用 echo 模拟完整的 main 执行。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "task.md"), "w") as f:
                f.write("Total Tokens: 300")

            sys.argv = ["harness.py", "--task-dir", tmpdir, "--agent", "echo", "--timeout", "10"]
            main()
            captured = capsys.readouterr()
            assert "avg_token_cost:" in captured.out
            assert "pass_rate:" in captured.out
            assert "300" in captured.out

    def test_main_env_vars(self, capsys, monkeypatch):
        """环境变量配置应被正确读取。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "task.md"), "w") as f:
                f.write("test")

            monkeypatch.setenv("HARNESS_AGENT", "echo")
            monkeypatch.setenv("HARNESS_TIMEOUT", "10")
            monkeypatch.setenv("HARNESS_TOKEN_LIMIT", "5000")

            sys.argv = ["harness.py", "--task-dir", tmpdir]
            main()
            captured = capsys.readouterr()
            assert "token_limit:        5000" in captured.out
            assert "agent:              echo" in captured.out


# ---------------------------------------------------------------------------
# 示例目录完整性测试
# ---------------------------------------------------------------------------


class TestExampleDirectory:
    def _example_dir(self):
        return os.path.join(os.path.dirname(__file__), "..", "example")

    def test_example_task_exists(self):
        example_dir = self._example_dir()
        if not os.path.isdir(example_dir):
            pytest.skip("example 目录不存在")
        assert os.path.isfile(os.path.join(example_dir, "task.md"))

    def test_example_task_loadable(self):
        example_dir = self._example_dir()
        if not os.path.isdir(example_dir):
            pytest.skip("example 目录不存在")
        content = load_task(example_dir)
        assert len(content) > 0

    def test_example_skills_dir_exists(self):
        """示例目录应包含 .agents/skills/ 目录。"""
        example_dir = self._example_dir()
        if not os.path.isdir(example_dir):
            pytest.skip("example 目录不存在")
        skills_dir = os.path.join(example_dir, ".agents", "skills")
        assert os.path.isdir(skills_dir)

    def test_example_has_skill_files(self):
        """示例目录的 .agents/skills/ 下应包含 .md 文件。"""
        example_dir = self._example_dir()
        if not os.path.isdir(example_dir):
            pytest.skip("example 目录不存在")
        skills_dir = os.path.join(example_dir, ".agents", "skills")
        md_files = []
        for dirpath, _, filenames in os.walk(skills_dir):
            for f in filenames:
                if f.endswith(".md"):
                    md_files.append(os.path.join(dirpath, f))
        assert len(md_files) >= 1, "示例目录应包含至少一个 Skill 文件"

    def test_example_has_nested_skills(self):
        """示例目录应包含多层级 Skill 文件。"""
        example_dir = self._example_dir()
        if not os.path.isdir(example_dir):
            pytest.skip("example 目录不存在")
        skills_dir = os.path.join(example_dir, ".agents", "skills")
        has_nested = False
        for dirpath, _, filenames in os.walk(skills_dir):
            if dirpath != skills_dir:
                for f in filenames:
                    if f.endswith(".md"):
                        has_nested = True
                        break
        assert has_nested, "示例目录应包含子目录中的 Skill 文件"


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

    def test_pass_threshold_range(self):
        """PASS_THRESHOLD 应在 0 到 1 之间。"""
        assert 0 <= PASS_THRESHOLD <= 1

    def test_default_timeout_positive(self):
        """默认超时应为正数。"""
        assert DEFAULT_TIMEOUT > 0


# ---------------------------------------------------------------------------
# 集成测试：完整流程
# ---------------------------------------------------------------------------


class TestIntegration:
    def test_full_evaluation_flow(self):
        """完整评估流程：创建任务目录 → 评估 → 检查结果。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建任务目录结构
            task_md = os.path.join(tmpdir, "task.md")
            skills_dir = os.path.join(tmpdir, ".agents", "skills")
            os.makedirs(skills_dir)

            with open(task_md, "w") as f:
                f.write("# 测试任务\nTotal Tokens: 250")
            with open(os.path.join(skills_dir, "test.md"), "w") as f:
                f.write("# 测试 Skill")

            result = evaluate(tmpdir, agent_cmd="echo", timeout=10, num_runs=3)

            assert result.task_dir == tmpdir
            assert result.num_runs == 3
            assert result.num_passed == 3
            assert result.pass_rate == 1.0
            assert result.total_tokens == 750
            assert result.avg_token_cost == 250.0

    def test_cli_execution(self):
        """通过命令行执行 harness.py。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "task.md"), "w") as f:
                f.write("Total Tokens: 100")

            harness_path = os.path.join(os.path.dirname(__file__), "..", "harness.py")
            proc = subprocess.run(
                [sys.executable, harness_path,
                 "--task-dir", tmpdir, "--agent", "echo", "--timeout", "10"],
                capture_output=True, text=True, timeout=30,
            )
            assert proc.returncode == 0
            assert "avg_token_cost:" in proc.stdout
            assert "pass_rate:" in proc.stdout
