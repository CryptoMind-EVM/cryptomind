"""已讀之後側欄「社群」徽章要跟著少（2026-10-06：看完群組訊息，側欄還掛著 1，重整才消）。

NavBadges 只在 notificationsUpdated／登入／切回視窗時重抓，所以每條「標已讀」成功路徑
都要自己叫 window.NavBadges?.schedule()，不能只重載聊天列表。
"""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

# (檔案, 該路徑的呼叫特徵)。每條都是「改變 server 端會進側欄數字的狀態」的使用者動作，
# 成功後要自己叫 NavBadges.schedule()——這些路徑不一定有 notificationsUpdated 補位。
BADGE_AFFECTING_PATHS = [
    # 標已讀：群組、私訊（輸入/收訊時標）、私訊（開對話時標）
    ("web/js/social-groups.js", "GroupChatAPI.markRead("),
    ("web/js/friends.js", "markCurrentAsRead: function"),
    ("web/js/friends.js", "SocialHub.loadMessages: mark as read failed"),
    # 靜音的群不算進側欄數字，後端沒有 push 通知
    ("web/js/social-groups.js", "await GroupChatAPI.mute("),
    # 退群／解散：該群的未讀、@、邀請都從 server 數字消失
    ("web/js/social-groups.js", "await GroupChatAPI.leave("),
    ("web/js/social-groups.js", "await GroupChatAPI.dissolve("),
    # 別的裝置或別人造成的 加入／退出／解散／被踢（WS group_updated）
    ("web/js/social-groups.js", "case 'group_updated':"),
    # 接受／拒絕群組邀請、好友請求：通知若早已讀就沒有 push 補這一步
    ("web/js/social-groups.js", "bindGroupInviteActions(listEl"),
    ("web/js/friends.js", "function refreshFriendsUI"),
    # 封鎖／解除封鎖／檢舉並封鎖：對話從列表消失（或回來）、對方送我的邀請被刪掉，
    # 這些都會進側欄數字，但這幾條路只重載好友頁資料、沒走 refreshFriendsUI
    ("web/js/friends.js", "await FriendsAPI.blockUser(userId)"),
    ("web/js/friends.js", "await FriendsAPI.unblockUser(userId)"),
    ("web/js/friends.js", "onBlocked: () =>"),
    # 刪除對話：自己這邊的未讀跟著歸零（helpers.hide_conversation_for_user）
    ("web/js/friends.js", "AppAPI.delete(`/api/conversations/"),
    # 自己在別台裝置讀了群組（WS group_read 廣播給自己）
    ("web/js/social-groups.js", "case 'group_read':"),
    # 鈴鐺標已讀：notifyUpdate 早於 POST，NavBadges 的 debounce 可能比 POST 先到
    ("web/js/notification-service.js", "/read?user_id="),
    ("web/js/notification-service.js", "/read-all?user_id="),
]


@pytest.mark.parametrize(("rel", "marker"), BADGE_AFFECTING_PATHS)
def test_badge_affecting_action_refreshes_nav_badges(rel, marker):
    src = (REPO / rel).read_text(encoding="utf-8")
    start = src.index(marker)
    # 取該路徑附近的程式碼（呼叫 → 成功後的 then／await 之後）
    window = src[max(0, start - 300) : start + 900]
    assert re.search(r"NavBadges\?\.schedule\(\)", window), (
        f"{rel}: {marker} 成功後沒叫 NavBadges.schedule()"
    )


def test_nav_badges_schedule_is_debounced():
    """連續已讀（一串來訊）會合併成一次重抓，不是每則都打 /api/notifications/badges。"""
    src = (REPO / "web/js/nav-badges.js").read_text(encoding="utf-8")
    assert "function schedule(" in src and "clearTimeout(timer)" in src


def test_nav_badges_ignores_out_of_order_responses():
    """兩次重抓亂序回來時，舊結果不能蓋掉新結果（序號守衛）。"""
    src = (REPO / "web/js/nav-badges.js").read_text(encoding="utf-8")
    assert "++reqSeq" in src and "seq !== reqSeq" in src


def test_nav_badges_refreshes_when_restored_from_bfcache():
    """上一頁／下一頁從 bfcache 還原時 visibilitychange 不一定會觸發。"""
    src = (REPO / "web/js/nav-badges.js").read_text(encoding="utf-8")
    assert "'pageshow'" in src and "e.persisted" in src
