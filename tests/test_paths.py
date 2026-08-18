from __future__ import annotations

from pathlib import Path

from studio.paths import STORAGE_ENV, resolve_storage_root, speech_core_data_dir


def test_source_runs_preserve_repository_local_storage(tmp_path: Path):
    source = tmp_path / "repo"
    assert resolve_storage_root(source, frozen=False, environ={}) == source.resolve()


def test_explicit_storage_override_wins_even_for_frozen_builds(tmp_path: Path):
    source = tmp_path / "bundle"
    override = tmp_path / "portable-data"
    result = resolve_storage_root(
        source,
        frozen=True,
        platform="win32",
        environ={STORAGE_ENV: str(override), "LOCALAPPDATA": str(tmp_path / "Local")},
        home=tmp_path / "home",
    )
    assert result == override.resolve()


def test_frozen_windows_uses_local_app_data(tmp_path: Path):
    source = tmp_path / "bundle"
    local = tmp_path / "LocalAppData"
    result = resolve_storage_root(
        source,
        frozen=True,
        platform="win32",
        environ={"LOCALAPPDATA": str(local)},
        home=tmp_path / "home",
    )
    assert result == (local / "CreatorStudio").resolve()


def test_frozen_windows_has_safe_home_fallback(tmp_path: Path):
    home = tmp_path / "home"
    result = resolve_storage_root(
        tmp_path / "bundle",
        frozen=True,
        platform="win32",
        environ={},
        home=home,
    )
    assert result == (home / "AppData" / "Local" / "CreatorStudio").resolve()


def test_frozen_linux_uses_xdg_data_home(tmp_path: Path):
    xdg = tmp_path / "xdg"
    result = resolve_storage_root(
        tmp_path / "bundle",
        frozen=True,
        platform="linux",
        environ={"XDG_DATA_HOME": str(xdg)},
        home=tmp_path / "home",
    )
    assert result == (xdg / "CreatorStudio").resolve()


def test_speech_core_uses_one_subdirectory_under_storage_root(tmp_path: Path):
    root = tmp_path / "portable"
    assert speech_core_data_dir(root) == (root / "data" / "speech-core").resolve()
