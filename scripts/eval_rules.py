#!/usr/bin/env python3
"""
Rule layer evaluation script.
Reads a CSV of (phrase, expected: open_notes or none) and reports:
accuracy, false positives, and false negatives.
"""

import argparse
import csv
import os
from pathlib import Path
import sys

# Auto-reexec inside the virtual environment if available
PROJECT_ROOT = Path(__file__).resolve().parent.parent
venv_python = PROJECT_ROOT / ".venv" / "bin" / "python"
if venv_python.is_file() and sys.executable != str(venv_python):
    try:
        os.execv(str(venv_python), [str(venv_python)] + sys.argv)
    except Exception:
        pass

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from engine.commands import match_command, normalize_command_text


def evaluate_rules(csv_path: Path) -> int:
    """Evaluate rule matching accuracy on CSV dataset."""
    if not csv_path.is_file():
        print(f"Error: CSV file not found at: {csv_path}")
        return 1

    rows = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        for i, row in enumerate(reader):
            if not row or not row[0].strip():
                continue
            # Skip header if present
            if i == 0 and row[0].strip().lower() == "phrase" and len(row) > 1 and row[1].strip().lower() == "expected":
                continue
            phrase = row[0].strip()
            expected = row[1].strip().lower() if len(row) > 1 else "none"
            if expected not in ("open_notes", "take_note", "none"):
                expected = "none" if expected in ("", "0", "false") else expected
            rows.append((phrase, expected))

    if not rows:
        print(f"Error: No valid evaluation rows found in {csv_path}")
        return 1

    total = len(rows)
    correct = 0
    false_positives = []
    false_negatives = []
    results = []

    for phrase, expected in rows:
        match = match_command(phrase)
        predicted = match.action_id if match else "none"
        is_correct = (predicted == expected)

        if is_correct:
            correct += 1
        else:
            if predicted != "none" and expected == "none":
                false_positives.append((phrase, predicted, expected))
            elif predicted == "none" and expected != "none":
                false_negatives.append((phrase, predicted, expected))
            else:
                false_positives.append((phrase, predicted, expected))

        results.append({
            "phrase": phrase,
            "normalized": normalize_command_text(phrase),
            "expected": expected,
            "predicted": predicted,
            "correct": is_correct,
        })

    accuracy = (correct / total) * 100.0

    print("\n" + "=" * 90)
    print(f"RULE MATCHING EVALUATION: {csv_path.name} ({total} phrases)")
    print("=" * 90)
    print(f"| {'Status':<6} | {'Phrase':<40} | {'Expected':<12} | {'Predicted':<12} |")
    print("|" + "-" * 88 + "|")

    for r in results:
        status_sym = "✅ PASS" if r["correct"] else "❌ FAIL"
        disp_phrase = r["phrase"] if len(r["phrase"]) <= 40 else (r["phrase"][:37] + "...")
        print(f"| {status_sym:<6} | {disp_phrase:<40} | {r['expected']:<12} | {r['predicted']:<12} |")

    print("\n" + "=" * 60)
    print("EVALUATION SUMMARY")
    print("=" * 60)
    print(f"Total Phrases:    {total}")
    print(f"Correct:          {correct}/{total}")
    print(f"Accuracy:         {accuracy:.2f}%")
    print(f"False Positives:  {len(false_positives)} (triggered unexpected action)")
    print(f"False Negatives:  {len(false_negatives)} (valid command missed)")
    print("=" * 60)

    if false_positives:
        print("\n⚠️  FALSE POSITIVES DETAIL:")
        for phrase, pred, exp in false_positives:
            print(f"  • Phrase: \"{phrase}\" -> Predicted: {pred} (Expected: {exp})")

    if false_negatives:
        print("\n⚠️  FALSE NEGATIVES DETAIL:")
        for phrase, pred, exp in false_negatives:
            print(f"  • Phrase: \"{phrase}\" -> Predicted: {pred} (Expected: {exp})")

    print()
    return 0 if (len(false_positives) == 0 and len(false_negatives) == 0) else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate rule matching accuracy against test phrases.")
    parser.add_argument(
        "--csv",
        type=Path,
        default=PROJECT_ROOT / "tests" / "data" / "eval_rules.csv",
        help="Path to CSV file with (phrase, expected). Default: tests/data/eval_rules.csv",
    )
    args = parser.parse_args()
    sys.exit(evaluate_rules(args.csv))


if __name__ == "__main__":
    main()
