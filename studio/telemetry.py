from __future__ import annotations

import json
import os
import platform
import urllib.request
from dataclasses import dataclass
from typing import Any


SAFE_EVENTS = frozenset(
    {
        "studio_opened",
        "generation_started",
        "generation_completed",
        "generation_failed",
        "engine_install_started",
        "engine_install_completed",
        "engine_install_failed",
        "transcription_started",
        "transcription_completed",
        "transcription_failed",
    }
)

SAFE_PROPERTY_KEYS = frozenset(
    {
        "app_version",
        "engine_id",
        "capability",
        "priority",
        "compute",
        "resource_tier",
        "duration_bucket",
        "success",
        "error_class",
        "os_family",
    }
)


@dataclass(frozen=True, slots=True)
class TelemetryConfig:
    enabled: bool = False
    project_token: str | None = None
    host: str = "https://us.i.posthog.com"

    @classmethod
    def from_settings(cls, settings: dict[str, Any]) -> "TelemetryConfig":
        return cls(
            enabled=bool(settings.get("telemetry_enabled", False)),
            project_token=os.getenv("POSTHOG_PROJECT_TOKEN") or None,
            host=(os.getenv("POSTHOG_HOST") or "https://us.i.posthog.com").rstrip("/"),
        )


class Telemetry:
    """Tiny opt-in analytics boundary.

    No user content is accepted by this API. Event names and property keys are
    allowlisted so a future UI change cannot accidentally send scripts, transcripts,
    voice references, account names, absolute paths or generated audio metadata.
    """

    def __init__(self, config: TelemetryConfig, *, distinct_id: str = "anonymous-local-install"):
        self.config = config
        self.distinct_id = distinct_id

    @property
    def active(self) -> bool:
        return bool(self.config.enabled and self.config.project_token)

    @staticmethod
    def _safe_properties(properties: dict[str, Any] | None) -> dict[str, Any]:
        safe: dict[str, Any] = {"os_family": platform.system() or "unknown"}
        for key, value in (properties or {}).items():
            if key not in SAFE_PROPERTY_KEYS:
                continue
            if value is None or isinstance(value, (str, int, float, bool)):
                safe[key] = value
        return safe

    def capture(self, event: str, properties: dict[str, Any] | None = None) -> bool:
        if event not in SAFE_EVENTS or not self.active:
            return False
        payload = {
            "api_key": self.config.project_token,
            "event": event,
            "properties": {
                "distinct_id": self.distinct_id,
                **self._safe_properties(properties),
            },
        }
        request = urllib.request.Request(
            f"{self.config.host}/capture/",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=2) as response:  # noqa: S310 - explicit configured PostHog host
                return 200 <= int(response.status) < 300
        except Exception:
            # Analytics must never break local creation.
            return False
