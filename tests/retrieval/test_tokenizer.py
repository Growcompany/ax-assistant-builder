import pytest

from axbuilder.retrieval.tokenizer import tokenize


def test_keeps_content_morphemes():
    tokens = tokenize("예금자보호법에 따른 보호한도는 얼마인가요?")
    assert "보호" in tokens
    assert "에" not in tokens and "는" not in tokens  # 조사 제거


def test_query_without_content_tokens():
    assert tokenize("은?") == []


@pytest.mark.parametrize("text", ["가?", "을?", "를?", "의?", "로?", "요?", "", "   ", "?!"])
def test_lone_particles_and_blank_yield_nothing(text):
    # Kiwi는 문맥 없이 조사 하나만 오면 명사(NNG)로 읽는 경우가 있다
    assert tokenize(text) == []


def test_verbs_are_lemmatized():
    assert "따르" in tokenize("법에 따른 절차")


def test_keeps_numbers_and_latin_case_insensitive():
    tokens = tokenize("OTP 3회 오류")
    assert "otp" in tokens and "3" in tokens and "오류" in tokens
    assert tokenize("otp") == tokenize("OTP")
