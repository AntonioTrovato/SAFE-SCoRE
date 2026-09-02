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
├── converter/     OpenSCENARIO (.xosc) → Scenic conversion, used by runner/ for non-.scenic input
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

# you chose a folder to clone Scenic in
cd ../path/to/Scenic/parent/folder 

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
cd path/to/SAFE-SCoRE
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
- `--max_wall_seconds` (default `300`): real-world (wall-clock) cap per execution - see "CARLA server management" below.
- `--carla_exe`, `--carla_launch_args`, `--carla_boot_timeout`: let the pipeline launch and, if it crashes, restart the CARLA server itself, and shut it down automatically when the pipeline finishes - see below.
- `--skip_enrichment`: only execute the scenarios, without running Stage 1 afterwards (e.g. to inspect the raw base logs first).

Instead of starting CARLA yourself, you can point `--carla_exe` at `CarlaUE4.exe` and let the pipeline launch (and, if needed, relaunch) it for you:

```bash
python -m src.runner.run_experiment \
  --input_dir scenic_example/example_suite \
  --output_folder example_suite_output \
  --num_runs 10 \
  --max_wall_seconds 60 \
  --carla_exe "C:\path\to\your\CARLA\CarlaUE4.exe" \
  --carla_launch_args="-RenderOffScreen -quality-level=Low" \
  --carla_boot_timeout 120
```

### CARLA server management and crash recovery

Two independent problems can otherwise stall or silently derail a long batch of runs, so the runner guards against both:

- **A scenario can stall without ever finishing** (e.g. a pile-up that leaves vehicles unable to move). CARLA's own `maxSteps` cap only bounds *simulated* time, not wall-clock time - if ticks themselves start taking far longer than real time, it never fires. `--max_wall_seconds` is a genuine wall-clock backstop, checked once per simulated step, that aborts and saves whatever was recorded so far instead of hanging.
- **CARLA itself can crash** (observed as a `CarlaUE4-Win64-Shipping.exe` "Fatal error!" access violation in the Unreal Engine landscape renderer, triggered by repeatedly reloading the same map across runs - an engine bug, not something this repo can fix directly). When that happens, CARLA's client library can get stuck endlessly retrying the dead connection without ever raising a Python exception, so no in-process check could reliably catch it. To guarantee recovery regardless, each execution runs in its own OS process: the parent waits up to `--max_wall_seconds` (plus a short grace period) and force-kills the process if it hasn't returned by then - bypassing any crash dialog instead of waiting for it to be dismissed by hand. If `--carla_exe` was given, the pipeline then checks whether the server is still reachable before the *next* run and restarts it if not, abandoning only the run that was in flight when the crash happened.

  Known limitation: right after a crash, CARLA's lightweight liveness probe can still report the server as reachable for a little while even though it's already unable to actually run a scenario, so the very next run can occasionally also stall and get killed before the restart is triggered on the run after that. Recovery still happens, just one run later than ideal in that case.

If `--carla_exe` was used to launch (or restart) the server, it is stopped automatically once the pipeline finishes - whether it completes normally or exits on an error. If CARLA was already running when you started (no `--carla_exe`), it is left untouched.

If you only want to (re-)run Stage 1 on datasets you already have (e.g. produced by an externally-integrated tool per `docs/integration.md`), you can run it standalone:

```bash
python -m src.pipeline.run_pipeline
```

This processes every dataset folder already present under `outputs/`.

---

# How to run SAFE-SCoRE

SAFE-SCoRE takes a folder of Scenic scenarios, runs each one *n* times, records
what happened, and computes SOTIF metrics over the results. There are two modes,
and they differ in **one thing only: who drives the ego car.**

| Mode | Who drives the ego | Needs |
|---|---|---|
| `behavior_agent` *(default)* | The `behavior` written in the `.scenic` file | CARLA |
| `autoware` | A real Autoware Universe stack | CARLA + Autoware + matching maps |

Everything else is identical - the same scenarios, the same log format, the same
metrics - which is what makes results from the two modes comparable.

---

## Mode 1: CARLA only (`behavior_agent`)

The simplest case. Start a CARLA server, then:

```bash
python -m src.runner.run_experiment \
  --input_dir scenic_example/common \
  --output_folder my_results \
  --num_runs 10
```

That runs every `.scenic` file under `--input_dir` ten times, writes one log per
run to `outputs/my_results/`, and then computes all the SOTIF metrics.

Let the tool manage CARLA for you, including restarting it if it crashes:

```bash
python -m src.runner.run_experiment \
  --input_dir scenic_example/common \
  --output_folder my_results \
  --num_runs 10 \
  --carla_exe "C:\path\to\CARLA_0.9.15\WindowsNoEditor\CarlaUE4.exe" \
  --carla_launch_args="-prefernvidia -quality-level=Low -RenderOffScreen"
```

Useful flags:

- `--num_runs` - executions per scenario (10 by default; SOTIF asks for repeated
  stochastic execution)
- `--max_wall_seconds` - real-time cap per run, so a gridlocked scenario cannot
  hang the suite
- `--skip_enrichment` - only run the scenarios, skip the metrics (useful when
  inspecting raw logs)

---

## Mode 2: CARLA + Autoware (`autoware`)

Here Autoware drives the ego and Scenic drives everything else. Same scenarios,
same outputs, but a real AD stack is now the thing under test.

### One-time setup

**1. Build the Autoware maps for your town.** Autoware needs a lanelet2 map and
a point-cloud map per town, and is bound to **one map per launch**.

**2. Stop Autoware ticking the simulation.** In
`.../autoware_carla_interface/src/autoware_carla_interface/carla_autoware.py`,
add `import os` and make its tick conditional:

```python
# was: if self.running:
if self.running and os.environ.get("CARLA_EXTERNAL_TICK") != "1":
    CarlaDataProvider.get_world().tick()
```

CARLA advances only when a client tells it to; if both Autoware and Scenic do
that, frames double-step and the recorded metrics are corrupted. One line, no
rebuild, and Autoware behaves normally when the variable is absent.

**3. Raise Autoware's speed ceiling.** In
`autoware_launch/config/planning/scenario_planning/common/common.param.yaml`,
`max_vel` ships at `4.17` m/s (15 km/h). That value **clamps** everything. Scenic
scenarios run their NPCs at ~6 m/s, so at 4.17 the ego is permanently the
slowest car on the road. Raise it to a ceiling (e.g. `11.11` = 40 km/h) - the
tool still sets the actual speed per scenario, below that ceiling.

**4. Trim the cameras if you use Town05.** Town05 crashes CARLA with five or
more cameras. Comment out all but one or two in the bridge's
`config/sensor_mapping.yaml` under `enabled_sensors`.

**5. Install `verifai`** if your scenarios use `VerifaiRange`:
`pip install verifai`.

### Running it: the tool manages everything

Give it the two process-management flags and it does the rest - starts CARLA,
starts Autoware, checks they are healthy, runs the scenarios, and restarts both
if either crashes:

```bash
python -m src.runner.run_experiment \
  --input_dir scenic_example/aw_focus \
  --output_folder aw_results \
  --num_runs 10 \
  --engine autoware \
  --carla_exe "C:\path\to\CARLA_0.9.15\WindowsNoEditor\CarlaUE4.exe" \
  --carla_launch_args="-prefernvidia -quality-level=Low -RenderOffScreen" \
  --autoware_map_path '$HOME/autoware/autoware_map/Town05'
```

### Watching a run

Add `--follow_camera` and CARLA's spectator camera chases the ego for the whole
suite, reconnecting by itself across restarts:

```bash
  --follow_camera behind      # or: top, front
```

It is a strict observer - it reads the ego's position and moves the camera,
never ticks the world - so it cannot disturb the simulation or the metrics.

**It needs a CARLA window**, so drop `-RenderOffScreen` from
`--carla_launch_args`. Rendering a window costs GPU: runs took ~46 s instead of
~30 s in one measured comparison. Leave it off for long unattended suites.

**Start it with CARLA and Autoware not already running** - the tool needs to own
both, and will stop any it finds when it first restarts.

What it does on its own:

1. Starts CARLA, waits for it, starts Autoware with `CARLA_EXTERNAL_TICK=1`, and
   ticks the world so Autoware can finish its own startup.
2. Runs a **pre-flight**: the simulation clock advances correctly, there are no
   stale ROS nodes, exactly one Autoware bridge is running. Nothing runs until
   this passes.
3. Runs each scenario *n* times, writing one log per run.
4. **On any crash** - CARLA or Autoware - stops **both**, restarts **both**,
   re-verifies, and retries the same run. Up to 5 attempts (`--max_run_attempts`),
   then it discards that scenario and moves on.
5. Prints a summary of what completed and what was discarded.

Both sides are always restarted together, whichever failed: a CARLA crash leaves
Autoware stranded (it retries a dead connection forever without erroring), and
restarting Autoware against a live CARLA makes it reload the map CARLA already
has - a known engine crash.

### Running it yourself

To keep control, omit `--carla_exe` and `--autoware_map_path` and start both by
hand. The tool then only connects and runs; it will not start, stop or restart
anything. A crash ends the suite and you restart manually.

Start CARLA, then in WSL:

```bash
cd ~/autoware
sudo ip link set lo multicast on
sudo sysctl -w net.core.rmem_max=2147483647
sudo sysctl -w net.ipv4.ipfrag_time=3
sudo sysctl -w net.ipv4.ipfrag_high_thresh=134217728
source install/setup.bash

CARLA_EXTERNAL_TICK=1 ros2 launch autoware_launch e2e_simulator.launch.xml \
  map_path:=$HOME/autoware/autoware_map/Town05 vehicle_model:=sample_vehicle \
  sensor_model:=carla_sensor_kit simulator_type:=carla
```

The `sudo` lines are lost on every WSL restart and must be repeated.

**No ego will appear and RViz will look inert - that is correct.** With external
ticking Autoware cannot finish starting until SAFE-SCoRE begins ticking.

To stop Autoware, press `Ctrl+C` and **wait** for it to finish. Killing it
abruptly leaves stale ROS registrations behind that eventually block autonomous
mode; if that happens, `wsl --shutdown` clears them.

---

## What happens during an Autoware run

1. Read the ego's target speed from the sampled scene (`EGO_SPEED` and similar),
   set Autoware's speed limit to match, and size the goal against the same
   number.
2. Move the ego to the scene's starting position and initialise localization.
3. Compute a destination and set it as the route.
4. Engage autonomous mode.
5. Run the scenario: Scenic spawns the NPCs and drives them, Autoware drives the
   ego, every frame is logged.
6. Reset, ready for the next run.

**NPCs only appear at step 5.** If you see the ego move to a new position and
take a route but no NPCs ever appear, the run failed at step 4.

### Where the destination comes from

A `.scenic` file has no destination, so one is derived per run: the ego follows
its lane, and at each junction picks one of the possible branches at random; the
goal is the furthest point it could plausibly reach within the scenario's time
limit, given the speed it will actually drive at. Because the scenario is
re-sampled every run, the start differs each time and the goal is recomputed
each time.

---

## Understanding the output

`outputs/<your_folder>/` contains one JSON log per run, plus the metric CSVs:

| File | Contains |
|---|---|
| `<scenario>_run_NN_log_basic.json` | Frame-by-frame ego and NPC state, plus recorded events |
| `SOTIF_Final.csv` | Per-scenario hazard rates, residual risk, average execution time |
| `sotif_hazard_leaderboard.csv` | Per-hazard breakdown and acceptability |
| `odd_scores.csv` | ODD scoring and triggering conditions |
| `odd_tc_coverage_*.csv` | ODD/TC coverage and entropy |

**All hazard metrics describe the ego only.** NPCs appear in each frame as
context (they are what time-to-collision is measured against) but never generate
violations of their own.

---

## When something goes wrong

The single most useful check, for any symptom in Autoware mode:

```python
import carla
w = carla.Client('127.0.0.1', 2000).get_world()
a = w.get_snapshot().timestamp
w.tick()
b = w.get_snapshot().timestamp
print('frame +', b.frame - a.frame, ' time +', round(b.elapsed_seconds - a.elapsed_seconds, 4))
```

It must print `frame + 1  time + 0.05`. If it does not, the simulation clock is
wrong and nothing else can work.

| Symptom | Likely cause |
|---|---|
| Ego frozen, "AUTO" greyed out, but localization and routing green | Stale ROS nodes, or a bad clock - see `docs/LESSONS_LEARNED.md` §1-2 |
| Autoware starts but no ego appears | Expected with `CARLA_EXTERNAL_TICK=1`; it needs SAFE-SCoRE to start ticking |
| Ego drives only a metre or two | Something else is ticking the world too |
| A scenario is skipped with a map message | Autoware is bound to a different map |
| CARLA "Fatal error!" every few minutes | Known instability on Town05 with sensors attached - let the tool restart it |

Two companion documents cover this properly:

- **`docs/KNOWN_INSTABILITIES.md`** - every observed CARLA and Autoware failure
  mode, with the logs and measurements that demonstrate each one, plus the
  problems that remain unexplained.
- **`docs/LESSONS_LEARNED.md`** - how to diagnose them, including the checks
  that give false answers.

### Known limitations

- Town05 supports at most 4 cameras in CARLA 0.9.15. With cameras reduced,
  traffic-light recognition degrades, so `red_light` counts in Autoware mode
  need checking before you trust them.
- CARLA on Town05 with Autoware attached is unstable (~9 minute median uptime),
  and Autoware itself degrades after roughly 3-4 runs. Both are handled by the
  automatic restart, at the cost of ~2 minutes per recovery.
- An Autoware run cannot be faster than real time: budget ~30-45 s of wall time
  per 20 s scenario.
- Autoware-mode runs carry a computed goal that `behavior_agent` runs do not.
  Worth stating when comparing the two.
