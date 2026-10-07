"""
驗證器模組
"""

from .content_filter import filter_sensitive_content, sanitize_description


def mask_wallet_address(address: str, mask_length: int = 4) -> str:
    """遮罩錢包地址以保護隱私（前後各保留 mask_length 字元）。"""
    if not address or len(address) <= mask_length * 2:
        return address
    prefix = address[:mask_length]
    suffix = address[-mask_length:]
    return f"{prefix}...{suffix}"


__all__ = [
    "mask_wallet_address",
    "filter_sensitive_content",
    "sanitize_description",
]
