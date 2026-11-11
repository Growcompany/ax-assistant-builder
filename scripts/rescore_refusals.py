"""저장된 평가 보고서의 거절 판정을 새 규칙으로 다시 계산해 전후 표를 낸다 (모델 재실행 없음).

사용: python scripts/rescore_refusals.py reports/bank/20261110-0119-hybrid-2.json [보고서.json ...]

전(strict) = 정해진 문구(EXACT)만 거절로 센다. v0.2.0까지의 규칙이다.
후        = 문구를 바꿔 말한 거절(PARAPHRASE)도 거절로 센다. classify_refusal의 판정이다.

보고서 항목의 `answer`로 판정한다. `answer`는 그때의 규칙으로 정규화한 텍스트라서, 정해진 문구로
거절한 답은 고정 문구로 바뀌어 있고(→ EXACT) 그 밖의 답은 인용 번호만 다시 매긴 모델 원문이다.
판정은 인용 표시를 지우고 하므로 번호가 달라도 결과는 같아야 한다. 보고서에 모델 원문
(`raw_answer` 또는 `llm.text`)이 있으면 그것으로도 판정해 두 결과가 다른 문항을 알려 준다.

지표도 두 가지로 낸다. 기존 지표는 "범위 밖 문항이면 거절이 정답"이고, 근거 기준 지표는
"검색 상위 k에 라벨된 근거가 하나도 없으면 거절이 정답"이다(문항의 recall_at_k > 0으로 정한다).
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from axbuilder.eval.metrics import grounded_refusal_correct, refusal_correct
from axbuilder.generation.refusal import RefusalKind, classify_refusal

OUT_OF_SCOPE = "out_of_scope"


def _raw_text(item: dict[str, Any]) -> str | None:
    if item.get("raw_answer") is not None:
        return item["raw_answer"]
    llm = item.get("llm")
    return llm.get("text") if isinstance(llm, dict) else None


def rescore(report: dict[str, Any]) -> dict[str, Any]:
    """보고서 하나를 다시 채점한다. 답변이 없는 항목(검색만 한 보고서)은 건너뛴다."""
    rows = []
    for item in report["items"]:
        if "answer" not in item:
            continue
        kind = classify_refusal(item["answer"])
        raw = _raw_text(item)
        rows.append(
            {
                "id": item["id"],
                "qtype": item["qtype"],
                "kind": kind,
                "before": kind is RefusalKind.EXACT,
                "after": kind is not RefusalKind.NONE,
                # 보고서를 만들 때의 판정과 strict 재계산이 같은가
                "stored_agrees": item.get("refused") == (kind is RefusalKind.EXACT),
                # 정규화 전 원문으로 판정해도 같은가 (원문이 없으면 None)
                "raw_agrees": None if raw is None else classify_refusal(raw) is kind,
                # 검색이 근거를 하나도 못 가져온 문항인가 (범위 밖 문항은 근거가 없어 None)
                "retrieval_miss": item.get("recall_at_k") == 0,
                # 상위 k에 라벨된 근거가 하나라도 있는가 (eval.run._record와 같은 규칙)
                "evidence_in_context": bool(item.get("recall_at_k")),
            }
        )
    return {"rows": rows, "config": report.get("config", {})}


def _accuracy(rows: list[dict[str, Any]], key: str) -> float:
    return sum(refusal_correct(r[key], r["qtype"]) for r in rows) / len(rows)


def _ids(rows: list[dict[str, Any]]) -> str:
    return ", ".join(r["id"].rsplit("-", 1)[-1] for r in rows) or "-"


def format_rescore(name: str, result: dict[str, Any]) -> str:
    rows = result["rows"]
    if not rows:
        return f"### {name}\n\n답변이 있는 항목이 없다(검색만 한 보고서)."
    lines = [
        f"### {name}",
        "",
        "| 구분 | n | 전(strict) | 후 | 뒤집힌 문항 |",
        "|---|---|---|---|---|",
    ]
    qtypes = list(dict.fromkeys(r["qtype"] for r in rows))
    for label, group in [("전체", rows)] + [
        (q, [r for r in rows if r["qtype"] == q]) for q in qtypes
    ]:
        flipped = [r for r in group if r["before"] != r["after"]]
        lines.append(
            f"| {label} | {len(group)} | {_accuracy(group, 'before'):.3f} "
            f"| {_accuracy(group, 'after'):.3f} | {_ids(flipped)} |"
        )

    oos = [r for r in rows if r["qtype"] == OUT_OF_SCOPE]
    answerable = [r for r in rows if r["qtype"] != OUT_OF_SCOPE]
    lines += ["", "| 항목 | 전(strict) | 후 |", "|---|---|---|"]
    lines.append(
        f"| 범위 밖 문항을 거절 | {sum(r['before'] for r in oos)}/{len(oos)} "
        f"| {sum(r['after'] for r in oos)}/{len(oos)} |"
    )
    for label, miss in [
        ("검색이 근거를 가져왔는데 거절", False),
        ("검색이 근거를 못 가져와 거절", True),
    ]:
        group = [r for r in answerable if r["retrieval_miss"] == miss]
        before = [r for r in group if r["before"]]
        after = [r for r in group if r["after"]]
        lines.append(
            f"| 답해야 하는 문항을 거절: {label} | {len(before)}/{len(group)} ({_ids(before)}) "
            f"| {len(after)}/{len(group)} ({_ids(after)}) |"
        )

    # 판정(strict / 새 판정)과 지표 정의(범위 밖 기준 / 근거 기준)를 따로 바꿔 본다
    lines += ["", "| 판정 | 지표 | 거절 정확도 | 오답 |", "|---|---|---|---|"]
    for verdict, key, metric, grounded in [
        ("strict (EXACT만)", "before", "기존: 범위 밖이면 거절", False),
        ("새 판정", "after", "기존: 범위 밖이면 거절", False),
        ("새 판정", "after", "근거 기준: 상위 k에 근거가 없으면 거절", True),
    ]:
        wrong = [
            r
            for r in rows
            if not (
                grounded_refusal_correct(r[key], r["evidence_in_context"])
                if grounded
                else refusal_correct(r[key], r["qtype"])
            )
        ]
        lines.append(
            f"| {verdict} | {metric} | {1 - len(wrong) / len(rows):.3f} "
            f"| {len(wrong)} ({_ids(wrong)}) |"
        )
    lines.append("")
    for label, in_context, refused in [
        ("근거가 있는데 거절(over_refusals)", True, True),
        ("근거가 없어서 거절(grounded_refusals)", False, True),
        ("근거가 없는데 답함(ungrounded_answers)", False, False),
    ]:
        group = [
            r for r in rows if r["evidence_in_context"] == in_context and r["after"] == refused
        ]
        lines.append(f"- 새 판정에서 {label}: {len(group)} ({_ids(group)})")

    kinds = {kind: [r for r in rows if r["kind"] is kind] for kind in RefusalKind}
    lines += [
        "",
        f"- 판정: EXACT {len(kinds[RefusalKind.EXACT])}, "
        f"PARAPHRASE {len(kinds[RefusalKind.PARAPHRASE])} ({_ids(kinds[RefusalKind.PARAPHRASE])}), "
        f"NONE {len(kinds[RefusalKind.NONE])}",
    ]
    stored_differs = [r for r in rows if not r["stored_agrees"]]
    lines.append(
        f"- 보고서의 `refused`와 strict 재계산이 다른 문항: {len(stored_differs)}"
        + (f" ({_ids(stored_differs)})" if stored_differs else "")
    )
    with_raw = [r for r in rows if r["raw_agrees"] is not None]
    raw_differs = [r for r in with_raw if not r["raw_agrees"]]
    lines.append(
        f"- `answer`와 모델 원문의 판정이 다른 문항: {len(raw_differs)}/{len(with_raw)}"
        + (f" ({_ids(raw_differs)})" if raw_differs else "")
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # Windows 콘솔에서 한글이 깨지지 않게
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("reports", nargs="+", type=Path, help="평가 보고서 JSON (--with-llm 실행)")
    args = parser.parse_args(argv)
    sections = []
    for path in args.reports:
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"cannot read {path}: {e}", file=sys.stderr)
            return 1
        sections.append(format_rescore(path.stem, rescore(report)))
    print("\n\n".join(sections))
    return 0


if __name__ == "__main__":
    sys.exit(main())
