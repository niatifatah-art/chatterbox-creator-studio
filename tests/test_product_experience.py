from __future__ import annotations

import json
from pathlib import Path

import pytest

from studio.hardware import HardwareProfile, resolve_compute_preference
from studio.model_manager import LocalModelManager
from studio.product import (
    ProductSystemProfile,
    compatible_models,
    detect_script_language,
    model_id_from_ui_name,
    quality_policy,
    resolve_language,
    resolve_model_id,
    safe_compare_order,
    script_looks_arabic,
)


def test_product_names_accept_friendly_and_legacy_values():
    assert model_id_from_ui_name("Multilingual") == "multilingual-v3"
    assert model_id_from_ui_name("Expressive") == "turbo"
    assert model_id_from_ui_name("Light") == "nano"
    assert model_id_from_ui_name("Chatterbox Nano") == "nano"
    assert model_id_from_ui_name("Auto") is None


def test_auto_model_prefers_multilingual_for_arabic():
    script = "مرحبا، هذا اختبار واضح للصوت باللغة العربية."
    profile = ProductSystemProfile(compute="cpu", ram_gb=16, vram_gb=None)
    assert script_looks_arabic(script)
    assert resolve_language("Auto", script) == "Arabic"
    assert resolve_model_id("Auto", "Auto", script, profile) == "multilingual-v3"
    assert compatible_models("Auto", script) == ("multilingual-v3",)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Привет, это тест голоса.", "Russian"),
        ("你好，这是一个语音测试。", "Chinese"),
        ("これは音声のテストです。", "Japanese"),
        ("שלום, זה מבחן קולי.", "Hebrew"),
        ("नमस्ते, यह आवाज़ का परीक्षण है।", "Hindi"),
        ("Γεια σας, αυτή είναι μια δοκιμή.", "Greek"),
        ("안녕하세요. 음성 테스트입니다.", "Korean"),
    ],
)
def test_auto_language_recognizes_distinctive_scripts(text: str, expected: str):
    assert detect_script_language(text) == expected
    assert resolve_language("Auto", text) == expected
    profile = ProductSystemProfile(compute="cpu", ram_gb=16, vram_gb=None)
    assert resolve_model_id("Auto", "Auto", text, profile) == "multilingual-v3"


def test_auto_language_is_conservative_for_latin_script():
    # Short Latin-script creator text is ambiguous across many supported languages.
    # Auto stays predictable instead of pretending to know; the language selector
    # remains available when the user wants Spanish/French/etc.
    assert detect_script_language("Hola, esto es una prueba de voz.") is None
    assert resolve_language("Auto", "Hola, esto es una prueba de voz.") == "English"


def test_auto_model_prefers_light_on_cpu_for_english():
    profile = ProductSystemProfile(compute="cpu", ram_gb=16, vram_gb=None)
    assert resolve_model_id("Auto", "English", "Hello there.", profile) == "nano"


def test_auto_model_prefers_expressive_when_tags_are_used():
    profile = ProductSystemProfile(compute="gpu", ram_gb=32, vram_gb=12)
    assert resolve_model_id("Auto", "English", "That worked. [laugh] Nice.", profile) == "turbo"


def test_explicit_english_only_model_rejects_non_english():
    profile = ProductSystemProfile(compute="gpu", ram_gb=32, vram_gb=12)
    with pytest.raises(ValueError, match="supports English only"):
        resolve_model_id("Light", "Arabic", "مرحبا", profile)


def test_quality_modes_are_simple_but_meaningful():
    assert quality_policy("Fast") == {"quality_check": False, "auto_retries": 0, "best_of_n": 1}
    assert quality_policy("Balanced") == {"quality_check": True, "auto_retries": 0, "best_of_n": 1}
    assert quality_policy("Best") == {"quality_check": True, "auto_retries": 0, "best_of_n": 2}


def test_compare_order_is_predictable_and_memory_safe():
    assert safe_compare_order(["nano", "multilingual-v3", "turbo"]) == (
        "multilingual-v3",
        "turbo",
        "nano",
    )


def test_compute_auto_uses_available_cuda_and_manual_cpu_is_honored():
    profile = HardwareProfile(
        os_name="Windows",
        cpu_name="CPU",
        logical_cpus=8,
        ram_gb=16,
        physical_gpus=("NVIDIA Test GPU",),
        torch_version="2.x",
        cuda_available=True,
        mps_available=False,
        accelerator_name="NVIDIA Test GPU",
        vram_gb=8,
        recommended_compute="cuda",
        acceleration_note="ready",
    )
    assert resolve_compute_preference("Auto", profile)[0] == "cuda"
    assert resolve_compute_preference("CPU", profile)[0] == "cpu"


def test_compute_gpu_override_fails_cleanly_when_runtime_cannot_use_gpu():
    profile = HardwareProfile(
        os_name="Windows",
        cpu_name="CPU",
        logical_cpus=8,
        ram_gb=16,
        physical_gpus=("NVIDIA Test GPU",),
        torch_version="2.x+cpu",
        cuda_available=False,
        mps_available=False,
        accelerator_name=None,
        vram_gb=None,
        recommended_compute="cpu",
        acceleration_note="cpu only",
    )
    with pytest.raises(ValueError, match="cannot use a GPU"):
        resolve_compute_preference("GPU", profile)


def test_existing_hf_cache_is_imported_and_pinned(monkeypatch, tmp_path: Path):
    hub = tmp_path / "hub"
    monkeypatch.setenv("HF_HUB_CACHE", str(hub))
    cache = hub / "models--ResembleAI--chatterbox-nano"
    snapshot = cache / "snapshots" / "abc123"
    snapshot.mkdir(parents=True)
    (snapshot / "weights.safetensors").write_bytes(b"1234")
    (cache / "refs").mkdir()
    (cache / "refs" / "main").write_text("abc123", encoding="utf-8")

    manager = LocalModelManager(tmp_path / "model_state.json")
    status = manager.status("nano")
    assert status.installed
    assert status.revision == "abc123"
    assert Path(status.snapshot_path or "") == snapshot

    state = json.loads((tmp_path / "model_state.json").read_text(encoding="utf-8"))
    assert state["models"]["nano"]["revision"] == "abc123"

    # A moving Hub ref must not silently replace the snapshot selected by the app.
    newer = cache / "snapshots" / "def456"
    newer.mkdir(parents=True)
    (newer / "weights.safetensors").write_bytes(b"5678")
    (cache / "refs" / "main").write_text("def456", encoding="utf-8")
    pinned = manager.status("nano")
    assert pinned.revision == "abc123"
    assert Path(pinned.snapshot_path or "") == snapshot


def test_remove_model_never_touches_projects_voices_or_other_revisions(monkeypatch, tmp_path: Path):
    hub = tmp_path / "hub"
    monkeypatch.setenv("HF_HUB_CACHE", str(hub))
    cache = hub / "models--ResembleAI--chatterbox-nano"
    snapshot = cache / "snapshots" / "abc123"
    snapshot.mkdir(parents=True)
    (snapshot / "weights.safetensors").write_bytes(b"1234")
    newer = cache / "snapshots" / "def456"
    newer.mkdir(parents=True)
    (newer / "other.safetensors").write_bytes(b"5678")
    (cache / "refs").mkdir()
    (cache / "refs" / "main").write_text("abc123", encoding="utf-8")
    (cache / "refs" / "other").write_text("def456", encoding="utf-8")

    voices = tmp_path / "data" / "voices"
    projects = tmp_path / "data" / "projects"
    voices.mkdir(parents=True)
    projects.mkdir(parents=True)
    (voices / "voice.wav").write_bytes(b"voice")
    (projects / "project.json").write_text("{}", encoding="utf-8")

    manager = LocalModelManager(tmp_path / "model_state.json")
    assert manager.status("nano").revision == "abc123"
    assert manager.remove("nano") is True
    assert not snapshot.exists()
    assert newer.exists()
    assert (cache / "refs" / "other").exists()
    assert (voices / "voice.wav").exists()
    assert (projects / "project.json").exists()
