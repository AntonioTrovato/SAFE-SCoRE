"""
run_analysis.py

Ad-hoc driver for the cross-tool research-question (RQ) analyses in
src/analysis/. This is a scratch/example script, not a stable CLI - most
analyses below are commented out; uncomment the ones you need and adjust
the paths to your own outputs/ layout.

Usage (from the repository root):
    python -m src.analysis.run_analysis
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
for _p in (REPO_ROOT, SRC_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from analysis.pipeline import AnalysisPipeline
from analysis.rq2_coverage_entropy import RQ3CoverageEntropyAnalyzer
from analysis.rq2_event_percentage import RQ3EventPercentagesAnalyzer
from analysis.rq4_driving_style_non_collision import RQ4DrivingStyleNonCollisionAnalyzer

if __name__ == "__main__":
    CSV_DIR = "./outputs"
    OUT_DIR = "./src/analysis/results"

    pipe = AnalysisPipeline(csv_root_dir=CSV_DIR)
    # results = pipe.run_all(output_base_dir=OUT_DIR)

    # print("Analysis complete.")
    # print("RQ1 report:", results.rq1.report_txt_path)
    # print("RQ1 summary:", results.rq1.summary_csv_path)
    # print("RQ1 HR boxplot:", results.rq1.hr_boxplot_path)
    # print("RQ1 R boxplot:", results.rq1.r_boxplot_path)
    # print("RQ1 heatmap:", results.rq1.hazard_heatmap_path)

    # rq3_part1_dir = "analysis_outputs/rq3_part1_scenarios"

    # Part 1: UMAP + KMeans over RUNS
    # rq3 = pipe.run_rq3_part1(output_dir=rq3_part1_dir, level="scenarios")
    # print("K*:", rq3.k_star)
    # print("Silhouette plot:", rq3.silhouette_plot_path)
    # print("UMAP hull plot:", rq3.umap_tool_hulls_plot_path)

    # Part 2: Coverage + Entropy over RUNS
    # an = RQ3CoverageEntropyAnalyzer(csv_root_dir=Path(rq3_part1_dir), level="scenarios", min_count_per_cluster=1)
    # res = an.run(output_dir=Path(rq3_part1_dir))

    # print("RQ2 - CSV metrics:", res.metrics_csv_path)
    # print("RQ2 - Coverage plot:", res.coverage_plot_path)
    # print("RQ2 - Entropy plot:", res.entropy_plot_path)

    # Part 3: Hazard percentage
    leaderboard_files = [
        "./outputs/ScenarioFuzzLLM/sotif_hazard_leaderboard.csv",
        "./outputs/SimADFuzz/sotif_hazard_leaderboard.csv",
        "./outputs/TMFuzz/sotif_hazard_leaderboard.csv",
    ]

    # an = RQ3EventPercentagesAnalyzer(leaderboard_files=leaderboard_files)
    # res = an.run(output_dir="analysis_outputs/rq3_part3")
    # print("RQ2 - CSV Percentages:", res.output_csv_path)

    an = RQ4DrivingStyleNonCollisionAnalyzer(
        leaderboard_files=leaderboard_files,
        driving_hazards=["lane_invasion", "off_road", "red_light", "stop_sign"],
        p_threshold=0.0
    )

    # rq4 = pipe.run_rq4_driving_style(output_dir="analysis_outputs/rq4", leaderboard_files=leaderboard_files)
    # print(rq4.summary_csv_path)
    # print(rq4.per_hazard_csv_path)
    # print(rq4.radar_plot_path)

    eff = pipe.run_efficiency_time_to_hazard(
        output_dir="analysis_outputs/efficiency",
        logs_dir="./outputs/"  # folder containing the *_log_basic.json files
    )

    print(eff.per_hazard_csv_path)
    print(eff.hit_rates_plot_path)
    print(eff.mean_ttf_plot_path)
    print(eff.boxplot_path)
