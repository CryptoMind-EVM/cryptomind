"""E2E guards for the mobile bottom stack (input / hint bar / scroll padding).

背景:手機版底部有兩層 — 聊天輸入框、無金鑰提示條(2026-09-24 貼底導覽列拔除前是三層)。
它們的位置全部來自 ui-shell.js 量出來的 --shell-* 變數。曾經壞掉的方式:

- .tab-content 的動畫留下 transform 與 will-change,讓 tab 成為 fixed 子孫的
  containing block,底部整組跟著 tab 位移 10px。
- 輸入框在 HTML 是 sticky,但手機版 CSS 的 bottom 值是按 fixed 寫的,差幾 px。
- 捲動區的底部留白寫死 11rem,與實際量到的高度對不上。

這些都不會噴錯,只會讓元素互相遮住,所以用實際幾何來守。
"""

from __future__ import annotations

import importlib.util

import pytest

BASE_URL = "http://127.0.0.1:8770/static/index.html"
MOBILE_VIEWPORT = {"width": 375, "height": 812}


def _requires_playwright():
    if importlib.util.find_spec("playwright") is None:
        pytest.skip("playwright is not installed")


async def _open_mobile(page):
    await page.set_viewport_size(MOBILE_VIEWPORT)
    await page.goto(BASE_URL)
    await page.wait_for_selector("[data-shell-fixed-input]", state="attached")
    # 等 tab 進場動畫跑完 — transform 殘留只有在動畫結束後才看得準
    await page.wait_for_timeout(700)


@pytest.mark.e2e
async def test_visible_tabs_do_not_become_fixed_containing_block(page):
    """.tab-content 不可留下 transform:底部 fixed 元素的參考框必須是視窗。"""
    _requires_playwright()
    await _open_mobile(page)

    offenders = await page.evaluate(
        """() => [...document.querySelectorAll('main .tab-content')]
              .filter(el => !el.classList.contains('hidden'))
              .map(el => ({ id: el.id, transform: getComputedStyle(el).transform }))
              .filter(el => el.transform && el.transform !== 'none')"""
    )

    assert offenders == [], (
        "這些 tab 帶著 transform,會讓底部 fixed 元素相對 tab 而不是視窗定位: "
        f"{offenders}"
    )


@pytest.mark.e2e
async def test_bottom_layers_do_not_overlap(page):
    """輸入框貼底、無金鑰提示條在它上面,彼此不可重疊;底部導覽列已不存在。"""
    _requires_playwright()
    await _open_mobile(page)

    geometry = await page.evaluate(
        """async () => {
            const warn = document.getElementById('no-llm-key-warning');
            // 強制顯示提示條,重現「已登入但沒有 API Key」
            warn.classList.remove('hidden');
            warn.style.setProperty('display', 'block', 'important');
            window.UIShell.syncLayout();
            await new Promise(r => setTimeout(r, 300));

            const box = (el) => {
                const r = el.getBoundingClientRect();
                return { top: r.top, bottom: r.bottom, height: r.height };
            };
            return {
                viewportHeight: window.innerHeight,
                hasBottomNav: !!document.getElementById('global-nav-container'),
                input: box(document.querySelector('[data-shell-fixed-input]')),
                inputRow: box(document.querySelector('[data-shell-fixed-input] > div')),
                warning: box(warn),
            };
        }"""
    )

    input_bar = geometry["input"]
    input_row = geometry["inputRow"]
    warning = geometry["warning"]

    assert geometry["hasBottomNav"] is False, "手機底部導覽列已於 2026-09-24 拔除,不該再出現"
    assert input_bar["bottom"] == pytest.approx(geometry["viewportHeight"], abs=1), (
        f"輸入框沒有貼齊視窗底部: bottom={input_bar['bottom']} viewport={geometry['viewportHeight']}"
    )
    assert input_row["bottom"] <= geometry["viewportHeight"] + 1, (
        f"輸入列超出視窗底部: inputRow.bottom={input_row['bottom']}"
    )
    assert warning["bottom"] <= input_bar["top"] + 1, (
        f"提示條壓到輸入框: warning.bottom={warning['bottom']} input.top={input_bar['top']}"
    )


@pytest.mark.e2e
async def test_input_bar_is_flush_with_viewport_bottom(page):
    """輸入框要貼齊視窗底部、有實心底 — 底下不可留縫,縫裡會透出正在捲動的訊息。

    iPhone home 指示條由輸入框自己的 padding-bottom(--mobile-safe-bottom)讓開;
    2026-09-24 以前這段空間是貼底導覽列墊的。
    """
    _requires_playwright()
    await _open_mobile(page)

    geometry = await page.evaluate(
        """() => {
            const el = document.querySelector('[data-shell-fixed-input]');
            const r = el.getBoundingClientRect();
            const cs = getComputedStyle(el);
            return {
                viewportHeight: window.innerHeight,
                bottom: r.bottom,
                position: cs.position,
                backgroundColor: cs.backgroundColor,
            };
        }"""
    )

    assert geometry["bottom"] == pytest.approx(geometry["viewportHeight"], abs=1), (
        "輸入框沒有貼齊視窗底部,底下會留下一條會透出內容的縫: "
        f"bottom={geometry['bottom']} viewport={geometry['viewportHeight']}"
    )
    # 貼底之後上緣直接切在訊息上,必須有實心底色,不能只靠漸層(透明段會透出訊息)
    assert geometry["backgroundColor"] not in ("rgba(0, 0, 0, 0)", "transparent"), (
        f"輸入框沒有實心底色,訊息會透在它後面: {geometry['backgroundColor']}"
    )


@pytest.mark.e2e
@pytest.mark.parametrize("safe_px", [0, 34])
async def test_no_empty_band_under_input(page, safe_px):
    """輸入列最後一行（免責聲明）到視窗底部只該剩安全區，不能再疊空白。

    2026-09-24 拔掉底部導覽列後，.chat-input-row 的 pb-4（16px）加上外層
    「安全區 + 0.5rem」疊在一起，免責聲明底下空了 24px（iPhone 58px）——
    以前這段被導覽列蓋住看不出來（DANNY 回報「下面會空空的」）。
    """
    _requires_playwright()
    await _open_mobile(page)
    gap = await page.evaluate(
        """(safe) => {
            document.documentElement.style.setProperty('--mobile-safe-bottom', safe + 'px');
            const d = document.getElementById('chat-disclaimer');
            return window.innerHeight - d.getBoundingClientRect().bottom;
        }""",
        safe_px,
    )
    allowed = max(8, safe_px) + 1
    assert gap <= allowed, f"免責聲明底下空了 {gap}px（安全區 {safe_px}px，上限 {allowed}px）"


@pytest.mark.e2e
async def test_measured_offsets_match_real_geometry(page):
    """量出來的 --shell-* 必須跟實際幾何一致,不可停在上一輪版面的數字。

    寫進變數會觸發 padding 重算,版面可能再變一次 —— 單次量測拿到的是「上一輪版面」
    (底部導覽列還在時實測差過 59px,表現為輸入框壓住最後一則訊息)。ResizeObserver 只在
    尺寸變化時觸發,量測自己造成的位移沒有人補量,所以 syncLayout 必須量到收斂為止。
    """
    _requires_playwright()
    await _open_mobile(page)

    result = await page.evaluate(
        """async () => {
            const bar = document.querySelector('[data-shell-fixed-input]');
            const read = () => {
                const cs = getComputedStyle(document.documentElement);
                const num = (name) => parseFloat(cs.getPropertyValue(name)) || 0;
                return {
                    stack: num('--shell-fixed-stack-offset'),
                    clearance: num('--shell-content-clearance'),
                };
            };

            // 重現「底部佔位在量完之後才出現」那一刻
            bar.style.setProperty('display', 'none', 'important');
            window.UIShell.syncLayout();
            await new Promise(r => setTimeout(r, 200));
            bar.style.removeProperty('display');

            // 這一次呼叫必須自己量到收斂 —— 再呼叫一次不該讓任何數字改變。
            // 不靠 setTimeout 等別的 mutation 補量:那樣就算單次量測有誤差也會碰巧通過。
            window.UIShell.syncLayout();
            const first = read();
            window.UIShell.syncLayout();
            const second = read();

            const input = bar.getBoundingClientRect();
            return {
                first: first,
                second: second,
                realUnion: window.innerHeight - input.top,
            };
        }"""
    )

    assert result["first"] == result["second"], (
        "syncLayout 呼叫一次沒有收斂(再呼叫一次數字就變了): "
        f"{result['first']} → {result['second']}"
    )
    assert result["first"]["stack"] == pytest.approx(result["realUnion"], abs=1), (
        "--shell-fixed-stack-offset 與實際底部佔位對不上: "
        f"變數={result['first']['stack']} 實際={result['realUnion']}"
    )


@pytest.mark.e2e
async def test_last_message_clears_input_without_hint_bar(page):
    """有 API Key(提示條不顯示)時,最後一則訊息仍不可被輸入框蓋住。

    這是線上實際會遇到的狀況:提示條原本替捲動區多墊了 ~78px,剛好蓋掉量測的誤差,
    所以「有金鑰的使用者」才會看到最後一則被輸入框壓住。
    """
    _requires_playwright()
    await _open_mobile(page)

    result = await page.evaluate(
        """async () => {
            const warn = document.getElementById('no-llm-key-warning');
            warn.style.setProperty('display', 'none', 'important');
            const box = document.getElementById('chat-messages');
            for (let i = 0; i < 20; i++) {
                const row = document.createElement('div');
                row.className = 'py-3 px-4 mx-3 mb-3 rounded-2xl bg-white/5 text-sm';
                row.textContent = '訊息 ' + (i + 1);
                box.appendChild(row);
            }
            await new Promise(r => setTimeout(r, 400));

            // 重現「底部佔位在量完之後才出現」那一刻,之後不再 await ——
            // 等下去的話應用自己的 DOM 變動會觸發補量,把單次量測的誤差蓋掉。
            const bar = document.querySelector('[data-shell-fixed-input]');
            bar.style.setProperty('display', 'none', 'important');
            window.UIShell.syncLayout();
            bar.style.removeProperty('display');
            window.UIShell.syncLayout();

            box.scrollTop = box.scrollHeight;

            const last = box.lastElementChild.getBoundingClientRect();
            const input = document.querySelector('[data-shell-fixed-input]').getBoundingClientRect();
            return { lastBottom: last.bottom, inputTop: input.top };
        }"""
    )

    assert result["lastBottom"] <= result["inputTop"] + 1, (
        "最後一則訊息被輸入框蓋住: "
        f"last.bottom={result['lastBottom']} input.top={result['inputTop']}"
    )


@pytest.mark.e2e
async def test_scroll_containers_reserve_room_for_bottom_stack(page):
    """每個捲動容器的底部留白都要蓋過被底部浮層遮住的高度。"""
    _requires_playwright()
    await _open_mobile(page)

    shortfalls = await page.evaluate(
        """() => {
            const chromeRect = document.querySelector('[data-shell-fixed-input]').getBoundingClientRect();
            const scrollers = [...document.querySelectorAll('*')].filter(el => {
                const cs = getComputedStyle(el);
                return (cs.overflowY === 'auto' || cs.overflowY === 'scroll')
                    && el.scrollHeight > el.clientHeight + 1;
            });
            return scrollers.map(el => {
                const rect = el.getBoundingClientRect();
                const pad = parseFloat(getComputedStyle(el).paddingBottom) || 0;
                // 捲動容器底邊伸到底部輸入列之下多少,就得用 padding 補回來
                const covered = Math.max(0, rect.bottom - chromeRect.top);
                return { id: el.id, cls: (el.className || '').toString().slice(0, 60),
                         padBottom: pad, covered };
            }).filter(s => s.padBottom + 1 < s.covered);
        }"""
    )

    assert shortfalls == [], (
        "這些捲動容器底部留白不足,最後一列內容會被底部浮層遮住: " f"{shortfalls}"
    )


@pytest.mark.e2e
async def test_clearance_uses_layout_viewport_not_visual(page):
    """量測基準必須跟 getBoundingClientRect 同一套座標(layout viewport)。

    鍵盤打開、或 iOS 的底部工具列出現時,visualViewport.height 會縮小,但 rect 仍然是
    layout viewport 座標。兩套混用的話量出來的底部佔位會少掉整個鍵盤/工具列的高度
    (實測要 399px 只量到 137px),捲動區留白不足 —— AI 回覆最後那句免責聲明就滑到
    輸入框底下。桌機瀏覽器模擬手機時兩個高度相同,所以只能用假的 visualViewport 重現。
    """
    _requires_playwright()
    await _open_mobile(page)

    result = await page.evaluate(
        """async () => {
            const root = document.documentElement;
            const realVV = window.visualViewport;
            // 重現真實手機:可見高度縮小,layout viewport 不變
            Object.defineProperty(window, 'visualViewport', {
                value: { height: 512, offsetTop: 0, addEventListener() {}, removeEventListener() {} },
                configurable: true, writable: true,
            });
            root.style.setProperty('--chat-keyboard-offset', '300px');
            root.classList.add('chat-keyboard-open');
            window.UIShell.syncLayout();

            const scroller = document.getElementById('chat-messages');
            const input = document.querySelector('[data-shell-fixed-input]').getBoundingClientRect();
            const out = {
                clearance:
                    parseFloat(getComputedStyle(root).getPropertyValue('--shell-content-clearance')) || 0,
                // 捲動區底邊被輸入框吃掉多少 —— 這才是要留的白
                needed: scroller.getBoundingClientRect().bottom - input.top,
            };

            Object.defineProperty(window, 'visualViewport', {
                value: realVV, configurable: true, writable: true,
            });
            root.classList.remove('chat-keyboard-open');
            root.style.setProperty('--chat-keyboard-offset', '0px');
            window.UIShell.syncLayout();
            return out;
        }"""
    )

    assert result["clearance"] == pytest.approx(result["needed"], abs=1), (
        "可見高度縮小時量出來的底部佔位不足(基準混用了 visualViewport): "
        f"clearance={result['clearance']} 需要={result['needed']}"
    )


@pytest.mark.e2e
async def test_scroll_stays_anchored_when_bottom_stack_grows(page):
    """底部佔位變大時,原本貼底的內容要留在底部,不可被擠到浮層後面。

    只加 padding 不夠:scrollTop 不動,最後一列還在原來的 y,新出現的浮層就蓋在它上面。
    鍵盤打開、輸入框長高、提示條出現都是同一類情況。
    """
    _requires_playwright()
    await _open_mobile(page)

    result = await page.evaluate(
        """async () => {
            const box = document.getElementById('chat-messages');
            const warn = document.getElementById('no-llm-key-warning');
            box.innerHTML = '';
            warn.style.setProperty('display', 'none', 'important');
            for (let i = 0; i < 20; i++) {
                const row = document.createElement('div');
                row.className = 'py-3 px-4 mx-3 mb-3 rounded-2xl bg-white/5 text-sm';
                row.textContent = '訊息 ' + (i + 1);
                box.appendChild(row);
            }
            await new Promise(r => setTimeout(r, 300));
            window.UIShell.syncLayout();
            box.scrollTop = box.scrollHeight;   // 使用者正在看最新的一則

            // 底部佔位變大:提示條出現。之後不 await,直接看最後一列還在不在
            warn.classList.remove('hidden');
            warn.style.setProperty('display', 'block', 'important');
            window.UIShell.syncLayout();

            const last = box.lastElementChild.getBoundingClientRect();
            return { lastBottom: last.bottom, layerTop: warn.getBoundingClientRect().top };
        }"""
    )

    assert result["lastBottom"] <= result["layerTop"] + 1, (
        "底部佔位變大之後最後一列被浮層蓋住(沒有重新貼底): "
        f"last.bottom={result['lastBottom']} layer.top={result['layerTop']}"
    )


@pytest.mark.e2e
async def test_message_growth_reanchors_without_help_from_render_site(page):
    """訊息自己長高之後也要留在底部 —— 不可依賴渲染點記得補捲。

    最終渲染(isStreaming=false)那一刻才會加上免責聲明與耗時徽章,實測一次長 84px。
    底部佔位沒變,所以 ui-shell 的重新貼底不會被觸發;而補捲原本是各個渲染點自己
    手寫的,chat-hitl.js 的完成分支就漏了 —— 長出來的那段留在輸入框後面,使用者
    看到的最後一句(免責聲明)剛好被蓋住。

    所以這個測試刻意「不」在長高後補捲,長高本身就必須讓捲動區自己回到底部。
    """
    _requires_playwright()
    await _open_mobile(page)

    result = await page.evaluate(
        """async () => {
            const warn = document.getElementById('no-llm-key-warning');
            warn.style.setProperty('display', 'none', 'important');
            const box = document.getElementById('chat-messages');
            // 先讓應用自己把歷史/歡迎畫面畫完 —— loadChatHistory 會整個覆寫
            // #chat-messages,搶在它前面注入的話訊息會被抽掉,量到的全是 0
            await new Promise(r => setTimeout(r, 600));
            box.innerHTML = '';

            for (let i = 0; i < 10; i++) {
                const { row, content } = window.buildMessageRow(i % 2 ? 'bot' : 'user');
                content.textContent = '歷史訊息 ' + (i + 1);
                box.appendChild(row);
            }
            const { row, content: botMsgDiv } = window.buildMessageRow('bot');
            box.appendChild(row);

            const text = '## BTC 分析\\n\\n' + Array.from({ length: 8 },
                (_, i) => '- 第 ' + (i + 1) + ' 點觀察，這裡是一段比較長的說明讓訊息長高。'
            ).join('\\n');

            // 串流中的樣子:還沒有免責聲明
            botMsgDiv.innerHTML = window.renderStoredBotMessage(text, true, '5.0');
            await new Promise(r => setTimeout(r, 350));
            window.UIShell.syncLayout();
            box.scrollTop = box.scrollHeight;
            await new Promise(r => setTimeout(r, 100));
            // 量訊息本身,不要量捲動區的 scrollHeight —— 後者含 padding,
            // 會被重新量測寫進去的底部留白抵銷掉
            const before = botMsgDiv.offsetHeight;

            // 最終渲染:免責聲明 + 耗時徽章一次長出來。之後刻意不補捲。
            botMsgDiv.innerHTML = window.renderStoredBotMessage(text, false, '5.0');
            const badge = document.createElement('div');
            badge.className = 'mt-4 text-xs text-textMuted/60 font-mono';
            badge.textContent = '耗時 5.0s';
            botMsgDiv.appendChild(badge);

            // 使用者看到的是下一次繪製之後的畫面
            await new Promise(r => requestAnimationFrame(() => r()));

            const last = botMsgDiv.lastElementChild.getBoundingClientRect();
            const input = document.querySelector('[data-shell-fixed-input]').getBoundingClientRect();
            return {
                grew: botMsgDiv.offsetHeight - before,
                lastBottom: last.bottom,
                inputTop: input.top,
                // 被應用覆寫掉的話量出來的全是 0,那是測試壞了不是版面壞了
                attached: document.contains(botMsgDiv),
            };
        }"""
    )

    assert result["attached"], (
        f"訊息被應用自己的渲染覆寫掉了,這一輪量到的不是真的版面: {result}"
    )
    assert result["grew"] > 0, (
        f"最終渲染沒有長高,這個測試就守不到東西了(免責聲明是不是被移掉了?): {result}"
    )
    assert result["lastBottom"] <= result["inputTop"] + 1, (
        "訊息長高之後最後一句被輸入框蓋住(沒有自動回到底部): "
        f"長高={result['grew']}px last.bottom={result['lastBottom']} input.top={result['inputTop']}"
    )


@pytest.mark.e2e
async def test_shell_never_extends_below_visible_viewport(page):
    """shell 的底邊不可以落在可見視窗之外。

    實機（POCO F8 Ultra / Chrome）量到:視窗 clientHeight 718、捲動區 clientHeight 713、
    頁首 56 —— 捲動區底邊落在 774,比視窗低 56px。於是就算捲到底、留白也夠
    (padding 174 ≥ 需要的 158),最後一列仍停在輸入框後面(被蓋住 40px)。

    根因是 height:100% 對 initial containing block 解析,而 Chrome Android 的 ICB 用的是
    **大視窗**(工具列收起時);工具列正顯示時可見區更小,整個 column 就高出一截。
    桌機模擬沒有會伸縮的工具列,所以這裡量到的永遠是 0 —— 這支測試守的是「不可以有正值」,
    真正的證據來自實機的數字面板。
    """
    _requires_playwright()
    await _open_mobile(page)

    result = await page.evaluate(
        """() => {
            const vh = document.documentElement.clientHeight;
            const offenders = [];
            ['main-content', 'chat-tab', 'chat-messages'].forEach((id) => {
                const el = document.getElementById(id);
                if (!el) return;
                const over = Math.round(el.getBoundingClientRect().bottom - vh);
                if (over > 1) offenders.push({ id, over });
            });
            return { vh, offenders };
        }"""
    )

    assert not result["offenders"], (
        "這些容器的底邊落在可見視窗之外,底部內容會沉到瀏覽器工具列/輸入框後面: "
        f"{result['offenders']}（視窗 {result['vh']}px）"
    )


@pytest.mark.e2e
async def test_layout_debug_panel_is_opt_in(page):
    """版面數字面板預設不可出現,只有本機網址帶 #layout-debug 才掛上去。"""
    _requires_playwright()
    await _open_mobile(page)

    assert await page.query_selector("#layout-debug-panel") is None, (
        "沒有要求的情況下 debug 面板就出現在畫面上了"
    )

    # 連點頁首的開法已於 2026-08-25 移除——它不在守衛後面、直接 mount()，
    # 而頁首正是國旗/主題鈕/通知鈴，一般使用者在正式站會誤觸（DANNY 回報）。
    for _ in range(8):
        await page.click("main header", position={"x": 150, "y": 20})
    assert await page.query_selector("#layout-debug-panel") is None, (
        "連點頁首仍會開面板——手勢應該已經整個移除"
    )

    # 語言講明：以前 en.json 的 layoutDebug.covered 錯放成繁中「★ 被蓋住」（2026-09-25 歸位），
    # 無頭瀏覽器預設 en 時這行斷言是靠那個錯位才過的
    await page.add_init_script("localStorage.setItem('selectedLanguage', 'zh-TW');")
    # query string 才是耐用的開法:hash 會被 spa.js 的 switchTab 改寫掉
    for url in (BASE_URL + "?layout-debug=1", BASE_URL + "#layout-debug"):
        await page.goto(url)
        await page.wait_for_selector("#layout-debug-panel", timeout=5000)
        snapshot = await page.evaluate(
            "() => document.getElementById('layout-debug-panel').dataset.snapshot"
        )
        assert "★ 被蓋住" in snapshot, f"{url} 的面板沒有印出關鍵那一行: {snapshot}"


@pytest.mark.e2e
async def test_scroll_stays_anchored_when_viewport_shrinks(page):
    """可用高度變小時,最後一句仍不可被輸入框蓋住。

    手機上網址列出現、鍵盤彈出、轉向都會讓捲動區的 clientHeight 縮小,而 scrollTop
    不動 —— 原本貼在底部的內容就被推到輸入框後面,最後一行被攔腰切掉(實測視窗高度
    少 144px,最後 128px 躲進輸入框後面)。

    底部佔位沒變,所以 ui-shell 的重新貼底不會被觸發;DOM 也沒變,所以內容變動的
    錨定也不會被觸發。**其餘測試一律在固定視窗尺寸下量,所以這一類問題測不出來。**
    """
    _requires_playwright()
    await _open_mobile(page)

    await page.evaluate(
        """async () => {
            const warn = document.getElementById('no-llm-key-warning');
            warn.style.setProperty('display', 'none', 'important');
            const box = document.getElementById('chat-messages');
            box.innerHTML = '';
            for (let i = 0; i < 20; i++) {
                const { row, content } = window.buildMessageRow(i % 2 ? 'bot' : 'user');
                content.textContent = '訊息 ' + (i + 1);
                box.appendChild(row);
            }
            await new Promise(r => setTimeout(r, 300));
            window.UIShell.syncLayout();
            box.scrollTop = box.scrollHeight;      // 使用者正在看最新的一則
            await new Promise(r => requestAnimationFrame(() => r()));
        }"""
    )

    # 網址列出現 / 鍵盤彈出:可用高度縮小,但底部佔位與 DOM 都沒變
    await page.set_viewport_size({"width": MOBILE_VIEWPORT["width"], "height": 640})
    await page.wait_for_timeout(300)

    result = await page.evaluate(
        """() => {
            const box = document.getElementById('chat-messages');
            const input = document.querySelector('[data-shell-fixed-input]').getBoundingClientRect();
            return {
                distanceFromBottom: box.scrollHeight - box.scrollTop - box.clientHeight,
                lastBottom: box.lastElementChild.getBoundingClientRect().bottom,
                inputTop: input.top,
            };
        }"""
    )

    assert result["lastBottom"] <= result["inputTop"] + 1, (
        "可用高度變小之後最後一列被輸入框蓋住(沒有重新貼底): "
        f"離底部={result['distanceFromBottom']}px "
        f"last.bottom={result['lastBottom']} input.top={result['inputTop']}"
    )
