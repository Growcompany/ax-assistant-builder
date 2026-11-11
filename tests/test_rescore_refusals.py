"""scripts/rescore_refusals.py: 저장된 보고서의 거절 판정을 다시 계산한다 (모델 재실행 없음)."""

import importlib.util
import json
from pathlib import Path

from axbuilder.generation.answer import REFUSAL_TEXT

REPO_ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "rescore_refusals", REPO_ROOT / "scripts" / "rescore_refusals.py"
)
rescore_refusals = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rescore_refusals)

PARAPHRASED = "제공된 자료로는 달러 환율을 확인할 수 없습니다."


def item(
    item_id: str, qtype: str, answer: str, refused: bool, recall: float | None, raw: str | None
) -> dict:
    return {
        "id": item_id,
        "qtype": qtype,
        "answer": answer,
        "refused": refused,
        "recall_at_k": recall,
        "llm": None if raw is None else {"text": raw},
    }


# v0.2.0 형식의 보고서: refusal_kind·raw_answer가 없고, 정해진 문구로 거절한 답은 고정 문구다
REPORT = {
    "config": {},
    "items": [
        item(
            "bank-001", "lookup", "한도는 1억원입니다 [1].", False, 1.0, "한도는 1억원입니다 [2]."
        ),
        # 근거를 가져왔는데 정해진 문구로 거절 (원문에는 덧붙인 말이 있었다)
        item("bank-002", "lookup", REFUSAL_TEXT, True, 1.0, f"{REFUSAL_TEXT} (참고: [1])"),
        # 검색이 근거를 못 가져왔고 문구를 바꿔 거절
        item("bank-003", "lookup", PARAPHRASED, False, 0.0, PARAPHRASED),
        item("bank-045", "out_of_scope", PARAPHRASED, False, None, PARAPHRASED),
        item("bank-048", "out_of_scope", REFUSAL_TEXT, True, None, REFUSAL_TEXT),
        {"id": "bank-099", "qtype": "lookup", "recall_at_k": 1.0},  # 답변이 없는 항목은 건너뛴다
    ],
}


def test_rescore_before_is_the_stored_rule_and_after_adds_paraphrases():
    rows = {r["id"]: r for r in rescore_refusals.rescore(REPORT)["rows"]}
    assert set(rows) == {"bank-001", "bank-002", "bank-003", "bank-045", "bank-048"}
    assert [rows[i]["before"] for i in sorted(rows)] == [False, True, False, False, True]
    assert [rows[i]["after"] for i in sorted(rows)] == [False, True, True, True, True]
    # 전(strict)은 보고서에 저장된 refused와 같고, 원문으로 판정해도 결과가 같다
    assert all(r["stored_agrees"] and r["raw_agrees"] for r in rows.values())
    assert rows["bank-003"]["retrieval_miss"] is True
    assert rows["bank-045"]["retrieval_miss"] is False


def test_format_rescore_table():
    text = rescore_refusals.format_rescore("demo", rescore_refusals.rescore(REPORT))
    lines = text.splitlines()
    assert lines[0] == "### demo"
    # 전: 001·003·048 맞음 = 3/5. 후: 045가 맞게 되고 003은 틀리게 된다 = 3/5
    assert "| 전체 | 5 | 0.600 | 0.600 | 003, 045 |" in lines
    assert "| lookup | 3 | 0.667 | 0.333 | 003 |" in lines
    assert "| out_of_scope | 2 | 0.500 | 1.000 | 045 |" in lines
    assert "| 범위 밖 문항을 거절 | 1/2 | 2/2 |" in lines
    assert (
        "| 답해야 하는 문항을 거절: 검색이 근거를 가져왔는데 거절 | 1/2 (002) | 1/2 (002) |"
    ) in lines
    assert (
        "| 답해야 하는 문항을 거절: 검색이 근거를 못 가져와 거절 | 0/1 (-) | 1/1 (003) |"
    ) in lines
    assert "- 판정: EXACT 2, PARAPHRASE 2 (003, 045), NONE 1" in lines
    assert "- `answer`와 모델 원문의 판정이 다른 문항: 0/5" in lines


def test_rescore_flags_items_whose_raw_text_is_judged_differently():
    report = {
        "items": [item("bank-001", "lookup", "한도는 1억원입니다 [1].", False, 1.0, PARAPHRASED)]
    }
    result = rescore_refusals.rescore(report)
    assert result["rows"][0]["raw_agrees"] is False
    assert "판정이 다른 문항: 1/1 (001)" in rescore_refusals.format_rescore("x", result)


def test_raw_answer_field_is_preferred_over_llm_text():
    row = item("bank-001", "lookup", REFUSAL_TEXT, True, 1.0, "한도는 1억원입니다.")
    row["raw_answer"] = REFUSAL_TEXT
    assert rescore_refusals.rescore({"items": [row]})["rows"][0]["raw_agrees"] is True


def test_cli_prints_one_section_per_report(tmp_path, capsys):
    first, second = tmp_path / "a-hybrid.json", tmp_path / "b-hybrid.json"
    for path in (first, second):
        path.write_text(json.dumps(REPORT, ensure_ascii=False), encoding="utf-8")
    assert rescore_refusals.main([str(first), str(second)]) == 0
    out = capsys.readouterr().out
    assert out.count("| 전체 | 5 | 0.600 | 0.600 | 003, 045 |") == 2
    assert "### a-hybrid" in out and "### b-hybrid" in out


def test_cli_retrieval_only_report_and_missing_file(tmp_path, capsys):
    path = tmp_path / "bm25.json"
    path.write_text(json.dumps({"items": [{"id": "bank-001", "qtype": "lookup"}]}), "utf-8")
    assert rescore_refusals.main([str(path)]) == 0
    assert "답변이 있는 항목이 없다" in capsys.readouterr().out
    assert rescore_refusals.main([str(tmp_path / "none.json")]) == 1
    assert "cannot read" in capsys.readouterr().err
