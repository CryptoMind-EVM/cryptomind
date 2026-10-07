"""同步工具/官方 Skill 目錄的 i18n keys 到前端 JSON（toolSettings/skill-manager 查表層的資料源）。

問題背景（2026-08-16 DANNY 回報）：
- Tools：前端 toolSettings.js 已有 ``tools.<id>.name/.description`` 查表層，但 4 語 JSON
  只含 42/84 個工具——後半批新工具缺譯，UI fallback 顯示後端 _TOOLS_SEED 的繁中。
- Skills：18 個官方 skill 描述為英文寫死，skill-manager.js 無查表層。

本腳本（冪等，可重複執行）：
1. Tools —— 資料源 ``core/agents/tool_name_translations.py``（en/zh-CN/ru）＋
   ``core/database/tools.py`` ``_TOOLS_SEED``（zh-TW display_name/description），
   產出 ``tools.<id>.{name,description}`` ×4 語。新增工具時補翻譯表後重跑即可。
2. Skills —— 內嵌 ``SKILL_TRANSLATIONS``（官方 skill 名稱/描述 ×4 語），
   產出 ``skills.<id>.{name,description}``。官方 skill 清單異動時同步更新本 dict。

用法：``.venv/bin/python scripts/gen_catalog_i18n.py``
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

I18N_DIR = ROOT / "web" / "js" / "i18n"
LANGS = ("zh-TW", "zh-CN", "en", "ru")

# ── 官方 Skill 目錄翻譯（名稱＋描述；body 為功能性 prompt 內容不翻譯）──
# 對應 core/agents/skill_loader.py 載入的官方 skills；鍵 = skill name。
SKILL_TRANSLATIONS: dict[str, dict[str, dict[str, str]]] = {
    "global-stock-analysis": {
        "zh-TW": {"name": "全球股市分析", "description": "港/日/韓/印/A 股指數的市場結構、外資流向、板塊輪動與估值比較。問港股、日股、韓股、恒指、日經、KOSPI 時使用。"},
        "zh-CN": {"name": "全球股市分析", "description": "港/日/韩/印/A 股指数的市场结构、外资流向、板块轮动与估值比较。问港股、日股、韩股、恒指、日经、KOSPI 时使用。"},
        "en": {"name": "Global Stock Analysis", "description": "Market structure, foreign-capital flow, sector rotation, and valuation comparison for HK / Japan / Korea / India / A-share indices. Use for HK/JP/KR stock questions."},
        "ru": {"name": "Анализ мировых рынков", "description": "Структура рынка, потоки иностранного капитала, ротация секторов и оценка для индексов Гонконга, Японии, Кореи, Индии, Китая."},
    },
    "commodity-analysis": {
        "zh-TW": {"name": "大宗商品分析", "description": "黃金/原油/白銀的供需結構、庫存數據、季節性型態與美元相關性。問商品、黃金、油價、白銀、原物料時使用。"},
        "zh-CN": {"name": "大宗商品分析", "description": "黄金/原油/白银的供需结构、库存数据、季节性形态与美元相关性。问商品、黄金、油价、白银、原材料时使用。"},
        "en": {"name": "Commodity Analysis", "description": "Supply-demand structure, inventory data, seasonal patterns, and USD correlation for gold / oil / silver."},
        "ru": {"name": "Анализ сырьевых товаров", "description": "Структура спроса-предложения, данные по запасам, сезонность и корреляция с USD для золота, нефти, серебра."},
    },
    "macro-economic-indicators": {
        "zh-TW": {"name": "宏觀經濟指標解讀", "description": "CPI/PPI/非農/GDP/Fed 決策的數據解讀與跨市場影響評估。問通膨、升息、聯準會、經濟數據時使用。"},
        "zh-CN": {"name": "宏观经济指标解读", "description": "CPI/PPI/非农/GDP/Fed 决策的数据解读与跨市场影响评估。问通胀、加息、美联储、经济数据时使用。"},
        "en": {"name": "Macro Indicators", "description": "Data reading and cross-market impact assessment for CPI/PPI/NFP/GDP/Fed decisions."},
        "ru": {"name": "Макроиндикаторы", "description": "Интерпретация CPI/PPI/NFP/ВВП/решений ФРС и их влияние на рынки."},
    },
    "comparison-analysis": {
        "zh-TW": {"name": "多資產比較分析", "description": "比較兩個以上資產（幣/股/商品）時的資料蒐集方法與表格輸出格式。「A 跟 B 哪個好」「比較 A 和 B」時使用。"},
        "zh-CN": {"name": "多资产比较分析", "description": "比较两个以上资产（币/股/商品）时的数据收集方法与表格输出格式。「A 跟 B 哪个好」「比较 A 和 B」时使用。"},
        "en": {"name": "Comparison Analysis", "description": "Data collection and table output format for comparing two or more assets (coins, stocks, commodities)."},
        "ru": {"name": "Сравнительный анализ", "description": "Методика сбора данных и формат таблицы для сравнения двух и более активов."},
    },
    "crypto-technical-analysis": {
        "zh-TW": {"name": "加密貨幣技術分析", "description": "RSI/MACD/KD/均線、支撐壓力與趨勢方向的完整解讀。問技術面、價格走勢、超買超賣、支撐壓力時使用。"},
        "zh-CN": {"name": "加密货币技术分析", "description": "RSI/MACD/KD/均线、支撑压力与趋势方向的完整解读。问技术面、价格走势、超买超卖、支撑压力时使用。"},
        "en": {"name": "Crypto Technical Analysis", "description": "Comprehensive read of RSI/MACD/KD/moving averages, support/resistance, and trend direction."},
        "ru": {"name": "Теханализ крипто", "description": "Полный разбор RSI/MACD/KD/скользящих средних, поддержки/сопротивления и направления тренда."},
    },
    "tw-stock-technical": {
        "zh-TW": {"name": "台股技術分析", "description": "K 線型態、均線排列、KD/RSI/MACD、量價關係與融券融資綜合解讀。問台股技術面、價格走勢、支撐壓力位時使用。"},
        "zh-CN": {"name": "台股技术分析", "description": "K 线形态、均线排列、KD/RSI/MACD、量价关系与融资融券综合解读。问台股技术面、价格走势、支撑压力位时使用。"},
        "en": {"name": "TW Stock Technical", "description": "Candlestick patterns, moving averages, KD/RSI/MACD, volume-price relationship, and margin trading read."},
        "ru": {"name": "Теханализ тайваньских акций", "description": "Свечные паттерны, скользящие средние, KD/RSI/MACD, связь объёма и цены, маржинальная торговля."},
    },
    "forex-macro-analysis": {
        "zh-TW": {"name": "外匯宏觀分析", "description": "主要貨幣對（美元/歐元/日圓/英鎊/人民幣）的利差、央行政策、避險資金流與技術結構。問匯率、外匯、美元走勢時使用。"},
        "zh-CN": {"name": "外汇宏观分析", "description": "主要货币对（美元/欧元/日元/英镑/人民币）的利差、央行政策、避险资金流与技术结构。问汇率、外汇、美元走势时使用。"},
        "en": {"name": "Forex Macro Analysis", "description": "Interest-rate differentials, central bank policy, safe-haven flows, and technicals for major currency pairs."},
        "ru": {"name": "Макроанализ форекс", "description": "Дифференциалы ставок, политика ЦБ, защитные потоки и техструктура основных валютных пар."},
    },
    "tw-stock-institutional": {
        "zh-TW": {"name": "台股籌碼分析", "description": "外資/投信/自營商三大法人買賣超、外資持股與營收分析。問台股籌碼面、法人動向時使用。"},
        "zh-CN": {"name": "台股筹码分析", "description": "外资/投信/自营商三大法人买卖超、外资持股与营收分析。问台股筹码面、法人动向时使用。"},
        "en": {"name": "TW Institutional Flow", "description": "Net buy/sell by foreign / investment trust / dealer, foreign holdings, and monthly revenue analysis."},
        "ru": {"name": "Институционалы Тайваня", "description": "Чистые покупки/продажи иностранцев, трастов и дилеров, доля иностранцев, месячная выручка."},
    },
    "us-stock-earnings": {
        "zh-TW": {"name": "美股財報分析", "description": "EPS/營收/財測、機構持倉與內部人交易解讀。問財報、EPS、法人動態時使用。"},
        "zh-CN": {"name": "美股财报分析", "description": "EPS/营收/财测、机构持仓与内部人交易解读。问财报、EPS、法人动态时使用。"},
        "en": {"name": "US Earnings Analysis", "description": "EPS/revenue/guidance, institutional holdings, and insider-transaction interpretation."},
        "ru": {"name": "Отчётности США", "description": "EPS/выручка/прогнозы, институциональные позиции и сделки инсайдеров."},
    },
    "crypto-fundamental-analysis": {
        "zh-TW": {"name": "加密貨幣基本面分析", "description": "鏈上數據、TVL、代幣經濟、鯨魚動向與資金費率的綜合評估。問某幣值不值得買、基本面、鏈上數據時使用。"},
        "zh-CN": {"name": "加密货币基本面分析", "description": "链上数据、TVL、代币经济、鲸鱼动向与资金费率的综合评估。问某币值不值得买、基本面、链上数据时使用。"},
        "en": {"name": "Crypto Fundamentals", "description": "On-chain data, TVL, tokenomics, whale activity, and funding rates assessment."},
        "ru": {"name": "Фундаментал крипто", "description": "On-chain данные, TVL, токеномика, активность китов и ставки финансирования."},
    },
    "us-stock-technical": {
        "zh-TW": {"name": "美股技術分析", "description": "RSI/MACD/布林/均線、52 週高低點與技術訊號解讀。問美股技術面、走勢、圖表時使用。"},
        "zh-CN": {"name": "美股技术分析", "description": "RSI/MACD/布林/均线、52 周高低点与技术信号解读。问美股技术面、走势、图表时使用。"},
        "en": {"name": "US Stock Technical", "description": "RSI/MACD/Bollinger/moving averages, 52-week highs/lows, and technical-signal interpretation."},
        "ru": {"name": "Теханализ акций США", "description": "RSI/MACD/полосы Боллинджера, 52-недельные экстремумы и интерпретация сигналов."},
    },
    "risk-assessment-review": {
        "zh-TW": {"name": "風控委員會覆核", "description": "給出判斷後，從積極/中性/保守三種風險傾向覆核風險揭露是否充足，涵蓋波動度、歷史回撤、集中度、流動性與資料限制。只做分析，不產出個人化買賣規則（改編自 TradingAgents 風控委員會）。"},
        "zh-CN": {"name": "风控委员会复核", "description": "给出判断后，从积极/中性/保守三种风险倾向复核风险披露是否充足，涵盖波动度、历史回撤、集中度、流动性与数据限制。只做分析，不产出个人化买卖规则（改编自 TradingAgents 风控委员会）。"},
        "en": {"name": "Risk Committee Review", "description": "After a judgment is given, review risk disclosure from aggressive, neutral, and conservative views: volatility, historical drawdowns, concentration, liquidity, data limits. Analysis only, no personalised trading rules (adapted from TradingAgents)."},
        "ru": {"name": "Комитет по риску", "description": "После оценки — проверка раскрытия рисков с трёх профилей риска: волатильность, исторические просадки, концентрация, ликвидность, ограничения данных. Только анализ, без персональных торговых правил (по TradingAgents)."},
    },
    "swap-quote": {
        "zh-TW": {"name": "TON 代幣換匯報價", "description": "透過 Omniston 聚合器查詢 TON 換匯報價（最佳路徑/匯率/滑價/價格影響）。唯讀，不執行換匯。問「TON 換 USDt 能拿多少」時使用。"},
        "zh-CN": {"name": "TON 代币兑换报价", "description": "通过 Omniston 聚合器查询 TON 兑换报价（最佳路径/汇率/滑点/价格影响）。只读，不执行兑换。问「TON 换 USDt 能拿多少」时使用。"},
        "en": {"name": "TON Swap Quote", "description": "TON/jetton swap quote via Omniston aggregator: best route, rate, slippage, price impact. Read-only — does NOT execute."},
        "ru": {"name": "Обмен TON (котировка)", "description": "Котировка обмена TON через Omniston: маршрут, курс, проскальзывание. Только чтение — без исполнения."},
    },
    "bull-bear-debate": {
        "zh-TW": {"name": "多空辯論壓力測試", "description": "當使用者質疑判斷或要求深入權衡時，先模擬多空對辯再下結論，避免單一視角偏差（改編自 TradingAgents 多空研究員機制）。"},
        "zh-CN": {"name": "多空辩论压力测试", "description": "当用户质疑判断或要求深入权衡时，先模拟多空对辩再下结论，避免单一视角偏差（改编自 TradingAgents 多空研究员机制）。"},
        "en": {"name": "Bull-Bear Debate", "description": "When the user challenges a judgment, force a simulated bull vs bear debate before concluding, avoiding single-perspective bias (adapted from TradingAgents)."},
        "ru": {"name": "Дебаты «быки против медведей»", "description": "При сомнении в выводе — принудительные дебаты быков и медведей перед заключением, против одностороннего смещения (по TradingAgents)."},
    },
    "tw-stock-fundamentals": {
        "zh-TW": {"name": "台股基本面分析", "description": "本益比、殖利率、股價淨值比、股利政策、重大公告與估值評估。問台股基本面、值不值得買、股利時使用。"},
        "zh-CN": {"name": "台股基本面分析", "description": "市盈率、殖利率、市净率、股利政策、重大公告与估值评估。问台股基本面、值不值得买、股利时使用。"},
        "en": {"name": "TW Stock Fundamentals", "description": "P/E, dividend yield, P/B, dividend policy, major announcements, and valuation assessment."},
        "ru": {"name": "Фундаментал тайваньских акций", "description": "P/E, дивидендная доходность, P/B, дивидендная политика, объявления и оценка."},
    },
    "investment-judgment": {
        "zh-TW": {"name": "結構化投資判斷", "description": "被問某資產值不值得買/是否進場時，強制「多 → 空 → 綜合」結構（改編自 FinRobot Financial CoT），研究沒做完不給倉促答案。"},
        "zh-CN": {"name": "结构化投资判断", "description": "被问某资产值不值得买/是否进场时，强制「多 → 空 → 综合」结构（改编自 FinRobot Financial CoT），研究没做完不给仓促答案。"},
        "en": {"name": "Structured Judgment", "description": "When asked if an asset is worth buying, force a 'bull → bear → synthesis' structure (adapted from FinRobot Financial CoT)."},
        "ru": {"name": "Структурное суждение", "description": "На вопрос «стоит ли покупать» — структура «быки → медведи → синтез» (по FinRobot Financial CoT)."},
    },
    "market-risk-assessment": {
        "zh-TW": {"name": "市場風險評估", "description": "跨市場風險分析：恐慌貪婪指數、資金費率、VIX 與相關性分析。問大盤風險、市場情緒時使用。"},
        "zh-CN": {"name": "市场风险评估", "description": "跨市场风险分析：恐慌贪婪指数、资金费率、VIX 与相关性分析。问大盘风险、市场情绪时使用。"},
        "en": {"name": "Market Risk Assessment", "description": "Cross-market risk analysis: fear & greed index, funding rates, VIX, and correlation analysis."},
        "ru": {"name": "Оценка рыночного риска", "description": "Кросс-рыночный анализ риска: индекс страха и жадности, ставки финансирования, VIX, корреляции."},
    },
    "community-engagement": {
        "zh-TW": {"name": "社群與 TON 生態指引", "description": "發文建議、打賞流程、會員升級與 TON Connect 操作指引。問論壇、發文、打賞、升級、社群功能時使用。"},
        "zh-CN": {"name": "社群与 TON 生态指引", "description": "发文建议、打赏流程、会员升级与 TON Connect 操作指引。问论坛、发文、打赏、升级、社群功能时使用。"},
        "en": {"name": "Community & TON Guide", "description": "Posting advice, tipping flow, membership upgrade, and TON Connect operation guides."},
        "ru": {"name": "Сообщество и TON", "description": "Советы по постам, чаевые, повышение членства и инструкции TON Connect."},
    },
}


def build_tool_entries() -> dict[str, dict[str, dict[str, str]]]:
    """tool_id → lang → {name, description}（zh-TW 取自 _TOOLS_SEED）。"""
    from core.agents.tool_name_translations import _TRANSLATIONS
    from core.database.tools import _TOOLS_SEED

    entries: dict[str, dict[str, dict[str, str]]] = {}
    missing: list[str] = []
    for tool in _TOOLS_SEED:
        tid = tool["tool_id"]
        per_lang: dict[str, dict[str, str]] = {
            "zh-TW": {
                "name": tool["display_name"],
                "description": tool.get("description", ""),
            }
        }
        for lang in ("en", "zh-CN", "ru"):
            t = _TRANSLATIONS.get(tid, {}).get(lang, {})
            if not t.get("name"):
                missing.append(f"{tid}[{lang}]")
            # 翻譯表欄位為 name/desc（見 tool_name_translations.py 結構）
            per_lang[lang] = {
                "name": t.get("name", tool["display_name"]),
                "description": t.get("desc", tool.get("description", "")),
            }
        entries[tid] = per_lang
    if missing:
        print(f"⚠️ 翻譯表缺 {len(missing)} 項（fallback seed 繁中）: {missing[:5]}")
    return entries


def main() -> None:
    tool_entries = build_tool_entries()
    for lang in LANGS:
        path = I18N_DIR / f"{lang}.json"
        data = json.loads(path.read_text(encoding="utf-8"))

        tools = data.setdefault("tools", {})
        for tid, per_lang in tool_entries.items():
            tools[tid] = per_lang[lang]
        skills = data.setdefault("skills", {})
        for sid, per_lang in SKILL_TRANSLATIONS.items():
            skills[sid] = per_lang[lang]

        # indent=2 + newline="\n"：對齊版本庫裡 4 份 JSON 的既有格式。
        # 寫錯任一個都會讓整份檔案重排——indent=4 會改掉每一行的縮排，而
        # write_text 預設的 newline=None 在 Windows 上會把 \n 換成 \r\n。
        # 兩者都會讓「只改一行文案」變成 8000+ 行的 diff（2026-09-22 踩過）。
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print(f"{lang}: tools={len(tools)} skills={len(skills)} ✓")
    print("完成——記得 bump web/js/i18n.js 的 ?v= 版號。")


if __name__ == "__main__":
    main()
