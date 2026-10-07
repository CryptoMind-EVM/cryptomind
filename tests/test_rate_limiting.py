"""Tests for per-route rate limiting configuration.

Verifies that sensitive endpoints have @limiter.limit decorators
with appropriate rate limits applied via SlowAPI's internal registry.
"""

import pytest

RATE_LIMITED_ENDPOINTS = {
    # Admin
    "api.routers.admin.config.admin_update_config": "20 per 1 minute",
    # 設定中心的後台覆寫（2026-09-27）
    "api.routers.admin.settings_center.admin_set_override": "30 per 1 minute",
    "api.routers.admin.settings_center.admin_clear_override": "30 per 1 minute",
    "api.routers.admin.forum.admin_resolve_report": "30 per 1 minute",
    "api.routers.admin.forum.admin_toggle_comment_visibility": "30 per 1 minute",
    "api.routers.admin.forum.admin_toggle_post_pin": "30 per 1 minute",
    "api.routers.admin.forum.admin_toggle_post_visibility": "30 per 1 minute",
    "api.routers.admin.notifications.broadcast_notification": "5 per 1 minute",
    "api.routers.admin.stats.admin_stats_forum": "30 per 1 minute",
    "api.routers.admin.stats.admin_stats_funnel": "30 per 1 minute",  # PR-5 轉換漏斗
    "api.routers.admin.stats.admin_stats_agent_runs": "30 per 1 minute",
    "api.routers.admin.stats.admin_stats_overview": "30 per 1 minute",
    "api.routers.admin.stats.admin_stats_revenue": "30 per 1 minute",
    "api.routers.admin.stats.admin_stats_users": "30 per 1 minute",
    "api.routers.admin.stats.admin_stats_visitors_list": "30 per 1 minute",
    "api.routers.admin.stats.admin_stats_visitors_summary": "30 per 1 minute",
    "api.routers.admin.stats.admin_stats_visitors_trend": "30 per 1 minute",
    "api.routers.admin.stats.admin_stats_wallet_monitor": "10 per 1 minute",
    "api.routers.admin.stats.admin_stats_login_methods": "30 per 1 minute",
    "api.routers.admin.stats.admin_stats_moderation": "30 per 1 minute",
    "api.routers.forum.posts.check_post_content": "30 per 1 minute",
    "api.routers.admin.users.bootstrap_admin": "5 per 1 minute",
    "api.routers.admin.users.set_user_membership": "20 per 1 minute",
    "api.routers.admin.users.settle_usdc_payment": "10 per 1 minute",
    "api.routers.admin.users.set_user_role": "20 per 1 minute",
    "api.routers.admin.users.set_user_status": "20 per 1 minute",
    # Auth / user
    "api.routers.user.dev_login": "5 per 1 minute",
    # c041 多鏈身份（multichain design Part B）
    "api.routers.user.get_evm_nonce": "10 per 1 minute",
    "api.routers.user.evm_login": "20 per 1 minute",
    "api.routers.user.bind_wallet": "5 per 1 minute",
    # WC 連線遙測（2026-09-05 前端無聲卡死回報，匿名純記錄）
    "api.routers.user.wc_connect_event": "10 per 1 minute",
    # c041 付款 rail（multichain design Part C）
    "api.routers.premium.create_payment_order": "20 per 1 minute",
    "api.routers.guest.guest_analyze": "5 per 1 minute",  # 訪客模式（2026-08-19）
    "api.routers.guest.guest_quota": "30 per 1 minute",  # 訪客 banner 額度查詢（唯讀、不扣額）
    "api.routers.memory.verify_fact": "20 per 1 minute",  # c031 記憶治理
    "api.routers.memory.fact_history": "20 per 1 minute",  # c031 記憶治理
    "api.routers.journal.list_entries": "30 per 1 minute",  # c033 統一帳本
    "api.routers.journal.create_entry": "20 per 1 minute",
    "api.routers.journal.delete_entry": "20 per 1 minute",
    "api.routers.journal.update_entry": "20 per 1 minute",
    "api.routers.journal.get_summary": "30 per 1 minute",
    "api.routers.journal.list_revisions": "30 per 1 minute",  # c037 版本史
    "api.routers.journal.restore_entry": "20 per 1 minute",
    "api.routers.journal.list_deleted": "30 per 1 minute",
    "api.routers.journal.list_positions": "30 per 1 minute",  # UX 第二輪投資視圖
    "api.routers.journal.get_base_currency": "30 per 1 minute",  # c039 報表基準幣
    # 切換基準幣會整批搬移換算值——限流較嚴
    "api.routers.journal.set_base_currency": "6 per 1 minute",
    # 以下 4 個為既有端點（先前 PR 加的），全 app import 時註冊，補齊預期清單
    "api.routers.discover.discover_opportunities": "30 per 1 minute",
    "api.routers.scam_tracker.reports.address_checkup": "30 per 1 minute",
    # 公開查詢＋查到舊 upper() 列會在背景寫 DB（自我修復），跟 /check 同一個上限
    "api.routers.scam_tracker.reports.search_scam_wallet": "30 per 1 minute",
    # submit 頁顯示今日剩餘舉報次數；每次一個 COUNT 查詢
    "api.routers.scam_tracker.reports.get_report_quota": "60 per 1 minute",
    "api.routers.studio.add_reference": "20 per 1 minute",
    # AI 回答快照分享（2026-10-05，旗標預設關）
    "api.routers.answer_share.preview_answer_share": "30 per 1 minute",
    "api.routers.answer_share.create_answer_share": "10 per 1 minute",
    "api.routers.answer_share.list_answer_shares": "30 per 1 minute",
    "api.routers.answer_share.revoke_answer_share": "30 per 1 minute",
    "api.routers.answer_share.get_public_answer_share": "30 per 1 minute",
    "api.routers.answer_share.answer_share_page": "60 per 1 minute",
    "api.routers.studio.remove_reference": "20 per 1 minute",
    "api.routers.user.refresh_access_token": "30 per 1 minute",
    "api.routers.user.save_user_api_key_endpoint": "10 per 1 minute",
    "api.routers.user.delete_user_api_key_endpoint": "10 per 1 minute",
    "api.routers.user.save_user_model_endpoint": "10 per 1 minute",
    "api.routers.watchlist.add_watchlist": "20 per 1 minute",
    "api.routers.watchlist.remove_watchlist": "20 per 1 minute",
    "api.routers.watchlist.put_market_watchlist": "30 per 1 minute",
    "api.routers.user.logout": "30 per 1 minute",
    "api.routers.user.upsert_analysis_preference": "20 per 1 minute",
    "api.routers.user.delete_analysis_preference": "20 per 1 minute",
    "api.routers.user.set_user_display_name_pref": "5 per 1 minute",
    "api.routers.user.set_user_llm_provider_pref": "30 per 1 minute",  # #356 LLM provider 同步
    # Premium
    "api.routers.premium.upgrade_to_premium": "10 per 1 minute",
    # Forum
    "api.routers.forum.posts.create_new_post": "20 per 1 minute",
    "api.routers.forum.posts.create_post_payment_order": "20 per 1 minute",
    "api.routers.forum.posts.update_post_content": "10 per 1 minute",
    "api.routers.forum.posts.delete_post_by_id": "10 per 1 minute",
    "api.routers.forum.comments.add_new_comment": "30 per 1 minute",
    "api.routers.forum.comments.push_post": "30 per 1 minute",
    "api.routers.forum.comments.boo_post": "30 per 1 minute",
    "api.routers.forum.tips.create_tip_payment_order": "20 per 1 minute",
    "api.routers.forum.tips.tip_post": "10 per 1 minute",
    # Messages
    "api.routers.messages.send_message_endpoint": "30 per 1 minute",
    "api.routers.messages.send_greeting_endpoint": "5 per 1 minute",
    "api.routers.messages.mark_read_endpoint": "30 per 1 minute",
    "api.routers.messages.delete_message_endpoint": "20 per 1 minute",
    "api.routers.messages.hide_message_endpoint": "20 per 1 minute",
    # 表情：連點換來換去是正常操作，比送訊息寬
    "api.routers.messages.set_reaction_endpoint": "60 per 1 minute",
    "api.routers.messages.remove_reaction_endpoint": "60 per 1 minute",
    # 檢舉：一小時 10 則，擋洗版濫檢
    "api.routers.messages.report_message_endpoint": "10 per 1 hour",
    "api.routers.admin.dm_reports.admin_list_dm_reports": "30 per 1 minute",
    "api.routers.admin.dm_reports.admin_resolve_dm_report": "30 per 1 minute",
    "api.routers.admin.group_reports.admin_list_group_reports": "30 per 1 minute",
    "api.routers.admin.group_reports.admin_resolve_group_report": "30 per 1 minute",
    "api.routers.messages.delete_conversation_endpoint": "10 per 1 minute",
    # 群組聊天（2026-10-01，c066）
    "api.routers.group_chat.list_groups": "60 per 1 minute",
    "api.routers.group_chat.create_group": "10 per 1 minute",
    "api.routers.group_chat.list_invites": "60 per 1 minute",
    "api.routers.group_chat.accept_invite": "20 per 1 minute",
    "api.routers.group_chat.decline_invite": "20 per 1 minute",
    "api.routers.group_chat.get_group": "60 per 1 minute",
    "api.routers.group_chat.update_group": "20 per 1 minute",
    "api.routers.group_chat.leave_group": "20 per 1 minute",
    "api.routers.group_chat.remove_member": "20 per 1 minute",
    "api.routers.group_chat.dissolve_group": "10 per 1 minute",  # 群主解散（c068）
    "api.routers.group_chat.transfer_owner": "10 per 1 minute",  # 群主手動轉讓（#1009）
    "api.routers.group_chat.mute_group": "30 per 1 minute",
    "api.routers.group_chat.invite_members": "20 per 1 minute",
    "api.routers.group_chat.get_group_messages": "60 per 1 minute",
    "api.routers.group_chat.send_group_message": "30 per 1 minute",
    "api.routers.group_chat.mark_group_read": "60 per 1 minute",
    "api.routers.group_chat.recall_group_message": "20 per 1 minute",
    "api.routers.group_chat.set_group_reaction": "60 per 1 minute",
    "api.routers.group_chat.remove_group_reaction": "60 per 1 minute",
    "api.routers.group_chat.report_group_message": "10 per 1 hour",
    # 對話置頂＋拖曳排序（2026-10-01，c067）
    "api.routers.chat_pins.list_chat_pins": "60 per 1 minute",
    "api.routers.chat_pins.reorder_chat_pins": "30 per 1 minute",
    "api.routers.chat_pins.pin_chat": "30 per 1 minute",
    "api.routers.chat_pins.unpin_chat": "30 per 1 minute",
    # 對話自訂順序（2026-10-01，c068）
    "api.routers.chat_order.reorder_chats": "30 per 1 minute",
    "api.routers.chat_order.reset_chat_order": "10 per 1 minute",
    "api.routers.analysis.reorder_pinned_sessions_endpoint": "30 per 1 minute",
    # 聊天室 AI 助理（另有 30 per 1 hour，test_chat_assistant_router 驗）
    "api.routers.chat_assistant.assistant_status": "60 per 1 minute",
    "api.routers.chat_assistant.ask_assistant": "3 per 1 minute",
    # 問答紀錄（跨裝置接續，2026-10-04）
    "api.routers.chat_assistant.assistant_history": "60 per 1 minute",
    "api.routers.chat_assistant.clear_assistant_history": "30 per 1 minute",
    "api.routers.chat_assistant.delete_assistant_turn": "60 per 1 minute",
    # 聊天搜尋（人／群組／訊息）
    "api.routers.chat_search.chat_search": "30 per 1 minute",
    # Friends
    "api.routers.friends.send_request": "10 per 1 minute",
    "api.routers.friends.accept_request": "10 per 1 minute",
    "api.routers.friends.reject_request": "10 per 1 minute",
    "api.routers.friends.cancel_request": "10 per 1 minute",
    "api.routers.friends.remove_friend_endpoint": "10 per 1 minute",
    "api.routers.friends.block_user_endpoint": "10 per 1 minute",
    "api.routers.friends.unblock_user_endpoint": "10 per 1 minute",
    # Notifications
    "api.routers.notifications.get_nav_badges": "60 per 1 minute",  # 功能選單未讀標示（2026-10-02）
    "api.routers.notifications.mark_as_read_endpoint": "30 per 1 minute",
    "api.routers.notifications.mark_all_as_read_endpoint": "10 per 1 minute",
    "api.routers.notifications.delete_notification_endpoint": "20 per 1 minute",
    # Governance
    "api.routers.governance.submit_report": "10 per 1 hour",
    "api.routers.governance.vote_on_pending_report": "30 per 1 hour",
    "api.routers.governance.finalize_report_decision": "10 per 1 minute",
    # Analysis
    "api.routers.analysis.analyze_crypto": "10 per 1 minute",
    "api.routers.analysis.get_chat_greeting": "10 per 1 minute",
    "api.routers.analysis.clear_chat_history_endpoint": "5 per 1 minute",
    "api.routers.analysis.delete_user_session": "20 per 1 minute",
    "api.routers.analysis.create_user_session": "20 per 1 minute",
    "api.routers.analysis.pin_user_session": "30 per 1 minute",
    "api.routers.analysis.submit_feedback": "20 per 1 minute",
    "api.routers.analysis.confirm_scam_verdict": "20 per 1 minute",
    # Swap（Omniston M3）— 碰錢更嚴
    # Memory（Hermes 主動記憶 #355）
    "api.routers.memory.list_facts": "30 per 1 minute",
    "api.routers.memory.list_private_facts": "30 per 1 minute",  # Mixer Step 4
    "api.routers.memory.create_fact": "20 per 1 minute",
    "api.routers.memory.update_fact": "20 per 1 minute",
    "api.routers.memory.delete_fact": "20 per 1 minute",
    # Agent Presets（Agent platform Phase 2）
    "api.routers.agent_presets.list_agent_profiles": "30 per 1 minute",
    "api.routers.agent_presets.list_agent_presets": "30 per 1 minute",
    "api.routers.agent_presets.create_agent_preset": "20 per 1 minute",
    "api.routers.agent_presets.create_preset_from_template": "20 per 1 minute",
    "api.routers.agent_presets.list_preset_templates": "30 per 1 minute",
    "api.routers.agent_presets.update_agent_preset": "20 per 1 minute",
    "api.routers.agent_presets.delete_agent_preset": "20 per 1 minute",
    "api.routers.agent_presets.activate_agent_preset": "20 per 1 minute",
    "api.routers.agent_presets.get_resolved_capabilities": "30 per 1 minute",
    # Agent Configs（Model Mixer Step 1：per-agent 模型指定）
    "api.routers.agent_configs.list_agent_configs": "30 per 1 minute",
    "api.routers.agent_configs.set_agent_config": "20 per 1 minute",
    "api.routers.agent_configs.delete_agent_config": "20 per 1 minute",
    # Discover（People & Projects 探索，Phase 3）
    "api.routers.discover.get_discover_config": "30 per 1 minute",
    "api.routers.discover.discover_search": "30 per 1 minute",
    "api.routers.discover.discover_project_detail": "30 per 1 minute",
    "api.routers.discover.discover_project_comments": "30 per 1 minute",
    "api.routers.discover.list_favorites": "30 per 1 minute",
    "api.routers.discover.add_favorite": "20 per 1 minute",
    "api.routers.discover.remove_favorite": "20 per 1 minute",
    "api.routers.discover.discover_recommend": "20 per 1 minute",
    "api.routers.discover.discover_ai_review": "10 per 1 minute",
    "api.routers.studio.list_drafts": "30 per 1 minute",
    "api.routers.studio.create_draft": "20 per 1 minute",
    "api.routers.studio.get_draft": "30 per 1 minute",
    "api.routers.studio.update_draft": "20 per 1 minute",
    "api.routers.studio.delete_draft": "20 per 1 minute",
    "api.routers.studio.add_version": "20 per 1 minute",
    "api.routers.studio.list_versions": "30 per 1 minute",
    "api.routers.studio.get_version": "30 per 1 minute",
    "api.routers.studio.diff_versions": "30 per 1 minute",
    "api.routers.studio.export_draft": "10 per 1 minute",
    "api.routers.studio.coach_draft": "6 per 1 minute",
    "api.routers.studio.share_draft": "10 per 1 minute",
    "api.routers.studio.unshare_draft": "10 per 1 minute",
    "api.routers.studio.get_shared_draft": "30 per 1 minute",
    "api.routers.studio.record_outcome": "30 per 1 minute",
    "api.routers.studio.list_exchanges": "30 per 1 minute",
    "api.routers.discover.discover_project_ask": "10 per 1 minute",
    "api.routers.discover.admin_prospect_logs": "10 per 1 minute",
    "api.routers.admin.stats.admin_stats_feature_usage": "30 per 1 minute",
    # Skills（使用者自訂 skill）
    "api.routers.skills.list_skills": "30 per 1 minute",
    "api.routers.skills.toggle_official_skill": "20 per 1 minute",
    "api.routers.skills.create_custom_skill": "10 per 1 minute",
    "api.routers.skills.update_custom_skill": "20 per 1 minute",
    "api.routers.skills.delete_custom_skill": "20 per 1 minute",
    # Market
    "api.routers.market.rest.run_screener": "10 per 1 minute",
    "api.routers.market.rest.get_klines_data": "60 per 1 minute",
    # System
    "api.routers.system.update_user_settings": "10 per 1 minute",
    "api.routers.system.validate_key": "10 per 1 minute",
    "api.routers.system.switch_test_tier": "5 per 1 minute",
    # Tools
    "api.routers.tools.set_tool_preference": "20 per 1 minute",
    "api.routers.tools.set_user_tool_preference": "20 per 1 minute",
    # Scam Tracker
    "api.routers.scam_tracker.votes.vote_on_report": "20 per 1 minute",
    "api.routers.scam_tracker.reports.create_new_scam_report": "5 per 1 minute",
    "api.routers.scam_tracker.comments.add_comment_to_report": "10 per 1 minute",
    # Alerts
    "api.routers.alerts.create_alert_endpoint": "10 per 1 minute",
    "api.routers.calendar.list_calendar": "30 per 1 minute",
    "api.routers.calendar.add_calendar": "20 per 1 minute",
    "api.routers.calendar.update_calendar": "20 per 1 minute",
    "api.routers.calendar.delete_calendar": "20 per 1 minute",
    "api.routers.brief.get_brief_prefs": "30 per 1 minute",
    "api.routers.brief.put_brief_prefs": "20 per 1 minute",
    "api.routers.brief.preview_brief": "5 per 1 minute",
    # 早報不列的標的（c057）：清單一次 GET；開關一檔一次 PUT，連點幾檔也不該被擋
    "api.routers.brief.get_brief_symbols": "30 per 1 minute",
    "api.routers.brief.put_brief_symbol": "60 per 1 minute",
    # PR-8 Email 早報：設定 email 會寄信給任意地址——每人每小時 3 次防濫用
    "api.routers.email_brief.get_email_brief": "30 per 1 minute",
    # 條款改版後的同意紀錄（#922）
    "api.routers.legal_consent.accept_legal": "10 per 1 minute",
    "api.routers.email_brief.put_email_brief": "3 per 1 hour",
    "api.routers.email_brief.delete_email_brief": "10 per 1 minute",
    "api.routers.email_brief.confirm_email_page": "20 per 1 minute",
    "api.routers.email_brief.confirm_email": "20 per 1 minute",
    "api.routers.email_brief.unsubscribe_email_page": "20 per 1 minute",
    # POST 退訂＝頁面按鈕＋RFC 8058 一鍵退訂（信箱服務商伺服器代送、多人共用 IP），放寬避免退訂被擋
    "api.routers.email_brief.unsubscribe_email": "300 per 1 minute",
    # 新手三步（PR-7）：聊天首頁一次唯讀 GET，跟 brief-prefs GET 同級
    "api.routers.onboarding.get_onboarding_status": "30 per 1 minute",
    # 綁定錢包鏈上持倉／帳本同步（2026-09-12）：holdings 打 RPC／TonAPI，sync 一分鐘一次
    "api.routers.onchain.wallet_holdings": "20 per 1 minute",
    "api.routers.onchain.onchain_sync_now": "1 per 1 minute",
    # 判斷評分（2026-09-13）：refresh 會抓價，一分鐘一次
    "api.routers.scorecard.get_scorecard": "30 per 1 minute",
    "api.routers.scorecard.get_entries": "30 per 1 minute",
    "api.routers.scorecard.refresh_scorecard": "1 per 1 minute",
    "api.routers.scorecard.list_calls": "30 per 1 minute",
    "api.routers.scorecard.create_call": "10 per 1 minute",
    "api.routers.scorecard.cancel_call": "10 per 1 minute",
    "api.routers.miniapp.miniapp_webhook": "120 per 1 minute",
    "api.routers.google_auth.google_login": "20 per 1 minute",
    "api.routers.google_auth.google_bind": "10 per 1 minute",
    "api.routers.google_auth.google_unlink": "5 per 1 minute",
    "api.routers.miniapp.register_notification_token": "30 per 1 minute",
    "api.routers.alerts.delete_alert_endpoint": "30 per 1 minute",
    # User — additional endpoints
    "api.routers.user.client_log": "30 per 1 minute",
    "api.routers.user.submit_user_feedback": "10 per 1 minute",
    "api.routers.user.set_user_language_pref": "10 per 1 minute",
    "api.routers.user.test_user_api_key_endpoint": "5 per 1 minute",
    "api.routers.user.telegram_login": "10 per 1 minute",
    # Analysis — additional
    "api.routers.analysis.set_current_session_endpoint": "20 per 1 minute",
    # 2026-09-22 平台免費模型狀態（6936fe8）：登入後唯讀 GET、一次 COUNT 查詢，
    # 與 get_unread_count 等輕量輪詢同級 60/m
    "api.routers.analysis.platform_model_status": "60 per 1 minute",
    # Market — additional
    "api.routers.market.rest.api_refresh_all_market_pulse": "5 per 1 minute",
    # System — additional
    "api.routers.system.discover_models": "10 per 1 minute",
    # Telegram Link
    "api.routers.telegram_link.bot_set_brief": "20 per 1 minute",
    # /lang（2026-09-28）：改帳號語言
    "api.routers.telegram_link.bot_set_language": "20 per 1 minute",
    "api.routers.telegram_link.create_link_token": "5 per 1 minute",
    "api.routers.telegram_link.unlink_telegram": "5 per 1 minute",
    "api.routers.telegram_link.verify_link": "10 per 1 minute",
    # PR-7 綁定確認步驟：預覽與 verify-link 同級、取消寬一點
    "api.routers.telegram_link.link_preview": "10 per 1 minute",
    "api.routers.telegram_link.link_cancel": "20 per 1 minute",
    "api.routers.telegram_link.bot_chat": "10 per 1 minute",
    # 2026-09-14 HITL 卡按鈕 callback（1aee1a7）：bot secret 驗證的 bot→API 呼叫，
    # 與同檔 bot_list_sessions／bot_use_session／bot_set_brief 同級 20/m
    "api.routers.telegram_link.bot_chat_resume": "20 per 1 minute",
    "api.routers.telegram_link.bot_list_sessions": "20 per 1 minute",
    "api.routers.telegram_link.bot_use_session": "20 per 1 minute",
    # Trust（信任等級 + EVM 綁定）
    "api.routers.trust.get_my_trust_score": "30 per 1 minute",
    "api.routers.trust.get_user_trust_badge": "60 per 1 minute",
    "api.routers.trust.trigger_recompute": "5 per 1 minute",
    "api.routers.trust.get_evm_bind_nonce": "10 per 1 minute",
    "api.routers.trust.bind_evm_address": "5 per 1 minute",
    "api.routers.trust.unbind_evm_address": "5 per 1 minute",
    # Wallet Monitor（錢包監測）
    "api.routers.wallet_monitor.wallet_overview": "30 per 1 minute",
    "api.routers.wallet_monitor.wallet_events": "30 per 1 minute",
    "api.routers.wallet_monitor.get_settings": "30 per 1 minute",
    "api.routers.wallet_monitor.update_settings": "20 per 1 minute",
    "api.routers.wallet_monitor.add_wallet": "10 per 1 minute",
    "api.routers.wallet_monitor.remove_wallet": "10 per 1 minute",
    "api.routers.wallet_monitor.wallet_detail": "30 per 1 minute",  # PR #418 錢包明細
    # External Agent Guard API / management
    "api.routers.guard_management.create_guard_client": "10 per 1 hour",
    "api.routers.guard_management.create_guard_api_key": "10 per 1 hour",
    "api.routers.guard_management.revoke_guard_api_key": "20 per 1 hour",
    "api.routers.guard_management.create_guard_policy": "20 per 1 hour",
    "api.routers.guard_management.get_guard_client_dashboard": "60 per 1 minute",
    "api.routers.guard_management.list_guard_client_decisions": "60 per 1 minute",
    "api.routers.guard_management.list_guard_client_receipts": "60 per 1 minute",
    "api.routers.guard_management.list_my_guard_receipts": "60 per 1 minute",
    "api.routers.guard_v1.evaluate_external_action": "120 per 1 minute",
    "api.routers.guard_v1.get_external_decision": "240 per 1 minute",
    "api.routers.guard_v1.record_external_approval": "60 per 1 minute",
    "api.routers.guard_v1.record_external_outcome": "120 per 1 minute",
    "api.routers.guard_v1.verify_external_receipt": "240 per 1 minute",
    # 2026-08-23 速率限制審計補齊（高成本 GET＋messages＋chat history）
    "api.routers.analysis.get_history": "30 per 1 minute",
    # 2026-08-30 vision Phase 2：對話附圖讀取（歷史重播縮圖/lightbox，owner-only）
    "api.routers.attachments.get_attachment": "60 per 1 minute",
    "api.routers.analysis.revoke_analysis_run": "10 per 1 minute",
    "api.routers.messages.get_conversations_endpoint": "30 per 1 minute",
    "api.routers.messages.get_messages_endpoint": "30 per 1 minute",
    "api.routers.messages.get_conversation_with_user_endpoint": "30 per 1 minute",
    "api.routers.messages.search_messages_endpoint": "30 per 1 minute",
    "api.routers.messages.get_message_limits_endpoint": "30 per 1 minute",
    # 2026-08-23 Error Boundary
    "api.routers.frontend_errors.report_frontend_errors": "10 per 1 minute",
    "api.routers.frontend_errors.list_frontend_errors": "30 per 1 minute",
    # 2026-08-23 Skill 版本歷史
    "api.routers.skills.get_skill_history": "20 per 1 minute",
    "api.routers.skills.rollback_skill": "10 per 1 minute",
    # 2026-08-23 股市行情限流（pulse 20/m、klines 30/m，9 個市場模組）。
    # 當時漏登記本清單——單跑測試（fixture 未 import 股市 router）僥倖通過，
    # 全套（conftest 載入 api_server）即爆「unexpected endpoints」。
    "api.routers.astock.get_a_pulse": "20 per 1 minute",
    "api.routers.astock.get_a_klines": "30 per 1 minute",
    "api.routers.twstock.get_tw_pulse": "20 per 1 minute",
    "api.routers.twstock.get_tw_klines": "30 per 1 minute",
    "api.routers.usstock.get_us_pulse": "20 per 1 minute",
    "api.routers.usstock.get_us_klines": "30 per 1 minute",
    "api.routers.hkstock.get_hk_pulse": "20 per 1 minute",
    "api.routers.hkstock.get_hk_klines": "30 per 1 minute",
    "api.routers.jpstock.get_jp_pulse": "20 per 1 minute",
    "api.routers.jpstock.get_jp_klines": "30 per 1 minute",
    "api.routers.krstock.get_kr_pulse": "20 per 1 minute",
    "api.routers.krstock.get_kr_klines": "30 per 1 minute",
    "api.routers.instock.get_in_pulse": "20 per 1 minute",
    "api.routers.instock.get_in_klines": "30 per 1 minute",
    "api.routers.commodity.get_commodity_pulse": "20 per 1 minute",
    "api.routers.commodity.get_commodity_klines": "30 per 1 minute",
    "api.routers.forex.get_forex_pulse": "20 per 1 minute",
    "api.routers.forex.get_forex_klines": "30 per 1 minute",
    # 2026-09-08 LINE bot 接入（#707）——漏登記第三次重演 2026-08-23 股市
    # router 的事故（單跑通過、全跑爆 unexpected）。本次已把 fixture 的手工
    # import 清單改成 pkgutil 自動探索，這份期望清單是唯一要維護的地方。
    "api.routers.line_link.create_line_link_token": "10 per 1 minute",
    "api.routers.line_link.line_unlink": "10 per 1 minute",
}


@pytest.fixture(scope="module")
def route_limits():
    # 自動探索 import 所有 api.routers 子模組（side effect：向共用 limiter
    # 註冊各端點限流）。此前是一份 46 項手工 import 清單，已經兩次漏掉新
    # router（2026-08-23 股市、2026-09-08 line_link）——單獨跑僥倖通過、
    # 全量跑時任何先 import api_server 的測試把漏網端點灌進共用註冊表，
    # test_no_unexpected_endpoints 於是 order-dependent 失敗。改 pkgutil
    # 自動 import 後清單不可能再漂；模組 import 失敗也會當場炸而非靜默漏網。
    import importlib
    import pkgutil

    import api.routers as routers_pkg

    for module_info in pkgutil.walk_packages(
        routers_pkg.__path__, prefix="api.routers."
    ):
        importlib.import_module(module_info.name)

    from api.middleware.rate_limit import limiter

    return limiter._route_limits


class TestAuthRateLimits:
    def test_dev_login_5_per_minute(self, route_limits):
        limits = route_limits.get("api.routers.user.dev_login", [])
        assert len(limits) >= 1
        assert "5 per 1 minute" in [str(r.limit) for r in limits]

    def test_ton_login_endpoint_is_gone(self, route_limits):
        """TON 登入端點 2026-09-08 移除（登入統一走 EVM，前端早無呼叫端）。

        改成守「它不該回來」：一個沒人用、卻能直接發 JWT cookie 的認證入口
        不值得留著。真正的登入速率上限看 test_evm_login_20_per_minute。
        """
        assert "api.routers.user.ton_login" not in route_limits

    def test_refresh_access_token_30_per_minute(self, route_limits):
        # 2026-08-14：10→30/min。access token 過期時 limiter key 退化為 IP，
        # 前端多路徑主動刷新（已加 60s 冷卻）實測仍可能序列觸發 >10 次。
        limits = route_limits.get("api.routers.user.refresh_access_token", [])
        assert len(limits) >= 1
        assert "30 per 1 minute" in [str(r.limit) for r in limits]


class TestPaymentRateLimits:
    def test_premium_upgrade_10_per_minute(self, route_limits):
        limits = route_limits.get("api.routers.premium.upgrade_to_premium", [])
        assert len(limits) >= 1
        assert "10 per 1 minute" in [str(r.limit) for r in limits]

    def test_tip_post_10_per_minute(self, route_limits):
        limits = route_limits.get("api.routers.forum.tips.tip_post", [])
        assert len(limits) >= 1
        assert "10 per 1 minute" in [str(r.limit) for r in limits]


class TestMessageRateLimits:
    def test_send_message_30_per_minute(self, route_limits):
        limits = route_limits.get("api.routers.messages.send_message_endpoint", [])
        assert len(limits) >= 1
        assert "30 per 1 minute" in [str(r.limit) for r in limits]

    def test_greeting_5_per_minute(self, route_limits):
        limits = route_limits.get("api.routers.messages.send_greeting_endpoint", [])
        assert len(limits) >= 1
        assert "5 per 1 minute" in [str(r.limit) for r in limits]


class TestForumRateLimits:
    def test_create_post_20_per_minute(self, route_limits):
        limits = route_limits.get("api.routers.forum.posts.create_new_post", [])
        assert len(limits) >= 1
        assert "20 per 1 minute" in [str(r.limit) for r in limits]

    def test_add_comment_30_per_minute(self, route_limits):
        limits = route_limits.get("api.routers.forum.comments.add_new_comment", [])
        assert len(limits) >= 1
        assert "30 per 1 minute" in [str(r.limit) for r in limits]

    def test_push_post_30_per_minute(self, route_limits):
        limits = route_limits.get("api.routers.forum.comments.push_post", [])
        assert len(limits) >= 1
        assert "30 per 1 minute" in [str(r.limit) for r in limits]

    def test_boo_post_30_per_minute(self, route_limits):
        limits = route_limits.get("api.routers.forum.comments.boo_post", [])
        assert len(limits) >= 1
        assert "30 per 1 minute" in [str(r.limit) for r in limits]


class TestGovernanceRateLimits:
    def test_report_10_per_hour(self, route_limits):
        limits = route_limits.get("api.routers.governance.submit_report", [])
        assert len(limits) >= 1
        assert "10 per 1 hour" in [str(r.limit) for r in limits]

    def test_vote_30_per_hour(self, route_limits):
        limits = route_limits.get("api.routers.governance.vote_on_pending_report", [])
        assert len(limits) >= 1
        assert "30 per 1 hour" in [str(r.limit) for r in limits]


class TestAnalysisRateLimit:
    def test_analyze_10_per_minute(self, route_limits):
        limits = route_limits.get("api.routers.analysis.analyze_crypto", [])
        assert len(limits) >= 1
        assert "10 per 1 minute" in [str(r.limit) for r in limits]


class TestLineRateLimits:
    """LINE 綁定（#707）——漏登記曾讓 test_no_unexpected_endpoints 變成
    order-dependent（2026-09-09 修）。"""

    def test_create_line_link_token_10_per_minute(self, route_limits):
        limits = route_limits.get("api.routers.line_link.create_line_link_token", [])
        assert len(limits) >= 1
        assert "10 per 1 minute" in [str(r.limit) for r in limits]

    def test_line_unlink_10_per_minute(self, route_limits):
        limits = route_limits.get("api.routers.line_link.line_unlink", [])
        assert len(limits) >= 1
        assert "10 per 1 minute" in [str(r.limit) for r in limits]


class TestTotalRateLimitedEndpoints:
    def test_all_expected_endpoints_are_limited(self, route_limits):
        expected = set(RATE_LIMITED_ENDPOINTS.keys())
        actual = set(route_limits.keys())
        missing = expected - actual
        assert not missing, f"Missing rate limits for: {missing}"

    def test_no_unexpected_endpoints(self, route_limits):
        expected = set(RATE_LIMITED_ENDPOINTS.keys())
        actual = set(route_limits.keys())
        extra = actual - expected
        assert not extra, f"Unexpected rate-limited endpoints: {extra}"
