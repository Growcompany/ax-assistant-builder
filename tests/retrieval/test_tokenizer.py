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


@pytest.mark.parametrize(("text", "word"), [("30도", "도"), ("말한 이가", "이"), ("가서", "가")])
def test_keeps_stopword_forms_used_as_content(text, word):
    # 불용어는 조사를 명사(NN*)로 잘못 읽은 경우에만 버린다. 의존명사·동사는 남긴다
    assert word in tokenize(text)


def test_keeps_number_words():
    # 수사(NR)가 없으면 둘 다 ["1", "원"]이 되어 금액을 구분하지 못한다
    assert tokenize("1억원") == ["1", "억", "원"]
    assert tokenize("1천만원") == ["1", "천만", "원"]


@pytest.mark.parametrize(
    ("text", "serial"), [("고객센터 1588-9999", "1588-9999"), ("2024.01.01 시행", "2024.01.01")]
)
def test_keeps_serials(text, serial):
    # Kiwi는 전화번호·날짜를 W_SERIAL 한 토큰으로 묶는다
    assert serial in tokenize(text)


def test_verbs_are_lemmatized():
    assert "따르" in tokenize("법에 따른 절차")


def test_keeps_numbers_and_latin_case_insensitive():
    tokens = tokenize("OTP 3회 오류")
    assert "otp" in tokens and "3" in tokens and "오류" in tokens
    assert tokenize("otp") == tokenize("OTP")
