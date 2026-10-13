#!/usr/bin/env python3
"""
evaluate.py — score the triage output against hand-written labels.

The point of this file: a reuse recommendation is only worth anything if its
precision is known. A tool that says 'reuse this answer' and is wrong a quarter
of the time costs the bid team more than it saves, because every suggestion then
has to be checked anyway.

Usage:
    python evaluate.py triage_output.json gold_labels.json
"""

import json
import sys
from pathlib import Path

ORDER = ["REUSE", "PARTIAL", "GAP"]


def main():
    pred_path = Path(sys.argv[1] if len(sys.argv) > 1 else "triage_output.json")
    gold_path = Path(sys.argv[2] if len(sys.argv) > 2 else "gold_labels.json")

    predicted = {r["ref"]: r for r in json.loads(pred_path.read_text())["requirements"]}
    gold = json.loads(gold_path.read_text())["labels"]

    matrix = {g: {p: 0 for p in ORDER} for g in ORDER}
    source_correct = source_total = 0
    errors = []

    for ref, truth in gold.items():
        pred = predicted.get(ref)
        if pred is None:
            errors.append((ref, truth["label"], "NOT EXTRACTED", None))
            continue
        matrix[truth["label"]][pred["status"]] += 1

        if truth["label"] == "REUSE" and pred["status"] == "REUSE":
            source_total += 1
            if pred["best_match_id"] == truth["source"]:
                source_correct += 1
            else:
                errors.append((ref, "right call, wrong source",
                               pred["best_match_id"], truth["source"]))
        elif truth["label"] != pred["status"]:
            errors.append((ref, truth["label"], pred["status"], pred["best_match_id"]))

    print("Confusion matrix (rows = manual label, columns = tool output)\n")
    print(f"{'':>10}" + "".join(f"{p:>10}" for p in ORDER))
    for g in ORDER:
        print(f"{g:>10}" + "".join(f"{matrix[g][p]:>10}" for p in ORDER))

    tp = matrix["REUSE"]["REUSE"]
    predicted_reuse = sum(matrix[g]["REUSE"] for g in ORDER)
    actual_reuse = sum(matrix["REUSE"][p] for p in ORDER)

    precision = tp / predicted_reuse if predicted_reuse else 0
    recall = tp / actual_reuse if actual_reuse else 0

    print(f"\nREUSE precision : {tp}/{predicted_reuse} = {precision:.0%}"
          "   (of the answers it said to reuse, how many genuinely were)")
    print(f"REUSE recall    : {tp}/{actual_reuse} = {recall:.0%}"
          "   (of the genuinely reusable answers, how many it found)")
    if source_total:
        print(f"Correct source  : {source_correct}/{source_total} = "
              f"{source_correct/source_total:.0%}   (right answer, not just right verdict)")

    # The expensive error: telling the bid team an answer exists when it does not.
    false_reuse = sum(matrix[g]["REUSE"] for g in ORDER if g != "REUSE")
    missed_gap = matrix["GAP"]["REUSE"]
    print(f"\nFalse REUSE calls      : {false_reuse}")
    print(f"  of which real gaps   : {missed_gap}  <- the ones that would ship unanswered")

    if errors:
        print("\nDisagreements:")
        for ref, truth, pred, extra in errors:
            tail = f" (expected {extra})" if extra else ""
            print(f"  {ref:<5} manual={truth:<24} tool={pred}{tail}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
