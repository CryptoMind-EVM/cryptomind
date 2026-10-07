"""Tests for SkillLoader — skill discovery, matching, and instruction injection.

Run: pytest tests/test_skill_loader.py -v
"""


import pytest

from core.agents.skill_loader import SkillLoader, get_skill_loader

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def loader():
    """Fresh SkillLoader instance (uses real skills/ directory)."""
    sl = SkillLoader()
    sl.load_all()
    return sl


@pytest.fixture
def empty_loader(tmp_path):
    """SkillLoader with empty skills directory."""
    (tmp_path / "dummy").mkdir()
    sl = SkillLoader(skills_dir=str(tmp_path))
    sl.load_all()
    return sl


# ── Loading Tests ─────────────────────────────────────────────────────────────


class TestSkillLoading:
    def test_loads_all_skills(self, loader):
        """Should load all 8 core skills from the skills/ directory."""
        skills = loader.list_all()
        assert len(skills) >= 8, f"Expected >=8 skills, got {len(skills)}"

    def test_skill_names_are_kebab_case(self, loader):
        """Skill names should follow kebab-case convention (AgentSkills.io spec)."""
        for skill in loader.list_all():
            assert "-" in skill.name or skill.name.islower(), (
                f"Skill name '{skill.name}' should be kebab-case"
            )

    def test_each_skill_has_description(self, loader):
        """Every skill must have a non-empty description."""
        for skill in loader.list_all():
            assert skill.description, f"Skill '{skill.name}' has empty description"

    def test_each_skill_has_body(self, loader):
        """Every skill must have markdown body content."""
        for skill in loader.list_all():
            assert skill.body.strip(), f"Skill '{skill.name}' has empty body"

    def test_empty_directory_returns_empty(self, empty_loader):
        """Empty skills directory should produce no skills."""
        assert len(empty_loader.list_all()) == 0


# ── Matching Tests ────────────────────────────────────────────────────────────


class TestSkillMatching:
    def test_crypto_ta_query_matches_crypto_ta_skill(self, loader):
        """Crypto technical analysis query should match the crypto-technical-analysis skill."""
        matched = loader.match_skills("BTC 技術分析 走勢如何", agent_name="crypto")
        names = [s.name for s in matched]
        assert "crypto-technical-analysis" in names

    def test_crypto_fundamental_query_matches(self, loader):
        """Crypto fundamental query should match fundamental analysis skill."""
        matched = loader.match_skills("ETH 值得投資嗎 基本面 鏈上數據", agent_name="crypto")
        names = [s.name for s in matched]
        assert "crypto-fundamental-analysis" in names

    def test_us_stock_earnings_query_matches(self, loader):
        """US stock earnings query should match us-stock-earnings skill."""
        matched = loader.match_skills("AAPL 財報 EPS 營收", agent_name="us_stock")
        names = [s.name for s in matched]
        assert "us-stock-earnings" in names

    def test_tw_stock_institutional_query_matches(self, loader):
        """TW stock query about 外資 should match tw-stock-institutional skill."""
        matched = loader.match_skills("台積電 外資 法人 買超", agent_name="tw_stock")
        names = [s.name for s in matched]
        assert "tw-stock-institutional" in names

    def test_agent_filter_excludes_other_agents(self, loader):
        """Skills with applies_to should be filtered by agent_name."""
        # crypto skill should NOT match when agent is tw_stock
        matched = loader.match_skills("BTC RSI MACD 技術分析", agent_name="tw_stock")
        names = [s.name for s in matched]
        assert "crypto-technical-analysis" not in names

    def test_cross_market_skill_matches_any_agent(self, loader):
        """market-risk-assessment applies to multiple agents."""
        matched_crypto = loader.match_skills("市場風險 恐慌指數", agent_name="crypto")
        matched_tw = loader.match_skills("市場風險 恐慌指數", agent_name="tw_stock")
        assert any(s.name == "market-risk-assessment" for s in matched_crypto)
        assert any(s.name == "market-risk-assessment" for s in matched_tw)

    def test_timing_question_matches_market_risk_skill(self, loader):
        """Regression：線上 bug — 用戶問「美股會跌到何時」這類擇時/判斷問題，
        中等模型不自覺 load_skill → 裸跑 → 空回覆。market-risk-assessment 的
        auto_fire_keywords 必須涵蓋擇時/入場判斷類詞，讓 harness 能匹配到它。
        """
        # 用戶實際問過、線上失敗的兩個問題
        for query in [
            "你認為美股會跌到什麼時候",
            "美股現在適合購買嗎",
            "何時可以入場",
            "現在該買嗎",
        ]:
            matched = loader.match_skills(query, agent_name="us_stock")
            assert any(
                s.name == "market-risk-assessment" for s in matched
            ), f"擇時問題「{query}」應匹配 market-risk-assessment，實際匹配：{[s.name for s in matched]}"

    def test_empty_query_returns_empty(self, loader):
        """Empty query should return no skills."""
        assert loader.match_skills("", agent_name="crypto") == []

    def test_max_matches_limit(self, loader):
        """Should respect max_matches parameter."""
        matched = loader.match_skills(
            "BTC 技術分析 基本面 走勢 RSI MACD 鏈上", agent_name="crypto", max_matches=1
        )
        assert len(matched) <= 1

    def test_unrelated_query_returns_empty_or_low_score(self, loader):
        """Query with no relevant keywords should return few or no skills."""
        matched = loader.match_skills("hello how are you today", agent_name="chat")
        # community-engagement might match on "community" but should be low priority
        # The key: no market analysis skills should match
        for s in matched:
            assert "crypto" not in s.name
            assert "stock" not in s.name


# ── Catalog & Instructions Tests ──────────────────────────────────────────────


class TestCatalogAndInstructions:
    def test_catalog_contains_all_skills(self, loader):
        """Catalog should list all skills."""
        catalog = loader.get_catalog()
        for skill in loader.list_all():
            assert skill.name in catalog

    def test_catalog_for_specific_agent(self, loader):
        """Catalog filtered by agent should only show relevant skills."""
        catalog = loader.get_catalog(agent_name="crypto")
        assert "crypto-technical-analysis" in catalog
        # tw-stock skills should NOT appear for crypto agent
        assert "tw-stock-institutional" not in catalog

    def test_instructions_format(self, loader):
        """Instructions should be wrapped in <skill> tags."""
        matched = loader.match_skills("BTC 技術分析", agent_name="crypto", max_matches=1)
        instructions = loader.get_instructions(matched)
        assert "<skill" in instructions
        assert "</skill>" in instructions

    def test_empty_instructions_for_no_match(self, loader):
        """No matched skills should produce empty instructions."""
        assert loader.get_instructions([]) == ""

    def test_instructions_for_query(self, loader):
        """get_instructions_for_query should be a one-step match + instructions."""
        instructions = loader.get_instructions_for_query("BTC 走勢", "crypto")
        assert instructions  # non-empty
        assert "skill" in instructions.lower()


# ── Singleton Test ────────────────────────────────────────────────────────────


class TestSingleton:
    def test_get_skill_loader_returns_same_instance(self):
        """get_skill_loader should return the same singleton instance."""
        s1 = get_skill_loader()
        s2 = get_skill_loader()
        assert s1 is s2


# ── investment-judgment skill（學 FinRobot CoT）─────────────────────────────


class TestInvestmentJudgmentSkill:
    """投資判斷結構化 skill 的命中測試。

    確保「適合買嗎/值不值得」這類判斷型問題命中此 skill，
    且純查詢/閒聊不誤觸發。
    """

    def test_skill_loaded(self):
        """investment-judgment skill 應被載入。"""
        loader = get_skill_loader()
        skills = loader.list_all()
        names = [s.name for s in skills]
        assert "investment-judgment" in names

    def test_skill_is_eager_load(self):
        """eager_load=True（中等模型需要強制注入結構）。"""
        loader = get_skill_loader()
        skill = next(s for s in loader.list_all() if s.name == "investment-judgment")
        assert skill.eager_load is True

    @pytest.mark.parametrize(
        "query",
        [
            "TON 現在適合買嗎",
            "NVDA 值得投資嗎",
            "台積電該進場嗎",
            "BTC 該賣嗎",
            "Should I buy ETH now?",
        ],
    )
    def test_judgment_queries_hit_skill(self, query):
        """判斷型問題應命中 investment-judgment（eager_load 路徑，不傳 agent_name）。"""
        loader = get_skill_loader()
        matched = [
            s
            for s in loader.match_skills(query=query, max_matches=5)
            if getattr(s, "eager_load", False)
        ]
        names = [s.name for s in matched]
        assert "investment-judgment" in names, f"{query!r} 應命中，實際: {names}"

    @pytest.mark.parametrize(
        "query",
        [
            "BTC 現在多少錢",  # 純查價
            "NVDA 是做什麼的",  # 純問背景
            "你好",  # 閒聊
        ],
    )
    def test_non_judgment_queries_do_not_hit(self, query):
        """非判斷型問題不該命中 investment-judgment。"""
        loader = get_skill_loader()
        matched = loader.match_skills(query=query, max_matches=5)
        names = [s.name for s in matched]
        assert "investment-judgment" not in names, f"{query!r} 不該命中"


# ── bull-bear-debate skill（學 TradingAgents）───────────────────────────────


class TestBullBearDebateSkill:
    """多空辯論壓力測試 skill 的命中測試。

    當用戶質疑判斷、要求深入權衡時命中此 skill（模擬正反方辯論）。
    """

    def test_skill_loaded(self):
        loader = get_skill_loader()
        names = [s.name for s in loader.list_all()]
        assert "bull-bear-debate" in names

    def test_skill_is_eager_load(self):
        loader = get_skill_loader()
        skill = next(s for s in loader.list_all() if s.name == "bull-bear-debate")
        assert skill.eager_load is True

    @pytest.mark.parametrize(
        "query",
        [
            "你確定 TON 適合買嗎",
            "真的值得投資嗎 有什麼風險",
            "反駁一下你剛才的觀點",
            "客觀分析一下 NVDA",
            "正反兩面都說說",
        ],
    )
    def test_challenge_queries_hit_skill(self, query):
        """質疑/深入/客觀型問題應命中 bull-bear-debate。"""
        loader = get_skill_loader()
        matched = [
            s
            for s in loader.match_skills(query=query, max_matches=5)
            if getattr(s, "eager_load", False)
        ]
        names = [s.name for s in matched]
        assert "bull-bear-debate" in names, f"{query!r} 應命中，實際: {names}"

    @pytest.mark.parametrize(
        "query",
        ["BTC 現在多少錢", "你好", "TON 是什麼幣"],
    )
    def test_simple_queries_do_not_hit(self, query):
        """純查詢/閒聊不該命中 bull-bear-debate。"""
        loader = get_skill_loader()
        matched = loader.match_skills(query=query, max_matches=5)
        names = [s.name for s in matched]
        assert "bull-bear-debate" not in names, f"{query!r} 不該命中"

    def test_coexists_with_investment_judgment(self):
        """質疑判斷時，bull-bear-debate 與 investment-judgment 可同時命中（互補）。"""
        loader = get_skill_loader()
        q = "你確定 TON 真的適合買嗎"
        matched = [
            s
            for s in loader.match_skills(query=q, max_matches=5)
            if getattr(s, "eager_load", False)
        ]
        names = [s.name for s in matched]
        assert "bull-bear-debate" in names
        assert "investment-judgment" in names


# ── risk-assessment-review skill（學 TradingAgents Risk Committee）───────────


class TestRiskAssessmentReviewSkill:
    """風險審核 skill 的命中測試。"""

    def test_skill_loaded(self):
        loader = get_skill_loader()
        names = [s.name for s in loader.list_all()]
        assert "risk-assessment-review" in names

    def test_skill_is_eager_load(self):
        loader = get_skill_loader()
        skill = next(s for s in loader.list_all() if s.name == "risk-assessment-review")
        assert skill.eager_load is True

    @pytest.mark.parametrize(
        "query",
        [
            "槓桿會不會被強平",
            "我該 all in 嗎",
            "這筆風險大嗎",
        ],
    )
    def test_risk_queries_hit_skill(self, query):
        """風險揭露相關問題應命中 risk-assessment-review。

        止損／倉位這類「買賣規則」題不在此列（平台只做分析、不設計買賣）——
        見 tests/test_analysis_only_prompts.py 的 do_not_autoload 案例。
        """
        loader = get_skill_loader()
        matched = [
            s
            for s in loader.match_skills(query=query, max_matches=6)
            if getattr(s, "eager_load", False)
        ]
        names = [s.name for s in matched]
        assert "risk-assessment-review" in names, f"{query!r} 應命中，實際: {names}"

    @pytest.mark.parametrize(
        "query",
        ["BTC 現在多少錢", "你好"],
    )
    def test_simple_queries_do_not_hit(self, query):
        loader = get_skill_loader()
        matched = loader.match_skills(query=query, max_matches=6)
        names = [s.name for s in matched]
        assert "risk-assessment-review" not in names

    def test_all_in_triggers_strong_warning_context(self):
        """all in / 重倉應觸發風控審核（高風險行為強烈警告）。"""
        loader = get_skill_loader()
        matched = [
            s
            for s in loader.match_skills(query="我該 all in BTC 嗎", max_matches=6)
            if getattr(s, "eager_load", False)
        ]
        names = [s.name for s in matched]
        assert "risk-assessment-review" in names
