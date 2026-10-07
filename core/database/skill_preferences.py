"""
Skill Preferences Store — 使用者 skill 開關 + 自訂 skill 管理（per-user）。

兩個功能：
1. 官方 skill 開關（user_skill_overrides）— 關閉 = 不注入該 skill
2. 使用者自訂 skill（user_custom_skills）— 可新建/編輯/刪除

安全設計：
- 自訂 skill body 限制 ≤ 3000 字（防 context 爆炸）
- 自訂 skill 數量上限 10 個
- trigger_keywords 限制 ≤ 200 字
- skill_name 限制 ≤ 50 字、只允許 [a-zA-Z0-9_-]
- API 層做 HTML sanitize；後端注入時用 <user_skill> 標籤 + 隱形安全框架
"""
from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional

from core.database.base import DatabaseBase

logger = logging.getLogger(__name__)

# 安全限制
MAX_CUSTOM_BODY = 3000
MAX_CUSTOM_SKILLS = 10
MAX_TRIGGER_LEN = 200
MAX_NAME_LEN = 50
_NAME_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")
# 注入 system prompt 時用 <user_skill> 包起來；內容自帶這個標籤就能提前
# 「關掉」外框，後面的文字變成框外的系統指令
_USER_SKILL_TAG_RE = re.compile(r"<\s*/?\s*user_skill", re.IGNORECASE)


class SkillPreferenceStore:
    """Per-user skill 偏好存取（官方開關 + 自訂 CRUD）。"""

    def __init__(self, user_id: str):
        self.user_id = user_id

    # ── 官方 skill 開關 ──────────────────────────────────────────────────

    def get_disabled_skills(self) -> set[str]:
        """回傳使用者關閉的官方 skill 名稱集合。"""
        try:
            rows = DatabaseBase.query_all(
                "SELECT skill_name FROM user_skill_overrides "
                "WHERE user_id = %s AND is_enabled = FALSE",
                (self.user_id,),
            )
            return {r["skill_name"] for r in (rows or [])}
        except Exception as exc:  # noqa: BLE001 — 查不到不阻塞對話
            logger.debug("[SkillPref] get_disabled_skills failed: %s", exc)
            return set()

    def set_skill_enabled(self, skill_name: str, is_enabled: bool) -> bool:
        """設定官方 skill 開關。回傳是否成功。"""
        try:
            DatabaseBase.execute(
                """
                INSERT INTO user_skill_overrides (user_id, skill_name, is_enabled, updated_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (user_id, skill_name)
                DO UPDATE SET is_enabled = EXCLUDED.is_enabled, updated_at = NOW()
                """,
                (self.user_id, skill_name, is_enabled),
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("[SkillPref] set_skill_enabled failed: %s", exc)
            return False

    # ── 使用者自訂 skill CRUD ────────────────────────────────────────────

    def get_custom_skills(self) -> List[Dict]:
        """列出使用者的自訂 skill。"""
        try:
            rows = DatabaseBase.query_all(
                """
                SELECT skill_name, description, trigger_keywords, body, is_enabled,
                       created_at, updated_at
                FROM user_custom_skills
                WHERE user_id = %s
                ORDER BY created_at DESC
                """,
                (self.user_id,),
            )
            return rows or []
        except Exception as exc:  # noqa: BLE001
            logger.debug("[SkillPref] get_custom_skills failed: %s", exc)
            return []

    def get_enabled_custom_skills(self) -> List[Dict]:
        """列出啟用的自訂 skill（供注入用）。"""
        try:
            rows = DatabaseBase.query_all(
                """
                SELECT skill_name, description, trigger_keywords, body
                FROM user_custom_skills
                WHERE user_id = %s AND is_enabled = TRUE
                ORDER BY created_at DESC
                """,
                (self.user_id,),
            )
            return rows or []
        except Exception as exc:  # noqa: BLE001
            logger.debug("[SkillPref] get_enabled_custom_skills failed: %s", exc)
            return []

    def get_custom_skill(self, skill_name: str) -> Optional[Dict]:
        """取得單一自訂 skill。"""
        try:
            rows = DatabaseBase.query_all(
                """
                SELECT skill_name, description, trigger_keywords, body, is_enabled,
                       created_at, updated_at
                FROM user_custom_skills
                WHERE user_id = %s AND skill_name = %s
                """,
                (self.user_id, skill_name),
            )
            return rows[0] if rows else None
        except Exception as exc:  # noqa: BLE001
            logger.debug("[SkillPref] get_custom_skill failed: %s", exc)
            return None

    def create_custom_skill(
        self,
        skill_name: str,
        description: str,
        trigger_keywords: str,
        body: str,
    ) -> Dict:
        """新建自訂 skill。回傳 {ok, error?}。"""
        err = _validate_custom_skill(skill_name, description, trigger_keywords, body)
        if err:
            return {"ok": False, "error": err}

        # 數量上限
        existing = self.get_custom_skills()
        if len(existing) >= MAX_CUSTOM_SKILLS:
            return {"ok": False, "error": f"自訂 skill 上限 {MAX_CUSTOM_SKILLS} 個"}

        # 名稱不可重複
        if any(s["skill_name"] == skill_name for s in existing):
            return {"ok": False, "error": f"skill 名稱「{skill_name}」已存在"}

        try:
            DatabaseBase.execute(
                """
                INSERT INTO user_custom_skills
                    (user_id, skill_name, description, trigger_keywords, body, is_enabled)
                VALUES (%s, %s, %s, %s, %s, TRUE)
                """,
                (self.user_id, skill_name, description, trigger_keywords, body),
            )
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            logger.warning("[SkillPref] create_custom_skill failed: %s", exc)
            return {"ok": False, "error": "建立失敗，請稍後再試"}

    def update_custom_skill(
        self,
        skill_name: str,
        description: Optional[str] = None,
        trigger_keywords: Optional[str] = None,
        body: Optional[str] = None,
        is_enabled: Optional[bool] = None,
    ) -> Dict:
        """修改自訂 skill。只更新非 None 的欄位。回傳 {ok, error?}。"""
        existing = self.get_custom_skill(skill_name)
        if not existing:
            return {"ok": False, "error": "找不到此 skill"}

        new_desc = description if description is not None else existing["description"]
        new_trig = trigger_keywords if trigger_keywords is not None else existing["trigger_keywords"]
        new_body = body if body is not None else existing["body"]

        err = _validate_custom_skill(skill_name, new_desc, new_trig, new_body)
        if err:
            return {"ok": False, "error": err}

        sets = ["description = %s", "trigger_keywords = %s", "body = %s", "updated_at = NOW()"]
        params: list = [new_desc, new_trig, new_body]
        if is_enabled is not None:
            sets.append("is_enabled = %s")
            params.append(is_enabled)
        params.extend([self.user_id, skill_name])

        try:
            # c031 版本治理：修改前先把舊版存入 revisions（冪等——同版不重存）
            self._snapshot_revision(existing)
            sets.append("revision = user_custom_skills.revision + 1")
            DatabaseBase.execute(
                f"""
                UPDATE user_custom_skills
                SET {", ".join(sets)}
                WHERE user_id = %s AND skill_name = %s
                """,
                tuple(params),
            )
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            logger.warning("[SkillPref] update_custom_skill failed: %s", exc)
            return {"ok": False, "error": "更新失敗，請稍後再試"}

    def _snapshot_revision(self, existing: Dict, edited_via: str = "user") -> None:
        """把當前版存入 user_skill_revisions（冪等——UNIQUE 約束擋重複）。"""
        try:
            skill_id = existing.get("id")
            if not skill_id:
                return
            DatabaseBase.execute(
                """
                INSERT INTO user_skill_revisions
                    (skill_id, revision, body, description, trigger_keywords, edited_via)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (skill_id, revision) DO NOTHING
                """,
                (
                    skill_id,
                    existing.get("revision", 1),
                    existing.get("body", ""),
                    existing.get("description", ""),
                    existing.get("trigger_keywords", ""),
                    edited_via,
                ),
            )
        except Exception as exc:  # noqa: BLE001 — 版本快照失敗不阻塞主更新
            logger.debug("[SkillPref] snapshot_revision failed (non-fatal): %s", exc)

    def get_skill_revisions(self, skill_name: str) -> List[Dict]:
        """列出自訂 skill 的版本歷史。"""
        try:
            existing = self.get_custom_skill(skill_name)
            if not existing:
                return []
            return DatabaseBase.query_all(
                """SELECT revision, body, description, trigger_keywords,
                          edited_via, created_at
                   FROM user_skill_revisions
                   WHERE skill_id = %s
                   ORDER BY revision DESC
                   LIMIT 50""",
                (existing["id"],),
            ) or []
        except Exception as exc:  # noqa: BLE001
            logger.warning("[SkillPref] get_skill_revisions failed: %s", exc)
            return []

    def rollback_skill(self, skill_name: str, target_revision: int) -> Dict:
        """回滾自訂 skill 到指定版本。回傳 {ok, error?}。"""
        try:
            existing = self.get_custom_skill(skill_name)
            if not existing:
                return {"ok": False, "error": "找不到此 skill"}
            # 先快取當前版（回滾也留歷史）
            self._snapshot_revision(existing)
            rev = DatabaseBase.query_one(
                """SELECT body, description, trigger_keywords
                   FROM user_skill_revisions
                   WHERE skill_id = %s AND revision = %s""",
                (existing["id"], target_revision),
            )
            if not rev:
                return {"ok": False, "error": f"找不到 revision {target_revision}"}
            DatabaseBase.execute(
                """
                UPDATE user_custom_skills
                SET body = %s, description = %s, trigger_keywords = %s,
                    updated_at = NOW(), revision = user_custom_skills.revision + 1
                WHERE user_id = %s AND skill_name = %s
                """,
                (
                    rev["body"], rev["description"], rev["trigger_keywords"],
                    self.user_id, skill_name,
                ),
            )
            return {"ok": True, "rolled_back_to": target_revision}
        except Exception as exc:  # noqa: BLE001
            logger.warning("[SkillPref] rollback_skill failed: %s", exc)
            return {"ok": False, "error": "回滾失敗"}

    def delete_custom_skill(self, skill_name: str) -> Dict:
        """刪除自訂 skill。回傳 {ok, error?}。"""
        try:
            affected = DatabaseBase.execute(
                "DELETE FROM user_custom_skills WHERE user_id = %s AND skill_name = %s",
                (self.user_id, skill_name),
            )
            if affected == 0:
                return {"ok": False, "error": "找不到此 skill"}
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            logger.warning("[SkillPref] delete_custom_skill failed: %s", exc)
            return {"ok": False, "error": "刪除失敗，請稍後再試"}


def _validate_custom_skill(
    name: str, description: str, trigger_keywords: str, body: str
) -> Optional[str]:
    """驗證自訂 skill 欄位。回傳錯誤訊息或 None（通過）。"""
    if not name or not _NAME_RE.match(name) or len(name) > MAX_NAME_LEN:
        return f"skill 名稱只能含英數/底線/連字號，且 ≤{MAX_NAME_LEN} 字"
    if len(body) > MAX_CUSTOM_BODY:
        return f"內容超過上限 {MAX_CUSTOM_BODY} 字（目前 {len(body)} 字）"
    if len(trigger_keywords) > MAX_TRIGGER_LEN:
        return f"觸發詞超過上限 {MAX_TRIGGER_LEN} 字"
    if not body.strip():
        return "內容不可為空"
    if any(
        _USER_SKILL_TAG_RE.search(field or "")
        for field in (description, trigger_keywords, body)
    ):
        return "內容不可包含 <user_skill> 標籤"
    return None
