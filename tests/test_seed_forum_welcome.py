"""scripts/seed_forum_welcome.py 的官方置頂文要能通過正常發文的同一套驗證。"""

import importlib.util
from pathlib import Path

from api.routers.forum.posts import VALID_CATEGORIES

ROOT = Path(__file__).resolve().parents[1]


def _load_seed():
    spec = importlib.util.spec_from_file_location(
        "seed_forum_welcome", ROOT / "scripts" / "seed_forum_welcome.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_seed_posts_pass_forum_validation():
    seed = _load_seed()
    titles = [p["title"] for p in seed.SEED_POSTS]
    assert len(titles) == len(set(titles))  # 以標題判斷是否已發過，不能重複
    for post in seed.SEED_POSTS:
        assert post["category"] in VALID_CATEGORIES
        assert 0 < len(post["title"]) <= 200
        assert 0 < len(post["content"]) <= 10000
        assert len(post["tags"]) <= 5


def test_official_account_cannot_be_reached_by_login_flows():
    """登入流程產生的 user_id 一律是 evm_／tg_ 開頭；官方帳號不能落在這些命名空間"""
    seed = _load_seed()
    assert not seed.OFFICIAL_USER_ID.startswith(("evm_", "tg_"))
