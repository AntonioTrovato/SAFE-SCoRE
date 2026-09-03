# Lessons learned: running Scenic scenarios against Autoware in CARLA

Everything here was paid for in debugging time. Most of these failures produce
the *same* visible symptom - the ego sits still while RViz shows localization
green, routing green and a trajectory drawn - so the fixes are worth knowing in
advance.

If you are just trying to run the tool, you do not need this file. Read it when
something breaks, or before changing how the environment is managed.

---

## 1. The clock is the first thing to check

**One tick must advance exactly one frame and one timestep.**

```python
import carla
w = carla.Client('127.0.0.1', 2000).get_world()
a = w.get_snapshot().timestamp
w.tick()
b = w.get_snapshot().timestamp
print('frame +', b.frame - a.frame, ' time +', round(b.elapsed_seconds - a.elapsed_seconds, 4))
```

Expect `frame + 1  time + 0.05`. Anything else and nothing downstream can work:
Autoware gets incoherent sensor timing, never publishes a usable trajectory,
never becomes engageable, and the ego never moves.

Three failure signatures actually observed:

| Output | Meaning |
|---|---|
| `frame + 1  time + 0.006` | The world is **not really synchronous**. Autoware has not finished starting (it applies the settings itself), or something dropped the world into async mode. |
| `frame + 42280  time + 605.5` | **Free-running with a backlog**, flushed on the first tick. |
| `frame + 9501  time + 35.06` | **A queue, not a broken clock.** Ticks were submitted faster than the server retired them - drain it and it settles. |

The last one matters: during Autoware's startup both the runner's tick pump and
the bridge's own startup ticks queue work faster than the server drains it. The
health check therefore ticks repeatedly until two consecutive ticks are clean,
rather than judging on the first.

**A misleading non-check:** sampling `get_snapshot().frame` *without* ticking
returns a cached value, so a free-running world looks frozen. Only tick deltas
are trustworthy.

---

## 2. "AUTO is greyed out" has at least four different causes

They are indistinguishable from RViz. Always read the reason Autoware prints:

```bash
d=$(ls -td ~/.ros/log/*/ | head -1)
grep -a "not available for the following reasons" -A 10 "$d"launch.log | tail -12
```

Only entries marked **ERROR** block engagement. `WARN` and `STALE` do not.

| Blocking entry | Cause | Fix |
|---|---|---|
| `system/duplicated_node_checker` | Stale DDS registrations from hard-killed Autoware instances | `wsl --shutdown` |
| `planning/topic_rate_check/trajectory` (+ `trajectory_validation`) | No usable trajectory - usually a broken clock, sometimes a dead planner | Check the clock first |
| `control/topic_rate_check/*` | Downstream of the same missing trajectory | As above |
| Engage refused with "target mode is not available" while everything looks green | Engaged before planning produced a trajectory | Poll `is_autonomous_mode_available` first (the runner does) |

---

## 3. Hard-killing Autoware poisons the next run

Killed nodes stay advertised in CycloneDDS. The registrations accumulate across
restarts: after a day of `pkill`, we measured **290 node registrations for 187
unique names**, each duplicate appearing four times. Autoware's
`duplicated_node_checker` then reports ERROR and **blocks autonomous mode
permanently** - while localization and routing still show green.

Our `config/cyclonedds.xml` makes it worse: `<ParticipantIndex>none</ParticipantIndex>`
is a *Jazzy* workaround, and on Humble it lets dead participants linger.

- Shut Autoware down with `Ctrl+C` (or SIGINT) and **wait** for it.
- Check with `ros2 node list | sort | uniq -d` - it must be empty.
- If it is not, `wsl --shutdown` is the only reliable cure. It also resets the
  per-boot kernel settings and can change the WSL IP.

---

## 4. Never restart Autoware against a live CARLA

Autoware's startup calls `client.load_world(<map>)`. **Reloading the map CARLA
is already running is the known UE4 access violation** - the same engine bug
this repo already documents for repeated map reloads. Restarting Autoware alone
therefore reliably kills CARLA.

Always restart **both**, CARLA first, so it comes up on its default map and
Autoware's load is a genuine map change. There is deliberately no
Autoware-only restart path in the runner.

The converse also holds: a CARLA crash strands Autoware, whose client retries a
dead connection forever without raising. It looks alive and is useless.

---

## 5. `CarlaUE4.exe` is only a launcher

It spawns `CarlaUE4-Win64-Shipping.exe` and exits immediately. Consequences:

- `Ctrl+C` in its terminal does nothing - the real server is a different
  process. Use `Stop-Process` / `taskkill`.
- A `Popen` handle to it is already dead and says nothing about the server.
- **Killing it and immediately launching a replacement leaves two servers bound
  to port 2000.** Autoware then talks to one and the runner to the other, and
  nothing works while everything looks alive. Always wait for zero
  `CarlaUE4-Win64` processes before starting a new one, and verify only one
  came up.

Diagnostic: `netstat -an | Select-String ":2000 "` - two `LISTENING` lines means
two servers.

---

## 6. Autoware must not tick when Scenic does

CARLA's synchronous mode advances only when a client says so. Two tick masters
double-step frames and desync sensor data, silently corrupting TTC, TET and the
dynamics metrics. The bridge is stopped from ticking via `CARLA_EXTERNAL_TICK=1`
(a one-line patch in `carla_autoware.py`).

But that creates a gap: **Autoware cannot finish its own startup unaided.** It
needs ticks to spawn its ego and set up sensors. Symptom: the bridge starts,
prints nothing at all, and no ego ever appears. The runner therefore pumps ticks
from the moment it takes ownership until Scenic's loop takes over.

---

## 7. Autoware is a real-time system; do not outrun it

Its perception, planning and control cost real wall-clock time no matter what
the simulation clock says. Left unthrottled, Scenic ticked at **2.1x real
time** and the ego crawled 0.8 m in a 20-second scenario, because control
commands arrived far too late to matter. Autoware's own bridge paces itself the
same way (`max_real_delta_seconds`); Scenic has no such throttle, so the runner
paces each tick to `timestep`.

Practical consequence: an Autoware-mode run cannot be faster than real time.
Budget ~30-45 s of wall time per 20 s scenario.

---

## 8. Autoware's speed limit is a ceiling, not a setting

`max_vel` in `common.param.yaml` **clamps** any runtime velocity limit you
publish. Autoware ships 4.17 m/s (15 km/h) while Scenic suites sample the ego at
6-11 m/s and run NPCs at 6 m/s - so out of the box the ego is permanently the
slowest vehicle on the road and can never complete the overtakes the scenarios
are built around.

Raise `max_vel` once to a ceiling (e.g. 11.11 = 40 km/h) and let the runner
publish the actual per-scenario speed to
`/planning/scenario_planning/max_velocity_default`. Because the goal budget is
computed from the same number, the goal and the driving stay consistent.

---

## 9. Teleporting the ego destabilises Autoware

Each run repositions the ego by publishing to `/initialpose`, which is what
physically moves the CARLA actor. Two consequences:

- **Localization refuses a moving vehicle.** The bridge drops the car from 2 m
  up (`position.z += 2.0`), so straight after a teleport it is in free fall and
  `initialize` fails with "The vehicle is not stopped." Wait for it to settle -
  and wait on *Autoware's* reported speed, not CARLA's, because Autoware's
  estimate lags behind and that is the value the service actually checks.
- **`pose_instability_detector` aborts after a few teleports**, and
  `mission_planner` segfaults a few minutes later. Observed three times in the
  same order. Once it happens, `set_route_points` stops answering (it times out
  rather than refusing) and no further run can start. In practice Autoware
  survives roughly 3-4 runs per launch.

---

## 10. Town05 tolerates at most 4 cameras

In CARLA 0.9.15, Town05 dies within a couple of ticks with **five or more RGB
cameras**, independent of resolution - four at 1600x900 are fine, five at
640x360 are not. `carla_sensor_kit` enables six, so `enabled_sensors` in the
bridge's `config/sensor_mapping.yaml` must be trimmed.

Consequence for results: with cameras reduced, **traffic-light recognition is
degraded**, so `red_light` hazard counts in Autoware mode should not be trusted
without checking. Town01 runs all six at full resolution.

Separately, CARLA on Town05 with Autoware attached is simply unstable -
observed uptimes ranged from 80 s to 53 minutes, median around 9 minutes. Long
suites need the automatic recovery.

---

## 11. A monkeypatch only protects its own context

Scenic's `CarlaSimulator.destroy()` sets `synchronous_mode = False`. The
ownership patches disable that - but `sim.destroy()` runs in the worker's
`finally` block, *outside* the patch's scope, so the original ran and left the
world free-running. The first run of a suite worked (its constructor re-applied
sync) and **every run after it failed while looking perfectly healthy.**

Anything in a `finally` that runs after a context manager exits sees the
original behaviour. The runner now restores the settings explicitly.

---

## 12. Measurement traps that produced false conclusions

Recorded because each one cost real time:

- **`pgrep -f <pattern>` matches the shell running it.** `bash -lic "pgrep -c
  -f component_container"` returns `1` with Autoware completely shut down,
  because the shell's own command line contains the pattern. This first
  produced a false "two Autoware bridges are running" diagnosis, and later - in
  the shutdown wait loop - meant the count could never reach zero, so Autoware
  was force-killed on *every* restart and left the stale DDS state of §3 behind.
  Bracket the first character (`'[c]omponent_container'`) so the pattern cannot
  match itself, or filter on the executable name. The same trap applies to
  PowerShell: a `Where-Object { $_.CommandLine -like '*foo*' }` filter matches
  the very process evaluating it - exclude `$PID`.
- **`get_snapshot().frame` without ticking is cached** (see §1).
- **A degraded environment invalidates every experiment run against it.** After
  any CARLA crash or Autoware node death, rebuild a clean environment before
  drawing conclusions - several A/B tests had to be discarded because the stack
  was already broken when they ran.
- **Piped output buffers.** `cmd | grep | tail` writes nothing until the process
  ends, so a long run looks dead. Redirect to a file and tail it.

---

## 13. Automate process management last, not first

Every one of these was self-inflicted by recovery code written before the
failure modes were understood:

| Cause | Effect |
|---|---|
| `clear_route` called on every goal attempt | Segfaulted `mission_planner_container` |
| Restarting Autoware against a live CARLA | Killed CARLA (same-map reload) |
| Restarts leaving a second Autoware alive | Two tick masters; AUTO permanently grey |
| A leftover runner process exiting | Shut down the CARLA and Autoware it had launched, tearing down a healthy environment |
| Relaunching CARLA before the old one died | Two servers on port 2000 |

Establish a working baseline with process management **off** (no `--carla_exe`,
no `--autoware_map_path`), confirm real runs, and only then enable automation -
so that when something breaks you know whether it is the integration or the
orchestration.

---
## 14. The CARLA client aborts the process; nothing can catch it

The single most expensive lesson here. `libcarla` responds to a stale world
handle - one obtained before the map was reloaded - by calling `abort()`. The
process dies on the spot: no Python exception, no traceback, no Windows crash
event, and no `try/except` in the stack ever runs.

The consequence was a pipeline that vanished mid-recovery for hours without
explanation, leaving CARLA in synchronous mode with nobody to tick it - which
looked exactly like "CARLA froze" and sent the investigation in the wrong
direction repeatedly.

**Rules that follow:**

- **Anything holding a CARLA connection runs in its own process.** Not a
  thread - a thread's abort kills its parent. Here: each scenario run, the
  Autoware-startup tick pump, the clock health check, and the spectator camera.
- **Drop CARLA handles across a map reload.** The camera is stopped before every
  restart and restarted once the environment is healthy again.
- **A child's death is recoverable information**; the parent reads the exit code
  and retries. A parent's death is the end of the suite.

## 15. `faulthandler` is the only way to see a native abort

Three separate wrong explanations were offered before this was enabled -
including blaming the user's Ctrl-C, which was wrong. None of the usual evidence
existed: no traceback, no Windows Application Error event, no non-zero exit code
recorded anywhere.

One line in `run_experiment.py`:

```python
if __name__ == "__main__":
    faulthandler.enable()
```

turned a silent disappearance into a stack that named the guilty thread and file
on the first reproduction:

```
Fatal Python error: Aborted
Thread 0x00008f74 (most recent call first):
  File "src/runner/follow_camera.py", line 124 in _worker
```

Enable it before theorising about any process that dies without a traceback.

## 16. Confirm which process actually died before blaming a component

"CARLA is frozen" was, every time, "the runner died and CARLA is waiting for a
tick". Two cheap checks settle it in seconds and should come before any other
diagnosis:

```powershell
# is the orchestrator even alive?
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*run_experiment*' }
```

```python
# is CARLA actually hung, or just un-ticked?
c = carla.Client('127.0.0.1', 2000); c.set_timeout(20.0)
print(c.get_server_version())     # answers instantly if healthy
```

A CARLA that answers is not the problem, however frozen its window looks.
