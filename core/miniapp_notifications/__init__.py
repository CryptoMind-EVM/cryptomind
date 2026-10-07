"""Base App／Farcaster mini app 通知（2026-09-13，DANNY：「Base App 內加入＋通知」）。

流程：使用者在宿主按「加入」→ 宿主把 notification token POST 到 manifest 的 ``webhookUrl``
（JSON Farcaster Signature，app key 簽）→ 我們驗簽＋查 Key Registry 後存 token；
前端登入後把 ``context.client.notificationDetails`` 回報一次，把 fid 綁到 user_id；
早報到點時對該使用者的 token 送一則（title ≤32、body ≤128、targetUrl 必須在本網域）。
規格：miniapps.farcaster.xyz/docs/guides/notifications。
"""
