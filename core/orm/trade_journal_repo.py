"""Trade Journal Repository — 投資帳本 CRUD + 加權平均損益計算。

設計：docs/plans/2026-08-21-investment-journal-design.md（DANNY Approve）

支援：
- 跨市場（crypto / tw_stock / us_stock / hk_stock / jp_stock / kr_stock / cn_stock / forex / commodity）
- 現貨（spot）/ 合約（futures）/ 融資（margin）
- 做多（long）/ 做空（short）
- 槓桿（現貨=1、合約可 >1）

損益公式：
- 現貨做多: (現價 - 成本) × 數量
- 合約做多: (現價 - 成本) × 數量 × 槓桿
- 合約做空: (成本 - 現價) × 數量 × 槓桿

加權平均成本法：同 symbol + instrument_type + direction 視為同一持倉。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple, Union

import psycopg2
from psycopg2.extras import Json

from core.database.base import DatabaseBase, DatabaseError

logger = logging.getLogger(__name__)

VALID_MARKETS = {
    "crypto", "tw_stock", "us_stock", "hk_stock",
    "jp_stock", "kr_stock", "cn_stock", "forex", "commodity",
}
VALID_INSTRUMENTS = {"spot", "futures", "margin"}
VALID_DIRECTIONS = {"long", "short"}
VALID_SOURCES = {"chat", "manual", "import"}

# PnL 數量單位換算：台股 quantity 記「張」（設計文件 §2.2.2，1張=1000股）、
# price 記「每股」→ 損益公式須 ×1000 才是真實金額（設計範例：2張@912、
# 現價918 → +NT$12,000 = (918-912)×2×1000）。其餘市場數量單位與價格單位
# 一致（幣量/股數），乘 1。
_PNL_UNIT_MULT = {"tw_stock": Decimal("1000")}

# ── 版本史（c037）──────────────────────────────────────────────────────
# 修訂快照涵蓋欄位（＝restore 還原範圍）。id/user_id 與時間戳為詮釋資料，
# 不參與還原；traded_at 屬歷史狀態，隨快照還原。
# ⚠️ restore_entry 的 SET 欄位順序必須與此 tuple 完全一致。
REVISION_SNAPSHOT_FIELDS = (
    "entry_type", "symbol", "market", "instrument_type", "direction", "side",
    "leverage", "quantity", "price", "currency", "fee", "fee_currency",
    "category", "base_currency", "converted_amount", "exchange_rate",
    "rate_source", "traded_at", "source", "note",
)


def _snapshot_of(row: Dict[str, Any]) -> Dict[str, Any]:
    """列 → JSON-safe 快照（Decimal→float、datetime→ISO）。"""
    snap: Dict[str, Any] = {}
    for f in REVISION_SNAPSHOT_FIELDS:
        v = row.get(f)
        if isinstance(v, datetime):
            v = v.isoformat()
        elif isinstance(v, Decimal):
            v = float(v)
        snap[f] = v
    return snap


def _diff_snapshot(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    """顯示用 diff：{field: {old, new}}，只含實際變動欄位。"""
    changed: Dict[str, Any] = {}
    for f in REVISION_SNAPSHOT_FIELDS:
        old, new = before.get(f), after.get(f)
        if old != new:
            changed[f] = {"old": old, "new": new}
    return changed


def _read_user_base_currency(user_id: str) -> Optional[str]:
    """讀取使用者設定的報表基準幣（c039，users.base_currency）。

    走 DatabaseBase.query_all——與 repo 其餘查詢同一條連線路徑（不另開
    raw connection）。完全容錯：欄位未 migrate／DB 不可用一律回 None，
    呼叫端以 TWD 兜底，帳本不得因為讀不到偏好就不能用。
    """
    try:
        rows = DatabaseBase.query_all(
            "SELECT base_currency FROM users WHERE user_id = %s", (user_id,)
        )
    except (DatabaseError, psycopg2.Error) as exc:
        logger.warning("[JournalRepo] base currency unavailable: %s", exc)
        return None
    if not rows:
        return None
    value = rows[0].get("base_currency") if hasattr(rows[0], "get") else None
    return str(value).strip().upper() if value else None


def _row_to_dict(cur, row) -> Dict[str, Any]:
    """cursor 列 → dict（以 description 欄名）。"""
    return {d.name: v for d, v in zip(cur.description, row)}


def _insert_revision(cur, entry_id: int, user_id: str, action: str,
                     snapshot: Dict[str, Any], changed: Optional[Dict[str, Any]],
                     source: str) -> None:
    """同交易寫入一筆修訂。revision_no = MAX+1（同 tx 計算，UNIQUE 兜底併發）。"""
    cur.execute(
        """INSERT INTO journal_revisions
           (entry_id, user_id, revision_no, action, changed_fields, snapshot, source)
           SELECT %s, %s, COALESCE(MAX(revision_no), 0) + 1, %s, %s, %s, %s
             FROM journal_revisions WHERE entry_id = %s""",
        (entry_id, user_id, action,
         Json(changed) if changed else None, Json(snapshot), source, entry_id),
    )


class TradeJournalRepo:
    """Per-user 統一帳本——投資/支出/收入 CRUD + PnL + 匯率凍結。"""

    # 報表基準幣可設定的範圍：只開放法幣（exchange_rate._FIAT_RATES 有靜態
    # 對照表，離線也一定換算得出）。加密貨幣當基準幣會讓 X→BTC 這類反向
    # 查詢在遠端 API 沒有該幣對時整片失敗，不是使用者該承受的風險。
    VALID_BASE_CURRENCIES = frozenset({"TWD", "USD", "HKD", "JPY", "CNY", "EUR"})

    def __init__(self, user_id: str, base_currency: Optional[str] = None):
        self.user_id = user_id
        self._base_currency = base_currency.upper() if base_currency else None

    @property
    def base_currency(self) -> str:
        """報表基準幣（c039，per-user；未設定＝TWD）。

        延遲讀取：repo 的建構可能發生在 event loop 上（API 端點本體），
        真正的 DB 存取都在 run_sync 的 executor 執行緒裡——查設定必須跟著
        延到第一次實際使用時，否則就是 async 裡的同步 I/O（AGENTS.md 禁）。
        讀失敗一律 TWD 兜底：帳本不得因為讀不到偏好就不能用。
        """
        if self._base_currency is None:
            self._base_currency = (
                _read_user_base_currency(self.user_id) or "TWD"
            ).upper()
        return self._base_currency

    # ── CRUD ──────────────────────────────────────────────────────────

    def add_entry(
        self,
        entry_type: str = "trade",
        symbol: str = "",
        market: str = "",
        side: str = "buy",
        quantity: float = 0,
        price: float = 0,
        currency: str = "USD",
        fee: float = 0,
        fee_currency: str = "",
        instrument_type: str = "spot",
        direction: str = "long",
        leverage: float = 1,
        category: str = "",
        base_currency: str = "",
        converted_amount: Optional[float] = None,
        exchange_rate: Optional[float] = None,
        rate_source: str = "auto",
        traded_at: Optional[datetime] = None,
        source: str = "chat",
        note: str = "",
        chain: Optional[str] = None,
        tx_hash: Optional[str] = None,
    ) -> Dict[str, Any]:
        """記錄一筆條目（trade/expense/income）。自動抓匯率並凍結。

        ``chain``／``tx_hash``：鏈上同步進來的條目帶來源（c047），同一筆轉帳重跑
        會撞唯一索引 → 回 ``{"ok": False, "duplicate": True}``。
        """
        entry_type = entry_type.strip().lower()
        if entry_type not in ("trade", "expense", "income"):
            return {"ok": False, "error": "entry_type 須為 trade/expense/income"}

        # UX 第二輪：trade 的 category 是冗餘維度——統一存 investment
        # （表單不選、chips 不列；顯示層以 legacy 標籤呈現）
        if entry_type == "trade" and not (category or "").strip():
            category = "investment"
        # 表單只填代號不選市場：空市場的 trade 以前被存成 cash，持倉拿不到現價、
        # 也沒有財報事件（2026-09-12）。依代號推市場，推不出來才維持舊行為。
        if entry_type == "trade" and not (market or "").strip():
            from core.markets import infer_market

            market = infer_market(symbol) or market

        # 支出/收入：金額放在 price 欄位（統一 quantity=1）
        if entry_type in ("expense", "income"):
            if not symbol:
                symbol = currency  # 支出以幣別為 symbol
            if not market:
                market = "cash"
            if quantity <= 0:
                quantity = 1
            if price <= 0:
                return {"ok": False, "error": "金額必須 > 0"}
            instrument_type = "spot"
            direction = "long"
            leverage = 1
            if not category:
                from core.tools.crypto_modules.exchange_rate import infer_category
                category = infer_category(note or symbol, entry_type)

        # trade 走原有邏輯
        result = self._validate_and_insert(
            symbol=symbol, market=market, side=side,
            quantity=quantity, price=price, currency=currency,
            fee=fee, fee_currency=fee_currency,
            instrument_type=instrument_type, direction=direction,
            leverage=leverage, entry_type=entry_type, category=category,
            base_currency=base_currency or self.base_currency,
            converted_amount=converted_amount, exchange_rate=exchange_rate,
            rate_source=rate_source, traded_at=traded_at,
            source=source, note=note, chain=chain, tx_hash=tx_hash,
        )
        return result

    def has_onchain_entry(self, chain: str, tx_hash: str, symbol: str, side: str) -> bool:
        """同一筆鏈上轉帳是否已記過（含使用者已刪除的——刪掉就不該再回來）。"""
        row = DatabaseBase.query_one(
            """SELECT 1 FROM trade_journal
               WHERE user_id = %s AND chain = %s AND tx_hash = %s
                 AND symbol = %s AND side = %s
               LIMIT 1""",
            (self.user_id, chain, tx_hash, (symbol or "").upper(), side),
        )
        return row is not None

    def add_trade(self, **kwargs) -> Dict[str, Any]:
        """向後相容——原 record_trade 呼叫端。"""
        kwargs.setdefault("entry_type", "trade")
        kwargs.setdefault("category", "investment")
        return self.add_entry(**kwargs)

    def _validate_and_insert(
        self, symbol, market, side, quantity, price, currency,
        fee, fee_currency, instrument_type, direction, leverage,
        entry_type, category, base_currency, converted_amount,
        exchange_rate, rate_source, traded_at, source, note,
        chain=None, tx_hash=None,
    ) -> Dict[str, Any]:
        """驗證＋匯率凍結＋寫入。"""
        symbol = (symbol or "").strip().upper()
        market = (market or "cash").strip().lower()
        instrument_type = (instrument_type or "spot").strip().lower()
        direction = (direction or "long").strip().lower()
        source = (source or "chat").strip().lower()
        side = (side or "buy").strip().lower()
        category = (category or "other").strip().lower()
        base_currency = (base_currency or self.base_currency).upper()

        if not symbol:
            return {"ok": False, "error": "symbol 不可為空"}
        if market not in VALID_MARKETS and market != "cash":
            return {"ok": False, "error": f"market 須為 {VALID_MARKETS} 或 cash"}
        if quantity <= 0:
            return {"ok": False, "error": "quantity 必須 > 0"}
        if price <= 0:
            return {"ok": False, "error": "price/金額 必須 > 0"}
        if leverage < 1:
            return {"ok": False, "error": "leverage 必須 >= 1"}

        # 現貨恆 long
        if instrument_type == "spot":
            direction = "long"
            leverage = 1

        # 匯率：caller 自填優先（使用者輸入／提案時凍結），其次自動抓。
        # 換算額一律以「實際存下來的那個匯率」計算——原實作在 caller 只給
        # exchange_rate、沒給 converted_amount 時，會拿 auto_rate 算換算額卻
        # 存 caller 的匯率（自相矛盾的凍結值）；匯率服務掛掉時更整段跳過，
        # 自填匯率連換算額都算不出來（2026-08-27 自填匯率補齊）。
        if exchange_rate is not None:
            if float(exchange_rate) <= 0:
                return {"ok": False, "error": "匯率必須 > 0"}
            exchange_rate = float(exchange_rate)
            if converted_amount is None:
                converted_amount = price * quantity * exchange_rate
            # caller 沒特別指定來源＝使用者自填，標記 manual 與自動抓的區分開
            if rate_source == "auto":
                rate_source = "manual"
        elif converted_amount is None or exchange_rate is None:
            from core.tools.crypto_modules.exchange_rate import get_exchange_rate

            auto_rate = get_exchange_rate(currency, base_currency)
            if auto_rate:
                exchange_rate = auto_rate
                if converted_amount is None:
                    converted_amount = price * quantity * auto_rate

        try:
            ts = traded_at or datetime.now(timezone.utc)
            # 寫入必須走 transaction()（會 commit）——query_all 的 context manager
            # 只 close 不 commit，INSERT 會在連線關閉時 rollback：表象是回傳 id
            # 成功、實際資料消失（2026-08-22 全面盤點抓到：記帳寫入自 c032
            # 上線以來從未持久化，REST POST/chat 核准全靜默失敗）。
            from core.database.base import transaction

            with transaction() as conn:
                with conn.cursor() as c:
                    c.execute(
                        """INSERT INTO trade_journal
                        (user_id, symbol, market, instrument_type, direction, leverage,
                         quantity, price, currency, fee, fee_currency,
                         entry_type, category, base_currency,
                         converted_amount, exchange_rate, rate_source,
                         traded_at, source, note, side, chain, tx_hash)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        RETURNING *""",
                        (
                            self.user_id, symbol, market, instrument_type, direction,
                            leverage, quantity, price, currency, fee, fee_currency,
                            entry_type, category, base_currency,
                            converted_amount, exchange_rate, rate_source,
                            ts, source, note, side, chain, tx_hash,
                        ),
                    )
                    row = c.fetchone()
                    if row:
                        # c037 版本史：同 tx 寫 create 修訂（row[0] = id）
                        inserted = _row_to_dict(c, row)
                        _insert_revision(
                            c, row[0], self.user_id, "create",
                            _snapshot_of(inserted), None, source,
                        )
            return {"ok": True, "id": row[0] if row else None}
        except psycopg2.errors.UniqueViolation:
            # c047：同一筆鏈上轉帳重跑（唯一部分索引）——不是錯誤，呼叫端算 duplicate
            return {"ok": False, "duplicate": True, "error": "duplicate onchain entry"}
        except (DatabaseError, psycopg2.Error) as exc:
            # 細節只進 log——內部錯誤字串（schema/SQL 片段）不得外洩到 API 回應
            logger.warning("[JournalRepo] add_entry failed: %s", exc)
            return {"ok": False, "error": "database error"}

    def delete_trade(self, trade_id: int, *, source: str = "manual") -> Dict[str, Any]:
        """Soft delete 一筆條目（標記 deleted_at）＋同交易寫入 delete 修訂。

        SELECT 移入 tx：快照與標記讀到同一狀態。0 rows（不存在／他人帳目
        ／已刪）→ not found，不回假成功。
        """
        try:
            from core.database.base import transaction

            with transaction() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT * FROM trade_journal
                           WHERE id = %s AND user_id = %s AND deleted_at IS NULL""",
                        (trade_id, self.user_id),
                    )
                    row = cur.fetchone()
                    if row is None:
                        raise LookupError("entry not found")
                    before = _row_to_dict(cur, row)
                    _insert_revision(cur, trade_id, self.user_id, "delete",
                                     _snapshot_of(before), None, source)
                    cur.execute(
                        """UPDATE trade_journal
                           SET deleted_at = NOW(), updated_at = NOW()
                           WHERE id = %s AND user_id = %s AND deleted_at IS NULL""",
                        (trade_id, self.user_id),
                    )
                    if not cur.rowcount:
                        raise LookupError("entry not found")
            return {"ok": True}
        except LookupError:
            return {"ok": False, "error": "entry not found"}
        except psycopg2.errors.UniqueViolation:
            # 兩寫者同 entry 競態：revision_no MAX+1 撞 UNIQUE——rollback 已
            # 由 transaction() 完成，回可重試語義而非 500/資料庫錯誤
            logger.info("[JournalRepo] delete_trade concurrent revision conflict id=%s", trade_id)
            return {"ok": False, "error": "conflict"}
        except DatabaseError as exc:
            logger.warning("[JournalRepo] delete_trade failed: %s", exc)
            return {"ok": False, "error": "database error"}

    def update_entry(
        self,
        entry_id: int,
        *,
        amount: Optional[float] = None,
        currency: Optional[str] = None,
        category: Optional[str] = None,
        note: Optional[str] = None,
        exchange_rate: Optional[float] = None,
        source: str = "manual",
    ) -> Dict[str, Any]:
        """修改一筆條目。改金額/幣別以當下匯率重算並覆蓋凍結值；覆蓋前的
        值保存於 journal_revisions（c037）。匯率 httpx 呼叫必須在開啟 tx
        之前——不得佔住 DB 連線。

        金額必須 > 0；**改幣別**但匯率服務暫時抓不到時 fail-closed
        （回 rate unavailable、不寫入）——帶著舊幣別的匯率寫新幣別會留下
        自相矛盾的凍結值（2026-08-26 review W2）。只改金額不重抓匯率：
        匯率凍結於記錄當下（成熟記帳/會計產品一致做法——歷史交易換算
        用當時匯率，不因今日行情變動），沿用凍結值重算換算額即自洽，
        匯率服務中斷也不該擋下改金額（2026-08-27 追修）。舊資料無凍結
        匯率者仍需補抓。傳入 exchange_rate＝使用者自填匯率：優先於一切
        自動查詢（匯率服務抓不到時的出口，也供使用者填自己的實際成交
        匯率），標記 rate_source=manual。無實際欄位變動時不寫修訂
        （避免空編輯產生幽靈「已編輯」時間線條目）。
        """
        if amount is not None and not (float(amount) > 0):
            return {"ok": False, "error": "金額必須 > 0"}
        try:
            rows = DatabaseBase.query_all(
                """SELECT * FROM trade_journal
                   WHERE id = %s AND user_id = %s AND deleted_at IS NULL""",
                (entry_id, self.user_id),
            )
            if not rows:
                return {"ok": False, "error": "entry not found"}
            row = rows[0]

            new_amount = float(amount) if amount is not None else float(row["price"])
            new_currency = (currency or row["currency"]).strip().upper()
            new_category = (category or row.get("category") or "other").strip().lower()
            new_note = note if note is not None else (row.get("note") or "")

            # 匯率重算（tx 外）：httpx 最多 5s，不得在 transaction 內
            amount_changed = new_amount != float(row["price"])
            currency_changed = new_currency != str(row["currency"]).upper()
            converted = row.get("converted_amount")
            rate = row.get("exchange_rate")
            rate_src = row.get("rate_source") or "auto"
            frozen_rate = float(rate) if rate is not None else None
            manual_rate = float(exchange_rate) if exchange_rate is not None else None
            if manual_rate is not None and manual_rate <= 0:
                return {"ok": False, "error": "匯率必須 > 0"}
            if manual_rate is not None:
                # 使用者自填匯率優先於一切自動查詢——這是匯率服務抓不到時
                # 的出口，也讓使用者能填自己的實際成交匯率
                rate = manual_rate
                rate_src = "manual"
            # 否則：只有幣別真的變了（或舊資料根本沒有凍結匯率）才需要新匯率——
            # 改金額沿用凍結匯率換算即自洽，不必因匯率服務中斷擋下編輯
            elif currency_changed or (amount_changed and not frozen_rate):
                from core.tools.crypto_modules.exchange_rate import get_exchange_rate

                fresh = get_exchange_rate(new_currency, self.base_currency)
                if not fresh:
                    # fail-closed：靜態法幣 fallback 抓不到（加密/跨法幣且
                    # 遠端 API 掛掉）——不寫入，避免新幣別配舊匯率的矛盾凍結值。
                    # 使用者可改填 exchange_rate 自行指定匯率繞過。
                    logger.info(
                        "[JournalRepo] update_entry rate unavailable "
                        "%s->%s id=%s", row["currency"], new_currency, entry_id,
                    )
                    return {"ok": False, "error": "rate unavailable"}
                rate = fresh
                rate_src = "auto"
            if amount_changed or currency_changed or manual_rate is not None:
                converted = new_amount * float(row["quantity"]) * float(rate)

            before_snap = _snapshot_of(row)
            after_snap = dict(before_snap)
            after_snap.update({
                "price": new_amount,
                "currency": new_currency,
                "category": new_category,
                "note": new_note,
                "converted_amount": float(converted) if converted is not None else None,
                "exchange_rate": float(rate) if rate is not None else None,
                "rate_source": rate_src,
            })
            changed = _diff_snapshot(before_snap, after_snap)

            from core.database.base import transaction

            with transaction() as conn:
                with conn.cursor() as cur:
                    if changed:
                        # 無變動（空 body / 同值）不寫修訂——時間線只記實際編輯
                        _insert_revision(cur, entry_id, self.user_id, "update",
                                         after_snap, changed, source)
                    cur.execute(
                        """UPDATE trade_journal
                           SET price = %s, currency = %s, category = %s, note = %s,
                               converted_amount = %s, exchange_rate = %s,
                               rate_source = %s, updated_at = NOW()
                           WHERE id = %s AND user_id = %s AND deleted_at IS NULL""",
                        (new_amount, new_currency, new_category, new_note,
                         converted, rate, rate_src, entry_id, self.user_id),
                    )
                    if not cur.rowcount:
                        # SELECT 後、UPDATE 前被刪除的競態——raise 觸發
                        # transaction() rollback（修訂一併回滾），不回假成功
                        raise LookupError("entry not found")
            return {"ok": True, "id": entry_id}
        except LookupError:
            return {"ok": False, "error": "entry not found"}
        except psycopg2.errors.UniqueViolation:
            logger.info("[JournalRepo] update_entry concurrent revision conflict id=%s", entry_id)
            return {"ok": False, "error": "conflict"}
        except DatabaseError as exc:
            logger.warning("[JournalRepo] update_entry failed: %s", exc)
            return {"ok": False, "error": "database error"}

    def restore_entry(
        self, entry_id: int, revision_no: Optional[int] = None
    ) -> Dict[str, Any]:
        """還原條目到指定修訂（快照逐欄套回，含凍結匯率——不重抓）。

        revision_no 省略＝刪除前狀態：優先取最新一筆 delete 修訂的快照
        （delete 修訂記錄的就是刪除瞬間的完整列狀態）；無 delete 修訂
        （c037 前舊資料）再退 create/update 最新值、最後退列上現值。
        先前取「最新 create/update」在刪除前發生過時間線還原時會拿到
        更舊的狀態（2026-08-26 review W1）。可還原已刪條目。
        歷史 append-only：還原寫一筆 restore 修訂，不改寫既有修訂。
        SET 欄位順序與 REVISION_SNAPSHOT_FIELDS 完全一致。
        """
        try:
            from core.database.base import transaction

            with transaction() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT * FROM trade_journal
                           WHERE id = %s AND user_id = %s""",
                        (entry_id, self.user_id),
                    )
                    row = cur.fetchone()
                    if row is None:
                        raise LookupError("entry not found")
                    current = _row_to_dict(cur, row)

                    if revision_no is None:
                        # 一鍵救回：刪除當下快照最精確（含刪除前曾 restore 的狀態）
                        cur.execute(
                            """SELECT snapshot FROM journal_revisions
                               WHERE entry_id = %s AND user_id = %s
                                 AND action = 'delete'
                               ORDER BY revision_no DESC LIMIT 1""",
                            (entry_id, self.user_id),
                        )
                        rev = cur.fetchone()
                        if rev is None:
                            cur.execute(
                                """SELECT snapshot FROM journal_revisions
                                   WHERE entry_id = %s AND user_id = %s
                                     AND action IN ('create', 'update')
                                   ORDER BY revision_no DESC LIMIT 1""",
                                (entry_id, self.user_id),
                            )
                            rev = cur.fetchone()
                        snap = dict(rev[0]) if rev else _snapshot_of(current)
                    else:
                        cur.execute(
                            """SELECT snapshot FROM journal_revisions
                               WHERE entry_id = %s AND user_id = %s
                                 AND revision_no = %s""",
                            (entry_id, self.user_id, revision_no),
                        )
                        rev = cur.fetchone()
                        if rev is None:
                            raise LookupError("revision not found")
                        snap = dict(rev[0])

                    cur.execute(
                        """UPDATE trade_journal
                           SET entry_type = %s, symbol = %s, market = %s,
                               instrument_type = %s, direction = %s, side = %s,
                               leverage = %s, quantity = %s, price = %s,
                               currency = %s, fee = %s, fee_currency = %s,
                               category = %s, base_currency = %s,
                               converted_amount = %s, exchange_rate = %s,
                               rate_source = %s, traded_at = %s, source = %s,
                               note = %s, deleted_at = NULL, updated_at = NOW()
                           WHERE id = %s AND user_id = %s""",
                        (snap.get("entry_type"), snap.get("symbol"),
                         snap.get("market"), snap.get("instrument_type"),
                         snap.get("direction"), snap.get("side"),
                         snap.get("leverage"), snap.get("quantity"),
                         snap.get("price"), snap.get("currency"),
                         snap.get("fee"), snap.get("fee_currency"),
                         snap.get("category"), snap.get("base_currency"),
                         snap.get("converted_amount"), snap.get("exchange_rate"),
                         snap.get("rate_source"), snap.get("traded_at"),
                         snap.get("source"), snap.get("note"),
                         entry_id, self.user_id),
                    )
                    if not cur.rowcount:
                        raise LookupError("entry not found")

                    before_snap = _snapshot_of(current)
                    after_snap = dict(before_snap)
                    after_snap.update(
                        {f: snap.get(f) for f in REVISION_SNAPSHOT_FIELDS}
                    )
                    changed = _diff_snapshot(before_snap, after_snap)
                    _insert_revision(cur, entry_id, self.user_id, "restore",
                                     after_snap, changed, "restore")
            return {"ok": True, "id": entry_id, "restored_fields": len(changed)}
        except LookupError as exc:
            return {"ok": False, "error": str(exc)}
        except psycopg2.errors.UniqueViolation:
            logger.info("[JournalRepo] restore_entry concurrent revision conflict id=%s", entry_id)
            return {"ok": False, "error": "conflict"}
        except DatabaseError as exc:
            logger.warning("[JournalRepo] restore_entry failed: %s", exc)
            return {"ok": False, "error": "database error"}

    def list_revisions(self, entry_id: int, limit: int = 100) -> List[Dict[str, Any]]:
        """條目修訂時間線（新到舊）。刻意不過濾已刪條目——回收筒內可查歷史。"""
        rows = DatabaseBase.query_all(
            """SELECT revision_no, action, changed_fields, source, created_at
               FROM journal_revisions
               WHERE entry_id = %s AND user_id = %s
               ORDER BY revision_no DESC
               LIMIT %s""",
            (entry_id, self.user_id, limit),
        )
        for r in rows:
            created = r.get("created_at")
            if created:
                r["created_at"] = created.isoformat()
        return rows

    def list_deleted(self, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        """回收筒：已 soft-delete 條目（依刪除時間新到舊）。"""
        rows = DatabaseBase.query_all(
            """SELECT * FROM trade_journal
               WHERE user_id = %s AND deleted_at IS NOT NULL
               ORDER BY deleted_at DESC
               LIMIT %s OFFSET %s""",
            (self.user_id, limit, offset),
        )
        for r in rows:
            for k in ("traded_at", "deleted_at", "created_at", "updated_at"):
                v = r.get(k)
                if v:
                    r[k] = v.isoformat()
        return rows

    def list_trades(
        self,
        symbol: Optional[str] = None,
        market: Optional[str] = None,
        entry_type: Optional[Union[str, List[str]]] = None,
        category: Optional[str] = None,
        search: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """列出條目（依時間倒序）。支援類別/類型/關鍵字/日期範圍篩選。

        entry_type 支援多值（收支視圖一次取 expense+income）；相容單值
        str（agent query_ledger 呼叫路徑）。
        DB 錯誤直接拋出（DatabaseError）——先前吞成空列表，斷線時
        使用者看到的是「沒有記錄」而非錯誤（2026-08-24 review）。
        """
        conditions, params = self._entry_filters(
            symbol=symbol,
            market=market,
            entry_type=entry_type,
            category=category,
            search=search,
            date_from=date_from,
            date_to=date_to,
        )
        params.extend([limit, offset])
        return DatabaseBase.query_all(
            f"""SELECT id, symbol, market, instrument_type, direction, leverage, side,
                       quantity, price, currency, fee, fee_currency,
                       entry_type, category, base_currency,
                       converted_amount, exchange_rate, rate_source,
                       traded_at, source, note, chain, tx_hash
                FROM trade_journal
                WHERE {' AND '.join(conditions)}
                ORDER BY traded_at DESC
                LIMIT %s OFFSET %s""",
            tuple(params),
        ) or []

    def get_entry_totals(
        self,
        symbol: Optional[str] = None,
        market: Optional[str] = None,
        entry_type: Optional[Union[str, List[str]]] = None,
        category: Optional[str] = None,
        search: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> Dict[str, float]:
        """同 list_trades 篩選下「全部」條目的金額合計，依 entry_type 分組。

        帳本頁的總支出／總收入卡以前拿列表（limit 100）加總，超過 100 筆就少算。
        金額口徑同前端原本的加總：有換算成基準幣的值就用它，沒有才用
        price x quantity。DB 錯誤直接拋出（同 list_trades）。

        篩選參數與 list_trades 一一對應（含 market——/api/journal/entries 沒開，
        但 list_trades 的簽名有），條件都由 _entry_filters 產生；新增篩選時兩邊一起加。
        """
        conditions, params = self._entry_filters(
            symbol=symbol,
            market=market,
            entry_type=entry_type,
            category=category,
            search=search,
            date_from=date_from,
            date_to=date_to,
        )
        rows = DatabaseBase.query_all(
            f"""SELECT entry_type,
                       SUM(CASE WHEN converted_amount > 0 THEN converted_amount
                                ELSE price * quantity END) AS total
                FROM trade_journal
                WHERE {' AND '.join(conditions)}
                GROUP BY entry_type""",
            tuple(params),
        ) or []
        return {r["entry_type"]: float(r["total"] or 0) for r in rows}

    def _entry_filters(
        self,
        symbol: Optional[str] = None,
        market: Optional[str] = None,
        entry_type: Optional[Union[str, List[str]]] = None,
        category: Optional[str] = None,
        search: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> Tuple[List[str], list]:
        """list_trades 與 get_entry_totals 共用的 WHERE 條件（兩邊必須一致）。"""
        conditions = ["user_id = %s", "deleted_at IS NULL"]
        params: list = [self.user_id]
        if symbol:
            conditions.append("symbol = %s")
            params.append(symbol.upper())
        if market:
            conditions.append("market = %s")
            params.append(market.lower())
        if entry_type:
            types = (
                [entry_type] if isinstance(entry_type, str) else list(entry_type)
            )
            types = [t.strip().lower() for t in types if t and t.strip()]
            if types:
                conditions.append("entry_type = ANY(%s)")
                params.append(types)
        if category:
            conditions.append("category = %s")
            params.append(category.lower())
        if search:
            conditions.append("(note ILIKE %s OR symbol ILIKE %s)")
            like = f"%{search}%"
            params.extend([like, like])
        if date_from:
            conditions.append("traded_at >= %s")
            params.append(date_from)
        if date_to:
            conditions.append("traded_at <= %s")
            params.append(date_to)
        return conditions, params

    # ── PnL 計算 ─────────────────────────────────────────────────────

    # ── 基準幣切換（c039）────────────────────────────────────────

    def rebase_to(self, new_base: str) -> Dict[str, Any]:
        """把這個使用者的帳本整體搬到新的報表基準幣。

        converted_amount / exchange_rate 都是「換算成基準幣」的派生值——
        基準幣一換它們就必須跟著換，否則彙總會把兩種幣別的數字直接相加
        （SUM(converted_amount) 不看 base_currency）。做法是乘上
        old_base→new_base 的交叉匯率：

            converted_new = converted_old x cross
            rate_new      = rate_old x cross

        事實欄位（price / currency / quantity）一律不動，
        converted = price x quantity x rate 的恆等式因此維持成立。

        兩個必須成立的性質：
        1. fail-closed：交叉匯率抓不到就一列都不寫（同 W2）。
        2. 原子：條目換算值與 users.base_currency 在同一個交易裡更新——
           否則會出現「設定說 USD、資料還停在 TWD」的錯位，所有金額都錯。

        3. compare-and-swap：users.base_currency 帶 old_base 條件更新——
           兩個併發切換請求會各自讀到同一個 old_base，沒有這個條件，後到的
           那個會搬到 0 列卻仍寫入自己的目標幣別（設定與資料錯位）。

        已刪除的條目一併搬（回收筒可還原，不能留在舊基準幣）。
        COALESCE 的預設是 'TWD'（欄位是 NOT NULL DEFAULT 'TWD'，此處純防禦：
        萬一有 NULL，它代表的是台幣時代的舊資料，不是「目前的基準幣」）。
        """
        new_base = (new_base or "").strip().upper()
        if new_base not in self.VALID_BASE_CURRENCIES:
            return {"ok": False, "error": "unsupported base currency"}
        old_base = self.base_currency
        if old_base == new_base:
            return {"ok": True, "rebased": 0, "rate": 1.0, "base_currency": new_base}

        # 交叉匯率必須在開啟 tx 之前取得——httpx 最多 5s，不得佔住 DB 連線
        from core.tools.crypto_modules.exchange_rate import get_exchange_rate

        cross = get_exchange_rate(old_base, new_base)
        if cross:
            # float x numeric 會被 Postgres 提升成 double precision 再塞回
            # numeric(20,8)——金額欄位改以 Decimal 走純 numeric 運算
            cross = Decimal(str(cross))
        if not cross:
            logger.info(
                "[JournalRepo] rebase rate unavailable %s->%s user=%s",
                old_base, new_base, self.user_id,
            )
            return {"ok": False, "error": "rate unavailable"}

        try:
            from core.database.base import transaction

            with transaction() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE trade_journal
                           SET converted_amount = converted_amount * %s,
                               exchange_rate = exchange_rate * %s,
                               base_currency = %s,
                               updated_at = NOW()
                           WHERE user_id = %s
                             AND COALESCE(base_currency, 'TWD') = %s""",
                        (cross, cross, new_base, self.user_id, old_base),
                    )
                    rebased = cur.rowcount or 0
                    # 殘留在第三種基準幣的條目（正常情況不存在）——交叉匯率
                    # 不適用它們，寧可回報也不要拿錯的匯率去乘
                    cur.execute(
                        """SELECT COUNT(*) FROM trade_journal
                           WHERE user_id = %s
                             AND COALESCE(base_currency, 'TWD') <> %s""",
                        (self.user_id, new_base),
                    )
                    stale = int((cur.fetchone() or [0])[0])
                    cur.execute(
                        """UPDATE users SET base_currency = %s
                           WHERE user_id = %s
                             AND COALESCE(base_currency, 'TWD') = %s""",
                        (new_base, self.user_id, old_base),
                    )
                    if not cur.rowcount:
                        # 併發切換：另一個請求已經改掉基準幣（或 users 列不存在）
                        # ——raise 觸發 rollback，條目的搬移一併回滾，不留錯位
                        raise LookupError("base currency changed concurrently")
            self._base_currency = new_base
            logger.info(
                "[JournalRepo] rebased %s->%s user=%s rows=%s rate=%s stale=%s",
                old_base, new_base, self.user_id, rebased, cross, stale,
            )
            return {
                "ok": True,
                "rebased": rebased,
                "rate": float(cross),
                "base_currency": new_base,
                "stale": stale,
            }
        except LookupError:
            logger.info(
                "[JournalRepo] rebase conflict user=%s %s->%s",
                self.user_id, old_base, new_base,
            )
            return {"ok": False, "error": "conflict"}
        except (DatabaseError, psycopg2.Error) as exc:
            logger.warning("[JournalRepo] rebase_to failed: %s", exc)
            return {"ok": False, "error": "database error"}

    def get_category_summary(
        self,
        entry_type: Optional[Union[str, List[str]]] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """依類別彙總（Dashboard 圓餅圖/長條圖用）。DB 錯誤直接拋出（同 list_trades）。

        entry_type 支援多值（收支視圖一次取 expense+income；UX 第二輪）。

        base_currency 一併分組並回傳（c039）：converted_amount 是換算成
        基準幣的值，不分組就會把兩種基準幣的金額直接相加。正常情況全部
        條目同一個基準幣（切換時 rebase_to 整批搬移），分組不會多出列；
        萬一出現混合狀態，這裡會讓它現形而不是無聲加總。
        """
        conditions = ["user_id = %s", "deleted_at IS NULL"]
        params: list = [self.user_id]
        if entry_type:
            types = (
                [entry_type] if isinstance(entry_type, str) else list(entry_type)
            )
            types = [t.strip().lower() for t in types if t and t.strip()]
            if types:
                conditions.append("entry_type = ANY(%s)")
                params.append(types)
        if date_from:
            conditions.append("traded_at >= %s")
            params.append(date_from)
        if date_to:
            conditions.append("traded_at <= %s")
            params.append(date_to)
        return DatabaseBase.query_all(
            f"""SELECT category,
                       COUNT(*) as count,
                       SUM(COALESCE(converted_amount, price * quantity)) as total,
                       currency,
                       COALESCE(base_currency, 'TWD') as base_currency
                FROM trade_journal
                WHERE {' AND '.join(conditions)}
                GROUP BY category, currency, COALESCE(base_currency, 'TWD')
                ORDER BY total DESC""",
            tuple(params),
        ) or []

    def get_positions(
        self,
        price_lookup: Optional[Dict[str, float]] = None,
    ) -> List[Dict[str, Any]]:
        """計算目前持倉＋損益（加權平均成本法）。

        Args:
            price_lookup: {symbol: current_price}——外部傳入現價
                         （agent 工具會先呼叫市場工具取得）。缺的 symbol
                         不算未實現損益（unrealized_pnl=None）。
        """
        # 2026-08-26 實測：list_trades 泛化支援 expense/income 後，這裡
        # 漏補 trade 過濾 → 支出被算成「持倉」（symbol=TWD、均價=平均支出）。
        trades = self.list_trades(entry_type="trade", limit=10000)
        if not trades:
            return []

        # list_trades 回 DESC（新→舊）；持倉累加必須按時間升冪——
        # 賣出若先於買入處理會在空倉上減成負數被歸零丟棄
        # （2026-08-22 盤點：買0.1賣0.05 算成 0.1 而非 0.05）。
        trades = sorted(trades, key=lambda t: (t.get("traded_at"), t.get("id", 0)))

        positions: Dict[str, Dict[str, Any]] = {}

        for t in trades:
            # 持倉 key = symbol + instrument + direction（BTC 現貨多和
            # BTC 合約多是不同持倉；做多和做空也是不同持倉）
            key = f"{t['symbol']}|{t['instrument_type']}|{t['direction']}"
            pos = positions.setdefault(key, {
                "symbol": t["symbol"],
                "market": t["market"],
                "instrument_type": t["instrument_type"],
                "direction": t["direction"],
                "leverage": float(t["leverage"]),
                "currency": t["currency"],
                "quantity": Decimal("0"),
                "avg_cost": Decimal("0"),
                "total_fee": Decimal("0"),
                "realized_pnl": Decimal("0"),
                "trade_count": 0,
                "first_traded_at": None,
            })

            qty = Decimal(str(t["quantity"]))
            price = Decimal(str(t["price"]))
            fee = Decimal(str(t["fee"] or 0))
            lev = Decimal(str(t["leverage"]))
            unit_mult = _PNL_UNIT_MULT.get(t["market"], Decimal("1"))
            pos["total_fee"] += fee
            pos["trade_count"] += 1

            if t["direction"] == "long":
                # 做多：buy 加倉、sell 減倉
                side = _infer_side(t, qty, price, pos)
                if side == "buy":
                    new_qty = pos["quantity"] + qty
                    if new_qty > 0:
                        pos["avg_cost"] = (
                            (pos["quantity"] * pos["avg_cost"] + qty * price)
                            / new_qty
                        )
                    pos["quantity"] = new_qty
                    _touch_first_traded(pos, t)
                else:  # sell
                    # 已實現損益只算手上有的量：賣超過持倉（帳本漏記買入）時，
                    # 超出的部分沒有成本可比，照舊在下面歸零丟棄、不開反向倉。
                    # 此前按整筆賣量算——空倉時 avg_cost=0，整筆賣額變成憑空獲利。
                    closed = min(qty, pos["quantity"])
                    pnl = (price - pos["avg_cost"]) * closed * lev * unit_mult
                    pos["realized_pnl"] += pnl
                    pos["quantity"] -= qty
            else:
                # 做空：sell 開倉、buy 平倉
                side = _infer_side_short(t, qty, price, pos)
                if side == "sell":  # 開空倉
                    new_qty = pos["quantity"] + qty
                    if new_qty > 0:
                        pos["avg_cost"] = (
                            (pos["quantity"] * pos["avg_cost"] + qty * price)
                            / new_qty
                        )
                    pos["quantity"] = new_qty
                    _touch_first_traded(pos, t)
                else:  # buy 平倉（同上：只算持倉內的量）
                    closed = min(qty, pos["quantity"])
                    pnl = (pos["avg_cost"] - price) * closed * lev * unit_mult
                    pos["realized_pnl"] += pnl
                    pos["quantity"] -= qty

            # 清倉歸零
            if pos["quantity"] <= 0:
                pos["quantity"] = Decimal("0")
                pos["avg_cost"] = Decimal("0")
                pos["first_traded_at"] = None  # 再開倉時重新起算

        # 加上未實現損益
        result = []
        for pos in positions.values():
            if pos["quantity"] > 0 and price_lookup:
                current = price_lookup.get(pos["symbol"])
                if current is not None:
                    if pos["direction"] == "long":
                        diff = Decimal(str(current)) - pos["avg_cost"]
                    else:
                        diff = pos["avg_cost"] - Decimal(str(current))
                    unit_mult = _PNL_UNIT_MULT.get(pos["market"], Decimal("1"))
                    pos["unrealized_pnl"] = (
                        diff * pos["quantity"] * Decimal(str(pos["leverage"])) * unit_mult
                    )
                    pos["current_price"] = current
            result.append(pos)

        return sorted(result, key=lambda p: p["symbol"])


def _touch_first_traded(pos: Dict[str, Any], trade: Dict[str, Any]) -> None:
    """更新持倉最早開倉時間（清倉歸零後重新起算；持有期間顯示用）。"""
    ts = trade.get("traded_at")
    if ts and (pos.get("first_traded_at") is None or ts < pos["first_traded_at"]):
        pos["first_traded_at"] = ts


def _infer_side(
    trade: Dict[str, Any], qty: float, price: float, pos: Dict[str, Any]
) -> str:
    """推斷這筆是 buy 還是 sell。

    c034 起 trade_journal 有 side 欄（buy/sell）；舊資料/缺欄 fallback 'buy'。
    2026-08-22 修：此前表沒有 side——賣出寫入即丟失，持倉計算全當加倉
    （0.1 買＋0.05 賣算成 0.15 而非淨倉 0.05）。
    """
    side = (trade.get("side") or "buy").strip().lower()
    return side if side in ("buy", "sell") else "buy"


def _infer_side_short(
    trade: Dict, qty: Decimal, price: Decimal, pos: Dict[str, Any]
) -> str:
    """做空持倉的 side 推斷——sell 開空倉、buy 平倉。

    2026-08-24 review：此前讀 ``trade.get("_side", "sell")``——``_side``
    鍵從未被寫入，side 恆為 sell → buy 平倉分支是死碼，做空倉只會
    翻倍不會平掉（short 1 @3000 再 cover 1 @2900 得 qty=2、PnL=0）。
    c034 起直接讀 side 欄；舊資料/缺欄 fallback sell（開倉）保持舊行為。
    """
    side = (trade.get("side") or "").strip().lower()
    return side if side in ("buy", "sell") else "sell"


def get_journal_repo(user_id: str) -> TradeJournalRepo:
    """每次呼叫建新 repo（stateless，無共用狀態）。

    2026-08-24 review：原為 module-level singleton 的 check-then-act——
    sync agent 工具跑在 executor threads，兩執行緒交錯下 user A 可能
    拿到 user B 的 repo（跨用戶帳本讀寫）。repo 無狀態，直接建構。
    """
    return TradeJournalRepo(user_id)
