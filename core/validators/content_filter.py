"""
內容審核過濾器
"""

import re
from typing import Dict


def filter_sensitive_content(text: str, min_length: int = 20) -> Dict:
    """
    檢查內容是否包含敏感資訊

    Args:
        text: 待檢查的文本
        min_length: 最短字數（舉報描述 20；詐騙舉報留言 10，同 CommentCreate）

    Returns:
        {
            "valid": bool,
            "warnings": List[str]
        }
    """
    if not text:
        return {"valid": False, "warnings": ["內容不能為空"]}

    warnings = []

    # 檢查長度
    if len(text) < min_length:
        warnings.append(f"描述過短（最少 {min_length} 字）")
    elif len(text) > 2000:
        warnings.append("描述過長（最多 2000 字）")

    # 檢查電子郵件
    email_pattern = r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
    if re.search(email_pattern, text):
        warnings.append("包含電子郵件地址")

    # 檢查電話號碼（使用更精確的模式，匹配常見電話格式）
    # 匹配：+886-9xx-xxx-xxx, 09xx-xxx-xxx, (02)xxxx-xxxx, +1-xxx-xxx-xxxx 等
    # 不再匹配純數字序列（如訂單編號、銀行帳戶）
    phone_pattern = r"""(
        \+?\d{1,3}[-.\s]?         # 國際冠碼可選 +1, +886, +44 等
        \(?\d{2,4}\)?[-.\s]?        # 區碼（2-4位），可選括號
        \d{3,4}[-.\s]?\d{3,4}      # 主體號碼（3+3, 3+4, 4+4 等常見格式）
    )"""
    if re.search(phone_pattern, text, re.VERBOSE):
        warnings.append("包含疑似電話號碼")

    # 檢查 URL（簡單版）
    url_pattern = r"https?://[^\s]+"
    urls = re.findall(url_pattern, text)
    if urls:
        # 允許本平台官方域名；其餘外部網址視為風險
        allowed_domains = ["cryptomind", "ton.org", "docs.ton.org"]
        for url in urls:
            if not any(domain in url for domain in allowed_domains):
                warnings.append("包含非官方網址")
                break

    # 敏感詞檢查（可從配置載入）
    sensitive_words = [
        "微信",
        "wechat",
        "telegram",
        "whatsapp",
        "私聊",
        "加我",
        "聯繫我",
    ]

    text_lower = text.lower()
    for word in sensitive_words:
        if word in text_lower:
            warnings.append(f"包含敏感詞: {word}")
            break

    # 發文內容檢查的保護型規則（2026-10-01，core/moderation/rules.py）：防詐回報本來就在引用詐騙
    # 訊息，所以模型和「加倍返還」不用；只擋會害到人的——貼出自己的助記詞／私鑰（受害者常把整段
    # 貼上來）、跟人要助記詞／私鑰、叫人把錢匯過去（「幫你追回資金」的二次詐騙）
    from core.moderation.rules import rule_reasons

    protect = {
        "leaked_secret": "內容含有助記詞或私鑰，請刪除（公開等於把資產交出去）",
        "asks_secret": "內容要求他人提供助記詞、私鑰或 API 金鑰",
        "asks_payment": "內容要求他人把錢匯到你的帳戶",
    }
    codes = [reason for reason in rule_reasons(text) if reason in protect]
    warnings.extend(protect[code] for code in codes)

    # codes：前端拿去對在地化文案（forum.moderation.reason.*），warnings 是中文給 log／舊路徑
    return {"valid": len(warnings) == 0, "warnings": warnings, "codes": codes}


def filter_chat_message(text: str) -> Dict:
    """
    Chat 訊息專用 filter — 比 filter_sensitive_content 寬鬆。

    保留：敏感詞檢查（防 spam / 引流到外部聯絡管道）
    移除：長度限制（chat 訊息本來就常短，如「BTC 多少」「2330 走勢」）
          email / 電話檢查（chat 中討論時不應一律封鎖）

    Args:
        text: 待檢查的訊息

    Returns:
        {"valid": bool, "warnings": List[str]}
    """
    if not text or not text.strip():
        return {"valid": False, "warnings": ["訊息不能為空"]}

    warnings = []

    # 上限保留（避免 prompt injection / 過長攻擊）
    if len(text) > 4000:
        warnings.append("訊息過長（最多 4000 字）")

    # 敏感詞檢查（防止有人在 chat 引流到外部聊天工具）
    sensitive_words = [
        "微信",
        "wechat",
        "telegram",
        "whatsapp",
        "私聊",
        "加我",
        "聯繫我",
    ]
    text_lower = text.lower()
    for word in sensitive_words:
        if word in text_lower:
            warnings.append(f"包含敏感詞: {word}")
            break

    return {"valid": len(warnings) == 0, "warnings": warnings}


def sanitize_description(text: str) -> str:
    """
    清理描述文本（移除多餘空白、換行）

    Args:
        text: 原始文本

    Returns:
        清理後的文本
    """
    if not text:
        return ""

    # 移除多餘空白
    text = " ".join(text.split())

    # 移除前後空白
    text = text.strip()

    return text
