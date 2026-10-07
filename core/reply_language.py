"""對話回覆語言：照使用者這則訊息的語言回；看不出來才用帳號語言（2026-09-28 DANNY）。

早報、系統通知這類主動發的訊息沒有「這則訊息」可以參考，仍照帳號語言（users.language）。
只認平台支援的四種（core.i18n.SUPPORTED_LANGUAGES）；日文、韓文等看不出來就回 None。

判斷刻意保守——短訊息、只有代號（BTC／NVDA）、地址、數字都不算，免得一句 "BTC?" 就把
繁中使用者切成英文。
"""

from __future__ import annotations

import re
import time
from collections import OrderedDict
from typing import Optional

# 簡體、繁體各自專用的常用字（兩邊寫法不同的字）；出現哪一邊多就是哪一邊
_SIMPLIFIED_ONLY = set(
    "这们个为说时会来对发么后过还没让请问买卖币价涨钱资产应该实现点几头样进开关间边从当经场动务帮吗"
    "吗们现给长东车书见关门听写业亿众优伤传侧军农冲决况净凭则刚创删别动劳势区华单卫却厂历压厉县参双"
    "变号叹吓后吴启员响团围图国圆块坏坚坛执报场尘尽层岁岛币师帐带帮广庆库应废开异弃张弹强归当录彻征径"
    "总恶惊惯态怀恋悬惧战户扑执扩扫扬扰抚抢护报担拟拥择挂挡挣挤挥损换据掳摄摇摊数断无旧时显晒晓术机杀"
    "杂权条来杨极构枪柜标栏树样档桥梦检楼欢歼毁毕气汇汉汤沟没泪泽洁洒浅测济浓涂涨润涩渊渐渔温湿湾满"
    "滚滞滤灭灯灵灾炉点炼烂热焕爱爷牵犹状独狭猎猫献现环玛琐电画畅疗疮疯痒症瘫盐监盖盘着矫码砖础确碍礼"
    "祸离种积称稳穷窃竞笔笋筑简粮紧纠红纤约级纪纯纲纳纵纷纸纹纺线练组细织终经结绕绘给络绝统继绩续维绵"
    "综绿缓编缘缩缴网罗罚罢职联聪肃肠肤肿胀胜脑脚脸腊腾舰艰艺节芦苏范茎荐荡药莱获萝营萧虑虽虾蚀蛮补装"
    "见观规视览觉触誉计订认讨让训议讯记讲许论设访证评识诉诊词译试诗诚话询该详语误说请诸读课谁调谈谋谢"
    "谣谱贝负贡财责败货质贩贪贫购贯贵贷费贸赋赌赏赔赖赚赛赞赠赢赵赶趋跃践踪轨轮软轰轻载较辅辆辈辉输辞"
    "边达迁过运还这进远违连迟适选逊递逻遗遥邓邮邻郑酱释针钓钟钢钥钱铁铃铜铭银铺链销锁锅错锻锦键镇镜长"
    "门闪闭问闯闲间闷闹闻阀阅队阳阴阵阶际陆陈陕险随隐隶难雾静韩页顶项顺须顾顿预领频题颜额风飞饭饮饰饱"
    "饼馆馈驱驶驻驾验骂骑骗骤鱼鲁鲜鸟鸡鸣鸭鸿龙"
)
_TRADITIONAL_ONLY = set(
    "這們個為說時會來對發麼後過還沒讓請問買賣幣價漲錢資產應該實現點幾頭樣進開關間邊從當經場動務幫嗎"
    "給長東車書見門聽寫業億眾優傷傳側軍農衝決況淨憑則剛創刪別勞勢區華單衛卻廠歷壓厲縣參雙變號嘆嚇吳"
    "啟員響團圍圖國圓塊壞堅壇執報塵盡層歲島師帳帶廣慶庫廢異棄張彈強歸錄徹徵徑總惡驚慣態懷戀懸懼戰戶撲"
    "擴掃揚擾撫搶護擔擬擁擇掛擋掙擠揮損換據擄攝搖攤數斷無舊顯曬曉術機殺雜權條楊極構槍櫃標欄樹檔橋夢檢"
    "樓歡殲毀畢氣匯漢湯溝淚澤潔灑淺測濟濃塗潤澀淵漸漁溫濕灣滿滾滯濾滅燈靈災爐煉爛熱煥愛爺牽猶狀獨狹獵"
    "貓獻環瑪瑣電畫暢療瘡瘋癢癥癱鹽監蓋盤著矯碼磚礎確礙禮禍離種積稱穩窮竊競筆筍築簡糧緊糾紅纖約級紀純"
    "綱納縱紛紙紋紡線練組細織終結繞繪絡絕統繼績續維綿綜綠緩編緣縮繳網羅罰罷職聯聰肅腸膚腫脹勝腦腳臉臘"
    "騰艦艱藝節蘆蘇範莖薦蕩藥萊獲蘿營蕭慮雖蝦蝕蠻補裝觀規視覽覺觸譽計訂認討訓議訊記講許論設訪證評識訴"
    "診詞譯試詩誠話詢詳語誤讀課誰調談謀謝謠譜貝負貢財責敗貨質販貪貧購貫貴貸費貿賦賭賞賠賴賺賽贊贈贏趙"
    "趕趨躍踐蹤軌輪軟轟輕載較輔輛輩輝輸辭達遷運遠違連遲適選遜遞邏遺遙鄧郵鄰鄭醬釋針釣鐘鋼鑰鐵鈴銅銘銀"
    "鋪鏈銷鎖鍋錯鍛錦鍵鎮鏡閃閉闖閒悶鬧聞閥閱隊陽陰陣階際陸陳陝險隨隱隸難霧靜韓頁頂項順須顧頓預領頻題"
    "顏額風飛飯飲飾飽餅館饋驅駛駐駕驗罵騎騙驟魚魯鮮鳥雞鳴鴨鴻龍"
)
_SIMPLIFIED_ONLY -= _TRADITIONAL_ONLY
_TRADITIONAL_ONLY -= _SIMPLIFIED_ONLY

# 不算語言的東西：網址、email、地址（0x…、TON EQ/UQ…、長 base58）、數字
_NOISE = re.compile(
    r"https?://\S+|\S+@\S+\.\S+|0x[0-9a-fA-F]{6,}|\b(?:EQ|UQ)[\w-]{20,}|\b[1-9A-HJ-NP-Za-km-z]{25,}\b|\d[\d.,%:/-]*"
)
_CJK = re.compile(r"[一-鿿㐀-䶿]")
_KANA_HANGUL = re.compile(r"[぀-ヿ가-힯]")
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")
_LATIN_WORD = re.compile(r"[A-Za-z][A-Za-z'’-]*")


def detect_message_language(
    text: Optional[str], account_language: Optional[str] = None
) -> Optional[str]:
    """這則訊息明顯是哪種支援語言；看不出來回 None。

    ``account_language`` 只用來決定「看得出是中文、但分不出簡繁」時用哪一種。
    """
    if not text:
        return None
    body = _NOISE.sub(" ", text)
    if _KANA_HANGUL.search(body):
        return None  # 日文／韓文：不支援，交給帳號語言
    cjk = _CJK.findall(body)
    if len(cjk) >= 2:
        simplified = sum(ch in _SIMPLIFIED_ONLY for ch in cjk)
        traditional = sum(ch in _TRADITIONAL_ONLY for ch in cjk)
        if simplified > traditional:
            return "zh-CN"
        if traditional > simplified:
            return "zh-TW"
        account = (account_language or "").strip()
        return account if account in ("zh-TW", "zh-CN") else "zh-TW"
    if len(_CYRILLIC.findall(body)) >= 3:
        return "ru"
    # 英文：至少兩個不是代號的詞（全大寫 2～6 字母＝BTC／NVDA 這類代號，不算）
    words = [
        w
        for w in _LATIN_WORD.findall(body)
        if len(w) >= 2 and not (w.isupper() and len(w) <= 6)
    ]
    if len(words) >= 2 and not cjk:
        return "en"
    return None


# 同一個對話記住上一則看得出的語言：接著只打 "and ETH?" 這種短句時沿用，不會中途跳回帳號語言。
# 每個 process 各一份（多 worker 時偶爾沒記到，就退回帳號語言，可接受）。
_STICKY_TTL_S = 2 * 60 * 60
_STICKY_MAX = 5000
_sticky: "OrderedDict[str, tuple[str, float]]" = OrderedDict()


def _remember(key: str, language: str) -> None:
    _sticky[key] = (language, time.monotonic())
    _sticky.move_to_end(key)
    while len(_sticky) > _STICKY_MAX:
        _sticky.popitem(last=False)


def _recall(key: str) -> Optional[str]:
    hit = _sticky.get(key)
    if not hit:
        return None
    language, at = hit
    if time.monotonic() - at > _STICKY_TTL_S:
        _sticky.pop(key, None)
        return None
    return language


def resolve_reply_language(
    message: Optional[str],
    account_language: Optional[str],
    default: str = "zh-TW",
    conversation_key: Optional[str] = None,
) -> str:
    """對話回覆語言：這則訊息看得出來就照訊息；否則同一對話上一則的語言；
    再否則帳號語言；最後 ``default``。"""
    detected = detect_message_language(message, account_language)
    if detected:
        if conversation_key:
            _remember(conversation_key, detected)
        return detected
    if conversation_key:
        sticky = _recall(conversation_key)
        if sticky:
            return sticky
    return account_language or default
