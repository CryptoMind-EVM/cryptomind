"""
用戶認證相關資料庫操作
包含：用戶管理、TON Connect 認證、會員等級
"""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

import psycopg2.errors

from core.onchain.addresses import (
    evm_identity_address,
    scam_address_key,
    wallet_identity_address,
)

from .connection import get_connection

logger = logging.getLogger(__name__)


def _normalize_membership_tier(tier: Optional[str]) -> str:
    return (
        "premium"
        if (tier or "free").strip().lower() in {"premium", "plus", "pro"}
        else "free"
    )


# ============================================================================
# 用戶 CRUD
# ============================================================================


def get_user_by_id(user_id: str) -> Optional[Dict]:
    """根據 ID 獲取用戶信息"""
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute(
            """
            SELECT user_id, username, auth_method,
                   role, is_active, membership_tier, membership_expires_at, created_at
            FROM users WHERE user_id = %s
        """,
            (user_id,),
        )
        row = c.fetchone()
        if row:
            return {
                "user_id": row[0],
                "username": row[1],
                "auth_method": row[2],
                "role": row[3] or "user",
                "is_active": row[4] if row[4] is not None else True,
                "membership_tier": _normalize_membership_tier(row[5]),
                "membership_expires_at": row[6].isoformat() if row[6] else None,
                "created_at": row[7].isoformat() if row[7] else None,
            }
        return None
    finally:
        conn.close()


def update_last_active(user_id: str) -> bool:
    """更新用戶最後活動時間"""
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute(
            """
            UPDATE users SET last_active_at = NOW()
            WHERE user_id = %s
        """,
            (user_id,),
        )
        conn.commit()
        return c.rowcount > 0
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.warning(f"Update last active error: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()


def get_current_session(user_id: str) -> Optional[str]:
    """讀取用戶跨平台共用的「當前對話 session」。

    Web 與 Telegram 共用同一個指標 → 任一端切換/發言都更新它，另一端讀它，
    達成跨平台對話延續。容錯：欄位不存在或查詢失敗回 None（呼叫端 fallback）。
    """
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute("SELECT current_session_id FROM users WHERE user_id = %s", (user_id,))
        row = c.fetchone()
        return row[0] if row else None
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:  # noqa: BLE001 — 欄位未 migrate 時不可炸關鍵路徑
        logger.warning(f"get_current_session unavailable: {e}")
        return None
    finally:
        conn.close()


def set_current_session(user_id: str, session_id: Optional[str]) -> bool:
    """設定用戶跨平台共用的「當前對話 session」。容錯，失敗不拋例外。"""
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute(
            "UPDATE users SET current_session_id = %s WHERE user_id = %s",
            (session_id, user_id),
        )
        conn.commit()
        return c.rowcount > 0
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning(f"set_current_session failed: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()


def get_user_language(user_id: str) -> Optional[str]:
    """讀取用戶儲存的 UI 語言偏好。完全容錯：連線/欄位未 migrate/查詢失敗都回 None
    （/api/user/me 等關鍵路徑在 DB 不可用時仍須正常運作）。"""
    conn = None
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute("SELECT language FROM users WHERE user_id = %s", (user_id,))
        row = c.fetchone()
        return row[0] if row and row[0] else None
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:  # noqa: BLE001 — 連線/欄位未 migrate 時不可炸關鍵路徑
        logger.warning(f"get_user_language unavailable: {e}")
        return None
    finally:
        if conn is not None:
            conn.close()


def set_user_language(user_id: str, language: Optional[str]) -> bool:
    """設定用戶 UI 語言偏好（跨裝置 + Telegram 共用）。完全容錯，失敗不拋例外。"""
    conn = None
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute(
            "UPDATE users SET language = %s WHERE user_id = %s",
            (language, user_id),
        )
        conn.commit()
        return c.rowcount > 0
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning(f"set_user_language failed: {e}")
        if conn is not None:
            conn.rollback()
        return False
    finally:
        if conn is not None:
            conn.close()


# ============================================================================
# 個人化暱稱（display_name）+ 24h 改名冷卻（Trustworthy AI Hackathon — Principal）
# ============================================================================

# 免費改名間隔：24 小時。Phase 2 加 TON 付款跳過冷卻時可繞過此常數。
DISPLAY_NAME_COOLDOWN_SECONDS = 24 * 60 * 60


def _is_within_cooldown(updated_at: Optional[datetime]) -> bool:
    """判斷 `updated_at` 是否仍在冷卻期內（< 24h）。

    - None / 過去 datetime 解析失敗 → 回 False（從未改過 = 無冷卻）
    - tz-aware 與 naive 都容忍（DB 可能回傳任一種）
    """
    if updated_at is None:
        return False
    try:
        ts = updated_at
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(timezone.utc) - ts).total_seconds()
        return elapsed < DISPLAY_NAME_COOLDOWN_SECONDS
    except (TypeError, ValueError):
        return False


def get_user_display_name(user_id: str) -> Optional[str]:
    """讀取使用者自訂暱稱。完全容錯（連線/欄位未 migrate/查詢失敗都回 None）。"""
    conn = None
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute(
            "SELECT display_name FROM users WHERE user_id = %s",
            (user_id,),
        )
        row = c.fetchone()
        return row[0] if row and row[0] else None
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:  # noqa: BLE001 — 連線/欄位未 migrate 時不可炸關鍵路徑
        logger.warning(f"get_user_display_name unavailable: {e}")
        return None
    finally:
        if conn is not None:
            conn.close()


def is_display_name_taken(display_name: str, exclude_user_id: Optional[str] = None) -> bool:
    """檢查 display_name 是否已被「其他使用者」使用。

    - exclude_user_id: 排除自己(改同名時不誤判)。
    - 大小寫敏感比對(與 DB 唯一索引一致)。
    - 完全容錯:查詢失敗時回傳 False(由 DB 唯一索引 + set_user_display_name
      捕捉 UniqueViolation 做最終把關,不依賴此預檢查)。
    """
    conn = None
    try:
        conn = get_connection()
        c = conn.cursor()
        if exclude_user_id:
            c.execute(
                "SELECT 1 FROM users WHERE display_name = %s AND user_id != %s LIMIT 1",
                (display_name, exclude_user_id),
            )
        else:
            c.execute(
                "SELECT 1 FROM users WHERE display_name = %s LIMIT 1",
                (display_name,),
            )
        return c.fetchone() is not None
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:  # noqa: BLE001 — 預檢查失敗不可擋關鍵路徑
        logger.warning(f"is_display_name_taken unavailable: {e}")
        return False
    finally:
        if conn is not None:
            conn.close()


def is_username_taken(username: str) -> bool:
    """檢查 username 是否已被使用(用於禁止暱稱取系統預設名 TON_xxx)。

    username 為唯一欄位,比對即「是否有人的預設名等於此值」。完全容錯。
    """
    conn = None
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute("SELECT 1 FROM users WHERE username = %s LIMIT 1", (username,))
        return c.fetchone() is not None
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning(f"is_username_taken unavailable: {e}")
        return False
    finally:
        if conn is not None:
            conn.close()


def get_user_display_name_cooldown(user_id: str) -> Optional[datetime]:
    """讀取上次改名時間（用於計算剩餘冷卻）。完全容錯。"""
    conn = None
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute(
            "SELECT display_name_updated_at FROM users WHERE user_id = %s",
            (user_id,),
        )
        row = c.fetchone()
        return row[0] if row else None
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning(f"get_user_display_name_cooldown unavailable: {e}")
        return None
    finally:
        if conn is not None:
            conn.close()


def set_user_display_name(user_id: str, display_name: str) -> tuple:
    """設定使用者暱稱，含 24h 冷卻檢查。

    Returns:
        (True, None): 成功。
        (False, "cooldown", next_available_at_iso): 24h 內重複改名被拒。
        (False, "db_error", None): 寫入失敗。
    """
    conn = None
    try:
        conn = get_connection()
        c = conn.cursor()

        # Atomic cooldown + update：用單一 UPDATE ... WHERE 條件含冷卻檢查，
        # 消除原本 SELECT→UPDATE 兩段式 TOCTOU race（並發 PUT 可雙雙繞過冷卻）。
        # rowcount==1 → 成功；rowcount==0 → 可能是冷卻中或 user 不存在，需後續 SELECT 釐清。
        c.execute(
            "UPDATE users SET display_name = %s, display_name_updated_at = NOW() "
            "WHERE user_id = %s "
            "AND (display_name_updated_at IS NULL "
            "     OR display_name_updated_at < NOW() - INTERVAL %s)",
            (display_name, user_id, f"{DISPLAY_NAME_COOLDOWN_SECONDS} seconds"),
        )
        conn.commit()
        if c.rowcount > 0:
            return (True, None)

        # rowcount==0：釐清是冷卻中還是 user 不存在。
        c.execute(
            "SELECT display_name_updated_at FROM users WHERE user_id = %s",
            (user_id,),
        )
        row = c.fetchone()
        if row is None:
            # user 不存在（不該發生，JWT 已驗證）；回 db_error 由 router 處理。
            return (False, "db_error", None)

        last_updated = row[0]
        # 通過 atomic UPDATE 卻 rowcount==0 → 必為冷卻中（或剛被並發搶先）。
        try:
            ts = last_updated
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            next_available = ts + timedelta(seconds=DISPLAY_NAME_COOLDOWN_SECONDS)
            next_iso = next_available.strftime("%Y-%m-%dT%H:%M:%SZ")
        except (TypeError, ValueError):
            next_iso = None
        return (False, "cooldown", next_iso)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning(f"set_user_display_name failed: {e}")
        if conn is not None:
            conn.rollback()
        # 唯一索引競態(預檢查與 UPDATE 之間有人搶先)→ 明確回 duplicate 供 router 409
        if "unique" in str(e).lower() or "duplicate" in str(e).lower():
            return (False, "duplicate", None)
        return (False, "db_error", None)
    finally:
        if conn is not None:
            conn.close()


# ============================================================================
# EVM 地址綁定（Human Passport 訊號用，c016）
# ============================================================================


def get_user_evm_address(user_id: str) -> Optional[str]:
    """讀使用者綁定的 EVM 地址。完全容錯（欄位未 migrate 時回 None）。"""
    conn = None
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute("SELECT evm_address FROM users WHERE user_id = %s", (user_id,))
        row = c.fetchone()
        return row[0] if row and row[0] else None
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning(f"get_user_evm_address unavailable: {e}")
        return None
    finally:
        if conn is not None:
            conn.close()


def is_evm_address_bound(evm_address: str, exclude_user_id: Optional[str] = None) -> bool:
    """檢查 EVM 地址是否已被綁定（防一地址綁多 user）。case-insensitive。"""
    conn = None
    try:
        conn = get_connection()
        c = conn.cursor()
        if exclude_user_id:
            c.execute(
                "SELECT 1 FROM users WHERE LOWER(evm_address) = LOWER(%s) "
                "AND user_id != %s LIMIT 1",
                (evm_address, exclude_user_id),
            )
        else:
            c.execute(
                "SELECT 1 FROM users WHERE LOWER(evm_address) = LOWER(%s) LIMIT 1",
                (evm_address,),
            )
        return c.fetchone() is not None
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning(f"is_evm_address_taken check failed: {e}")
        return False
    finally:
        if conn is not None:
            conn.close()


def set_user_evm_address(user_id: str, evm_address: str) -> tuple[bool, str]:
    """綁定 EVM 地址。回 (success, reason)。reason: 'ok'/'duplicate'/'db_error'。

    ⚠️ 不變式：只能在「這個地址的簽章已驗過」之後呼叫（SIWE 或 personal_sign）。
    本函式不驗簽，但 get_verified_evm_address 會把 users.evm_address 當成簽章
    證明過的地址——Agent principal、信任分數（錢包所有權、鏈上資歷）都信它。
    目前只有兩條路徑：evm_login 的 SIWE 驗章後 _sync_trust_evm_binding、
    /api/trust/evm/bind 的 verify_evm_signature 之後。新增呼叫點會讓
    tests/test_evm_address_write_guard.py 紅，要先確認那條路徑有驗簽。
    （沒有 ORM 版本：User 模型沒有對映這欄。）
    """
    conn = None
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute(
            "UPDATE users SET evm_address = %s, evm_bound_at = NOW() WHERE user_id = %s",
            (evm_address, user_id),
        )
        conn.commit()
        if c.rowcount == 0:
            return (False, "user_not_found")
        return (True, "ok")
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning(f"set_user_evm_address failed: {e}")
        if conn is not None:
            conn.rollback()
        if "unique" in str(e).lower() or "duplicate" in str(e).lower():
            return (False, "duplicate")
        return (False, "db_error")
    finally:
        if conn is not None:
            conn.close()


def get_wallet_alert_settings(user_id: str) -> Optional[Dict]:
    """讀取用戶的錢包監測警示設定（JSONB）。完全容錯（未設定/欄位未 migrate 回 None）。"""
    conn = None
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute(
            "SELECT wallet_alert_settings FROM users WHERE user_id = %s",
            (user_id,),
        )
        row = c.fetchone()
        if not row or row[0] is None:
            return None
        if isinstance(row[0], dict):
            return row[0]
        import json as _json
        return _json.loads(row[0])
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning(f"get_wallet_alert_settings unavailable: {e}")
        return None
    finally:
        if conn is not None:
            conn.close()


def save_wallet_alert_settings(user_id: str, settings: Dict) -> tuple[bool, str]:
    """儲存用戶的錢包監測警示設定。回 (success, reason)。"""
    import json as _json

    conn = None
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute(
            "UPDATE users SET wallet_alert_settings = %s WHERE user_id = %s",
            (_json.dumps(settings), user_id),
        )
        conn.commit()
        if c.rowcount == 0:
            return (False, "user_not_found")
        return (True, "ok")
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning(f"save_wallet_alert_settings failed: {e}")
        if conn is not None:
            conn.rollback()
        return (False, "db_error")
    finally:
        if conn is not None:
            conn.close()


def clear_user_evm_address(user_id: str) -> tuple[bool, str]:
    """解綁 EVM 地址。回 (success, reason)。"""
    conn = None
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute(
            "UPDATE users SET evm_address = NULL, evm_bound_at = NULL WHERE user_id = %s",
            (user_id,),
        )
        conn.commit()
        return (True, "ok")
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning(f"clear_user_evm_address failed: {e}")
        if conn is not None:
            conn.rollback()
        return (False, "db_error")
    finally:
        if conn is not None:
            conn.close()


def get_verified_evm_address(user_id: str) -> Optional[str]:
    """使用者已驗證的 EVM 地址（小寫）：user_wallets(chain='evm') → evm_ 身份 → users.evm_address。

    三個來源都有簽章證明（evm-login／wallets/bind 的 SIWE、trust 綁定的 personal_sign）。
    users.evm_address 這條靠呼叫慣例保證：set_user_evm_address 只能在驗簽後呼叫
    （見其 docstring；tests/test_evm_address_write_guard.py 盯呼叫點）。
    Agent principal、信任分數共用這一條。完全容錯：查不到或 DB 錯誤回 None。
    """
    if not user_id:
        return None
    conn = None
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute(
            """
            SELECT address FROM user_wallets
            WHERE user_id = %s AND chain = 'evm'
            ORDER BY is_primary DESC, bound_at ASC
            LIMIT 1
            """,
            (user_id,),
        )
        row = c.fetchone()
        if row and row[0]:
            return str(row[0]).lower()
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning(f"get_verified_evm_address: user_wallets unavailable: {e}")
    finally:
        if conn is not None:
            conn.close()
    identity = evm_identity_address(user_id)
    if identity:
        return identity
    bound = get_user_evm_address(user_id)
    return bound.lower() if bound else None


# 一個帳號連結的地址上限（綁定列通常 1–3 個；防異常資料拖垮扣分查詢）
_MAX_LINKED_WALLETS = 20


def get_linked_wallet_addresses(user_id: str) -> list[str]:
    """帳號連結過的每一個鏈上地址（去重、EVM 小寫）：詐騙扣分要全部查。

    來源：錢包身份（evm_…／舊 TON 地址）、user_wallets 全部列（evm 與 ton）、
    users.evm_address。principal／鏈上訊號只用主要地址（get_verified_evm_address），
    但扣分只查主要地址的話，換綁（users.evm_address 還是舊的 A、user_wallets 是新的 B）
    就能把 A 的詐騙檢舉洗掉。DB 錯誤往上拋，呼叫端退回只查主要地址。
    """
    candidates = [wallet_identity_address(user_id)]
    conn = get_connection()
    try:
        c = conn.cursor()
        c.execute(
            f"""
            SELECT address FROM user_wallets
            WHERE user_id = %s
            ORDER BY is_primary DESC, bound_at ASC
            LIMIT {_MAX_LINKED_WALLETS}
            """,
            (user_id,),
        )
        candidates += [row[0] for row in c.fetchall() if row and row[0]]
    finally:
        conn.close()
    candidates.append(get_user_evm_address(user_id))

    seen: set = set()
    addresses: list[str] = []
    for raw in candidates:
        addr = (raw or "").strip()
        if not addr:
            continue
        if addr.lower().startswith("0x"):
            addr = addr.lower()
        key = scam_address_key(addr)  # TON 各種寫法歸成 raw、EVM 不分大小寫
        if key not in seen:
            seen.add(key)
            addresses.append(addr)
    return addresses


def find_user_ids_by_evm_address(evm_address: str) -> list[str]:
    """持有這個 EVM 地址的帳號：綁定（user_wallets）、EVM 登入身份、trust 綁定欄位。

    詐騙檢舉命中時要找到錢包主人重算信任分數——EVM 使用者的 user_id 是 evm_0x…
    或 tg_／g_（綁了錢包），拿地址直接當 user_id 查永遠對不到。
    """
    addr = (evm_address or "").strip().lower()
    if not addr:
        return []
    conn = None
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute(
            """
            SELECT user_id FROM user_wallets WHERE chain = 'evm' AND LOWER(address) = %s
            UNION
            SELECT user_id FROM users WHERE user_id = %s
            UNION
            SELECT user_id FROM users WHERE LOWER(evm_address) = %s
            """,
            (addr, f"evm_{addr}", addr),
        )
        return [row[0] for row in c.fetchall() if row and row[0]]
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning(f"find_user_ids_by_evm_address failed: {e}")
        return []
    finally:
        if conn is not None:
            conn.close()


# ============================================================================
# 用戶身份（user_id = 登入身份：evm_<地址>／tg_<id>／g_<sub>；舊 TON 帳號即錢包地址）
# ============================================================================

# 系統自動產生的 username 前綴（default_username、各登入端點、TEST_MODE）。
# 暱稱不可用這些開頭（不分大小寫），否則能冒充別人的預設名。新增前綴時一起加。
SYSTEM_USERNAME_PREFIXES = ("TON_", "EVM_", "TG_", "G_", "USER_", "TestUser_")


def _auth_method_for_identity(identity: str) -> Optional[str]:
    """由身份格式推回登入方式；認不出來回 None（不再預設 ton_wallet）。"""
    if evm_identity_address(identity):
        return "evm_wallet"
    if identity.startswith("tg_"):
        return "telegram"
    if identity.startswith("g_"):
        return "google"
    return None


def default_username(identity: str) -> str:
    """沒給 username 時的系統預設名（前綴都在 SYSTEM_USERNAME_PREFIXES）。"""
    evm = evm_identity_address(identity)
    if evm:
        return f"EVM_{evm[2:8]}"
    if identity.startswith("tg_"):
        return f"TG_{identity[3:]}"
    if identity.startswith("g_"):
        return f"G_{identity[-6:]}"
    return f"USER_{identity[-6:]}"


def create_or_get_user(
    identity: str,
    username: Optional[str] = None,
    auth_method: Optional[str] = None,
) -> Dict:
    """
    創建或獲取用戶。

    identity 為用戶身份（evm_<地址>、tg_<id>、g_<sub>；舊 TON 帳號即錢包地址），
    同時作為 user_id（主鍵）。
    - 若該 identity 已存在，返回現有用戶
    - 若 username 被其他用戶使用，自動加隨機後綴
    - 否則創建新用戶：auth_method 由呼叫端帶實際登入方式，沒帶就依身份格式推
      （見 _auth_method_for_identity；認不出來存 NULL，不再預設 ton_wallet）
    """
    if not auth_method:
        auth_method = _auth_method_for_identity(identity)
    if not username:
        username = default_username(identity)
    conn = get_connection()
    c = conn.cursor()
    try:
        # 以 user_id（= identity）查是否已存在
        c.execute(
            "SELECT user_id, username, auth_method, role, membership_tier FROM users WHERE user_id = %s",
            (identity,),
        )
        row = c.fetchone()
        if row:
            return {
                "user_id": row[0],
                "username": row[1],
                "auth_method": row[2],
                "role": row[3] or "user",
                "membership_tier": _normalize_membership_tier(row[4]),
                "is_new": False,
            }

        # 檢查 username 是否被其他用戶使用
        c.execute(
            "SELECT user_id, auth_method FROM users WHERE username = %s", (username,)
        )
        existing = c.fetchone()
        if existing:
            original_username = username
            suffix = str(uuid.uuid4()).replace("-", "")[:8]
            username = f"{original_username}_{suffix}"

            c.execute("SELECT 1 FROM users WHERE username = %s", (username,))
            if c.fetchone():
                username = f"{original_username}_{str(uuid.uuid4())}"

        # 創建新用戶（user_id = identity = 錢包地址）
        user_id = identity
        try:
            c.execute(
                """
                INSERT INTO users (user_id, username, auth_method, created_at)
                VALUES (%s, %s, %s, NOW())
            """,
                (user_id, username, auth_method),
            )
            conn.commit()
        except psycopg2.errors.UniqueViolation:
            # 並發首登（雙擊／兩個 tab／網路重試）同時通過上方的 SELECT-無列
            # 檢查、雙雙 INSERT——較慢者撞 PK。回滾後重查回既有用戶，
            # 而不是讓 IntegrityError 穿透成 500（2026-09-10 登入鏈徹查）。
            conn.rollback()
            c.execute(
                "SELECT user_id, username, auth_method, role, membership_tier FROM users WHERE user_id = %s",
                (identity,),
            )
            row = c.fetchone()
            if row:
                return {
                    "user_id": row[0],
                    "username": row[1],
                    "auth_method": row[2],
                    "role": row[3] or "user",
                    "membership_tier": _normalize_membership_tier(row[4]),
                    "is_new": False,
                }
            raise

        return {
            "user_id": user_id,
            "username": username,
            "auth_method": auth_method,
            "role": "user",
            "membership_tier": "free",
            "is_new": True,
        }
    except ValueError:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_user_by_identity(identity: str) -> Optional[Dict]:
    """根據身份（user_id）獲取用戶信息"""
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute(
            "SELECT user_id, username, auth_method, created_at FROM users WHERE user_id = %s",
            (identity,),
        )
        row = c.fetchone()
        if row:
            return {
                "user_id": row[0],
                "username": row[1],
                "auth_method": row[2],
                "created_at": row[3],
            }
        return None
    finally:
        conn.close()


def get_user_wallet_status(user_id: str) -> Dict:
    """獲取用戶錢包綁定狀態。

    有錢包＝身份本身是錢包（evm_…／舊 TON 地址）或 user_wallets 有綁定列。
    以前用 auth_method is not None，Telegram／Google 帳號全被當成有錢包。
    wallet_address：綁定的 EVM 地址優先（平台 EVM 優先），其次是身份地址。
    """
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute(
            """
            SELECT u.auth_method,
                   EXISTS (SELECT 1 FROM user_wallets w WHERE w.user_id = u.user_id),
                   (SELECT w.address FROM user_wallets w
                     WHERE w.user_id = u.user_id AND w.chain = 'evm'
                     ORDER BY w.is_primary DESC, w.bound_at ASC LIMIT 1)
            FROM users u WHERE u.user_id = %s
        """,
            (user_id,),
        )
        row = c.fetchone()
        if not row:
            return {"has_wallet": False, "auth_method": None, "wallet_address": None}

        identity_address = wallet_identity_address(user_id)
        bound_evm = str(row[2]).lower() if row[2] else None
        return {
            "has_wallet": bool(identity_address) or bool(row[1]),
            "auth_method": row[0],
            "wallet_address": bound_evm or identity_address,
        }
    finally:
        conn.close()


# ============================================================================
# 會員等級
# ============================================================================


def get_user_membership(user_id: str) -> Dict:
    """
    獲取用戶會員狀態（只讀）。若需要降級過期會員，請呼叫 expire_user_membership()。

    Returns:
        {"tier": str, "expires_at": str | None, "is_premium": bool, "is_expired": bool}
    """
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute(
            """
            SELECT membership_tier, membership_expires_at
            FROM users WHERE user_id = %s
        """,
            (user_id,),
        )
        row = c.fetchone()

        if row:
            raw_tier = row[0] or "free"
            tier = _normalize_membership_tier(raw_tier)
            expires_at = row[1]
            is_premium = tier == "premium"
            is_expired = False

            # 檢查是否過期（只檢查，不自動更新）
            # DB 可能回傳 tz-aware 或 naive datetime；統一成 UTC tz-aware 後再比較，
            # 避免「offset-naive vs offset-aware」TypeError 被誤判成過期。
            if is_premium and expires_at:
                try:
                    expire_dt = expires_at
                    if expire_dt.tzinfo is None:
                        expire_dt = expire_dt.replace(tzinfo=timezone.utc)

                    if expire_dt < datetime.now(timezone.utc):
                        is_expired = True
                except (ValueError, TypeError):
                    # 日期格式錯誤，視為過期
                    is_expired = True

            # 轉換 expires_at 為字符串
            # isoformat（含 T 與時區）：JS new Date() 跨瀏覽器可解析——
            # 原 "%Y-%m-%d %H:%M:%S" 空格格式 Safari 拒 parse（2026-09-10）
            if expires_at:
                expires_at = expires_at.isoformat()

            is_premium = is_premium and not is_expired
            return {
                "tier": tier,
                "expires_at": expires_at,
                "is_premium": is_premium,
                "is_expired": is_expired,
            }
        return {
            "tier": "free",
            "expires_at": None,
            "is_premium": False,
            "is_expired": False,
        }
    finally:
        conn.close()


def expire_user_membership(user_id: str) -> bool:
    """
    手動將用戶會員狀態降級為免費（用於過期處理）
    這是一個獨立的寫入方法，與讀取分離
    """
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute(
            """
            UPDATE users
            SET membership_tier = 'free', membership_expires_at = NULL
            WHERE user_id = %s AND membership_tier IN ('pro', 'premium')
        """,
            (user_id,),
        )
        conn.commit()
        return c.rowcount > 0
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"Expire membership error: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()


def upgrade_to_pro(
    user_id: str,
    months: int = 1,
    tx_hash: Optional[str] = None,
    amount: Optional[float] = None,
) -> bool:
    """升級用戶為 Premium 會員並記錄支付（保留舊函式名相容，支援續費順延）

    ``amount``：實收金額（USDC 訂閱＝訂單的美元金額）。沒給才退回舊算法
    ——system_config 的 premium 價（歷來是 TON 定價）× 月數。
    """
    # EVM tx hash 是十六進位、節點不分大小寫：同一筆交易換個大小寫就能重複開通。
    # 一律存小寫、去重不分大小寫（舊資料可能是混合大小寫）。TON 的 base64 hash
    # 大小寫有意義，admin_grant_… 等內部標記也不動。
    is_evm_hash = bool(tx_hash) and tx_hash[:2].lower() == "0x"
    if is_evm_hash:
        tx_hash = tx_hash.lower()
    conn = get_connection()
    c = conn.cursor()
    try:
        # 0. 如果有交易哈希，檢查是否已被使用（防止重複提交）
        if tx_hash:
            c.execute(
                "SELECT user_id FROM membership_payments WHERE lower(tx_hash) = %s"
                if is_evm_hash
                else "SELECT user_id FROM membership_payments WHERE tx_hash = %s",
                (tx_hash,),
            )
            existing = c.fetchone()
            if existing:
                logger.warning(
                    f"[Upgrade] Duplicate tx_hash detected: {tx_hash} already used by user {existing[0]}"
                )
                raise ValueError("此交易已被處理（transaction hash已存在）")

        # 1. 查詢當前狀態，決定是「新購」還是「續費」
        #    用 DB 的 NOW() 判斷，避免 Python 時間與 DB 時間不一致
        c.execute(
            """
            SELECT membership_tier, membership_expires_at,
                   CASE
                     WHEN membership_tier IN ('premium', 'plus', 'pro')
                          AND membership_expires_at IS NOT NULL
                          AND membership_expires_at > NOW()
                     THEN true ELSE false
                   END AS is_active_pro
            FROM users WHERE user_id = %s
            """,
            (user_id,),
        )
        row = c.fetchone()

        if not row:
            raise ValueError("用戶不存在")

        _, expires_at, is_active_pro = row

        # 2. 更新會員狀態
        # SQL injection fix: Use parameterized query for INTERVAL
        if is_active_pro:
            # 續費：從原到期日往後順延
            c.execute(
                """
                UPDATE users
                SET membership_tier = 'premium',
                    membership_expires_at = membership_expires_at + INTERVAL %s
                WHERE user_id = %s
            """,
                (f"{months} months", user_id),
            )
        else:
            # 新購或已過期：從現在開始計算
            c.execute(
                """
                UPDATE users
                SET membership_tier = 'premium',
                    membership_expires_at = NOW() + INTERVAL %s
                WHERE user_id = %s
            """,
                (f"{months} months", user_id),
            )

        # 3. 如果有交易哈希，記錄支付流水帳
        if tx_hash:
            if amount is None:
                from core.database.system_config import get_prices

                prices = get_prices()
                amount = prices.get("premium", 1.0) * months
            c.execute(
                """
                INSERT INTO membership_payments (user_id, amount, months, tx_hash, created_at)
                VALUES (%s, %s, %s, %s, NOW())
            """,
                (user_id, amount, months, tx_hash),
            )

        conn.commit()
        return c.rowcount > 0
    except ValueError:
        # 重複交易或用戶不存在等業務錯誤，不回滾（因為沒有執行任何寫入）
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"Upgrade to premium error: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()
