"""
資料庫模組

統一導出所有資料庫操作函數，保持向後兼容。
使用 lazy import 避免 package import 時載入整個資料庫子系統，
這對 pytest collect 與工具腳本都更穩定。
"""

from importlib import import_module
from typing import Dict, Tuple

from . import connection as _connection

get_connection = _connection.get_connection
init_db = _connection.init_db
close_all_connections = _connection.close_all_connections

_EXPORTS: Dict[str, Tuple[str, str]] = {
    # user
    "get_user_by_id": (".user", "get_user_by_id"),
    "get_user_language": (".user", "get_user_language"),
    "update_last_active": (".user", "update_last_active"),
    "create_or_get_user": (".user", "create_or_get_user"),
    "get_user_by_identity": (".user", "get_user_by_identity"),
    "get_user_wallet_status": (".user", "get_user_wallet_status"),
    "get_user_membership": (".user", "get_user_membership"),
    "upgrade_to_pro": (".user", "upgrade_to_pro"),
    "get_current_session": (".user", "get_current_session"),
    "set_current_session": (".user", "set_current_session"),
    # telegram bindings
    "create_telegram_binding": (".telegram", "create_telegram_binding"),
    "get_binding_by_telegram_id": (".telegram", "get_binding_by_telegram_id"),
    "get_binding_by_user_id": (".telegram", "get_binding_by_user_id"),
    "update_telegram_last_used": (".telegram", "update_last_used"),
    "get_telegram_active_session": (".telegram", "get_active_session"),
    "set_telegram_active_session": (".telegram", "set_active_session"),
    "delete_telegram_binding": (".telegram", "delete_binding"),
    "delete_binding_by_user_id": (".telegram", "delete_binding_by_user_id"),
    # line bindings（與 telegram 同構，2026-09-08）
    "create_line_binding": (".line", "create_line_binding"),
    "get_binding_by_line_user_id": (".line", "get_binding_by_line_user_id"),
    "get_line_binding_by_user_id": (".line", "get_line_binding_by_user_id"),
    "update_line_last_used": (".line", "update_line_last_used"),
    "get_line_active_session": (".line", "get_line_active_session"),
    "set_line_active_session": (".line", "set_line_active_session"),
    "delete_line_binding": (".line", "delete_line_binding"),
    "delete_line_binding_by_user_id": (".line", "delete_line_binding_by_user_id"),
    # chat
    "create_session": (".chat", "create_session"),
    "ensure_session": (".chat", "ensure_session"),
    "update_session_title": (".chat", "update_session_title"),
    "toggle_session_pin": (".chat", "toggle_session_pin"),
    "reorder_pinned_sessions": (".chat", "reorder_pinned_sessions"),
    "get_sessions": (".chat", "get_sessions"),
    "delete_session": (".chat", "delete_session"),
    "check_session_ownership": (".chat", "check_session_ownership"),
    "get_session_owner": (".chat", "get_session_owner"),
    "save_chat_message": (".chat", "save_chat_message"),
    "clip_reasoning": (".chat", "clip_reasoning"),
    "get_chat_history": (".chat", "get_chat_history"),
    "clear_chat_history": (".chat", "clear_chat_history"),
    "save_codebook_feedback": (".chat", "save_codebook_feedback"),
    "save_user_feedback": (".user_feedback", "save_user_feedback"),
    # forum
    "get_boards": (".forum", "get_boards"),
    "get_board_by_slug": (".forum", "get_board_by_slug"),
    "check_daily_post_limit": (".forum", "check_daily_post_limit"),
    "create_post": (".forum", "create_post"),
    "get_posts": (".forum", "get_posts"),
    "get_post_by_id": (".forum", "get_post_by_id"),
    "update_post": (".forum", "update_post"),
    "delete_post": (".forum", "delete_post"),
    "get_user_posts": (".forum", "get_user_posts"),
    "get_daily_post_count": (".forum", "get_daily_post_count"),
    "add_comment": (".forum", "add_comment"),
    "get_comments": (".forum", "get_comments"),
    "get_daily_comment_count": (".forum", "get_daily_comment_count"),
    "create_tip": (".forum", "create_tip"),
    "get_tips_sent": (".forum", "get_tips_sent"),
    "get_tips_received": (".forum", "get_tips_received"),
    "get_tips_total_received": (".forum", "get_tips_total_received"),
    "get_trending_tags": (".forum", "get_trending_tags"),
    "get_posts_by_tag": (".forum", "get_posts_by_tag"),
    "search_tags": (".forum", "search_tags"),
    "get_user_forum_stats": (".forum", "get_user_forum_stats"),
    "get_user_payment_history": (".forum", "get_user_payment_history"),
    # trading
    "add_to_watchlist": (".trading", "add_to_watchlist"),
    "remove_from_watchlist": (".trading", "remove_from_watchlist"),
    "get_watchlist": (".trading", "get_watchlist"),
    # friends
    "search_users": (".friends", "search_users"),
    "get_public_user_profile": (".friends", "get_public_user_profile"),
    "send_friend_request": (".friends", "send_friend_request"),
    "accept_friend_request": (".friends", "accept_friend_request"),
    "reject_friend_request": (".friends", "reject_friend_request"),
    "cancel_friend_request": (".friends", "cancel_friend_request"),
    "remove_friend": (".friends", "remove_friend"),
    "get_friends_list": (".friends", "get_friends_list"),
    "get_pending_requests_received": (".friends", "get_pending_requests_received"),
    "get_pending_requests_sent": (".friends", "get_pending_requests_sent"),
    "get_friendship_status": (".friends", "get_friendship_status"),
    "get_bulk_friendship_status": (".friends", "get_bulk_friendship_status"),
    "get_friends_count": (".friends", "get_friends_count"),
    "get_pending_count": (".friends", "get_pending_count"),
    "is_blocked": (".friends", "is_blocked"),
    "is_friend": (".friends", "is_friend"),
    # messages
    "get_or_create_conversation": (".messages", "get_or_create_conversation"),
    "get_conversations": (".messages", "get_conversations"),
    "get_conversation_by_id": (".messages", "get_conversation_by_id"),
    "get_conversation_with_user": (".messages", "get_conversation_with_user"),
    "get_conversation_with_messages": (".messages", "get_conversation_with_messages"),
    "send_dm_message": (".messages", "send_message"),
    "get_dm_messages": (".messages", "get_messages"),
    "mark_as_read": (".messages", "mark_as_read"),
    "get_unread_count": (".messages", "get_unread_count"),
    "check_message_limit": (".messages", "check_message_limit"),
    "check_and_increment_message": (".messages", "check_and_increment_message"),
    "increment_message_count": (".messages", "increment_message_count"),
    "check_greeting_limit": (".messages", "check_greeting_limit"),
    "check_and_increment_greeting": (".messages", "check_and_increment_greeting"),
    "increment_greeting_count": (".messages", "increment_greeting_count"),
    "send_greeting": (".messages", "send_greeting"),
    "search_messages": (".messages", "search_messages"),
    "hide_dm_message_for_user": (".messages", "hide_dm_message_for_user"),
    "hide_conversation_for_user": (".messages", "hide_conversation_for_user"),
    # cache
    "set_cache": (".cache", "set_cache"),
    "get_cache": (".cache", "get_cache"),
    "delete_cache": (".cache", "delete_cache"),
    "clear_all_cache": (".cache", "clear_all_cache"),
    # system config
    "get_config": (".system_config", "get_config"),
    "get_all_configs": (".system_config", "get_all_configs"),
    "get_prices": (".system_config", "get_prices"),
    "get_limits": (".system_config", "get_limits"),
    "set_config": (".system_config", "set_config"),
    "update_price": (".system_config", "update_price"),
    "update_limit": (".system_config", "update_limit"),
    "bulk_update_configs": (".system_config", "bulk_update_configs"),
    "get_config_metadata": (".system_config", "get_config_metadata"),
    "list_all_configs_with_metadata": (
        ".system_config",
        "list_all_configs_with_metadata",
    ),
    "invalidate_config_cache": (".system_config", "invalidate_cache"),
    "get_config_history": (".system_config", "get_config_history"),
    "init_audit_table": (".system_config", "init_audit_table"),
    # notifications
    "create_notification": (".notifications", "create_notification"),
    "get_notifications": (".notifications", "get_notifications"),
    # price alerts
    "create_price_alerts_table": (".price_alerts", "create_price_alerts_table"),
    "create_alert": (".price_alerts", "create_alert"),
    "get_user_alerts": (".price_alerts", "get_user_alerts"),
    "delete_alert": (".price_alerts", "delete_alert"),
    "get_active_alerts": (".price_alerts", "get_active_alerts"),
    "mark_alert_triggered": (".price_alerts", "mark_alert_triggered"),
    "rearm_alert": (".price_alerts", "rearm_alert"),
    "count_user_alerts": (".price_alerts", "count_user_alerts"),
    # tools
    "seed_tools_catalog": (".tools", "seed_tools_catalog"),
    "get_allowed_tools": (".tools", "get_allowed_tools"),
    "check_tool_quota": (".tools", "check_tool_quota"),
    "increment_tool_usage": (".tools", "increment_tool_usage"),
    "get_tools_for_frontend": (".tools", "get_tools_for_frontend"),
    "update_user_tool_preference": (".tools", "update_user_tool_preference"),
    # preferences
    "get_all_preferences": (".preferences", "get_all_preferences"),
    "upsert_preference": (".preferences", "upsert_preference"),
    "delete_preference": (".preferences", "delete_preference"),
    # memory
    "MemoryStore": (".memory", "MemoryStore"),
    "get_memory_store": (".memory", "get_memory_store"),
}

__all__ = [
    "DATABASE_URL",
    "get_connection",
    "init_db",
    "close_all_connections",
    *_EXPORTS.keys(),
]


def __getattr__(name: str):
    if name == "DATABASE_URL":
        return _connection.get_database_url()
    if name in {"get_connection", "init_db", "close_all_connections"}:
        return globals()[name]
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = target
    module = import_module(module_name, __name__)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(__all__)
