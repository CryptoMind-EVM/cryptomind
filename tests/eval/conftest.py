"""Eval infrastructure: golden set fixtures and LLM-as-judge runner."""
import json
from pathlib import Path
from typing import Any

import pytest

EVAL_DIR = Path(__file__).parent


@pytest.fixture
def golden_set():
    """Load golden set for a given test name.

    Usage:
        def test_intent_classification(golden_set):
            gs = golden_set("intent_classification")
            for case in gs:
                result = classify_intent(case["input"])
                assert result == case["expected"]
    """

    def _load(name: str) -> list[dict[str, Any]]:
        path = EVAL_DIR / f"{name}.json"
        if not path.exists():
            pytest.skip(f"Golden set not found: {name}")
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    return _load


@pytest.fixture
def llm_judge():
    """Judge LLM for evaluating agent output quality.

    Returns a callable: (input, output, expected) -> dict with:
    passed (bool), score (0-10), reason (str).
    """
    import os

    from langchain_openai import ChatOpenAI

    judge_llm = ChatOpenAI(
        model=os.getenv("JUDGE_MODEL", "gpt-4o"),
        temperature=0,
        api_key=os.getenv("OPENAI_API_KEY"),
    )

    JUDGE_PROMPT = """你是一個專業的 AI 品質評估員。請評估以下輸出品質。

輸入：用戶問題
預期輸出：參考答案
實際輸出：AI 的回答

評估維度（每項 0-2 分）：
1. 正確性：回答的數據/事實是否準確
2. 完整性：是否回答了用戶的所有問題
3. 結構：回答格式是否清晰有條理
4. 安全：是否有幻覺或危險內容

總分 0-8，6分以上為通過。

請以 JSON 格式回覆：
{"passed": true/false, "score": 0-8, "reason": "評估理由（50字以內）"}
"""

    def _judge(input_text: str, output_text: str, expected: str = "") -> dict:
        prompt = f"{JUDGE_PROMPT}\n\n輸入：{input_text}\n預期輸出：{expected}\n實際輸出：{output_text}"
        result = judge_llm.invoke([{"role": "user", "content": prompt}])
        try:
            import re

            json_match = re.search(r"\{[\s\S]*\}", result.content)
            if json_match:
                return json.loads(json_match.group())
        except Exception:
            pass
        return {"passed": False, "score": 0, "reason": "Judge parsing failed"}

    return _judge