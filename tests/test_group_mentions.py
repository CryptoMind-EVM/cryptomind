"""群組 @提及解析（core/group_mentions.py）：訊息原文照存「@暱稱」，比對當下成員名單找出被提及的人。"""

from __future__ import annotations

import pytest

from core.group_mentions import find_mentions

pytestmark = pytest.mark.unit

NAMES = {"u1": "Danny", "u2": "Dan", "u3": "小明", "u4": "Amy Chen"}


def ids(text, names=NAMES):
    return [m["user_id"] for m in find_mentions(text, names)]


def test_basic_and_order_of_first_appearance():
    assert ids("@小明 早安 @Danny") == ["u3", "u1"]
    assert find_mentions("hi @Danny", NAMES) == [{"user_id": "u1", "name": "Danny"}]


def test_longest_name_wins():
    """「@Danny」不能同時算成提及 Dan"""
    assert ids("@Danny 你好") == ["u1"]
    assert ids("@Dan 你好") == ["u2"]


def test_cjk_without_space_and_name_with_space():
    assert ids("@小明你看這個") == ["u3"], "中文常常不打空白"
    assert ids("@Amy Chen 看一下") == ["u4"]
    assert ids("@Amy 看一下") == [], "名字沒打完整不算"


def test_case_insensitive_and_dedup():
    assert ids("@danny @DANNY @Danny") == ["u1"]


def test_not_a_mention():
    assert ids("寄信到 a@b.com") == [], "@ 後面不是成員名字"
    assert ids("@ Danny") == [], "@ 跟名字中間有空白不算"
    assert ids("Danny 沒有 @") == []
    assert ids("") == []
    assert ids("@Danny", {}) == []


def test_same_name_mentions_everyone_with_it():
    """兩個成員撞名：選單選的是誰後端分不出來，兩個都通知（比漏掉好）"""
    assert sorted(ids("@Bob 嗨", {"a": "Bob", "b": "Bob"})) == ["a", "b"]


def test_blank_names_ignored():
    assert ids("@ 嗨", {"a": "", "b": None}) == []


def test_fullwidth_at_sign():
    """注音輸入法預設打出全形「＠」：要跟半形一樣算（2026-10-02 DANNY 打 ＠ 選單沒反應）"""
    assert ids("＠小明 早安") == ["u3"]
    assert ids("＠Danny 跟 @Dan") == ["u1", "u2"]
