from __future__ import annotations

import pytest

from fatfish.tts import TTSError, split_for_tts


def test_short_text_is_one_chunk():
    assert split_for_tts("今天有点累，陪我说会儿话嘛。") == [
        "今天有点累，陪我说会儿话嘛。"
    ]


def test_long_text_splits_by_sentence():
    text = "今天上课老师讲得特别快。" * 12
    chunks = split_for_tts(text, max_chars=30, total_max=1000)
    assert len(chunks) > 1
    assert all(len(chunk) <= 30 for chunk in chunks)
    assert "".join(chunks).replace("\n", "") == text.replace("\n", "")


def test_total_limit_is_enforced():
    with pytest.raises(TTSError):
        split_for_tts("a" * 301, total_max=300)
