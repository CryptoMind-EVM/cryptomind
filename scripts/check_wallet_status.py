#!/usr/bin/env python3
"""
錢包狀態診斷工具
檢查當前用戶的錢包綁定狀態並提供修復建議
"""

import os
import sys

# 添加項目根目錄到 Python 路徑
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import TEST_MODE, TEST_USER
from core.database import get_connection, get_user_wallet_status


def check_wallet_status(user_id: str = None):
    """
    檢查用戶的錢包綁定狀態

    Args:
        user_id: 用戶ID，如果為 None 則使用測試用戶
    """
    # 如果沒有提供 user_id，使用測試用戶或詢問
    if not user_id:
        if TEST_MODE:
            user_id = TEST_USER.get("uid", "test-user-001")
            print(f"🧪 TEST MODE: 使用測試用戶 {user_id}")
        else:
            user_id = input("請輸入要檢查的用戶ID: ").strip()
            if not user_id:
                print("❌ 錯誤：用戶ID不能為空")
                return

    print(f"\n{'=' * 60}")
    print(f"🔍 檢查用戶錢包狀態: {user_id}")
    print(f"{'=' * 60}\n")

    # 從資料庫查詢用戶資料
    conn = get_connection()
    c = conn.cursor()

    try:
        c.execute(
            """
            SELECT user_id, username, auth_method, created_at
            FROM users WHERE user_id = %s
        """,
            (user_id,),
        )

        row = c.fetchone()

        if not row:
            print(f"❌ 錯誤：找不到用戶 {user_id}")
            return

        user_id, username, auth_method, created_at = row

        print("📊 資料庫用戶資料:")
        print(f"   用戶ID (TON 地址): {user_id}")
        print(f"   用戶名: {username}")
        print(f"   認證方式: {auth_method}")
        print(f"   創建時間: {created_at}")

        # 使用系統函數檢查錢包狀態
        print(f"\n{'=' * 60}")
        print("🔧 系統函數檢查結果:")
        print(f"{'=' * 60}\n")

        status = get_user_wallet_status(user_id)
        print(f"   has_wallet: {status.get('has_wallet')}")
        print(f"   auth_method: {status.get('auth_method')}")

        # 診斷與建議
        print(f"\n{'=' * 60}")
        print("💡 診斷結果與建議:")
        print(f"{'=' * 60}\n")

        has_wallet = status.get("has_wallet")

        if auth_method == "ton_wallet":
            if has_wallet:
                print("✅ 狀態正常：用戶已通過 TON Connect 登入（user_id 即錢包地址）")
            else:
                print("⚠️  資料不一致：auth_method 為 ton_wallet 但 has_wallet 為 False")
                print("   這表示 get_user_wallet_status() 函數可能有問題")
        elif auth_method == "dev_test":
            print("ℹ️  測試模式帳號（dev_test）")
        else:
            print(f"⚠️  未知狀態: auth_method={auth_method}, has_wallet={has_wallet}")

    finally:
        conn.close()

    print(f"\n{'=' * 60}\n")


if __name__ == "__main__":
    # 從命令列參數獲取 user_id
    user_id = sys.argv[1] if len(sys.argv) > 1 else None
    check_wallet_status(user_id)
