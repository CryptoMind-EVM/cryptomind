"""聊天室 AI 助理前端（web/js/chat-assistant.js）：node 跑純邏輯（SSE、送出內容、錯誤、渲染）。"""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="node 不可用")
@pytest.mark.parametrize("script", ["tests/js/chat_assistant.mjs", "tests/js/dm_actions.mjs", "tests/js/ai_card.mjs"])
def test_node(script):
    out = subprocess.run(
        ["node", script], cwd=REPO, capture_output=True, text=True, encoding="utf-8", timeout=60
    )
    assert out.returncode == 0, (out.stderr or out.stdout)[-3000:]


def test_entry_points_wired():
    """好友頁（私訊聊天室只剩這一個）標題列有 ✨，打開聊天室時記未讀"""
    tab = (REPO / "web/js/components/tab-friends.js").read_text(encoding="utf-8")
    assert 'id="social-ai-btn" data-click="SocialHub.openAssistant"' in tab
    friends = (REPO / "web/js/friends.js").read_text(encoding="utf-8")
    assert "this._assistantUnread = unread > 0 ? { unread_count: unread } : null;" in friends
    groups = (REPO / "web/js/social-groups.js").read_text(encoding="utf-8")
    assert "this._assistantUnread = newest > lastRead ? { from_id: lastRead + 1 } : null;" in groups


def test_confirms_use_official_dialog_not_browser_native():
    """分享到聊天室、清空紀錄的確認框走官方樣式；不能再直接呼叫 window.confirm（只剩 confirmAction 的退路）。

    抽屜是 z-index 10000，官方確認框預設 z-[100] 會被蓋住——所以 showConfirmDialog 要支援 zIndex。
    """
    src = (REPO / "web/js/chat-assistant.js").read_text(encoding="utf-8")
    assert src.count("window.confirm(") == 1, "只允許 confirmAction 裡那一個退路"
    assert src.count("confirmAction(") >= 3  # 定義＋分享＋清空
    shell = (REPO / "web/js/ui-shell.js").read_text(encoding="utf-8")
    assert "opts.zIndex" in shell


def test_group_info_panel_discloses_assistant():
    """揭露：助理開著時群組資訊面板要告訴成員（開關關著不顯示）"""
    groups = (REPO / "web/js/social-groups.js").read_text(encoding="utf-8")
    assert "openGroupInfoPanel({ group, myId: myId(), aiNotice: assistantEnabled() })" in groups
    dialogs = (REPO / "web/js/group-chat-dialogs.js").read_text(encoding="utf-8")
    assert "if (aiNotice) body.appendChild(" in dialogs and "t('groups.aiNotice')" in dialogs


def test_disclosure_matches_what_the_server_keeps():
    """問答存伺服器（2026-10-04）：抽屜說明、群組資訊、隱私權政策寫的天數／則數要跟後端、前端常數一致，
    而且不能再寫「不會保存」"""
    import json
    import re

    from core.orm.chat_assistant_history_repo import MAX_TURNS, RETENTION

    zh = json.loads((REPO / "web/js/i18n/zh-TW.json").read_text(encoding="utf-8"))
    for text in (zh["assistant"]["privateNote"], zh["groups"]["aiNotice"]):
        assert f"{RETENTION.days} 天" in text and f"{MAX_TURNS} 則" in text, text
        assert "不會保存" not in text and "不保存" not in text, text

    js = (REPO / "web/js/chat-assistant.js").read_text(encoding="utf-8")
    assert re.search(rf"const TURNS_MAX = {MAX_TURNS};", js), "前端 TURNS_MAX 要同後端 MAX_TURNS"
    assert not re.search(r"(local|session)Storage\s*[.\[]", js), "問答存伺服器，不寫瀏覽器"

    policy = (REPO / "web/legal/privacy-policy.html").read_text(encoding="utf-8")
    section = policy.split("1.7 聊天室 AI 助理", 1)[1].split("<h3", 1)[0]
    for needle in ("提問與回答不會保存", "Questions and answers are not saved", "Вопросы и ответы не сохраняются"):
        assert needle not in section, needle
    assert f"最近 {MAX_TURNS} 則、最長 {RETENTION.days} 天" in section
    assert f"latest {MAX_TURNS} per chat, for at most {RETENTION.days} days" in section
    assert f"последние {MAX_TURNS} в каждом чате, не более {RETENTION.days} дней" in section
    assert f"最近 {MAX_TURNS} 条、最长 {RETENTION.days} 天" in section


def test_share_sends_a_card_not_composer_text():
    """分享＝送出 AI 分析卡片（2026-10-04）：不再把純文字塞進輸入框（500 字放不下、表格被壓扁）；
    三個聊天介面都用 ai-card.js 畫卡片；內容由伺服器依 turn id 取，前端只送 assistant_turn_id"""
    js = (REPO / "web/js/chat-assistant.js").read_text(encoding="utf-8")
    assert "assistant_turn_id" in js
    for gone in ("findComposer", "buildShareText", "social-msg-input", "message-input", "DEFAULT_COMPOSER_MAX"):
        assert gone not in js, f"{gone} 是舊的「放進輸入框」分享，不該回來"
    # 不再靠 markdown-it：手機私訊頁沒載它
    assert "window.md" not in js

    for path in ("web/js/friends.js", "web/js/group-chat.js", "web/js/messages.js"):
        src = (REPO / path).read_text(encoding="utf-8")
        assert "aiCardRowHtml" in src and "isAiCard(msg)" in src, f"{path} 要畫 AI 分析卡片"
    # 送出後讓畫面立刻出現新卡片（跟一般送出一樣；WebSocket 推來的同一則用 id 去重）
    assert "onShared" in (REPO / "web/js/friends.js").read_text(encoding="utf-8")
    # 選單「複製」拿轉好的純文字（卡片畫面上的表格 textContent 會黏成一坨）
    assert "el.dataset?.plain" in (REPO / "web/js/dm-message-actions.js").read_text(encoding="utf-8")


def test_privacy_policy_describes_the_card():
    """隱私政策 1.7 的描述要跟行為一致：不再寫「貼進輸入框」，改成確認後以提問者名義送出一則 AI 分析訊息"""
    policy = (REPO / "web/legal/privacy-policy.html").read_text(encoding="utf-8")
    section = policy.split("1.7 聊天室 AI 助理", 1)[1].split("<h3", 1)[0]
    for stale in ("貼進輸入框", "粘贴到输入框", "put it in the message box", "вставить ли его в поле ввода"):
        assert stale not in section, stale
    for needle in ("「AI 分析」訊息", "“AI 分析”消息", "“AI analysis” message", "«Анализ ИИ»"):
        assert needle in section, needle
