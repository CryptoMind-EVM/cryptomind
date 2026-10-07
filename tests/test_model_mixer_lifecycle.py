"""Exercise the actual API input and checkpoint across new turns and HITL resumes."""

import importlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import interrupt

from core.agents.models import ManagerState

USER = {"user_id": "mixer-user", "membership_tier": "premium"}
PRESET = {
    "preset_id": "prst_one",
    "agent_ids": ["general_research"],
    "tool_names": ["web_search"],
}
SELECTION = {"provider": "openai", "model": "gpt-5.4-mini"}


@pytest.fixture
def lifecycle(monkeypatch):
    from api.routers import analysis as mod

    mod._local_analysis_runs.clear()
    monkeypatch.setattr(mod.limiter, "enabled", False)
    monkeypatch.setattr("core.feature_flags.agent_presets_enabled", lambda: True)
    states = []

    def node(state):
        states.append(dict(state))
        if state["query"] == "pause":
            interrupt({"question": "Continue?"})
        return {"final_response": "fixture complete"}

    builder = StateGraph(ManagerState)
    builder.add_node("claw_loop", node)
    builder.set_entry_point("claw_loop")
    builder.add_edge("claw_loop", END)
    graph = builder.compile(checkpointer=MemorySaver())
    manager = SimpleNamespace(graph=graph, progress_callback=None)
    boot = MagicMock(return_value=manager)
    monkeypatch.setattr(
        importlib.import_module("core.agents.bootstrap"), "bootstrap", boot
    )
    monkeypatch.setattr(mod, "save_chat_message", MagicMock())
    monkeypatch.setattr(mod, "get_chat_history", MagicMock(return_value=[]))
    monkeypatch.setattr(mod, "set_current_session", MagicMock())
    monkeypatch.setattr("core.database.user.get_user_display_name", lambda _: "Fixture")
    monkeypatch.setattr("core.analysis_queue.enqueue_job", lambda _: False)
    monkeypatch.setattr(mod.shared_cache, "set_json", MagicMock())
    monkeypatch.setattr(mod.shared_cache, "get_json", MagicMock(return_value=None))
    overrides = AsyncMock(return_value=dict(SELECTION))
    presets = AsyncMock(return_value=dict(PRESET))
    monkeypatch.setattr(mod, "_resolve_preset_model_override", overrides)
    monkeypatch.setattr(mod, "_resolve_agent_preset_config", presets)
    credentials = AsyncMock(
        side_effect=lambda user, provider: {
            "provider": provider or "openai",
            "model": "gpt-5.4",
            "api_key": "fixture-key",
        }
    )
    monkeypatch.setattr(mod, "resolve_user_llm_credentials", credentials)
    client_factory = MagicMock(return_value=SimpleNamespace(model_name="gpt-5.4-mini"))
    monkeypatch.setattr(mod, "create_user_llm_client", client_factory)
    app = FastAPI()
    app.include_router(mod.router)
    app.dependency_overrides[mod.get_current_user] = lambda: USER
    app.dependency_overrides[mod.get_async_session] = lambda: None
    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client,
            states=states,
            presets=presets,
            overrides=overrides,
            credentials=credentials,
            clients=client_factory,
            boot=boot,
            mod=mod,
        )
    mod._local_analysis_runs.clear()


def post_run(fixture, **fields):
    return fixture.client.post(
        "/api/analyze",
        json={
            "message": "fixture query",
            "session_id": "mixer-session",
            "user_provider": "openai",
            "user_model": "gpt-5.4",
            "system_prompt": "",
            "enabled_tools": [],
            **fields,
        },
    )


def run_id(response):
    assert response.status_code == 200, response.text
    events = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert not any("error" in event for event in events), events
    return next(
        event["run_id"] for event in events if event.get("type") == "run_started"
    )


def test_new_turn_clears_old_preset_in_real_checkpoint(lifecycle):
    run_id(post_run(lifecycle, preset_id="prst_one"))
    assert lifecycle.states[-1]["preset_id"] == "prst_one"
    lifecycle.presets.return_value = None
    lifecycle.overrides.return_value = None
    run_id(post_run(lifecycle, message="next turn"))
    assert lifecycle.states[-1].get("preset_config") is None
    assert lifecycle.states[-1].get("preset_id") is None


def test_hitl_keeps_original_model_when_saved_selection_changes(lifecycle):
    original = run_id(post_run(lifecycle, message="pause", preset_id="prst_one"))
    lifecycle.overrides.return_value = {
        "provider": "google_gemini",
        "model": "gemini-3.5-flash",
    }
    resumed = run_id(
        post_run(
            lifecycle, message="pause", resume_answer="yes", resume_run_id=original
        )
    )
    assert lifecycle.clients.call_args.kwargs["provider"] == SELECTION["provider"]
    assert lifecycle.clients.call_args.kwargs["model"] == SELECTION["model"]
    assert (
        lifecycle.boot.call_args.kwargs["user_model_preference"] == SELECTION["model"]
    )
    assert lifecycle.mod._load_analysis_run(resumed)["llm_selection"] == SELECTION
    assert (
        lifecycle.overrides.await_count == 1
    )  # Resume must not reread mutable config.
    assert lifecycle.states[-1]["preset_id"] == "prst_one"


@pytest.mark.parametrize(
    "tamper,expected",
    [
        ({"user_id": "other-user"}, 404),
        ({"session_id": "other-session"}, 404),
        ({"status": "completed"}, 409),
        ({"llm_selection": None}, 409),
        ({"revoked": True}, 409),
        ({"finished_at": 1}, 409),
    ],
)
def test_resume_rejects_foreign_stale_or_incomplete_run(lifecycle, tamper, expected):
    original = run_id(post_run(lifecycle, message="pause"))
    lifecycle.mod._local_analysis_runs[original].update(tamper)
    before = lifecycle.clients.call_count
    response = post_run(
        lifecycle, message="pause", resume_answer="yes", resume_run_id=original
    )
    assert response.status_code == expected, response.text
    assert lifecycle.clients.call_count == before


def test_resume_rejects_provider_fallback_when_original_key_disappears(lifecycle):
    original = run_id(post_run(lifecycle, message="pause"))
    lifecycle.credentials.side_effect = None
    lifecycle.credentials.return_value = {
        "provider": "google_gemini",
        "model": "gemini-3.5-flash",
        "api_key": "fixture",
    }
    response = post_run(
        lifecycle, message="pause", resume_answer="yes", resume_run_id=original
    )
    assert response.status_code == 409


def test_resume_unknown_run_fails_before_model_creation(lifecycle):
    response = post_run(
        lifecycle, message="pause", resume_answer="yes", resume_run_id="a" * 32
    )
    assert response.status_code == 404
    lifecycle.clients.assert_not_called()


@pytest.mark.parametrize("disabled", ["tier", "flag"])
def test_pinned_resume_rechecks_preset_availability(lifecycle, monkeypatch, disabled):
    original = run_id(post_run(lifecycle, message="pause"))
    if disabled == "tier":
        monkeypatch.setitem(USER, "membership_tier", "free")
    else:
        monkeypatch.setattr("core.feature_flags.agent_presets_enabled", lambda: False)
    before = lifecycle.clients.call_count
    response = post_run(
        lifecycle, message="pause", resume_answer="yes", resume_run_id=original
    )
    assert response.status_code == 409
    assert lifecycle.clients.call_count == before


def test_resume_id_requires_answer_and_valid_shape(lifecycle):
    assert post_run(lifecycle, resume_run_id="a" * 32).status_code == 400
    assert (
        post_run(lifecycle, resume_answer="yes", resume_run_id="../foreign").status_code
        == 422
    )
    lifecycle.clients.assert_not_called()


async def test_worker_stream_marks_waiting_before_question_is_consumed(
    lifecycle, monkeypatch
):
    async def events(*args, **kwargs):
        yield {"type": "hitl_question", "data": {"question": "Continue?"}}
        yield {"done": True, "waiting": True}

    monkeypatch.setattr("core.analysis_queue.subscribe_events", events)
    run = lifecycle.mod._create_analysis_run("mixer-session", USER["user_id"])
    stream = lifecycle.mod._stream_from_redis(run["run_id"], run)
    frame = await anext(stream)
    assert "hitl_question" in frame
    assert run["status"] == "waiting"
    await stream.aclose()


def test_bootstrap_pins_explicit_platform_model_on_cache_hits_and_clears():
    from core.agents.bootstrap import bootstrap, invalidate_manager_cache

    user_id = "mixer-cache-fixture"
    invalidate_manager_cache(user_id)
    try:
        for preference in ("gpt-5.4-mini", "gpt-5.4", None):
            manager = bootstrap(
                SimpleNamespace(model_name=preference or "gpt-5.4-mini"),
                user_id=user_id,
                session_id="same-session",
                user_tier="premium",
                user_model_preference=preference,
            )
            assert manager._user_model_preference == preference
            if preference:
                with patch.object(manager, "_create_model_instance") as create:
                    assert manager._get_routed_llm("simple_qa") is manager.llm
                    assert manager._get_routed_llm("router") is manager.llm
                    create.assert_not_called()
    finally:
        invalidate_manager_cache(user_id)


async def test_worker_retains_model_context_across_waiting_status(monkeypatch):
    from langgraph.types import Command

    from api.routers.analysis import _build_job_envelope
    from scripts.analysis_worker import _run_job

    cache = {}
    monkeypatch.setattr(
        "core.shared_cache.set_json", lambda key, value, ttl: cache.update({key: value})
    )
    monkeypatch.setattr("core.shared_cache.get_json", lambda key: cache.get(key))
    monkeypatch.setattr("core.analysis_queue.publish_event", lambda *args: None)
    monkeypatch.setattr("core.analysis_queue.listen_for_control", AsyncMock())
    graph = SimpleNamespace(
        ainvoke=AsyncMock(return_value={"__interrupt__": [SimpleNamespace(value={})]})
    )
    with (
        patch(
            "core.agents.bootstrap.bootstrap", return_value=SimpleNamespace(graph=graph)
        ) as boot,
        patch("utils.user_client_factory.create_user_llm_client"),
    ):
        job = _build_job_envelope(
            "run-one",
            "session-one",
            "user-one",
            {**SELECTION, "api_key": "fixture"},
            Command(goto="claw_loop", update={}),
            {},
            llm_selection=SELECTION,
            user_model_preference=SELECTION["model"],
        )
        await _run_job(job)
    assert boot.call_args.kwargs["user_model_preference"] == SELECTION["model"]
    stored = cache["analysis:run:run-one"]
    assert stored["status"] == "waiting"
    assert stored["llm_selection"] == SELECTION
    assert stored["user_id"] == "user-one"
    assert stored["session_id"] == "session-one"
    assert "api_key" not in json.dumps(stored)
