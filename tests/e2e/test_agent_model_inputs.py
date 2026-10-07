"""Browser-level model selection with real studio JS and a stubbed API boundary."""

import os
from pathlib import Path

import pytest
from playwright.async_api import async_playwright, expect

from core.model_config import MODEL_CONFIG

pytestmark = pytest.mark.e2e
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
async def studio_page():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel=os.getenv("PLAYWRIGHT_CHANNEL"))
        page = await browser.new_page()
        await page.set_content('<main id="studio"></main>')
        await page.add_script_tag(path=str(ROOT / "web/js/ai-studio.js"))
        await page.evaluate(
            """(cfg) => {
            window.saved = {model_selection: {provider: 'openai', model: 'gpt-5.4-mini'}, tools: ['web_search']};
            window.writes = [];
            window.dialogs = [];
            window.showToast = () => {};
            window.showInfoDialog = (data) => window.dialogs.push(data);
            window.AppAPI = {
                put: async (url, body) => {
                    if (window.failSave) throw {status: 403, message: 'fixture denial'};
                    window.writes.push({url, body});
                    Object.assign(window.saved, body);
                },
                get: async () => ({configs: [{agent_id: 'general_research', ...window.saved}]}),
            };
            AIStudioTab._modelConfig = cfg;
            // 2026-09-04（PR #640）：per-agent 模型下拉只列**已綁定金鑰**的
            // provider（原本列整份靜態目錄，選了沒金鑰的會靜默退回預設模型）。
            // 合成頁沒有真的登入態，所以要餵 llmState.savedKeys，否則下拉是空的。
            // has_key 是必要欄位（2026-09-07）：GET /api/user/api-keys 會替每個
            // 支援的 provider 補一筆 has_key:false 的空紀錄，所以「有 key 這一列」
            // 不等於「綁過」。fixture 要照真實回應的形狀給。
            window.llmState = {savedKeys: Object.fromEntries(
                Object.keys(cfg).map((p) => [p, {has_key: true, model: null, models: []}])
            )};
            // 2026-09-04（PR #641，c044）：per-agent 設定的作用域是 preset，
            // _saveAgentModel 沒有作用中的 preset 就直接拋錯（不再靜默寫到
            // 看不見的使用者層）。合成頁要給一個 is_default 的 preset。
            AIStudioTab._presets = [
                {preset_id: 'p_fixture', name: 'fixture', is_default: true,
                 agent_ids: ['general_research']},
            ];
            window.renderModel = () => {
                AIStudioTab._configs = {general_research: structuredClone(window.saved)};
                const box = document.querySelector('#studio');
                box.innerHTML = AIStudioTab._agentModelSelectHtml('general_research');
                AIStudioTab._bindAgentModelSelects(box);
            };
            window.renderModel();
        }""",
            MODEL_CONFIG,
        )
        yield page
        await browser.close()


@pytest.mark.parametrize("provider", ["nvidia", "volcengine", "openrouter"])
async def test_custom_provider_model_roundtrip_and_reset_preserves_tools(
    studio_page, provider
):
    page = studio_page
    select = page.locator("select[data-agent-model]")
    option = select.locator(f'option[data-provider="{provider}"][data-custom-model]')
    await expect(option).to_have_count(1)
    await select.select_option(await option.get_attribute("value"))
    field = page.locator("input[data-agent-model-input]")
    await expect(field).to_be_visible()
    await field.fill("vendor/custom-model-v1")
    await page.locator("button[data-agent-model-save]").click()
    await _wait_model(page, provider, "vendor/custom-model-v1")
    assert await page.evaluate("window.saved.tools") == ["web_search"]
    await page.evaluate("window.renderModel()")
    await expect(field).to_have_value("vendor/custom-model-v1")
    await select.select_option("")
    await page.wait_for_function("window.saved.model_selection === null")
    assert await page.evaluate("window.saved.tools") == ["web_search"]


async def _wait_model(page, provider, model):
    await page.wait_for_function(
        "([p, m]) => window.saved.model_selection?.provider === p && window.saved.model_selection?.model === m",
        arg=[provider, model],
    )


async def test_failed_change_restores_saved_provider_and_model(studio_page):
    page = studio_page
    await page.evaluate("window.failSave = true")
    select = page.locator("select[data-agent-model]")
    original = await select.input_value()
    await select.select_option(
        await select.locator(
            'option[data-provider="nvidia"][data-custom-model]'
        ).get_attribute("value")
    )
    field = page.locator("input[data-agent-model-input]")
    await field.fill("vendor/custom")
    await page.locator("button[data-agent-model-save]").click()
    await page.wait_for_function("window.dialogs.length === 1")
    await expect(select).to_have_value(original)
    assert await page.evaluate("window.saved.model_selection.provider") == "openai"
    assert await page.evaluate("window.writes.length") == 0


async def test_empty_custom_model_is_not_sent_as_reset(studio_page):
    page = studio_page
    select = page.locator("select[data-agent-model]")
    await select.select_option(
        await select.locator(
            'option[data-provider="nvidia"][data-custom-model]'
        ).get_attribute("value")
    )
    await page.locator("input[data-agent-model-input]").fill("   ")
    await page.locator("button[data-agent-model-save]").click()
    assert await page.evaluate("window.writes.length") == 0
    assert await page.evaluate("window.saved.model_selection.provider") == "openai"


async def test_unbound_providers_are_not_offered(studio_page):
    """沒綁金鑰的 provider 不得出現在 per-agent 模型下拉。

    2026-09-07 DANNY 回報「有些都沒有綁定」：GET /api/user/api-keys 會替每一個
    支援的 provider 補一筆 has_key:false 的空紀錄，而 _boundProviders() 直接拿
    Object.keys(savedKeys)——等於整份目錄都列出來，選了也跑不動。
    """
    page = studio_page
    await page.evaluate("""() => {
        AIStudioTab._modelConfig = {
            bound_one: {display: 'Bound', available_models: [{value: 'm1', name: 'M1'}]},
            never_bound: {display: 'Unbound', available_models: [{value: 'm2', name: 'M2'}]},
        };
        window.llmState = {savedKeys: {
            bound_one: {has_key: true, model: 'm1', models: ['m1']},
            // 後端補的空紀錄：有這一列不代表綁過
            never_bound: {has_key: false, model: null, models: []},
        }};
        window.saved.model_selection = null;
        window.renderModel();
    }""")
    select = page.locator("select[data-agent-model]")
    await expect(select.locator('option[data-provider="bound_one"]')).to_have_count(1)
    await expect(select.locator('option[data-provider="never_bound"]')).to_have_count(0)


async def test_only_user_bound_models_are_offered(studio_page):
    """綁定的 provider 底下也只列使用者自己綁的模型，不是整份目錄。"""
    page = studio_page
    await page.evaluate("""() => {
        AIStudioTab._modelConfig = {
            openai: {display: 'OpenAI', available_models: [
                {value: 'picked', name: 'Picked'},
                {value: 'not-picked', name: 'Not Picked'},
            ]},
        };
        window.llmState = {savedKeys: {
            openai: {has_key: true, model: 'picked', models: ['picked']},
        }};
        window.saved.model_selection = null;
        window.renderModel();
    }""")
    select = page.locator("select[data-agent-model]")
    await expect(select.locator('option[data-model="picked"]')).to_have_count(1)
    await expect(select.locator('option[data-model="not-picked"]')).to_have_count(0)


async def test_bound_key_without_saved_model_falls_back_to_catalog(studio_page):
    """綁了金鑰卻沒存過模型的舊資料：退回該 provider 完整清單，不要給空下拉。"""
    page = studio_page
    await page.evaluate("""() => {
        AIStudioTab._modelConfig = {
            openai: {display: 'OpenAI', available_models: [
                {value: 'a', name: 'A'}, {value: 'b', name: 'B'},
            ]},
        };
        window.llmState = {savedKeys: {
            openai: {has_key: true, model: null, models: []},
        }};
        window.saved.model_selection = null;
        window.renderModel();
    }""")
    select = page.locator("select[data-agent-model]")
    await expect(select.locator('option[data-provider="openai"]')).to_have_count(2)


async def test_no_bindings_shows_visible_hint_not_just_title(studio_page):
    """一個都沒綁：說明要看得見（手機沒有 hover，掛在 title 上等於沒有）。"""
    page = studio_page
    await page.evaluate("""() => {
        AIStudioTab._modelConfig = {openai: {display: 'OpenAI', available_models: [{value: 'a', name: 'A'}]}};
        window.llmState = {savedKeys: {openai: {has_key: false, model: null, models: []}}};
        window.saved.model_selection = null;
        window.renderModel();
    }""")
    box = page.locator("[data-agent-model-control]")
    await expect(box.locator("select option")).to_have_count(1)  # 只剩「預設（官方）」
    text = await box.inner_text()
    assert "綁定" in text or "bind" in text.lower(), f"沒有可見提示：{text!r}"


async def test_no_active_preset_disables_the_dropdown_instead_of_failing_on_save(studio_page):
    """沒有作用中 Preset 時不准讓人選了才報錯。

    2026-09-08 回報：綁好模型、下拉也選得到，一按下去卻跳「模型設定失敗 /
    登入已失效或尚無作用中的 Preset」。存檔需要作用中的 preset，但下拉照樣
    可互動——正是 ai-studio.js 自己註解在罵的「設定得下去但不生效」。
    """
    page = studio_page
    await page.evaluate("""() => {
        AIStudioTab._presets = [];            // 使用者還沒建任何 preset
        AIStudioTab._presetsLoadFailed = false;  // 且不是讀取失敗
        window.renderModel();
    }""")
    select = page.locator("select[data-agent-model]")
    await expect(select).to_be_disabled()


async def test_no_preset_message_tells_you_to_create_one_not_to_relogin(studio_page):
    """真正沒建 Preset 的人不該被叫去重新登入——他重登一百次也沒用。"""
    page = studio_page
    await page.evaluate("""() => {
        AIStudioTab._presets = [];
        AIStudioTab._presetsLoadFailed = false;
        window.renderModel();
    }""")
    text = await page.locator("[data-agent-model-control]").inner_text()
    assert "Preset" in text, f"沒有指出要去建 Preset：{text!r}"
    assert "登入" not in text, f"不該叫使用者重新登入：{text!r}"


async def test_failed_preset_load_says_session_not_missing_preset(studio_page):
    """讀取失敗（401）跟「還沒建」要講不同的話——以前兩者都塞同一句。"""
    page = studio_page
    await page.evaluate("""() => {
        AIStudioTab._presets = [];
        AIStudioTab._presetsLoadFailed = true;   // 401／斷線
        window.renderModel();
    }""")
    text = await page.locator("[data-agent-model-control]").inner_text()
    assert "登入" in text or "sign in" in text.lower(), f"讀取失敗時要提到登入：{text!r}"


async def test_active_preset_keeps_the_dropdown_usable(studio_page):
    """有作用中的 preset（fixture 預設狀態）就要能正常選。"""
    select = studio_page.locator("select[data-agent-model]")
    await expect(select).to_be_enabled()


async def test_same_model_name_in_two_providers_restores_exact_provider(studio_page):
    page = studio_page
    await page.evaluate("""() => {
        AIStudioTab._modelConfig = {
            first: {available_models: [{value: 'shared', name: 'Shared'}]},
            second: {available_models: [{value: 'shared', name: 'Shared'}]},
        };
        // 下拉只列已綁定的 provider（PR #640）——換了 _modelConfig 就要同步
        // savedKeys，否則兩個 provider 都不會被渲染出來。
        window.llmState = {savedKeys: {
            first: {has_key: true, model: null, models: []},
            second: {has_key: true, model: null, models: []},
        }};
        window.saved.model_selection = {provider: 'second', model: 'shared'};
        window.renderModel();
    }""")
    assert (
        await page.locator("select[data-agent-model] option:checked").get_attribute(
            "data-provider"
        )
        == "second"
    )


async def test_hitl_resume_sends_original_context_and_tracks_nested_run(studio_page):
    page = studio_page
    await page.add_script_tag(
        type="module", content=(ROOT / "web/js/chat-stream-ui.js").read_text(encoding="utf-8")
    )
    await page.add_script_tag(
        type="module", content=(ROOT / "web/js/chat-hitl.js").read_text(encoding="utf-8")
    )
    await page.evaluate("""() => {
        window.AuthManager = {currentUser: {user_id: 'fixture-user'}};
        window.isAnalyzing = true;
        // chat-state.js 的 per-session 狀態（續傳期間要標記分析中）；這頁只載
        // chat-hitl.js，補最小替身。目前對話＝這個 HITL 的對話，續傳才會把上下文掛回來
        window.currentSessionId = 'fixture-session';
        window.setSessionAnalyzing = () => {};
        window.setAnalysisController = () => {};
        window.clearAnalysisController = () => {};
        window.syncChatUIForCurrentSession = () => {};
        // per-session run id 與「掛在指定對話名下」的 HITL 上下文（2026-09-25）
        const runIds = {};
        window.getSessionRunId = (sid) => runIds[sid] || null;
        window.setSessionRunId = (rid, sid) => { runIds[sid] = rid; };
        window.setSessionHitlContext = (ctx) => { window._hitlContext = ctx; };
        window._hitlContext = {
            originalMessage: 'pause', sessionId: 'fixture-session',
            userProvider: 'openai', userSelectedModel: 'gpt-5.4',
            presetId: 'prst_original', runId: 'a'.repeat(32),
            startTime: Date.now(), botMsgDiv: document.querySelector('#studio'),
        };
        window.hitlRequests = [];
        window.fetch = async (url, init) => {
            window.hitlRequests.push({url, credentials: init.credentials, body: JSON.parse(init.body)});
            return new Response(
                'data: ' + JSON.stringify({type: 'run_started', run_id: 'b'.repeat(32)}) + '\\n\\n' +
                'data: ' + JSON.stringify({type: 'hitl_question', data: {question: 'Next?'}}) + '\\n\\n',
                {headers: {'Content-Type': 'text/event-stream'}}
            );
        };
    }""")
    await page.evaluate(
        'window.submitHITLAnswer(\'{"action":"consent","approved":true}\')'
    )
    first = await page.evaluate("window.hitlRequests[0]")
    assert first["credentials"] == "include"
    assert first["body"]["preset_id"] == "prst_original"
    assert first["body"]["resume_run_id"] == "a" * 32
    assert await page.evaluate("window._hitlContext.runId") == "b" * 32
    await page.evaluate(
        'window.submitHITLAnswer(\'{"action":"consent","approved":true}\')'
    )
    assert await page.evaluate("window.hitlRequests[1].body.resume_run_id") == "b" * 32


@pytest.fixture
async def tools_page(studio_page):
    page = studio_page
    await page.evaluate("""() => {
        document.querySelector('#studio').innerHTML = '<div id="ai-studio-profiles"></div>';
        const candidate = (id, required = false) => ({tool_id: id, category: 'general', required});
        AIStudioTab._profiles = ['general_research', 'finance_markets'].map(id => ({
            id, display_name: id, candidate_tools: [candidate('web_search', true), candidate('fetch_url')],
        }));
        AIStudioTab._configs = {};
        window.savedConfigs = {};
        window.AppAPI.put = async (url, body) => {
            if (window.failSave) throw {status: 403, message: 'fixture denial'};
            window.writes.push({url, body});
            if (window.holdSave) await new Promise(resolve => {window.releaseSave = resolve;});
            const id = url.split('/').pop();
            const cfg = {...window.savedConfigs[id], tools: body.tools || []};
            window.savedConfigs[id] = cfg;
            return {success: true, config: {agent_id: id, ...cfg}};
        };
        window.AppAPI.get = async () => ({configs: Object.entries(window.savedConfigs).map(
            ([agent_id, cfg]) => ({agent_id, ...cfg})
        )});
        AIStudioTab.renderProfiles();
        // Exercise the same data-click dispatch contract as the app, without its shell.
        document.addEventListener('click', event => {
            const button = event.target.closest('[data-click]');
            if (button && !button.disabled) {
                const method = button.dataset.click.split('.').pop();
                AIStudioTab[method](decodeURIComponent(button.dataset.clickArg));
            }
        });
        document.querySelectorAll('[data-tools-panel]').forEach(panel => {panel.open = true;});
    }""")
    return page


@pytest.mark.parametrize("method", ["saveAgentTools", "resetAgentTools"])
async def test_tool_save_preserves_other_card_drafts_and_custom_model(tools_page, method):
    page = tools_page
    other = page.locator('[data-tools-panel="finance_markets"]')
    await other.locator('[data-agent-tool="fetch_url"]').uncheck()
    select = page.locator('[data-agent-model="general_research"]')
    await select.select_option(await select.locator(
        'option[data-provider="nvidia"][data-custom-model]'
    ).get_attribute('value'))
    model_input = page.locator('[data-agent-model-control]').filter(has=select).locator('input')
    await model_input.fill('vendor/unsaved-draft')
    panel = page.locator('[data-tools-panel="general_research"]')
    await panel.locator('[data-agent-tool="fetch_url"]').uncheck()
    await panel.locator(f'[data-click="AIStudioTab.{method}"]').click()
    await page.wait_for_function("window.savedConfigs.general_research !== undefined")
    await expect(other.locator('[data-agent-tool="fetch_url"]')).not_to_be_checked()
    await expect(model_input).to_have_value('vendor/unsaved-draft')
    await expect(other).to_have_attribute('open', '')
    assert await page.evaluate('window.writes[0].body') == {
        'tools': ['web_search'] if method == 'saveAgentTools' else None
    }
    if method == 'resetAgentTools':
        await expect(panel.locator('[data-agent-tool="fetch_url"]')).to_be_checked()


async def test_tool_save_serializes_same_panel_mutations(tools_page):
    page = tools_page
    await page.evaluate('window.holdSave = true')
    panel = page.locator('[data-tools-panel="general_research"]')
    save = panel.locator('[data-click="AIStudioTab.saveAgentTools"]')
    await save.click()
    await page.wait_for_function('window.releaseSave !== undefined')
    await expect(save).to_be_disabled()
    await expect(panel.locator('[data-click="AIStudioTab.resetAgentTools"]')).to_be_disabled()
    await expect(panel.locator('[data-agent-tool="fetch_url"]')).to_be_disabled()
    await page.evaluate("AIStudioTab.resetAgentTools('general_research')")
    assert await page.evaluate('window.writes.length') == 1
    await page.evaluate('window.releaseSave()')
    await expect(save).to_be_enabled()
    await expect(panel.locator('[data-agent-tool="web_search"]')).to_be_disabled()


async def test_failed_tool_save_retains_draft_and_unlocks_controls(tools_page):
    page = tools_page
    await page.evaluate('window.failSave = true')
    panel = page.locator('[data-tools-panel="general_research"]')
    await panel.locator('[data-agent-tool="fetch_url"]').uncheck()
    await panel.locator('[data-click="AIStudioTab.saveAgentTools"]').click()
    await page.wait_for_function('window.dialogs.length === 1')
    await expect(panel.locator('[data-agent-tool="fetch_url"]')).not_to_be_checked()
    await expect(panel.locator('[data-click="AIStudioTab.saveAgentTools"]')).to_be_enabled()
    await expect(panel.locator('[data-agent-tool="web_search"]')).to_be_disabled()
    assert await page.evaluate('window.writes.length') == 0
