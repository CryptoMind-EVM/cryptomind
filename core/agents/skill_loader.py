"""
Agent V4 — Skill Loader

從 core/agents/skills/*/SKILL.md 載入分析方法知識（skills），
讓 LLM 不只選對 agent，還帶著正確的分析步驟去執行。

設計基於 AgentSkills.io 規範 + progressive disclosure 模式：
1. Catalog（~100 tokens/skill）: name + description → 注入 Manager prompt
2. Instructions（<2000 tokens）: 完整 SKILL.md body → skill 被匹配時載入
3. Resources: 額外參考檔 → 按需載入（本版暫不實作 resource 層）

與 DescriptionLoader 的差異：
- DescriptionLoader 告訴 LLM「WHEN to use this agent」（路由用）
- SkillLoader 告訴 LLM「HOW to approach this problem」（方法用）
- 兩者互補：description 選 agent，skill 選方法
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)


@dataclass
class Skill:
    """一個分析方法 skill 的資料結構。"""

    # Frontmatter（輕量級，用於 catalog 和匹配）
    name: str
    description: str
    applies_to: List[str] = field(default_factory=list)
    priority: int = 10
    auto_fire_keywords: List[str] = field(default_factory=list)
    recommended_tools: List[str] = field(default_factory=list)
    # eager_load（學 Hermes issue #14405）：標記為 True 的 skill，在 match_skills
    # 命中時直接注入完整 body（不只目錄）。用在「description 不足以讓中等模型
    # 自覺 load_skill」的 skill——例如 market-risk-assessment 的目錄描述
    # （市場風險/大盤/恐慌）無法讓模型聯想到擇時問題（會跌到何時/適合買嗎）。
    eager_load: bool = False

    raw_content: str = ""
    body: str = ""

    @property
    def catalog_entry(self) -> str:
        """生成 catalog 條目（輕量，~100 tokens）。"""
        agents = ", ".join(self.applies_to) if self.applies_to else "all"
        return f"- **{self.name}**: {self.description} (applies_to: {agents})"


class SkillLoader:
    """
    Skill 載入器，使用方式和 AgentDescriptionLoader 一致：

        loader = SkillLoader()
        loader.load_all()

        # 取得 catalog（注入 Manager prompt）
        catalog = loader.get_catalog()

        # 根據 query + agent 匹配 skills
        matched = loader.match_skills(query="BTC 技術分析", agent_name="crypto")

        # 取得完整 skill 指令（注入 agent prompt）
        instructions = loader.get_instructions(matched)
    """

    def __init__(self, skills_dir: Optional[str] = None):
        if skills_dir is None:
            skills_dir = Path(__file__).parent / "skills"
        self.skills_dir = Path(skills_dir)
        self._cache: Dict[str, Skill] = {}
        self._loaded: bool = False

    def load_all(self) -> Dict[str, Skill]:
        """載入所有 skill（掃描 skills/*/SKILL.md）。"""
        if self._loaded:
            return self._cache

        if not self.skills_dir.exists():
            logger.debug(f"[SkillLoader] Skills dir not found: {self.skills_dir}")
            self._loaded = True
            return {}

        for skill_dir in self.skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists():
                continue
            try:
                skill = self._parse_skill(skill_file)
                if skill:
                    self._cache[skill.name] = skill
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                logger.warning(
                    f"[SkillLoader] Failed to parse {skill_file}: {e}"
                )

        self._loaded = True
        logger.info(
            f"[SkillLoader] Loaded {len(self._cache)} skills: "
            f"{list(self._cache.keys())}"
        )
        return self._cache

    def _parse_skill(self, file_path: Path) -> Optional[Skill]:
        """解析 SKILL.md（YAML frontmatter + markdown body）。"""
        content = file_path.read_text(encoding="utf-8")

        frontmatter = self._extract_frontmatter(content)
        if not frontmatter:
            return None

        body = self._extract_body(content)

        applies_to_raw = frontmatter.get("applies_to", [])
        if not isinstance(applies_to_raw, list):
            applies_to_raw = [applies_to_raw] if applies_to_raw else []

        keywords_raw = frontmatter.get("auto_fire_keywords", [])
        if not isinstance(keywords_raw, list):
            keywords_raw = [keywords_raw] if keywords_raw else []

        tools_raw = frontmatter.get("recommended_tools", [])
        if not isinstance(tools_raw, list):
            tools_raw = [tools_raw] if tools_raw else []

        return Skill(
            name=frontmatter.get("name", file_path.parent.name),
            description=frontmatter.get("description", ""),
            applies_to=[str(a) for a in applies_to_raw],
            priority=int(frontmatter.get("priority", 10)),
            auto_fire_keywords=[str(k).lower().strip() for k in keywords_raw],
            recommended_tools=[str(t).strip() for t in tools_raw],
            eager_load=bool(frontmatter.get("eager_load", False)),
            raw_content=content,
            body=body,
        )

    def _extract_frontmatter(self, content: str) -> Optional[dict]:
        pattern = r"^---\s*\n(.*?)\n---\s*\n"
        match = re.match(pattern, content, re.DOTALL)
        if not match:
            return None
        try:
            return yaml.safe_load(match.group(1))
        except yaml.YAMLError:
            return None

    def _extract_body(self, content: str) -> str:
        """移除 frontmatter，返回純 markdown body。"""
        return re.sub(r"^---\s*\n.*?\n---\s*\n", "", content, flags=re.DOTALL)

    # ── Catalog（Tier 1: 輕量級，注入 Manager prompt）──────────────────────

    def get_catalog(self, agent_name: Optional[str] = None) -> str:
        """
        取得 skill catalog 字串（~100 tokens/skill）。

        Args:
            agent_name: 如果提供，只返回適用於該 agent 的 skills。
        """
        if not self._loaded:
            self.load_all()

        lines: List[str] = []
        for skill in sorted(self._cache.values(), key=lambda s: s.priority):
            if agent_name and skill.applies_to and agent_name not in skill.applies_to:
                continue
            lines.append(skill.catalog_entry)

        if not lines:
            return ""

        header = "## Available Analysis Skills\n"
        footer = (
            "\n> Skills 提供分析方法和輸出規範。"
            "在規劃任務時，如果查詢匹配某個 skill，"
            "在 task description 中加入 [skill:skill-name] 標記。"
        )
        return header + "\n".join(lines) + footer

    # ── Matching（根據 query + agent 匹配 skills）──────────────────────────

    def match_skills(
        self,
        query: str,
        agent_name: Optional[str] = None,
        max_matches: int = 3,
    ) -> List[Skill]:
        """
        根據用戶查詢和目標 agent 匹配最相關的 skills。

        匹配邏輯：
        1. 先篩 applies_to（如果指定 agent_name）
        2. auto_fire_keywords 關鍵詞匹配（加權計分）
        3. description 關鍵詞匹配（加權計分）
        4. 取分數最高的 max_matches 個

        Args:
            query: 用戶的原始查詢
            agent_name: 目標 agent 名稱（用於篩選 applies_to）
            max_matches: 最多返回幾個 skills
        """
        if not self._loaded:
            self.load_all()

        if not self._cache or not query:
            return []

        query_lower = query.lower()
        scored: List[tuple[int, Skill]] = []

        for skill in self._cache.values():
            # 篩 applies_to
            if (
                agent_name
                and skill.applies_to
                and agent_name not in skill.applies_to
            ):
                continue

            score = 0

            # auto_fire_keywords 匹配（高權重）
            for kw in skill.auto_fire_keywords:
                if _match_token(kw, query_lower):
                    score += 10

            # description 關鍵詞匹配（中權重）
            desc_lower = skill.description.lower()
            # 從 description 抽取有意義的詞（>2 chars）
            desc_words = re.findall(r"[\w\u4e00-\u9fff]{2,}", desc_lower)
            for word in desc_words:
                if len(word) >= 2 and _match_token(word, query_lower):
                    score += 2

            # priority 只當 tiebreaker：必須先有關鍵詞/描述命中才加分。
            # 若無條件加分，取消 applies_to 過濾（CLAW 全能 agent）後
            # 任何問題都會硬塞 3 個不相關 skill 進 prompt。
            if score > 0:
                score += max(0, 20 - skill.priority)
                scored.append((score, skill))

        # 排序取 top N
        scored.sort(key=lambda x: -x[0])
        return [skill for _, skill in scored[:max_matches]]

    # ── Instructions（Tier 2: 完整指令，注入 agent prompt）─────────────────

    def get_instructions(self, skills: List[Skill]) -> str:
        """
        將匹配到的 skills 格式化為可注入 agent system prompt 的字串。

        每個 skill 的 body 都被包裝在 <skill> 標籤中，
        讓 LLM 明確知道這是方法指引。
        """
        if not skills:
            return ""

        sections: List[str] = [
            "\n\n## 🔬 Analysis Skills（分析方法指引）\n"
            "以下是適用於本次查詢的分析方法。請遵循這些方法的步驟和輸出規範。\n"
        ]

        for skill in skills:
            sections.append(f"<skill name=\"{skill.name}\">")
            sections.append(skill.body.strip())
            sections.append("</skill>\n")

        return "\n".join(sections)

    def get_instructions_for_query(
        self,
        query: str,
        agent_name: Optional[str] = None,
        max_matches: int = 3,
    ) -> str:
        """一步到位：query → match → instructions。"""
        matched = self.match_skills(query, agent_name, max_matches)
        return self.get_instructions(matched)

    def get_skill(self, name: str) -> Optional[Skill]:
        """取得特定 skill。"""
        if not self._loaded:
            self.load_all()
        return self._cache.get(name)

    def list_all(self) -> List[Skill]:
        """列出所有 skills。"""
        if not self._loaded:
            self.load_all()
        return sorted(self._cache.values(), key=lambda s: s.priority)


# ── 全域單例（與 DescriptionLoader 同模式）──────────────────────────────────

_loader_instance: Optional[SkillLoader] = None


def _match_token(token: str, query_lower: str) -> bool:
    """查詢是否命中 skill 的關鍵詞（description 詞 / auto_fire_keyword）。

    ASCII 詞用字母/數字邊界匹配：避免「to」誤中「ton」「risk」誤中「brisk」這類
    子字串假陽性（線上案例：『TON 是什麼幣』被 bull-bear-debate 的 description
    詞 "to" 命中）。CJK 詞維持子字串匹配（中文沒有詞界）。
    """
    if not token:
        return False
    if token.isascii() and token.isalnum():
        return (
            re.search(
                rf"(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])", query_lower
            )
            is not None
        )
    return token in query_lower


def get_skill_loader() -> SkillLoader:
    """取得全域 SkillLoader 實例。"""
    global _loader_instance
    if _loader_instance is None:
        _loader_instance = SkillLoader()
        _loader_instance.load_all()
    return _loader_instance
