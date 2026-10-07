"""私訊表情回應的 key（DB 只存 key，圖案是前端 dm-message-actions.js 的自繪 SVG）。

改這份清單要一起改：前端 REACTIONS、c060 的 CHECK、i18n messages.reactions.*。
"""

REACTION_KEYS = ("like", "love", "haha", "wow", "sad", "rocket", "diamond", "ok")
