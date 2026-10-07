import asyncio
import tomllib

import pytest

from app.services import ai_provider as mod


def test_task_config_overrides_do_not_change_global_profile(monkeypatch, tmp_path):
    source = {"model": "original", "model_reasoning_effort": "low"}
    monkeypatch.setattr(mod, "_read_codex_config", lambda: source)
    monkeypatch.setattr(mod, "current_ai_model", lambda: "gpt-6.1-sol")
    monkeypatch.setattr(mod, "current_codex_reasoning_effort", lambda: "xhigh")
    for model, effort in [("gpt-6-luna", "high"), ("gpt-6.1-sol", "max")]:
        path = tmp_path / (effort + ".toml")
        mod._write_compatible_codex_config(path, task_model=model, task_effort=effort)
        config = tomllib.loads(path.read_text(encoding="utf-8"))
        assert config["model"] == model and config["model_reasoning_effort"] == effort
    assert source == {"model": "original", "model_reasoning_effort": "low"}
    assert mod.current_ai_model() == "gpt-6.1-sol" and mod.current_codex_reasoning_effort() == "xhigh"


def test_task_cli_and_disposable_config_match(monkeypatch):
    prepared, captured = [], []
    monkeypatch.setattr(mod, "_codex_base_command", lambda: ["codex"])
    monkeypatch.setattr(mod, "_prepare_codex_home", lambda path, **kw: prepared.append(kw))
    monkeypatch.setattr(mod, "_codex_process_env", lambda path: {})
    def process(args, prompt, env, timeout):
        captured.append(args)
        return 0, b"ok", b""
    monkeypatch.setattr(mod, "_run_codex_process", process)
    monkeypatch.setattr(mod, "_read_output_file", lambda path: "review")
    async def run():
        assert await mod._run_codex_cli([{"role": "user", "content": "test"}], max_tokens=None, timeout=1,
                                        task_model="gpt-6-luna", task_effort="max") == "review"
    asyncio.run(run())
    assert prepared == [{"task_model": "gpt-6-luna", "task_effort": "max"}]
    assert captured[0][captured[0].index("--model") + 1] == "gpt-6-luna"


def test_parallel_task_profiles_are_request_local(monkeypatch):
    calls = []
    monkeypatch.setattr(mod, "is_codex_cli_provider", lambda: True)
    async def cli(messages, **kwargs):
        await asyncio.sleep(0)
        calls.append(kwargs)
        return kwargs["task_model"]
    monkeypatch.setattr(mod, "_run_codex_cli", cli)
    async def run():
        values = await asyncio.gather(*[mod.generate_ai_text([{"role": "user", "content": "test"}],
                  codex_model=model, codex_reasoning_effort=effort) for model, effort in
                  [("gpt-6-luna", "high"), ("gpt-6.1-sol", "max")]])
        assert values == ["gpt-6-luna", "gpt-6.1-sol"]
    asyncio.run(run())
    assert [(row["task_model"], row["task_effort"]) for row in calls] == [("gpt-6-luna", "high"), ("gpt-6.1-sol", "max")]


@pytest.mark.parametrize("model,effort", [("untrusted", "high"), ("gpt-6-luna", "ultra")])
def test_invalid_task_profile_is_rejected_before_execution(monkeypatch, model, effort):
    monkeypatch.setattr(mod, "is_codex_cli_provider", lambda: True)
    async def run():
        with pytest.raises(ValueError):
            await mod.generate_ai_text([], codex_model=model, codex_reasoning_effort=effort)
    asyncio.run(run())


def test_task_override_requires_codex(monkeypatch):
    monkeypatch.setattr(mod, "is_codex_cli_provider", lambda: False)
    async def run():
        with pytest.raises(ValueError):
            await mod.generate_ai_text([], codex_model="gpt-6-luna", codex_reasoning_effort="high")
    asyncio.run(run())
