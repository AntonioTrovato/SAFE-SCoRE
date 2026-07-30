"""
merge_feature_vectors.py
RQ3: Automatic merge of scenario-level feature vectors.

Recursively searches outputs_dir for all files matching:
  *_feature_vectors_scenarios.csv

Concatenates them into a single CSV:
  rq3_merged_feature_vectors_scenarios.csv

Usage:
python merge_feature_vectors.py --outputs_dir outputs
"""

from __future__ import annotations
import argparse
from pathlib import Path
import pandas as pd


def infer_tool_name(path: Path) -> str:
    # try to derive it from the file name: <Tool>_feature_vectors_scenarios.csv
    name = path.name
    if name.endswith("_feature_vectors_scenarios.csv"):
        return name.replace("_feature_vectors_scenarios.csv", "")
    # fallback: parent folder name
    return path.parent.name


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outputs_dir", required=True, help="outputs/ folder (recursive)")
    ap.add_argument("--pattern", default="*_feature_vectors_scenarios.csv", help="File pattern to merge")
    ap.add_argument("--out_name", default="rq3_merged_feature_vectors_scenarios.csv", help="Merged output name")
    args = ap.parse_args()

    outputs_dir = Path(args.outputs_dir)
    if not outputs_dir.exists():
        raise FileNotFoundError(f"outputs_dir not found: {outputs_dir}")

    files = sorted(outputs_dir.rglob(args.pattern))
    if not files:
        raise RuntimeError(f"No files found matching pattern {args.pattern} inside {outputs_dir}")

    print(f"[INFO] Found {len(files)} scenario-level CSV files to merge:")
    for p in files:
        print(f" - {p}")
    print("", flush=True)

    dfs = []
    for p in files:
        tool = infer_tool_name(p)
        df = pd.read_csv(p)
        if "tool" not in df.columns or df["tool"].isna().all():
            df["tool"] = tool
        dfs.append(df)

    merged = pd.concat(dfs, ignore_index=True)

    out_path = outputs_dir / args.out_name
    merged.to_csv(out_path, index=False)

    print(f"[DONE] Merged file written: {out_path.resolve()}")
    print(f"[INFO] Rows: {len(merged)} | Columns: {len(merged.columns)}")
    print("[INFO] Count per tool:")
    print(merged["tool"].value_counts(dropna=False).to_string())


if __name__ == "__main__":
    main()
