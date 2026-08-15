from studio.text import smart_chunks, split_sentences


def test_arabic_question_mark_is_sentence_boundary():
    assert split_sentences("مرحبا؟ كيف حالك؟ أنا بخير.") == [
        "مرحبا؟",
        "كيف حالك؟",
        "أنا بخير.",
    ]


def test_cjk_punctuation_splits_without_spaces():
    assert split_sentences("你好。今天很好！再见。") == ["你好。", "今天很好！", "再见。"]


def test_smart_chunks_respect_limit_for_normal_sentences():
    chunks = smart_chunks("One short sentence. Another short sentence. Third one.", max_chars=34)
    assert all(len(chunk) <= 34 for chunk in chunks)
    assert " ".join(chunks) == "One short sentence. Another short sentence. Third one."


def test_smart_chunks_do_not_insert_spaces_after_cjk_punctuation():
    chunks = smart_chunks("你好。今天很好！再见。" * 10, max_chars=32)
    assert all("。 " not in chunk and "！ " not in chunk for chunk in chunks)
