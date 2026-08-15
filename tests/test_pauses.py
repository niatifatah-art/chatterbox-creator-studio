import pytest

from studio.pauses import Pause, Speech, find_invalid_pause_markers, parse_script, pause_samples


def test_parse_exact_pause():
    assert parse_script("Hello. [pause=0.25] World.") == [
        Speech("Hello."),
        Pause(0.25),
        Speech("World."),
    ]


def test_parse_milliseconds_and_seconds_suffix():
    assert parse_script("A[pause=250ms]B[pause:1.5s]C") == [
        Speech("A"),
        Pause(0.25),
        Speech("B"),
        Pause(1.5),
        Speech("C"),
    ]


def test_adjacent_pauses_are_merged():
    assert parse_script("A[pause=0.2][pause=300ms]B") == [Speech("A"), Pause(0.5), Speech("B")]


def test_sample_precision():
    assert pause_samples(0.25, 24000) == 6000
    assert pause_samples(0.375, 24000) == 9000


def test_invalid_pause_is_detected():
    assert find_invalid_pause_markers("Hello [pause=abc] world") == ["[pause=abc]"]


def test_pause_safety_limit():
    with pytest.raises(ValueError):
        parse_script("A[pause=31]B")
