"""
src/analysis/pipeline.py

Analysis pipeline: loads the CSVs produced by the SOTIF pipeline and runs
the individual research-question (RQ) analyzers.

Usage:
    from analysis.pipeline import AnalysisPipeline

    pipe = AnalysisPipeline(csv_root_dir="/path/to/csv")
    results = pipe.run_rq1(output_dir="analysis_outputs/rq1")
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any, Optional

from analysis.rq1_hazard_effectiveness import RQ1HazardEffectivenessAnalyzer, RQ1Result
from analysis.rq2_diversity import RQ3DiversityUMAPKMeansAnalyzer, RQ3Part1Result
from analysis.rq2_coverage_entropy import RQ3CoverageEntropyAnalyzer, RQ3Part2Result
from analysis.rq2_event_percentage import RQ3EventPercentagesAnalyzer, RQ3Part3Result
from analysis.rq4_driving_style_non_collision import RQ4DrivingStyleNonCollisionAnalyzer, RQ4Result
from analysis.rq_efficiency_time_to_hazard import EfficiencyTimeToHazardAnalyzer, EfficiencyTTFResult

@dataclass
class PipelineResults:
    rq1: Optional[RQ1Result] = None
    rq3_part1: Optional[RQ3Part1Result] = None
    # rq3: ...
    # rq4: ...


class AnalysisPipeline:
    """
    Orchestrates the research-question analyses.
    Takes as input a directory containing the final CSVs for each tool
    (e.g. *_SOTIF_Final.csv / *_sotif_hazard_leaderboard.csv / etc.)
    and dispatches to the individual analyzer classes.
    """

    def __init__(self, csv_root_dir: str):
        self.csv_root_dir = Path(csv_root_dir).resolve()
        if not self.csv_root_dir.exists():
            raise FileNotFoundError(f"CSV directory not found: {self.csv_root_dir}")

    def run_rq1(self, output_dir: str) -> RQ1Result:
        out = Path(output_dir).resolve()
        out.mkdir(parents=True, exist_ok=True)

        analyzer = RQ1HazardEffectivenessAnalyzer(csv_root_dir=self.csv_root_dir)
        return analyzer.run(output_dir=out)
    
    def run_rq3_part1(self, output_dir: str, level: str = "scenarios"):
        out = Path(output_dir).resolve()
        out.mkdir(parents=True, exist_ok=True)

        analyzer = RQ3DiversityUMAPKMeansAnalyzer(
            csv_root_dir=self.csv_root_dir,
            level=level,
            k_min=2,
            k_max=12,
            force_k = 6
        )
        return analyzer.run(output_dir=out)
    
    def run_rq3_part2(self, output_dir: str, level: str = "scenarios", min_count_per_cluster: int = 1) -> RQ3Part2Result:
        out = Path(output_dir).resolve()
        out.mkdir(parents=True, exist_ok=True)

        analyzer = RQ3CoverageEntropyAnalyzer(
            csv_root_dir=out,
            level=level,
            min_count_per_cluster=min_count_per_cluster,
        )
        return analyzer.run(output_dir=out)
    
    def run_rq3_part3(self, output_dir: str, leaderboard_files: list[str]) -> RQ3Part3Result:
        out = Path(output_dir).resolve()
        out.mkdir(parents=True, exist_ok=True)

        analyzer = RQ3EventPercentagesAnalyzer(leaderboard_files=leaderboard_files)
        return analyzer.run(output_dir=str(out))
    
    def run_rq4_driving_style(self, output_dir: str, leaderboard_files: list[str]) -> RQ4Result:
        out = Path(output_dir).resolve()
        out.mkdir(parents=True, exist_ok=True)

        analyzer = RQ4DrivingStyleNonCollisionAnalyzer(
            leaderboard_files=leaderboard_files,
            driving_hazards=["lane_invasion", "off_road", "red_light", "stop_sign"],  # curated whitelist
            p_threshold=0.0
        )
        return analyzer.run(output_dir=str(out))
    
    def run_efficiency_time_to_hazard(self, output_dir: str, logs_dir: str) -> EfficiencyTTFResult:
        out = Path(output_dir).resolve()
        out.mkdir(parents=True, exist_ok=True)

        analyzer = EfficiencyTimeToHazardAnalyzer(
            logs_dir=logs_dir,
            output_dir=str(out),
            timeout_s=60.0
        )
        return analyzer.run()

    def run_all(self, output_base_dir: str = "analysis_outputs") -> PipelineResults:
        base = Path(output_base_dir).resolve()
        base.mkdir(parents=True, exist_ok=True)

        res = PipelineResults()
        res.rq1 = self.run_rq1(output_dir=str(base / "rq1"))
        return res