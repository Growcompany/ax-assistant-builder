"""Kiwi 형태소 분석으로 BM25용 내용어 토큰을 뽑는다."""

from kiwipiepy import Kiwi

# 명사, 동사, 형용사, 어근, 외국어, 숫자 (Kiwi 태그는 "VV-R"처럼 접미가 붙을 수 있어 접두로 비교)
_CONTENT_TAG_PREFIXES = ("NN", "VV", "VA", "XR", "SL", "SN")

# 문맥 없이 조사 하나만 오면("은?") Kiwi가 명사로 읽는다. "으"·"르"는 "으로?"·"를?"의 분석 조각
_STOPWORDS = frozenset("은 는 이 가 을 를 의 에 와 과 도 로 요 으 르".split())

# 사전 로딩에 1초 이상 걸려서 모듈에서 한 번만 만든다
_kiwi = Kiwi()


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for token in _kiwi.tokenize(text):
        if not token.tag.startswith(_CONTENT_TAG_PREFIXES):
            continue
        form = token.form.lower() if token.tag.startswith("SL") else token.form
        if form not in _STOPWORDS:
            tokens.append(form)
    return tokens
