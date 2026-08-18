from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Mapping


STORAGE_ENV = "CREATOR_STUDIO_STORAGE_ROOT"
APP_DIR_NAME = "CreatorStudio"
SPEECH_CORE_DIR_NAME = "speech-core"


def resolve_storage_root(
    source_root: Path,
    *,
    frozen: bool | None = None,
    platform: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return the writable root for user-created data.

    Source/development runs preserve the repository-local layout so existing users and
    tests do not move unexpectedly. Frozen desktop builds use the operating system's
    user-data area so updates/uninstalls never treat voices, projects, recipes, or
    generated audio as application files.

    CREATOR_STUDIO_STORAGE_ROOT is an explicit override for tests, portable setups,
    and advanced users.
    """

    env = os.environ if environ is None else environ
    override = (env.get(STORAGE_ENV) or "").strip()
    if override:
        return Path(override).expanduser().resolve()

    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else bool(frozen)
    if not is_frozen:
        return Path(source_root).resolve()

    target_platform = sys.platform if platform is None else platform
    user_home = Path.home() if home is None else Path(home)

    if target_platform.startswith("win"):
        local = (env.get("LOCALAPPDATA") or "").strip()
        base = Path(local) if local else user_home / "AppData" / "Local"
        return (base / APP_DIR_NAME).resolve()

    if target_platform == "darwin":
        return (user_home / "Library" / "Application Support" / APP_DIR_NAME).resolve()

    xdg = (env.get("XDG_DATA_HOME") or "").strip()
    base = Path(xdg) if xdg else user_home / ".local" / "share"
    return (base / APP_DIR_NAME).resolve()


def speech_core_data_dir(storage_root: str | Path) -> Path:
    """Canonical private data directory for the reusable Speech Core.

    Callers may choose a different storage root, but they should not invent a second
    layout under it. Keeping this helper shared prevents the desktop UI, CLI and an
    external local client from silently creating separate voice-profile stores.
    """

    return Path(storage_root).expanduser().resolve() / "data" / SPEECH_CORE_DIR_NAME
