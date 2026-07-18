# Entity Matching

## Goal

Build an app that matches two lists of company information to identify entity matches using open-source matching libraries.

## Core approach: stepwise waterfall matching

Apply matching algorithms sequentially, strictest first:

1. Run algorithm 1 across all records; remove matched pairs from the pool.
2. Run algorithm 2 on the remaining unmatched entities only.
3. Repeat with each subsequent algorithm until all algorithms have run.

Each stage only sees entities that earlier (more precise) stages failed to match, so cheaper/stricter methods handle easy cases and fuzzier methods handle the residuals.

## Data files

- `companies_file_a.csv` / `companies_file_b.csv` — the two company lists to match against each other (synthetic test data).
- `ground_truth_matches.csv` — known correct matches for evaluating precision/recall of each algorithm stage.
- `generate_test_data.py` — script that generated the test data.

## Notes

- Prefer open-source matching libraries (e.g., recordlinkage, splink, rapidfuzz, dedupe) over hand-rolled matching.
- Evaluate every stage against ground truth: report matches found, precision, and recall per algorithm, plus cumulative totals.
