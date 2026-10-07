"""記憶檢索 P0 修正（2026-08-21，DANNY 與 OpenAI 討論 TencentDB Agent Memory
後的四項查證——全部對當前 code 屬實，本檔鎖住回歸）：

- P0-1：experience 檢索 task_family 精確相等 'chat' 會漏掉 crypto/tw_stock
  等寫入 family → retrieve_relevant 接受 None 跨 family；SQL 的 family
  條件僅在指定時存在
- P0-2：FTS 只算 ts_rank 不設命中條件 → rank=0 無關資料按日期混入 →
  experiences/knowledge 的 SQL 都要有 @@ 命中
- P0-3：read_facts 的 touch 全量 +1 → 改只 touch MAX_FACTS_IN_PROMPT 內
- P0-4：custom skills 全量注入 → 有觸發詞者目錄化、命中才載入正文
"""

from __future__ import annotations

import inspect

import pytest

pytestmark = pytest.mark.unit


class TestP0_1_TaskFamilyRouting:
    def test_retrieve_accepts_none_family(self):
        from core.database.experiences import ExperienceStore

        sig = inspect.signature(ExperienceStore.retrieve_relevant)
        params = {k: v for k, v in sig.parameters.items() if k != "self"}
        assert "task_family" in params

    def test_layer1_2_sql_family_clause_optional(self):
        from core.database.experiences import ExperienceStore

        src = inspect.getsource(ExperienceStore._layer1_2_query)
        assert "family_clause" in src, "family 條件必須可選（None=跨 family）"

    def test_claw_loop_passes_none(self):
        import core.agents.manager.claw_loop as m

        src = inspect.getsource(m)
        assert '"chat",  # task_family' not in src, "claw_loop 不應再精確傳 'chat'"

    def test_layer1_2_sql_has_fts_hit(self):
        from core.database.experiences import ExperienceStore

        src = inspect.getsource(ExperienceStore._layer1_2_query)
        assert "query_tsv @@ plainto_tsquery" in src, "P0-2：experiences FTS 必須有 @@ 命中"


class TestP0_2_KnowledgeFtsHit:
    def test_knowledge_sql_has_fts_hit(self):
        import core.database.knowledge as k

        src = inspect.getsource(k)
        assert "body_tsv @@ plainto_tsquery" in src, "P0-2：knowledge FTS 必須有 @@ 命中"


class TestP0_3_FactsTouchScope:
    def test_touch_limits_to_injected_keys(self):
        import core.database.memory as m

        src = inspect.getsource(m.MemoryStore.read_facts)
        assert "key = ANY(%s)" in src, "touch 必須限定在注入的 keys"
        assert "results[:MAX_FACTS_IN_PROMPT]" in src, "只 touch 預算內的 facts"


class TestP0_4_SkillProgressiveDisclosure:
    def _build(self):
        from core.agents.base_react_agent import _build_custom_skill_block

        return _build_custom_skill_block

    def test_triggered_skill_injects_body(self):
        build = self._build()
        skills = [
            {
                "skill_name": "台股加權分析法",
                "description": "加權指數的分析框架",
                "trigger_keywords": "台股,加權,大盤",
                "body": "第一步看外資買賣超…第二步看融資維持率…",
            }
        ]
        block = build(skills, query="台股大盤後市如何")
        assert "外資買賣超" in block, "命中觸發詞必須載入完整正文"

    def test_untriggered_skill_catalog_only(self):
        build = self._build()
        skills = [
            {
                "skill_name": "台股加權分析法",
                "description": "加權指數的分析框架",
                "trigger_keywords": "台股,加權,大盤",
                "body": "SECRET_METHOD_BODY",
            }
        ]
        block = build(skills, query="比特幣後市如何")
        assert "SECRET_METHOD_BODY" not in block, "未命中不應注入正文"
        assert "台股加權分析法" in block, "目錄仍應列出（一行）"

    def test_no_keyword_skill_always_full(self):
        build = self._build()
        skills = [
            {
                "skill_name": "一律繁中回答",
                "description": "語言偏好",
                "trigger_keywords": "",
                "body": "所有回答使用繁體中文。",
            }
        ]
        block = build(skills, query="whatever query")
        assert "所有回答使用繁體中文。" in block, "無觸發詞＝一律適用偏好，維持全量（向後相容）"
