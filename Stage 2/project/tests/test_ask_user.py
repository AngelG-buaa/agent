"""AskUserTool 单元测试 —— Agent 反问工具。"""

import pytest
from unittest.mock import patch

from tools.ask_user import AskUserTool


@pytest.fixture
def tool():
    return AskUserTool()


class TestAskUserToolSchema:
    """工具 schema 验证。"""

    def test_name(self, tool):
        assert tool.name == "ask_user"

    def test_parameters_contains_question(self, tool):
        params = tool.get_parameters()
        names = {p.name for p in params}
        assert "question" in names

    def test_question_is_required(self, tool):
        params = tool.get_parameters()
        question_param = next(p for p in params if p.name == "question")
        assert question_param.required is True

    def test_to_schema_produces_valid_openai_format(self, tool):
        schema = tool.to_schema()
        assert schema["type"] == "function"
        assert "ask_user" in schema["function"]["name"]
        assert "question" in schema["function"]["parameters"]["required"]


class TestAskUserToolRun:
    """run() 行为测试。"""

    def test_normal_answer(self, tool):
        """正常回答：返回 answer + is_valid=True。"""
        with patch("builtins.input", return_value="data.json"):
            result = tool.run({"question": "哪个文件？"})
        assert result["answer"] == "data.json"
        assert result["is_valid"] is True

    def test_empty_question(self, tool):
        """空问题参数返回 error。"""
        result = tool.run({"question": ""})
        assert "error" in result

    def test_missing_question(self, tool):
        """缺少 question 参数返回 error。"""
        result = tool.run({})
        assert "error" in result

    def test_empty_answer(self, tool):
        """用户输入空 → is_valid=False。"""
        with patch("builtins.input", return_value=""):
            result = tool.run({"question": "哪个文件？"})
        assert result["is_valid"] is False

    def test_whitespace_only_answer(self, tool):
        """用户只输入空格 → is_valid=False。"""
        with patch("builtins.input", return_value="   "):
            result = tool.run({"question": "哪个文件？"})
        assert result["is_valid"] is False

    def test_keyboard_interrupt(self, tool):
        """Ctrl+C 跳过 → 返回中断标记。"""
        with patch("builtins.input", side_effect=KeyboardInterrupt):
            result = tool.run({"question": "哪个文件？"})
        assert "用户未回答" in result.get("answer", "")
        assert result["is_valid"] is False

    def test_eof_interrupt(self, tool):
        """EOF 跳过 → 返回中断标记。"""
        with patch("builtins.input", side_effect=EOFError):
            result = tool.run({"question": "哪个文件？"})
        assert result["is_valid"] is False


class TestInvalidAnswerDetection:
    """无效回答检测。"""

    @pytest.mark.parametrize("answer", [
        "不知道",
        "不知道啊",
        "随便",
        "都行",
        "你自己决定",
        "你看着办",
        "无所谓",
        "都可以",
        "dont know",
        "I don't know",
        "whatever",
    ])
    def test_detects_invalid_answers(self, tool, answer):
        """各种变体的"不知道/随便"应被检测为无效。"""
        with patch("builtins.input", return_value=answer):
            result = tool.run({"question": "哪个文件？"})
        assert result["is_valid"] is False, f"'{answer}' 应为 is_valid=False"

    @pytest.mark.parametrize("answer", [
        "data.json",
        "用 JSON 格式",
        "分析 data.csv 这个文件",
        "第一个选项",
    ])
    def test_detects_valid_answers(self, tool, answer):
        """正常回答应被检测为有效。"""
        with patch("builtins.input", return_value=answer):
            result = tool.run({"question": "哪个文件？"})
        assert result["is_valid"] is True, f"'{answer}' 应为 is_valid=True"

    def test_case_insensitive(self, tool):
        """无效回答检测不区分大小写。"""
        with patch("builtins.input", return_value="WHATEVER"):
            result = tool.run({"question": "哪个文件？"})
        assert result["is_valid"] is False

    def test_partial_match(self, tool):
        """包含无效关键词即为无效（如"我觉得都行吧"）。"""
        with patch("builtins.input", return_value="我觉得都行吧"):
            result = tool.run({"question": "哪个文件？"})
        assert result["is_valid"] is False
