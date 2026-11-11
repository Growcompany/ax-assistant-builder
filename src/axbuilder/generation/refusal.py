"""거절 판정: 모델의 답이 "자료로는 답할 수 없다"는 말뿐인지 규칙으로 가른다.

LLM을 한 번 더 부르지 않는다. 정상 답을 거절로 바꾸면 사용자가 답을 잃으므로 정밀도가
우선이다: 애매하면 NONE(답변)이다. 규칙은 qwen3.5:4b의 실제 출력으로 다듬고 검증했다
(docs/experiments/2026-11-11-refusal-detection.md). 다른 모델을 쓰면 다시 검증해야 한다.
"""

import re
from enum import StrEnum

REFUSAL_TEXT = "제공된 자료로는 확인할 수 없습니다."


class RefusalKind(StrEnum):
    EXACT = "exact"  # 정해진 문구로 시작하거나 내용이 없다
    PARAPHRASE = "paraphrase"  # 문구는 다르지만 답 전체가 "자료에 없다"와 그 이유뿐이다
    NONE = "none"  # 그 밖: 답변, 부분 답변


# ---- EXACT ----

# 거절 판정 때 무시하는 것: 공백, 따옴표, 백틱, 마크다운 굵게(**), 인용 표시 [n]
_NOISE = re.compile(r"[\s\"'“”‘’`*]+")
# 인용 매핑(answer.CITATION)과 달리 유니코드 숫자(\d: 전각 '０', 아라비아 '٣')도 받는다.
# 인용으로 매핑하지 않는 '[０]'도 내용은 아니므로, 그것만 있는 답은 거절이다
_MARKER = re.compile(r"\[\d+(?:\s*,\s*\d+)*\]")
_WORD = re.compile(r"\w")  # 글자·숫자(한글 포함)


def _normalize(text: str) -> str:
    return _NOISE.sub("", _MARKER.sub("", text))


# 끝의 마침표는 빼고 비교한다: "…없습니다!", "…없습니다。"도 거절이다
_REFUSAL_CORE = _normalize(REFUSAL_TEXT).rstrip(".")


def _is_exact(text: str) -> bool:
    """정규화한 응답에 글자·숫자가 없거나 거절 문구로 시작하면 거절이다.

    글자·숫자가 없는 응답 = Claude의 안전 거절(stop_reason="refusal")로 온 빈 텍스트, 또는
    "[9].", "[1]."처럼 인용 표시와 문장부호뿐인 응답.
    "확인할 수 없습니다. [1]에는 …이 없습니다"처럼 거절 이유에 자료 번호가 나오기도 해서,
    거절 문구로 시작하면 뒤에 인용이 있어도 거절로 본다. 앞에 실제 답이 있고 거절 문구가
    뒤에 붙은 부분 답변은 거절이 아니다.
    """
    core = _normalize(text)
    return not _WORD.search(core) or core.startswith(_REFUSAL_CORE)


# ---- PARAPHRASE ----
#
# 답을 문장으로 나눠 "모든 문장이 없음 서술"일 때만 PARAPHRASE다. 문장 하나가 없음 서술이려면:
#   1. 문장의 맨 끝 서술어가 아래 네 가지 "없음" 꼴 가운데 하나다.
#   2. 숫자가 없다. 수치(기간, 금액, 비율, 조항 번호)는 질문에 대한 답일 수 있다.
#   3. "…지만/…으나"로 꺾이는 문장이면, 꺾이기 전 절이 "자료가 무엇을 다룬다"는 설명이다.
# 그리고 답의 첫 문장이 "자료/문서"를 가리켜야 한다.
#
# 아래 어간과 명사는 다듬기 묶음(실행 0110, 0119-2)의 거절 출력에 실제로 나온 것만 넣었다.
# 넓게 잡으면 재현율은 오르지만 "중도해지할 수 없습니다"(정답) 같은 문장을 삼킬 위험이 커진다.

# 문장 끝: 높임말 어미와 닫는 문장부호
_END = r"(?:습니다|어요|아요|다|음)?[\s.!?。…)\]]*$"
_ABSENCE = re.compile(
    "(?:"
    # (가) 알 수 없다: "확인할 수 없습니다", "답변드리기 어렵습니다". 어간을 확인·답변·판단으로
    #      묶어야 "중도해지할 수 없습니다", "질권설정할 수 없습니다"가 걸리지 않는다
    r"(?:확인|답변|판단)(?:할|\s?드릴)\s?수(?:는|가)?\s?없"
    r"|(?:확인|답변|판단)(?:하기|\s?드리기)\s?어렵"
    r"|답변을\s?하지\s?않"
    # (나) 자료에 실려 있지 않다(피동): "포함되어 있지 않습니다", "언급되지 않았습니다"
    r"|(?:확인|포함|명시|언급|기재)(?:되어\s?있지|되지)(?:는)?\s?않았?"
    # (다) 자료가 다루지 않는다(능동): "다루고 있지 않습니다", "알려주고 있지 않습니다"
    r"|(?:다루|알려\s?주)고\s?있지(?:는)?\s?않았?"
    # (라) 자료에 그런 것이 없다: "…정보는 없습니다", "…규정은 제공된 문서에 없습니다".
    #      명사를 묶어야 "수수료가 없습니다", "제한이 없습니다"(정답)가 걸리지 않는다
    r"|(?:정보|내용|규정|근거|자료)(?:은|는|이|가|도)?\s?(?:(?:제공된\s?)?(?:자료|문서)에는?\s?)?없"
    r"|(?:자료|문서)에는?\s?없"
    ")" + _END
)
# 숫자가 있으면 없음 서술이 아니다. 인용 표시 [1], [자료 2]는 숫자로 치지 않는다
_REFERENCE = re.compile(r"\[(?:자료\s*)?\d+(?:\s*,\s*\d+)*\]")
_DIGIT = re.compile(r"\d")
# 첫 문장은 자료를 가리켜야 한다: "전화로는 본인 여부를 확인할 수 없습니다"(정답)와 가른다
_MATERIAL = re.compile(r"자료|문서")
# 문장이 꺾이는 곳. 앞 절이 자료 설명("[1]은 …을 다루고 있으나")일 때만 이유 설명으로 본다.
# 앞 절이 "철회는 가능하지만"이면 부분 답변이다
_CONTRAST = re.compile(r"(?:지만|으나|하나)(?=[,\s])")
_DESCRIBES = re.compile(r"(?:제공|명시|언급|다루|정의)\S{0,6}(?:\s\S{1,4}){0,2}$")
# 문장 맨 앞의 접속어 "하지만", "그러나"는 꺾이는 곳이 아니다
_LEADING_CONJUNCTION = re.compile(r"^(?:하|그러|그렇)?$")
# 문장 나누기: 줄바꿈, 또는 문장부호 뒤 공백. "0.70%"의 점은 뒤에 공백이 없어 나뉘지 않는다
_SENTENCE_BREAK = re.compile(r"\n+|(?<=[.!?。])\s+")
_BULLET = re.compile(r"^(?:[-*•·]\s+|#+\s+)+")


def _sentences(text: str) -> list[str]:
    """인용 표시와 마크다운 꾸밈을 지우고 문장으로 나눈다. 글자가 없는 조각은 버린다."""
    plain = _REFERENCE.sub(" ", text).replace("**", "").replace("`", "")
    parts = (_BULLET.sub("", part.strip()) for part in _SENTENCE_BREAK.split(plain))
    return [part for part in parts if _WORD.search(part)]


def _is_absence_statement(sentence: str) -> bool:
    if _DIGIT.search(sentence) or not _ABSENCE.search(sentence):
        return False
    for turn in _CONTRAST.finditer(sentence):
        before = sentence[: turn.start()].strip()
        if _LEADING_CONJUNCTION.match(before):
            continue
        if not _DESCRIBES.search(before):
            return False
    return True


def _is_paraphrase(text: str) -> bool:
    sentences = _sentences(text)
    if not sentences or not _MATERIAL.search(sentences[0]):
        return False
    return all(_is_absence_statement(sentence) for sentence in sentences)


def classify_refusal(text: str) -> RefusalKind:
    """모델의 답(정규화 전 원문)을 EXACT / PARAPHRASE / NONE으로 가른다."""
    if _is_exact(text):
        return RefusalKind.EXACT
    if _is_paraphrase(text):
        return RefusalKind.PARAPHRASE
    return RefusalKind.NONE
