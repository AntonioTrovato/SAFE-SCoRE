import logging
import subprocess
from pathlib import Path


class SOTIFPipeline:
    """
    Full SOTIF-like pipeline (multi-dataset):
    For each folder under base_dir/outputs:
    0. Critical/functional/dynamics metrics enrichment (TTC, MDBV, TET, etc.)
    1. Sanity check of the base logs
    2. ODD computation (descriptive)
    3. Hazard computation (Leaderboard-based)
    4. Final report generation
    5. ODD/Triggering-condition coverage and entropy
    """

    def __init__(self, base_dir: Path):
        self.base_dir = base_dir

        # outputs directory (the dynamic source of input data, and where results are written)
        self.outputs_dir = base_dir / "outputs"

        # existing enrichment/analysis scripts
        self.step0_orchestrator_script = base_dir / "src" / "data_gathering" / "enriching" / "orchestrator.py"
        self.stepA_odd_script = base_dir / "src" / "data_gathering" / "enriching" / "compute_sotif_odd.py"
        self.stepB_hazard_script = base_dir / "src" / "data_gathering" / "enriching" / "compute_sotif_hazard.py"
        self.stepC_report_script = base_dir / "src" / "data_gathering" / "enriching" / "compute_sotif.py"
        self.stepB2_feature_script = base_dir / "src" / "data_gathering" / "enriching" / "compute_feature_vectors.py"
        self.stepD_coverage_script = base_dir / "src" / "analysis" / "odd_tc_coverage.py"

        self._setup_logger()

    # --------------------------------------------------
    # Logger
    # --------------------------------------------------
    def _setup_logger(self):
        logging.basicConfig(
            level=logging.INFO,
            format="[%(levelname)s] %(message)s"
        )
        self.logger = logging.getLogger("SOTIFPipeline")

    # --------------------------------------------------
    # Utility to run a script (with a --dataset_dir argument)
    # --------------------------------------------------
    def _run_script(self, script: Path, step_name: str, dataset_dir: Path):
        if not script.exists():
            raise FileNotFoundError(f"{step_name} script not found: {script}")

        self.logger.info(f"▶ Starting {step_name} | dataset: {dataset_dir.name}")
        result = subprocess.run(
            ["python", str(script), "--dataset_dir", str(dataset_dir)],
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            self.logger.error(f"✖ Error in {step_name} | dataset: {dataset_dir.name}")
            if result.stderr:
                self.logger.error(result.stderr)
            raise RuntimeError(f"{step_name} failed for {dataset_dir.name}")

        self.logger.info(f"✔ {step_name} completed | dataset: {dataset_dir.name}")
        if result.stdout:
            self.logger.debug(result.stdout)

    # --------------------------------------------------
    # Dataset discovery
    # --------------------------------------------------
    def list_dataset_folders(self):
        if not self.outputs_dir.exists():
            raise FileNotFoundError(f"outputs folder not found: {self.outputs_dir}")

        folders = [p for p in self.outputs_dir.iterdir() if p.is_dir()]
        if not folders:
            raise RuntimeError(f"No subfolders found in {self.outputs_dir}")

        self.logger.info(f"Found {len(folders)} dataset folders in: {self.outputs_dir}")
        return sorted(folders)

    # --------------------------------------------------
    # STEP 0 - Sanity check logs (per dataset)
    # --------------------------------------------------
    def check_logs(self, dataset_dir: Path):
        self.logger.info(f"Checking for base logs in: {dataset_dir}")

        # if the logs sit directly inside the dataset folder:
        logs = list(dataset_dir.glob("*_log_basic.json"))

        # if you also want to search subfolders, use instead:
        # logs = list(dataset_dir.rglob("*_log_basic.json"))

        if not logs:
            raise RuntimeError(f"No base logs found in {dataset_dir}. Pipeline aborted for this dataset.")

        self.logger.info(f"Found {len(logs)} base logs in {dataset_dir.name}.")
        return logs

    # --------------------------------------------------
    # STEP 0.5 - Critical/functional/dynamics metrics (TTC, MDBV, TET, etc.)
    # --------------------------------------------------
    def compute_enriched_metrics(self, dataset_dir: Path):
        step_name = "STEP 0.5 - Critical/Functional/Dynamics metrics"
        if not self.step0_orchestrator_script.exists():
            raise FileNotFoundError(f"{step_name} script not found: {self.step0_orchestrator_script}")

        self.logger.info(f"▶ Starting {step_name} | dataset: {dataset_dir.name}")
        result = subprocess.run(
            [
                "python", str(self.step0_orchestrator_script),
                "--input_dir", str(dataset_dir),
                "--output_dir", str(dataset_dir),
            ],
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            self.logger.error(f"✖ Error in {step_name} | dataset: {dataset_dir.name}")
            if result.stderr:
                self.logger.error(result.stderr)
            raise RuntimeError(f"{step_name} failed for {dataset_dir.name}")

        self.logger.info(f"✔ {step_name} completed | dataset: {dataset_dir.name}")
        if result.stdout:
            self.logger.debug(result.stdout)

    # --------------------------------------------------
    # STEP 1 - Enrichment + ODD
    # --------------------------------------------------
    def compute_odd(self, dataset_dir: Path):
        self._run_script(
            self.stepA_odd_script,
            "STEP A - ODD analysis (descriptive)",
            dataset_dir
        )

    # --------------------------------------------------
    # STEP 2 - Hazard + probability + severity
    # --------------------------------------------------
    def compute_hazard(self, dataset_dir: Path):
        self._run_script(
            self.stepB_hazard_script,
            "STEP B - Hazard & Severity (Leaderboard)",
            dataset_dir
        )

    # --------------------------------------------------
    # STEP 3 - Feature vectors for UMAP and K-Means
    # --------------------------------------------------
    def compute_feature_vectors(self, dataset_dir: Path):
        self._run_script(
            self.stepB2_feature_script,
            "STEP B2 - Feature vector extraction (RQ3)",
            dataset_dir
        )

    # --------------------------------------------------
    # STEP 4 - Final SOTIF report
    # --------------------------------------------------
    def compute_final_report(self, dataset_dir: Path):
        self._run_script(
            self.stepC_report_script,
            "STEP C - Final SOTIF report",
            dataset_dir
        )

    # --------------------------------------------------
    # STEP 5 - ODD and Triggering-Condition Coverage/Entropy
    # --------------------------------------------------
    def compute_odd_tc_coverage(self, dataset_dir: Path):
        self._run_script(
            self.stepD_coverage_script,
            "STEP D - ODD & Triggering-Condition Coverage/Entropy",
            dataset_dir
        )

    # --------------------------------------------------
    # Full pipeline (for all datasets)
    # --------------------------------------------------
    def run(self):
        self.logger.info("===== STARTING SOTIF PIPELINE (MULTI-DATASET) =====")

        dataset_folders = self.list_dataset_folders()

        for dataset_dir in dataset_folders:
            self.logger.info(f"\n===== DATASET: {dataset_dir.name} =====")
            self.check_logs(dataset_dir)
            self.compute_enriched_metrics(dataset_dir)
            self.compute_odd(dataset_dir)
            self.compute_hazard(dataset_dir)
            self.compute_final_report(dataset_dir)
            self.compute_odd_tc_coverage(dataset_dir)

        self.logger.info("\n===== SOTIF PIPELINE COMPLETE =====")
        self.logger.info(f"Final outputs available in: {self.outputs_dir}")
