from __future__ import annotations

from threading import Event


class GenerationCancelled(RuntimeError):
    """Raised at a safe generation/download boundary after the user requests Stop."""


_generation_cancel = Event()


def clear_generation_cancel() -> None:
    _generation_cancel.clear()


def request_generation_cancel() -> None:
    _generation_cancel.set()


def generation_cancel_requested() -> bool:
    return _generation_cancel.is_set()


def raise_if_generation_cancelled() -> None:
    if _generation_cancel.is_set():
        raise GenerationCancelled("Generation stopped.")
