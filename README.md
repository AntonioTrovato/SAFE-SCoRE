# SOTIF-Compliant Framework for Scenario Generator Evaluation in CARLA

## Overview

This project implements a **SOTIF-aligned evaluation framework** for the empirical comparison of automatic scenario generation tools used in the validation of **Advanced Driver Assistance Systems (ADAS)** and **Automated Driving Systems (ADS)** in simulation.

The main goal is not to evaluate a single autonomous driving system in isolation, but to provide a **structured and reproducible methodology** for comparing different scenario generators according to criteria that are meaningful from a **safety-of-the-intended-functionality** perspective. In particular, the framework is designed to support the analysis of:

- the capability of a generator to produce **hazardous scenarios**;
- the coverage of the **Operational Design Domain (ODD)** and related **triggering conditions**;
- the **diversity** of the hazardous situations discovered;
- the overall **efficiency** of the generation-and-evaluation process.

The project is grounded in the principles of **ISO 21448:2022 (SOTIF)**, which focuses on hazardous behaviors caused not by faults in the system, but by functional insufficiencies or foreseeable limitations of the intended functionality.

## Motivation

Testing ADS/ADAS exclusively in the real world is not sufficient for a rigorous safety assessment. Rare but safety-critical events are difficult, expensive, and often unsafe to reproduce on public roads. For this reason, simulation environments such as **CARLA** play a central role in modern validation workflows.

However, manually crafting scenarios for simulation is time-consuming and difficult to scale. Automatic scenario generators address this limitation by producing test scenarios in a systematic way. The problem is that, without a standard-oriented evaluation methodology, it is hard to understand **which generator is actually more useful for safety validation**.

This project addresses that gap by proposing a framework that evaluates scenario generators through a SOTIF-oriented lens, combining **risk-based assessment**, **ODD analysis**, and **scenario diversity analysis**.

## Project Objective

The objective of the framework is twofold:

1. **Empirical comparison of scenario generation tools** in a common evaluation setting;
2. **Demonstration of a reusable SOTIF-compliant methodology** for assessing the quality of generated scenarios in simulation.

The framework is designed to be as **generator-agnostic** as possible: the same evaluation logic can be applied to scenarios produced by different tools, provided that they are executed in a uniform simulation pipeline and logged in a compatible format.

## What the framework evaluates

The framework supports the analysis of multiple complementary dimensions:

### 1. Hazard effectiveness
It measures how effective a scenario generator is at exposing potentially unsafe behavior, such as collisions or traffic-rule violations, by aggregating simulation outcomes across repeated executions.

### 2. ODD and Triggering Condition coverage
Each generated scenario is analyzed with respect to ODD-related dimensions such as environmental, infrastructural, traffic, and operational factors. In addition, the framework derives **triggering conditions** that may activate hazardous system behavior under challenging circumstances.

### 3. Hazardous scenario diversity
The framework also investigates whether a generator discovers genuinely different hazardous situations or simply repeats variations of the same failure pattern. To support this analysis, feature vectors can be extracted from enriched execution logs for downstream clustering and diversity assessment.

### 4. Final SOTIF-oriented reporting
The different analysis steps are aggregated into final outputs that support the interpretation of the generator’s safety relevance in a structured way.

## Current pipeline structure

The project is organized as a single, end-to-end pipeline that goes from a suite of [Scenic](https://scenic-lang.org/) (`.scenic`) scenario files to a full SOTIF report, in two stages:

**Stage 0 - Scenario execution (`src/runner/`).** Each `.scenic` scenario is executed on CARLA a configurable number of times (10 by default, as required by SOTIF's residual-risk-estimation guidance), producing one base-log JSON per run. The ego can either be driven by Scenic's own compiled driving behavior (default), or the runner can instead connect to a remote CARLA+Autoware service. See [Running the pipeline on a suite of scenarios](#running-the-pipeline-on-a-suite-of-scenarios) below.

**Stage 1 - SOTIF enrichment (`src/pipeline/`, `src/data_gathering/enriching/`, `src/analysis/`).** For every dataset folder under `outputs/` (whether produced by Stage 0 or provided independently by an externally-integrated tool, see `docs/integration.md`), the pipeline performs:

1. **Sanity check of base logs**
2. **Critical/functional/dynamics metrics** - time-to-collision (TTC), minimum distance before violation (MDBV), time-exposed-TTC (TET), route-completion/stability, and driving-dynamics metrics, computed per run from the raw frame-by-frame data and saved back into each log's `results.critical_metrics`/`functional_metrics`/`dynamics_metrics`.
3. **Descriptive ODD scoring and triggering-condition detection** - driven by the user-editable `config/sotif_odd_tc.yaml` (ODD factor taxonomy, value→score mapping, triggering-condition rules), rather than hardcoded assumptions, so the framework can be pointed at a different System Under Test's ODD without touching code.
4. **Hazard and residual-risk computation** - for each of 7 hazard categories (vehicle/pedestrian/static collisions, red-light running, stop-sign running, off-road, lane invasion), using the established CARLA-Leaderboard-style severity weights already validated in this project. A scenario is flagged **non-acceptable** when its residual risk exceeds an acceptance threshold (0.2 by default, configurable in `config/sotif_odd_tc.yaml`).
5. **Final SOTIF report generation** - per-scenario hazard rates, residual risk, **average execution time**, and route-completion rate.
6. **ODD/triggering-condition coverage and entropy** - how much of the declared ODD/TC taxonomy the suite exercises, and how evenly, computed both over the whole suite and restricted to non-acceptable scenarios only.

The pipeline is designed to process multiple datasets in a uniform way, making it suitable for comparing outputs produced by different scenario generation tools under the same evaluation workflow.

### A note on the metrics used

Execution-time measurement and the coverage/entropy computations are not new metrics bolted on as an afterthought: they are built on top of the same risk and behavioral metrics (time-to-collision, minimum distance before violation/MDBV, time-exposed-TTC, dynamics, etc.) this project already computed for its multi-generator comparative analyses (`src/analysis/`). Reusing them here - rather than introducing a parallel metric suite - keeps the stage-1 tool and the broader research pipeline consistent with each other.

## Conceptual workflow

In conceptual terms, the project follows this logic:

- scenario generators produce candidate driving scenarios;
- scenarios are executed in simulation;
- execution logs are collected in a common format;
- logs are enriched with ODD-related, hazard-related, and behavioral information;
- aggregated metrics are computed to support comparative analysis across generators.

This separation between **scenario generation**, **scenario execution**, and **post-execution SOTIF analysis** makes the framework modular and easier to extend.

## Why this repository matters

This repository is meant to serve as a practical implementation of a broader research effort on the standardized evaluation of scenario generators for autonomous driving validation. Rather than focusing only on raw failure discovery, the project aims to provide a more meaningful assessment based on:

- safety relevance,
- operational-context coverage,
- diversity of discovered hazards,
- reproducibility of the evaluation process.

In this sense, the repository is both a **research artifact** and a **reusable experimental pipeline** for future studies on scenario-based validation in CARLA.

## Project structure

All Python source lives under `src/`, organized by responsibility:

```
src/
├── runner/        Stage 0: executes .scenic scenarios on CARLA (run_experiment.py)
├── data_gathering/ Base logging (CarlaBasicLogger, ViolationMonitor) + enrichment scripts
│   └── enriching/  ODD/hazard/final-report computation, config-driven via config/sotif_odd_tc.yaml
├── pipeline/      Stage 1 orchestration (run_pipeline.py, SOTIFPipeline)
├── analysis/      Cross-tool research-question analyses (run_analysis.py) + ODD/TC coverage-entropy
└── utils/         Shared CARLA/JSON helpers
```

Non-code assets stay at the repository root: `config/` (the ODD/TC YAML), `outputs/` (base logs and produced CSVs), `docs/`, `scenic_example/` (sample scenario suites).

## Requirements

The project has been developed and tested using the following environment:

- **Python version:** `3.10` (required by the `scenic` scenario-execution dependency; earlier releases of this project targeted `3.7.16`, which cannot run Scenic 3.x)
- **Operating system:** Ubuntu 22.04 or Windows
- **CARLA:** `0.9.16`, already running/installed separately (not vendored in this repo)

Before running the pipelines, make sure that the correct Python version is available and that all required dependencies are installed.

### Create a virtual environment (recommended)

It is strongly recommended to run the project inside a virtual environment.

```bash
# on Ubuntu
python3.10 -m venv safe_score
source safe_score/bin/activate

# on Windows
py -3.10 -m venv safe_score
safe_score\Scripts\Activate
```

### Install Scenic

With the prompt still in the safe_score virtual environment and in with the prompt in the project root.

```bash
python -m pip install --upgrade pip
cd ../path/to/Scenic

# on Ubuntu
git clone https://github.com/BerkeleyLearnVerify/Scenic
cd Scenic
python -m pip install -e .

# on Windows
git clone https://github.com/BerkeleyLearnVerify/Scenic
cd Scenic
python -m pip install -e .
```

## Install project dependencies

Once the virtual environment is activated, install the required libraries using:

```bash
pip install -r requirements.txt
```

This installs, among others, the `carla` client package (make sure its version matches your CARLA server) and `PyYAML` (used to load `config/sotif_odd_tc.yaml`).

## Running the pipeline on a suite of scenarios

With a CARLA `0.9.16` server already running (locally, or the address of a remote CARLA+Autoware service), the whole pipeline - Stage 0 scenario execution followed by Stage 1 SOTIF enrichment - is a single command, run from the repository root:

```bash
python -m src.runner.run_experiment \
  --input_dir scenic_example/common \
  --output_folder scenic_demo \
  --num_runs 10
```

This executes every `.scenic` file found (recursively) under `--input_dir` 10 times each, writes the base logs to `outputs/scenic_demo/`, and then automatically runs the full SOTIF enrichment pipeline (critical/functional/dynamics metrics, ODD/TC scoring, hazard/residual-risk computation, final report, coverage/entropy) on the result. Everything for this run lands together in `outputs/scenic_demo/`: the per-run logs plus `SOTIF_Final.csv`, `sotif_hazard_leaderboard.csv`, `odd_scores.csv`, and `odd_tc_coverage_{all,non_acceptable}.csv`.

Useful flags:
- `--engine {behavior_agent,autoware}` (default `behavior_agent`): drive the ego with Scenic's own compiled behavior, or connect instead to a remote CARLA+Autoware service via `--address`/`--port`.
- `--skip_enrichment`: only execute the scenarios, without running Stage 1 afterwards (e.g. to inspect the raw base logs first).

If you only want to (re-)run Stage 1 on datasets you already have (e.g. produced by an externally-integrated tool per `docs/integration.md`), you can run it standalone:

```bash
python -m src.pipeline.run_pipeline
```

This processes every dataset folder already present under `outputs/`.
