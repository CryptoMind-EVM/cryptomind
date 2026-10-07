#!/usr/bin/env python3
"""開機前的環境變數健檢——專抓「兩個變數黏成一行」。

## 為什麼需要這支

2026-09-06 同一個錯誤在幾小時內犯了兩次，第二次讓網站停機約一小時：

    APP_LOG_LEVEL=INFOAUTH_LOCKOUT_ENABLED＝true    ← 第一次，靜默：log 等級退回
                                                        WARNING、AUTH_LOCKOUT 失效
    WEB_CONCURRENCY=1AUTH_LOCKOUT_ENABLED＝true      ← 第二次，致命：gunicorn.conf.py
                                                        的 int() 直接 ValueError

第二次的關鍵是**時機**：`gunicorn.conf.py` 在設定解析階段就炸了，而 #669 的
守衛住在 app lifespan 裡——那時候還沒跑到。所以檢查必須前移到 entrypoint，
在 gunicorn 起來之前。

而且要抓的是**模式**不是單一變數。共同特徵有三個，都極不可能是合法值：

1. 值裡有**全形等號 ＝**（U+FF1D）——貼上時輸入法留下的痕跡
2. 值裡夾著另一個已知環境變數的名字
3. 值裡有換行

## 為什麼分成 warn 與 fail 兩級

只 warn 不夠：`WEB_CONCURRENCY` 那次無論如何都會炸，早點炸並說清楚才有用。
全部 fail 也不對：多加一個會擋開機的關卡，等於自己製造新的停機來源。

所以只有「本來就會讓程式掛掉」的（數值型欄位解析不了）才 exit 1，其餘一律
warn。這支的目標是把難懂的崩潰換成看得懂的訊息，不是增加失敗模式。
"""

from __future__ import annotations

import os
import re
import sys

#: 必須是整數的環境變數。解析不了 = 本來就會炸，這裡提早炸並說清楚。
NUMERIC_VARS = (
    "WEB_CONCURRENCY",
    "ANALYSIS_TIMEOUT_SECONDS",
    "ROUTER_TIMEOUT_SECONDS",
    "AUTH_FAILURE_THRESHOLD",
    "AUTH_FAILURE_WINDOW_SECONDS",
    "AUTH_LOCKOUT_THRESHOLD",
    "GUEST_DAILY_QUESTIONS",
    "GUEST_GLOBAL_DAILY_CAP",
)

#: entrypoint 認得的「確定是 env 有問題」離場碼。其他非零一律當成
#: 「檢查本身壞了」，不擋開機——見 main() 的註解。
#:
#: 刻意不用 1 或 2：python 自己就會用它們（1 = 未捕捉的例外、
#: 2 = 檔案打不開／argparse 錯誤）。撞號的話「腳本沒進 image」會被誤判成
#: 「env 壞掉」而擋住開機——那正是這支要避免的事。42 沒有任何直譯器路徑會產生。
EXIT_BAD_ENV = 42

#: 全形等號／冒號。半形以外的等號出現在值裡，幾乎只可能是貼上時黏到的。
_FULLWIDTH = re.compile(r"[＝：]")


def _looks_like_glued(name: str, value: str, all_names: set[str]) -> str | None:
    """回傳問題描述，沒問題回 None。"""
    if _FULLWIDTH.search(value):
        return "值裡有全形等號／冒號——多半是兩個變數貼成同一行"
    if "\n" in value or "\r" in value:
        return "值裡有換行——多半是兩個變數貼成同一行"
    for other in all_names:
        if other != name and len(other) >= 8 and other in value:
            return f"值裡夾著另一個環境變數的名字 {other}——多半是兩個變數貼成同一行"
    return None


def main() -> int:
    env = dict(os.environ)
    names = set(env)
    problems: list[str] = []
    fatal: list[str] = []

    for name in sorted(env):
        value = env[name]
        if not value:
            continue
        reason = _looks_like_glued(name, value, names)
        if reason:
            # 不印完整值：可能是金鑰。只印前 12 字元足以認出是哪一行。
            problems.append(f"  ⚠️  {name}={value[:12]!r}... — {reason}")

    for name in NUMERIC_VARS:
        raw = env.get(name)
        if raw is None or raw.strip() == "":
            continue
        try:
            int(raw)
        except ValueError:
            fatal.append(
                f"  ❌ {name}={raw!r} 不是整數。"
                f"這個值會讓 gunicorn/應用在啟動時直接崩潰（2026-09-06 因此停機約一小時）。"
            )

    if problems:
        print("[env-check] 疑似黏行的環境變數：", file=sys.stderr)
        for p in problems:
            print(p, file=sys.stderr)

    if fatal:
        print("[env-check] 會導致啟動失敗的環境變數：", file=sys.stderr)
        for f in fatal:
            print(f, file=sys.stderr)
        print(
            "[env-check] 請到 .env.production（自架 VM）把上列變數拆成獨立的行、等號用半形 =。",
            file=sys.stderr,
        )
        # 刻意用 2 而不是 1：entrypoint 只在「確定是 env 有問題」時中止。
        # 這支自己壞掉（檔案沒進 image、import 失敗、python 版本問題）會回
        # 1 或 127，那種情況**不該**擋開機——否則這個為了防停機而加的檢查，
        # 自己變成新的停機來源。
        return EXIT_BAD_ENV

    if not problems:
        print("[env-check] 環境變數健檢通過。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
