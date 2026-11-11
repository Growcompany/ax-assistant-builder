"""거절 판정(classify_refusal) 테스트. 계획(M-2 축소판 Task 20)의 테스트 명세."""

import json
from pathlib import Path

import pytest

from axbuilder.generation.answer import REFUSAL_TEXT
from axbuilder.generation.refusal import RefusalKind, classify_refusal

FIXTURE = Path(__file__).parents[1] / "fixtures" / "refusal_outputs.jsonl"
# 규칙을 다듬을 때 본 실행 2개와, 규칙을 고치지 않고 재기만 한 검증 실행 1개
TUNING_RUNS = {"20261110-0110-hybrid", "20261110-0119-hybrid-2"}
VALIDATION_RUNS = {"20261110-1304-hybrid"}


def load_fixture() -> list[dict]:
    with FIXTURE.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# --- EXACT: 기존 _is_refusal과 같은 판정 (tests/generation/test_answer.py의 목록 그대로) ---


@pytest.mark.parametrize(
    "reply",
    [
        REFUSAL_TEXT,
        "제공된 자료로는 확인할 수 없습니다",  # 마침표 없음
        f"  {REFUSAL_TEXT}\n",
        f'"{REFUSAL_TEXT}"',
        f"**{REFUSAL_TEXT}**",
        f"`{REFUSAL_TEXT}`",  # 백틱(인라인 코드)
        f"**“{REFUSAL_TEXT}”**",
        "제공된 자료로는 확인할 수 없습니다!",
        "제공된 자료로는  확인할 수\n없습니다。",
        f"{REFUSAL_TEXT} [1]",  # 거절문에 붙은 인용은 근거가 아니다
        f"[1] {REFUSAL_TEXT}",
        f"{REFUSAL_TEXT} 자료에는 신용카드 연회비 정보가 없습니다.",  # 이유를 덧붙인 거절
        f"{REFUSAL_TEXT} [1]에는 신용카드 연회비 정보가 없습니다.",  # 이유에 자료 번호가 나옴
        "",
        "   \n",
        "[9]",  # 범위 밖 번호를 지우면 아무 내용도 없다
        "[9].",
        "[1].",
        "[1, 2]。",
        ".",
        "…",
        "[０]",
        "[０].",
        "[٣]",
        "[１, ２]。",
        "[1, ٣]",
        f"[０] {REFUSAL_TEXT}",
        f"{REFUSAL_TEXT} [１]",
        # 거절 문구로 시작하면 뒤에 인용 단 내용이 있어도 EXACT (기존 규칙 그대로)
        f"{REFUSAL_TEXT} 다만 일반 예금은 1억원까지 보호됩니다 [1].",
    ],
)
def test_fixed_phrase_and_its_variants_are_exact(reply):
    assert classify_refusal(reply) is RefusalKind.EXACT


# --- PARAPHRASE: 기준선(20261110-0119-hybrid-2)의 바꿔 말한 거절 4건, 모델 출력 그대로 ---

BASELINE_PARAPHRASES = {
    "bank-045": (
        "제공된 자료로는 달러 환율이나 현재 환전 시 발생할 수 있는 손해에 대한 정보를 "
        "확인할 수 없습니다."
    ),
    "bank-046": (
        "제공된 자료로는 옆 은행 파킹통장의 금리가 얼마인지 확인할 수 없습니다. "
        "[1] 및 [2] 는 AXB은행의 정기예금 상품에 대해만 서술되어 있으며, "
        "다른 은행 관련 정보는 포함되어 있지 않습니다."
    ),
    "bank-047": (
        "제공된 자료로는 AXB은행이 코인 거래소를 지원하는 여부나 계좌 간 연결 여부를 "
        "확인할 수 없습니다."
    ),
    "bank-050": (
        "제공된 자료로는 AXB 체크카드의 해외 사용 수수료에 대한 정보가 확인되지 않았습니다."
    ),
}


def test_baseline_paraphrases_match_the_fixture():
    # 위 문장이 손으로 고친 것이 아니라 실제 출력임을 fixture로 확인한다
    stored = {
        row["id"]: row["answer"] for row in load_fixture() if row["run"] == "20261110-0119-hybrid-2"
    }
    for item_id, text in BASELINE_PARAPHRASES.items():
        assert stored[item_id] == text


@pytest.mark.parametrize("item_id", sorted(BASELINE_PARAPHRASES))
def test_baseline_paraphrased_refusals_are_paraphrase(item_id):
    assert classify_refusal(BASELINE_PARAPHRASES[item_id]) is RefusalKind.PARAPHRASE


@pytest.mark.parametrize(
    "reply",
    [
        "제공된 자료로는 답변할 수 없습니다.",
        "제공된 자료로는 확인하기 어렵습니다.",
        "제공된 자료로는 확인되지 않습니다.",
        # 없음 서술 뒤에 고정 문구가 한 번 더 붙은 것
        "제공된 자료에는 체크카드 해외 사용 수수료에 대한 정보가 포함되어 있지 않습니다.\n\n"
        f"{REFUSAL_TEXT}",
        # 이유 설명: 자료가 무엇을 다루는지 말하고 "…으나, …없다"로 끝난다
        "제공된 자료로는 수령 비용을 확인할 수 없습니다. [1]과 [2]는 분쟁조정 절차를 "
        "다루고 있으나, 녹취 사본 수령 비용은 포함되지 않았습니다.",
    ],
)
def test_other_paraphrased_refusals(reply):
    assert classify_refusal(reply) is RefusalKind.PARAPHRASE


# --- NONE: Review Focus 1(부분 답변), 2("…할 수 없습니다"가 정답) ---


@pytest.mark.parametrize(
    "reply",
    [
        # 부분 답변: 일부는 없다고 하고 일부는 답한다
        "퇴직연금 한도는 자료에 없지만 청약철회 기간은 3일입니다 [1].",
        "제공된 자료로는 퇴직연금 한도를 확인할 수 없습니다. 일반 예금은 1억원까지 보호됩니다 [1].",
        "제공된 자료로는 수수료를 확인할 수 없습니다. 다만 철회는 서면으로 신청합니다 [1].",
        "연체이자는 약정 대출금리에 연 3%p를 더합니다 [1].\n\n"
        "몇 달 밀리면 한꺼번에 갚아야 하는지는 제공된 자료에는 없습니다.",
        # 없음 서술과 같은 문장 안에 답이 있다
        "철회는 가능하지만 수수료는 제공된 자료로는 확인할 수 없습니다.",
        "제공된 자료로는 수수료를 확인할 수 없지만 철회는 영업점에서 신청합니다.",
        # 이유 설명처럼 보여도 수치가 있으면 그것이 답일 수 있다 (0119-2 bank-042 유형)
        "제공된 자료로는 연체 조건을 확인할 수 없습니다. [1]에는 연체이자율이 약정 대출금리 + "
        "연 3%p라고 명시되어 있지만, 기한의 이익 상실 시점은 언급되지 않았습니다.",
        # "…할 수 없습니다"가 정답인 경우
        "만기 전에는 중도해지할 수 없습니다 [1].",
        "입출금이 자유로운 예금은 질권설정할 수 없습니다. [1]",
        "전화로는 본인 여부를 확인할 수 없습니다 [1].",
        "상담원은 내부 신용등급 산정 방법을 안내할 수 없습니다 [1].",
        "이 상품에는 중도상환수수료가 없습니다 [1].",
        "우대금리는 중도해지할 때 적용되지 않습니다 [1].",
        # 거절 문구가 맨 뒤에 붙은 답
        f"일반 예금은 1억원까지 보호됩니다 [1]. 퇴직연금 한도는 {REFUSAL_TEXT}",
        f"일반 예금은 1억원까지 보호됩니다 [1].\n\n**{REFUSAL_TEXT}**",
        f"시행 전에 접수한 콜백은 종전 기준으로 처리합니다 [1]. {REFUSAL_TEXT}",
        # 망설이다가 답한 것
        "제공된 자료로는 정확히 판단하기 어렵습니다. 다만 [1]에 따르면 대출 실행일 전후 "
        "1개월 이내의 권유는 금지됩니다.",
        "제공된 자료로는 본부 승인 여부를 확인할 수 있습니다. 본부에 승인을 요청합니다 [1].",
        # 평범한 답, 인용 없는 답
        "보호 한도는 1억원입니다 [1].",
        "네, 가능합니다.",
        "혼인관계증명서를 내야 합니다 [1].",
    ],
)
def test_answers_and_partial_answers_are_none(reply):
    assert classify_refusal(reply) is RefusalKind.NONE


def test_citation_only_reply_keeps_the_existing_rule():
    # 인용 표시만 있는 답은 글자·숫자가 없어 기존 규칙대로 EXACT(내용 없음)다.
    # 인용 표시에 글자가 한 자라도 붙으면 답이다
    assert classify_refusal("[1]") is RefusalKind.EXACT
    assert classify_refusal("[1] 참조") is RefusalKind.NONE
    assert classify_refusal("[1]에 따릅니다.") is RefusalKind.NONE


# --- fixture: 실제 모델 출력 150개 ---


def confusion(rows: list[dict]) -> dict[str, int]:
    counts = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    for row in rows:
        predicted = classify_refusal(row["answer"]) is not RefusalKind.NONE
        actual = row["label"] == "refusal"
        counts[("t" if predicted == actual else "f") + ("p" if predicted else "n")] += 1
    return counts


def test_fixture_has_150_labeled_outputs():
    rows = load_fixture()
    assert len(rows) == 150
    assert {row["run"] for row in rows} == TUNING_RUNS | VALIDATION_RUNS
    assert {row["label"] for row in rows} == {"refusal", "answer", "partial"}
    assert all(set(row) == {"run", "id", "qtype", "answer", "label", "ambiguous"} for row in rows)


@pytest.mark.parametrize(
    "runs",
    [TUNING_RUNS, VALIDATION_RUNS, TUNING_RUNS | VALIDATION_RUNS],
    ids=["tuning", "validation", "all"],
)
def test_fixture_precision_is_at_least_098(runs):
    # 거절로 판정한 것 중 라벨이 refusal인 비율. 검증 묶음만 따로 재도 지켜야 한다.
    # 재현율은 단언하지 않는다(정밀도 우선). 값은 docs/experiments/2026-11-11-refusal-detection.md
    counts = confusion([row for row in load_fixture() if row["run"] in runs])
    assert counts["tp"] > 0
    assert counts["tp"] / (counts["tp"] + counts["fp"]) >= 0.98


def test_fixture_exact_matches_the_stored_flag():
    # 보고서가 고정 문구로 바꿔 둔 출력(기존 규칙의 거절)은 모두 EXACT이고, 그 밖에는 EXACT가 없다
    for row in load_fixture():
        is_exact = classify_refusal(row["answer"]) is RefusalKind.EXACT
        assert is_exact == (row["answer"] == REFUSAL_TEXT), (row["run"], row["id"])
