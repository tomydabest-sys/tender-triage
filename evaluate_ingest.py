#!/usr/bin/env python3
"""
evaluate_ingest.py — score document classification and requirement extraction
against hand-checked ground truth for the real Contracts Finder documents.

Two things are measured, because they fail differently:

  Classification — did it correctly decide whether the file is a question set?
    Calling a contract a question set is the expensive error: it fills the work
    queue with clauses nobody has to answer.

  Extraction — of the genuine requirements in the question sets, how many were
    found, and how many extracted items were not really requirements.

Usage:
    python evaluate_ingest.py real/docs real/ground_truth.json
"""

import json
import sys
from pathlib import Path

import ingest


def main():
    doc_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "real/docs")
    truth = json.loads(Path(sys.argv[2] if len(sys.argv) > 2
                            else "real/ground_truth.json").read_text())["documents"]

    # The real documents are not shipped with the repo (see README). Without
    # them every row reads MISSING FILE and the summary reports 0%, which looks
    # like a result and is not one.
    if not any((doc_dir / name).exists() for name in truth):
        print(f"No test documents found in {doc_dir}. Nothing to evaluate.")
        print("  The documents behind the published figures are not included in "
              "this repo; see README.md.")
        return 2

    print(f"{'document':<22} {'actual':<13} {'verdict':<13} {'true Q':>7} {'found':>6}   ")
    print("-" * 74)

    verdict_right = 0
    total_true = total_found = total_correct_docs = 0
    false_requirements = 0

    for name, expected in sorted(truth.items()):
        path = doc_dir / name
        if not path.exists():
            print(f"{name:<22} MISSING FILE")
            continue

        verdict, requirements, _ = ingest.extract(path)
        ok = verdict == expected["verdict"]
        verdict_right += ok

        true_q, found_q = expected["questions"], len(requirements)
        total_true += true_q
        total_found += found_q
        if true_q:
            total_correct_docs += min(found_q, true_q)
        else:
            false_requirements += found_q

        print(f"{name:<22} {expected['verdict']:<13} {verdict:<13} "
              f"{true_q:>7} {found_q:>6}   {'ok' if ok else 'MISCLASSIFIED'}")

    n = len(truth)
    print(f"\nClassification accuracy : {verdict_right}/{n} = {verdict_right/n:.0%}")
    print(f"Requirements present    : {total_true}")
    print(f"Requirements extracted  : {total_found}")
    print(f"Recall                  : {total_correct_docs}/{total_true} = "
          f"{total_correct_docs/total_true:.0%}" if total_true else "")
    print(f"Phantom requirements    : {false_requirements}"
          "   (items extracted from documents containing no questions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
