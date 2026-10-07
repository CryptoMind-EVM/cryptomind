"""Skill 的 Output Format 不可含有會被模型照抄的佔位符。

實際發生過的事故：使用者問台股大盤，agent 直接把 skill 範本原樣吐出來 ——
「加權指數：XX,XXX 點 (+/-.XX%)」「今日台股呈現[波動/上漲/下跌]格局」。

兩種寫法都危險，但危險的方式不同：
- `XX,XXX` 這種佔位符 → 明顯壞掉，使用者看得出來但體驗很差
- 範例數值（例如「外資賣超 9,637 張」）→ 被照抄時看起來像真數據，
  使用者完全無從辨識，比前者更嚴重

所以 Output Format 一律只描述欄位結構，不出現任何數字形狀的字串。
配套的執行期防線是 shared.yaml 的 no_placeholder_output 規則。
"""

from __future__ import annotations

import re
from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parents[1] / "core" / "agents" / "skills"

# X 當數字用：XX、X,XXX、XX.X、$XXX、±X、XX/100
PLACEHOLDER_DIGITS = re.compile(r"(?<![A-Za-z0-9])[±+\-$]?X[X,.\d]*(?![A-Za-z])")
# 方括號選項：[偏多/中性/偏空]、[上升/下降]
PLACEHOLDER_CHOICE = re.compile(r"\[[^\]\n]*/[^\]\n]*\]")


def _body_without_frontmatter(text: str) -> tuple[str, int]:
    """回傳 (正文, 正文起始行號)。frontmatter 的 keyword 清單本來就有斜線與括號。"""
    lines = text.split("\n")
    if lines and lines[0].strip() == "---":
        for idx in range(1, len(lines)):
            if lines[idx].strip() == "---":
                return "\n".join(lines[idx + 1 :]), idx + 2
    return text, 1


def _offending_lines(path: Path) -> list[str]:
    body, first_line = _body_without_frontmatter(path.read_text(encoding="utf-8"))
    hits = []
    for offset, line in enumerate(body.split("\n")):
        if PLACEHOLDER_DIGITS.search(line) or PLACEHOLDER_CHOICE.search(line):
            hits.append(f"  {path.name}:{first_line + offset}: {line.strip()[:80]}")
    return hits


def test_skills_have_no_output_placeholders():
    failures: list[str] = []

    for skill in sorted(SKILLS_DIR.rglob("SKILL.md")):
        hits = _offending_lines(skill)
        if hits:
            failures.append(f"{skill.parent.name} ({len(hits)} 行)")
            failures.extend(hits)

    assert not failures, (
        "以下 skill 的內容含有會被模型照抄的佔位符 —— 改成描述欄位結構，"
        "不要出現數字形狀的字串或 [A/B] 選項：\n" + "\n".join(failures)
    )
