"""평가 질문셋(골든셋) 읽기와 검증. 파일은 한 줄에 GoldItem JSON 하나(JSON Lines)."""

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

QType = Literal[
    "lookup", "table", "multi_doc", "out_of_scope", "needs_human", "no_access", "outdated"
]


class Evidence(BaseModel):
    """정답 근거 위치. article이 없으면 문서 단위 근거(그 문서의 아무 청크나 맞음)."""

    # "artcle" 같은 오타가 조용히 무시되면 문서 단위 근거가 되어 검색 지표가 부풀려진다
    model_config = ConfigDict(extra="forbid")

    doc_id: str
    article: str | None = None
    page: int | None = None


class GoldItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    question: str
    answer: str
    evidence: list[Evidence]
    qtype: QType


def load_golden(path: Path) -> list[GoldItem]:
    """빈 줄·잘못된 JSON·형식이 틀린 항목은 "파일:줄 번호"를 담은 ValueError로 알린다."""
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
    return items


def validate_golden(items: list[GoldItem], known_doc_ids: set[str]) -> list[str]:
    """형식은 맞지만 평가에 쓸 수 없는 항목을 찾는다. 빈 목록이면 정상."""
    problems = []
    seen: set[str] = set()
    for item in items:
        if item.id in seen:
            problems.append(f"{item.id}: duplicate id")
        seen.add(item.id)
        for ev in item.evidence:
            if ev.doc_id not in known_doc_ids:
                problems.append(f"{item.id}: unknown doc_id {ev.doc_id!r}")
        # 범위 밖 질문은 거절이 정답이라 근거가 없어야 하고, 나머지는 근거가 있어야 채점할 수 있다
        if item.qtype == "out_of_scope" and item.evidence:
            problems.append(f"{item.id}: out_of_scope item must have no evidence")
        elif item.qtype != "out_of_scope" and not item.evidence:
            problems.append(f"{item.id}: {item.qtype} item needs evidence")
    return problems
