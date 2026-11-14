import base64
import copy
import json
from io import BytesIO
from random import Random
from types import SimpleNamespace

import pytest
from PIL import Image

from octop.infra.db.repos.execution import ExecutionConflict
from octop.infra.group_experts.executor import build_model
from octop.infra.group_experts.publication import prepare_publication
from octop.infra.group_experts.snapshots import build_snapshot, format_answer, snapshot_digest
from octop.infra.utils.immutable_files import store_object


def inputs():
    return {
        "source_agent_id": "source",
        "definition": {
            "source_row_id": 7,
            "fields": {"default_model": "AUTO", "system_prompt": "Role"},
            "files": {
                "SOUL.md": "Role personality",
                "USER.md": "Private",
                "MEMORY.md": "Private",
                "TOOLS.md": "Secret",
            },
            "runtime_settings": {"max_tokens": 900, "temperature": 0.3},
        },
        "provider": SimpleNamespace(
            id=4,
            name="p",
            enabled=True,
            base_url="https://private.invalid",
            api_key="secret",
            get_models=lambda: [{"id": "m", "context_window": 8192, "max_output_tokens": 512}],
        ),
        "provider_version": {
            "provider_id": 4,
            "generation": "stable-generation",
            "revision": 3,
            "deleted": 0,
        },
        "global_model": ("p", "m"),
    }


def test_snapshot_has_only_approved_role_and_fixed_budget():
    source = inputs()
    snapshot = build_snapshot(**source)
    assert snapshot["model_ref"] == "p/m"
    assert snapshot["model_settings"]["max_tokens"] == 512
    assert snapshot["max_input_tokens"] == 8192 - 1
    assert snapshot["tools"] == []
    text = json.dumps(snapshot)
    assert "Role personality" in text
    assert all(
        secret not in text
        for secret in ("Private", "Secret", "secret", "https://", "USER.md", "TOOLS.md")
    )
    before = copy.deepcopy(snapshot)
    source["definition"]["runtime_settings"]["max_tokens"] = 1
    assert snapshot == before


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("api_mode", ["chat_completions", "responses"])
def test_model_factory_preserves_api_configuration_and_legacy_snapshots(legacy, api_mode):
    source = inputs()
    entry = {
        "id": "m",
        "context_window": 8192,
        "api_mode": api_mode,
        "api_fallback": "chat_completions" if api_mode == "responses" else "none",
        "private_extra": "provider secret",
    }
    source["provider"].kind = "openai"
    source["provider"].extra_json = "{}"
    source["provider"].get_models = lambda: [entry]
    snapshot = build_snapshot(**source)
    if legacy:
        snapshot.pop("model_config", None)
    else:
        assert snapshot["model_config"] == {
            "api_mode": entry["api_mode"],
            "api_fallback": entry["api_fallback"],
        }
        assert "provider secret" not in json.dumps(snapshot)
        # A frozen snapshot must keep its API even if the supplied row changes.
        source["provider"].get_models = lambda: [{"id": "m"}]
    model = build_model(source["provider"], snapshot).bound
    assert model.use_responses_api is (api_mode == "responses")
    assert hasattr(model, "_harness_api_fallback_model") is (api_mode == "responses")


@pytest.mark.parametrize("input_limit", [None, 1800])
def test_small_context_keeps_room_for_input_and_honors_the_input_limit(input_limit):
    source = inputs()
    entry = {"id": "m", "context_window": 4096}
    source["provider"].get_models = lambda: [entry]
    source["definition"]["runtime_settings"] = {}
    if input_limit is not None:
        source["definition"]["runtime_settings"]["max_input_length"] = input_limit
    snapshot = build_snapshot(**source)
    assert snapshot["max_input_tokens"] == (input_limit or 4095)
    assert snapshot["model_settings"]["max_tokens"] == 4095


@pytest.mark.parametrize("model", ["", "AUTO", "auto", None])
def test_auto_requires_enterprise_default_and_does_not_choose_any_model(model):
    source = inputs()
    source["definition"]["fields"]["default_model"] = model
    source["global_model"] = ("", "")
    with pytest.raises(ExecutionConflict, match="model_required"):
        build_snapshot(**source)


def test_explicit_missing_model_does_not_fall_back():
    source = inputs()
    source["definition"]["fields"]["default_model"] = "p/removed"
    with pytest.raises(ExecutionConflict, match="model_unavailable"):
        build_snapshot(**source)


def test_display_changes_do_not_change_execution_digest():
    source = inputs()
    first = snapshot_digest(build_snapshot(**source))
    source["definition"]["fields"].update(name="New name", icon_url="https://avatar.invalid")
    assert snapshot_digest(build_snapshot(**source)) == first
    source["provider_version"]["revision"] += 1
    assert snapshot_digest(build_snapshot(**source)) != first


@pytest.mark.asyncio
async def test_publication_compacts_group_portraits_without_changing_execution(tmp_path):
    source = inputs()
    portrait = BytesIO()
    Image.frombytes("RGB", (200, 200), Random(10).randbytes(120_000)).save(portrait, "PNG")
    original = portrait.getvalue()
    root = tmp_path / "company-runtime-skills"
    avatar = {"sha256": store_object(root, original), "media_type": "image/png"}
    definition = {**source["definition"], "avatar": avatar}
    services = SimpleNamespace(
        group_expert_repo=SimpleNamespace(
            sources=list, provider_version=lambda _: source["provider_version"]
        ),
        provider_repo=SimpleNamespace(list_all=lambda: [source["provider"]]),
        settings_repo=SimpleNamespace(get_active_model=lambda: source["global_model"]),
        paths=SimpleNamespace(root=tmp_path),
    )

    (published,) = await prepare_publication(services, {"definitions": {"source": definition}})

    assert published["snapshot"] == build_snapshot(**source)
    prefix, thumbnail = published["display"]["avatar"]["url"].split(",", 1)
    assert prefix == "data:image/webp;base64"
    assert len(thumbnail) < len(original)
    with Image.open(BytesIO(base64.b64decode(thumbnail))) as image:
        assert image.size == (64, 64)
    assert (root / avatar["sha256"]).read_bytes() == original


def test_unicode_reply_includes_suffix_within_character_limit():
    answer = format_answer("猫🙂" * 100, max_chars=30, suffix="\n答复已截断")
    assert len(answer["text"]) == 30 and answer["truncated"]
    incomplete = format_answer("Short", max_chars=30, suffix="\nIncomplete", incomplete=True)
    assert incomplete["text"].endswith("Incomplete") and incomplete["incomplete"]
    with pytest.raises(ExecutionConflict, match="empty_output"):
        format_answer(" \n ", max_chars=30, suffix="Truncated")
