"""평가 질문셋(골든셋) 읽기와 검증. 파일은 한 줄에 GoldItem JSON 하나(JSON Lines)."""

import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

QType = Literal[
    "lookup", "table", "multi_doc", "out_of_scope", "needs_human", "no_access", "outdated"
]
# 문항 태그(Task 24). 유형(qtype)과 따로, 태그별 표로 질의 성격에 따른 차이를 본다
TAGS = (
    "article_ref",  # 조항 번호로 묻는다("은행법 제30조의3")
    "product_name",  # 정식 상품명으로 묻는다("AXB 든든 정기예금")
    "negative_answer",  # 정답이 부정문이다("포함되지 않는다", "할 수 없다")
    "paraphrase",  # 원문 문장을 바꿔 썼다
    "internal_only",  # 근거가 internal 문서뿐이다
)
# ingest.chunking이 청크에 붙이는 article 라벨 형식. 이 밖의 라벨("7조", "제 7조", "제7조 ")은
# 어떤 청크와도 같지 않아 그 근거가 오류 없이 0점이 된다
ARTICLE_LABEL = re.compile(r"(?:부칙 )?제\d+조(?:의\d+)?|부칙")


class Evidence(BaseModel):
    """정답 근거 위치. article이 없으면 문서 단위 근거(그 문서의 아무 청크나 맞음)."""

    # "artcle" 같은 오타가 조용히 무시되면 문서 단위 근거가 되어 검색 지표가 부풀려진다
    model_config = ConfigDict(extra="forbid")

    doc_id: str
    article: str | None = None
    page: int | None = None
    # 답이 든 짧은 원문 문구(Task 24). 엄격 채점(metrics.matches_strict)은 이 문구가 든 청크만
    # 맞게 센다. 조항을 나눈 조각이나 문서 단위 근거에서 답이 없는 청크까지 정답으로 세지 않게
    anchor: str | None = None


class GoldItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    question: str
    answer: str
    evidence: list[Evidence]
    qtype: QType
    # 모르는 값도 읽어 두고 validate_golden이 알린다(Literal이면 오타 하나로 파일 전체를 못 읽는다)
    tags: list[str] = []


def load_golden(path: Path) -> list[GoldItem]:
    """빈 줄·잘못된 JSON·형식이 틀린 항목은 "파일:줄 번호"를 담은 ValueError로 알린다.

    문항이 하나도 없는 파일도 ValueError다(0문항 평가가 성공으로 보이지 않게).
    """
    # splitlines()는 JSON 문자열 안에 그대로 들어갈 수 있는 U+2028 등에서도 줄을 나누므로 "\n"으로만
    lines = path.read_text(encoding="utf-8").split("\n")
    if lines[-1] == "":  # 파일 끝 줄바꿈
        lines.pop()
    items = []
    for number, text in enumerate(lines, start=1):
        where = f"{path}:{number}"
        if not text.strip():
            raise ValueError(f"{where}: blank line (one JSON object per line)")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"{where}: invalid JSON: {e}") from e
        try:
            items.append(GoldItem.model_validate(data))
        except ValidationError as e:
            raise ValueError(f"{where}: {e}") from e
    if not items:
        raise ValueError(f"{path}: no items (empty golden set)")
    return items


def validate_golden(items: list[GoldItem], known_doc_ids: set[str]) -> list[str]:
    """형식은 맞지만 평가에 쓸 수 없는 항목을 찾는다. 빈 목록이면 정상."""
    problems = []
    seen: set[str] = set()
    for item in items:
        if item.id in seen:
            problems.append(f"{item.id}: duplicate id")
        seen.add(item.id)
        if not item.question.strip():
            problems.append(f"{item.id}: empty question")
        if not item.answer.strip():
            problems.append(f"{item.id}: empty answer")
        for tag in item.tags:
            if tag not in TAGS:
                problems.append(f"{item.id}: unknown tag {tag!r}")
        seen_evidence: set[tuple[str, str | None, int | None]] = set()
        for ev in item.evidence:
            if ev.doc_id not in known_doc_ids:
                problems.append(f"{item.id}: unknown doc_id {ev.doc_id!r}")
            if ev.article is not None and not ARTICLE_LABEL.fullmatch(ev.article):
                problems.append(f"{item.id}: malformed article label {ev.article!r}")
            if ev.anchor is not None and not ev.anchor.strip():
                where = " ".join(part for part in (ev.doc_id, ev.article) if part)
                problems.append(f"{item.id}: blank anchor {where}")
            key = (ev.doc_id, ev.article, ev.page)
            if key in seen_evidence:
                where = " ".join(str(part) for part in key if part is not None)
                problems.append(f"{item.id}: duplicate evidence {where}")
            seen_evidence.add(key)
        # 범위 밖 질문은 거절이 정답이라 근거가 없어야 하고, 나머지는 근거가 있어야 채점할 수 있다
        if item.qtype == "out_of_scope" and item.evidence:
            problems.append(f"{item.id}: out_of_scope item must have no evidence")
        elif item.qtype != "out_of_scope" and not item.evidence:
            problems.append(f"{item.id}: {item.qtype} item needs evidence")
    return problems
