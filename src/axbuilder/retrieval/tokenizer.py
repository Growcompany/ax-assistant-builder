"""Kiwi 형태소 분석으로 BM25용 내용어 토큰을 뽑는다."""

from kiwipiepy import Kiwi

# 명사, 동사, 형용사, 어근, 외국어, 숫자, 수사("억"·"천만"), 일련번호(전화번호·날짜)
# Kiwi 태그는 "VV-R"처럼 접미가 붙을 수 있어 접두로 비교한다
_CONTENT_TAG_PREFIXES = ("NN", "VV", "VA", "XR", "SL", "SN", "NR", "W_SERIAL")

# 문맥 없이 조사 하나만 오면("은?") Kiwi가 명사(NNG·NNP)로 읽는다. "으"·"르"는 "으로?"·"를?"의
# 분석 조각. 명사로 태깅된 토큰에만 적용해 동사 "가서"의 "가" 같은 내용어는 지키고,
# 의존명사로 자주 쓰이는 "도"(30도)·"이"(말한 이)는 넣지 않는다
_STOPWORDS = frozenset("은 가 을 의 과 로 요 으 르".split())

# 사전 로딩에 1초 이상 걸려서 모듈에서 한 번만 만든다
_kiwi = Kiwi()


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for token in _kiwi.tokenize(text):
        if not token.tag.startswith(_CONTENT_TAG_PREFIXES):
            continue
        if token.tag.startswith("NN") and token.form in _STOPWORDS:
            continue
        tokens.append(token.form.lower() if token.tag.startswith("SL") else token.form)
    return tokens
