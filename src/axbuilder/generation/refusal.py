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
# 가르려는 것: 거절은 "이 질문에 답할 근거가 자료에 없다"는 말이고, 정답은 "자료가 이렇게
# 말한다"는 말이다. 자료가 하는 말이 부정문이어도("법인은 가입 대상에 포함되지 않습니다",
# "면제하는 규정은 없습니다", "전화로는 본인 여부를 확인할 수 없습니다") 그것은 정답이다.
#
# 답을 문장으로 나눠 "모든 문장이 없음 서술"일 때만 PARAPHRASE다. 문장 하나가 없음 서술이려면:
#   1. 끝 서술어가 아래 두 꼴 가운데 하나다.
#      (가) 알 수 없다: "제공된 자료로는 … 확인할 수 없습니다". 같은 문장에 "제공된 자료로는"이
#           있어야 한다. 프롬프트의 거절 문구에 대상만 끼운 꼴이다.
#      (나) 자료에 정보가 없다: 주어가 "정보/내용"이거나 자리가 "자료에/문서에"다
#           ("… 정보는 포함되어 있지 않습니다", "… 은 자료에는 없습니다"). 주어가 "수수료",
#           "규정", "법인"이면 자료의 내용을 전하는 부정문일 수 있어 받지 않는다.
#   2. 출처 표지("따르면", "의하면", "에서는", "바로는")가 없다. 출처를 밝힌 문장은 답이다.
#   3. 인용 표시 [n]으로 끝나지 않는다. 문장 끝의 인용은 "근거가 있다"는 모델의 주장이다.
#      (거절 이유의 "[1]과 [2]는 …만 다루고 있으며"처럼 문장 앞·가운데의 인용은 괜찮다.)
#   4. 숫자가 없다. 수치(기간, 금액, 비율, 조항 번호)는 질문에 대한 답일 수 있다.
#   5. "…지만/…으나"로 꺾이는 문장이면, 꺾이기 전 절이 "자료가 무엇을 다룬다"는 설명이다.
# 그리고 답의 첫 문장에 "제공된 자료로는"이 있어야 한다.
#
# 어간과 명사는 다듬기 묶음(실행 0110, 0119-2)의 거절 출력에 실제로 나온 것만 넣었다.
# 2·3번과 (나)의 주어 제한은 PR #23 리뷰에서 나온 오탐("자료 [1]에 따르면 미성년자는 가입
# 대상에 포함되지 않습니다"가 거절이 됨)을 막으려고 더했다. 재현율을 내주고 얻은 것이다.

# 문장 끝: 높임말 어미와 닫는 문장부호
_END = r"(?:습니다|어요|아요|다|음)?[\s.!?。…)\]]*$"
# "제공된 자료로는": 자료를 수단으로 삼아 말하는 꼴. "자료에 따르면"·"자료의 약관에는"과 가른다
_QUALIFIER = re.compile(r"(?:제공된|주어진)\s?자료\s*(?:만으로는|로는)|^자료\s*(?:만으로는|로는)")
# (가) 어간을 확인·답변·판단으로 묶어야 "중도해지할 수 없습니다"(정답)가 걸리지 않는다
_UNKNOWN = re.compile(
    r"(?:(?:확인|답변|판단)(?:할|\s?드릴)\s?수(?:는|가)?\s?없"
    r"|(?:확인|답변|판단)(?:하기|\s?드리기)\s?어렵"
    r"|확인되지\s?않았?)" + _END
)
# (나) 주어가 정보·내용이거나, "자료에(는)" 바로 뒤에 없다는 말이 온다
_NOT_THERE = r"(?:없|(?:포함|명시|언급|기재|확인)(?:되어\s?있지|되지)(?:는)?\s?않았?)"
_MISSING = re.compile(
    r"(?:(?:정보|내용)(?:은|는|이|가|도)?\s?(?:(?:제공된\s?)?(?:자료|문서)에는?\s?)?"
    + _NOT_THERE
    + r"|(?:자료|문서)에는?\s?"
    + _NOT_THERE
    + ")"
    + _END
)
_SOURCE = re.compile(r"따르면|의하면|에서는|바로는")
# 인용 표시 [1], [자료 2]. 숫자로 치지 않도록 자리 표시 문자로 바꿔 두고 위치만 본다
_REFERENCE = re.compile(r"\[(?:자료\s*)?\d+(?:\s*,\s*\d+)*\]")
_CITED = ""  # 사용자 영역 문자: 본문에 나올 일이 없다
_ENDS_WITH_CITATION = re.compile(_CITED + r"[\s.!?。…)]*$")
_DIGIT = re.compile(r"\d")
# 문장이 꺾이는 곳. 앞 절이 자료 설명("[1]은 …을 다루고 있으나")일 때만 이유 설명으로 본다.
# 앞 절이 "철회는 가능하지만"이면 부분 답변이다
_CONTRAST = re.compile(r"(?:지만|으나|하나)(?=[,\s])")
_DESCRIBES = re.compile(r"(?:제공|명시|언급|다루|정의)\S{0,6}(?:\s\S{1,4}){0,2}$")
# 문장 맨 앞의 접속어 "하지만", "그러나"는 꺾이는 곳이 아니다
_LEADING_CONJUNCTION = re.compile(r"^(?:하|그러|그렇)?$")
# 문장 나누기: 줄바꿈, 또는 문장부호 뒤 공백. "0.70%"의 점은 뒤에 공백이 없어 나뉘지 않는다
_SENTENCE_BREAK = re.compile(r"\n+|(?<=[.!?。])\s+")
_BULLET = re.compile(r"^(?:[-*•·]\s+|#+\s+)+")


def _sentences(text: str) -> list[tuple[str, bool, bool]]:
    """(문장, 인용으로 끝나는가, 인용이 들어 있는가)의 목록. 인용 표시와 마크다운 꾸밈은 지운다.

    글자가 없는 조각은 버린다. 다만 인용 표시뿐인 조각("…없습니다. [1]"의 "[1]")은 앞 문장에
    붙은 인용으로 본다.
    """
    marked = _REFERENCE.sub(f" {_CITED} ", text.replace(_CITED, "")).replace("**", "")
    result: list[tuple[str, bool, bool]] = []
    for part in _SENTENCE_BREAK.split(marked.replace("`", "")):
        part = _BULLET.sub("", part.strip())
        plain = part.replace(_CITED, " ").strip()
        if _WORD.search(plain):
            result.append((plain, bool(_ENDS_WITH_CITATION.search(part)), _CITED in part))
        elif _CITED in part and result:
            result[-1] = (result[-1][0], True, True)
    return result


def _is_absence_statement(sentence: str, cited: bool, has_citation: bool = False) -> bool:
    if cited or _DIGIT.search(sentence) or _SOURCE.search(sentence):
        return False
    # "제공된 자료 [1]로는 …할 수 없습니다": 거절 문구와 같은 꼴이어도 그 문장 안에 인용을 달았으면
    # 자료가 그렇게 말한다는 답이다(인용은 "근거가 있다"는 주장이다)
    if has_citation and _QUALIFIER.search(sentence):
        return False
    unknown = _QUALIFIER.search(sentence) and _UNKNOWN.search(sentence)
    if not unknown and not _MISSING.search(sentence):
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
    if not sentences or not _QUALIFIER.search(sentences[0][0]):
        return False
    return all(_is_absence_statement(*sentence) for sentence in sentences)


def classify_refusal(text: str) -> RefusalKind:
    """모델의 답(정규화 전 원문)을 EXACT / PARAPHRASE / NONE으로 가른다."""
    if _is_exact(text):
        return RefusalKind.EXACT
    if _is_paraphrase(text):
        return RefusalKind.PARAPHRASE
    return RefusalKind.NONE
