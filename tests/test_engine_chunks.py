from studio.engine import _sentence_chunks


def test_short_text_is_not_split():
    assert _sentence_chunks("Hello world.", 32) == ["Hello world."]


def test_sentence_boundary_chunking():
    chunks = _sentence_chunks("One short sentence. Another short sentence. Third one.", 34)
    assert all(len(chunk) <= 34 for chunk in chunks)
    assert " ".join(chunks) == "One short sentence. Another short sentence. Third one."


def test_multilingual_boundary_chunking():
    chunks = _sentence_chunks("مرحبا؟ كيف حالك؟ أنا بخير جدا والحمد لله. " * 5, 48)
    assert len(chunks) > 1
    assert all(len(chunk) <= 48 for chunk in chunks)
