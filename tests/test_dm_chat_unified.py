"""私訊聊天室統一（2026-10-05，DANNY：「兩個入口應該都是進到相同聊天室，不要有兩套」）。

以前個人頁的「發訊息」開獨立頁 forum/messages.html，社群→好友則是好友頁（SocialHub）裡的聊天欄
（手機也跳去 messages.html）：兩套各自實作的 UI。現在只剩好友頁那一個：
- 個人頁、社群搜尋、舊網址都走深連結 /?chat=<userId>[&msg=<id>]#friends
- 舊的 /static/forum/messages.html?with=… 由後端 302 過去（書籤、舊推播、外部貼的網址照樣能用）
- 獨立頁與它專屬的程式整包移除
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from api_server import app

    return TestClient(app)


class TestOldMessagesUrlRedirects:
    @pytest.mark.parametrize("path", ["/static/forum/messages.html", "/forum/messages.html"])
    def test_with_and_msg_are_carried_over(self, client, path):
        res = client.get(f"{path}?with=bob&msg=55&source=social", follow_redirects=False)
        assert res.status_code == 302
        assert res.headers["location"] == "/?chat=bob&msg=55#friends"

    def test_without_params_goes_to_friends_tab(self, client):
        res = client.get("/static/forum/messages.html", follow_redirects=False)
        assert res.headers["location"] == "/#friends"

    def test_user_id_is_url_encoded(self, client):
        res = client.get("/static/forum/messages.html?with=tg%3A123%20x%26y", follow_redirects=False)
        assert res.headers["location"] == "/?chat=tg%3A123%20x%26y#friends"

    def test_bad_values_are_dropped(self, client):
        # msg 不是數字 → 不帶；with 太長 → 整個不帶
        res = client.get("/static/forum/messages.html?with=bob&msg=abc", follow_redirects=False)
        assert res.headers["location"] == "/?chat=bob#friends"
        res = client.get("/static/forum/messages.html?with=" + "a" * 129, follow_redirects=False)
        assert res.headers["location"] == "/#friends"
        res = client.get("/static/forum/messages.html?with=%20%20", follow_redirects=False)
        assert res.headers["location"] == "/#friends"

    def test_head_request_also_redirects(self, client):
        res = client.head("/static/forum/messages.html?with=bob", follow_redirects=False)
        assert res.status_code == 302


class TestStandalonePageIsGone:
    @pytest.mark.parametrize(
        "path",
        [
            "web/forum/messages.html",
            "web/forum/js/messages-page.js",
            "web/js/messages_page.js",
            "web/js/pages/forum-messages.js",
            "tests/js/messages_page_groups.mjs",
        ],
    )
    def test_file_removed(self, path):
        assert not (REPO / path).exists(), f"{path} 應已隨獨立私訊頁移除"

    def test_no_entry_still_builds_or_links_the_old_page(self):
        vite = (REPO / "vite.config.js").read_text(encoding="utf-8")
        assert "forum/messages" not in vite
        for rel in ("web/forum/js/profile-page.js", "web/js/friends.js", "web/js/social-search.js"):
            src = (REPO / rel).read_text(encoding="utf-8")
            assert "messages.html?with=" not in src, f"{rel} 還在連舊的私訊頁"

    def test_profile_message_button_uses_the_deep_link(self):
        src = (REPO / "web/forum/js/profile-page.js").read_text(encoding="utf-8")
        assert "/?chat=${encodeURIComponent(p.user_id)}#friends" in src


class TestDeepLinkWiring:
    def test_spa_forces_friends_tab_for_chat_param(self):
        spa = (REPO / "web/js/spa.js").read_text(encoding="utf-8")
        assert "_deepLinkParams.get('chat')" in spa

    def test_friends_init_opens_chat_from_url(self):
        friends = (REPO / "web/js/friends.js").read_text(encoding="utf-8")
        assert re.search(r"this\.openChatFromUrl\(\);", friends)
        # openChat／openConversation 不再有分寬度的跳頁
        for name in ("openChat(userId, username)", "openConversation: function (userId, username)"):
            body = friends[friends.index(name) :][:1500]
            assert "window.innerWidth" not in body.split("\n    },\n")[0], name
            assert "smoothNavigate" not in body.split("\n    },\n")[0], name

    @pytest.mark.skipif(shutil.which("node") is None, reason="node 不可用")
    def test_node_behaviour(self):
        out = subprocess.run(
            ["node", "tests/js/dm_deep_link.mjs"],
            cwd=REPO,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        assert out.returncode == 0, (out.stderr or out.stdout)[-3000:]
        assert "dm_deep_link: ok" in out.stderr
