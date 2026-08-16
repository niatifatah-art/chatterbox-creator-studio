from __future__ import annotations

import pytest

from studio.cancellation import (
    GenerationCancelled,
    clear_generation_cancel,
    generation_cancel_requested,
    raise_if_generation_cancelled,
    request_generation_cancel,
)


def test_generation_cancel_flag_is_explicit_and_resettable():
    clear_generation_cancel()
    assert generation_cancel_requested() is False

    request_generation_cancel()
    assert generation_cancel_requested() is True
    with pytest.raises(GenerationCancelled, match="Generation stopped"):
        raise_if_generation_cancelled()

    clear_generation_cancel()
    assert generation_cancel_requested() is False
    raise_if_generation_cancelled()
