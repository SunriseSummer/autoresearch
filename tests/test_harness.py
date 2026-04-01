"""harness.py 的单元测试。"""

import json
import os
import sys
import tempfile

# 将项目根目录加入 sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from harness import (
    Task,
    ExecutionResult,
    evaluate_quality,
    load_tasks,
    PASS_THRESHOLD,
)


# ---------------------------------------------------------------------------
# evaluate_quality 测试
# ---------------------------------------------------------------------------


class TestEvaluateQualityExactMatch:
    def test_exact_match_pass(self):
        task = Task("t1", "q", "hello world", "exact_match")
        assert evaluate_quality(task, "hello world") is True

    def test_exact_match_strip(self):
        task = Task("t1", "q", "hello", "exact_match")
        assert evaluate_quality(task, "  hello  ") is True

    def test_exact_match_fail(self):
        task = Task("t1", "q", "hello", "exact_match")
        assert evaluate_quality(task, "world") is False


class TestEvaluateQualityContains:
    def test_contains_single_keyword(self):
        task = Task("t1", "q", "Paris", "contains")
        assert evaluate_quality(task, "The capital is Paris.") is True

    def test_contains_case_insensitive(self):
        task = Task("t1", "q", "paris", "contains")
        assert evaluate_quality(task, "PARIS is the capital.") is True

    def test_contains_multiple_keywords(self):
        task = Task("t1", "q", ["Python", "Guido"], "contains")
        assert evaluate_quality(task, "Python was created by Guido.") is True

    def test_contains_missing_keyword(self):
        task = Task("t1", "q", ["Python", "Java"], "contains")
        assert evaluate_quality(task, "Python is great.") is False

    def test_contains_empty_output(self):
        task = Task("t1", "q", "hello", "contains")
        assert evaluate_quality(task, "") is False


class TestEvaluateQualityUnknown:
    def test_unknown_evaluator_falls_back_to_contains(self):
        task = Task("t1", "q", "hello", "unknown_eval")
        assert evaluate_quality(task, "hello world") is True


# ---------------------------------------------------------------------------
# load_tasks 测试
# ---------------------------------------------------------------------------


class TestLoadTasks:
    def test_load_tasks_from_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            task_data = {
                "task_id": "test_001",
                "input": "What is 1+1?",
                "expected": "2",
                "evaluator": "contains",
                "metadata": {"category": "math"},
            }
            with open(os.path.join(tmpdir, "task_001.json"), "w") as f:
                json.dump(task_data, f)

            tasks = load_tasks(tmpdir)
            assert len(tasks) == 1
            assert tasks[0].task_id == "test_001"
            assert tasks[0].evaluator == "contains"

    def test_load_tasks_sorted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            for i in [3, 1, 2]:
                data = {
                    "task_id": f"t{i}",
                    "input": f"q{i}",
                    "expected": f"a{i}",
                }
                with open(os.path.join(tmpdir, f"task_{i:03d}.json"), "w") as f:
                    json.dump(data, f)

            tasks = load_tasks(tmpdir)
            assert [t.task_id for t in tasks] == ["t1", "t2", "t3"]

    def test_load_tasks_missing_dir(self):
        """不存在的目录应触发 sys.exit。"""
        import pytest
        with pytest.raises(SystemExit):
            load_tasks("/nonexistent_dir_xyz")


# ---------------------------------------------------------------------------
# 任务文件完整性测试
# ---------------------------------------------------------------------------


class TestTaskFiles:
    def test_all_task_files_valid(self):
        """验证 tasks/ 目录下所有 JSON 文件结构正确。"""
        task_dir = os.path.join(os.path.dirname(__file__), "..", "tasks")
        if not os.path.isdir(task_dir):
            return  # 跳过（CI 环境可能无此目录）

        tasks = load_tasks(task_dir)
        assert len(tasks) == 20

        ids = [t.task_id for t in tasks]
        assert len(ids) == len(set(ids)), "task_id 必须唯一"

        for t in tasks:
            assert t.task_id, "task_id 不能为空"
            assert t.input, "input 不能为空"
            assert t.expected is not None, "expected 不能为 None"
            assert t.evaluator in ("exact_match", "contains", "llm_judge")


# ---------------------------------------------------------------------------
# ExecutionResult 数据类测试
# ---------------------------------------------------------------------------


class TestExecutionResult:
    def test_default_error_is_none(self):
        r = ExecutionResult("out", 10, 5, 15)
        assert r.error is None

    def test_with_error(self):
        r = ExecutionResult("", 0, 0, 0, error="timeout")
        assert r.error == "timeout"


# ---------------------------------------------------------------------------
# PASS_THRESHOLD 常量测试
# ---------------------------------------------------------------------------


class TestConstants:
    def test_pass_threshold(self):
        assert PASS_THRESHOLD == 0.9
