"""發文內容檢查（論壇發文／編輯／留言、檢舉排序）。

detector.py — 自己微調的 Laya 模型（只判斷該不該擋，ONNX，CPU），只在 moderation 容器裡載入
server.py   — moderation 容器的 HTTP 服務（python -m core.moderation.server）
service.py  — app 端：呼叫 moderation 容器＋兩段門檻，決定 pass／flagged／block／unavailable
rules.py    — 還原穿插符號、貼出自己的助記詞／私鑰；明確詐騙句型只給防詐回報用
"""
