# Known instabilities: CARLA and Autoware

Every failure mode below was observed and measured on 2026-09-02 while running
Scenic suites against Autoware on CARLA 0.9.15 / Town05. Each entry states what
happens, the evidence, and what (if anything) can be done.

**Scope note.** These are failures of *CARLA and Autoware themselves*. Bugs in
SAFE-SCoRE's own integration - and the diagnostic traps that hid them - are in
`LESSONS_LEARNED.md`.

**Summary of the day:** 33 CARLA crash dumps, and Autoware node deaths in 11 of
14 launches.

---

# Part 1 - CARLA

## 1.1 Baseline instability on Town05 with Autoware attached

CARLA crashes on its own, without provocation, at highly variable intervals.

**Evidence** - uptime at crash, from `CrashContext.runtime-xml` in each dump
(`%LOCALAPPDATA%\CarlaUE4\Saved\Crashes`):

```
16:49:59  uptime= 319s     20:44:39  uptime= 241s
17:43:03  uptime= 548s     21:01:01  uptime= 172s
17:47:27  uptime=  80s     21:11:42  uptime= 169s
19:17:33  uptime= 668s     21:36:17  uptime= 140s
20:02:02  uptime= 651s     21:41:08  uptime=  98s
20:29:12  uptime= 158s     21:51:20  uptime=  94s
20:40:18  uptime= 156s     22:08:01  uptime=  89s
```

Earlier in the day, before heavy use: 3159 s and 2548 s. **33 crash dumps in
one working day.** Median uptime around 9 minutes early on, degrading to
roughly 2 minutes under sustained load.

Every dump carries the same signature:

```
Unhandled Exception: EXCEPTION_ACCESS_VIOLATION
```

**Cause:** unknown, inside the Unreal engine. The repo already documented an
access violation in the landscape renderer for repeated map reloads; this looks
like the same class of fault but occurs without reloading.

**Mitigation:** none available - only automatic restart and retry. This is the
single largest limit on unattended suite length.

## 1.2 Town05 dies with five or more RGB cameras

Deterministic and immediate, independent of resolution.

**Evidence** - each configuration tested on a freshly started server, ticking a
stationary ego for 100 ticks:

| Cameras | Resolution | Result |
|---|---|---|
| 0 | - | survives |
| 1, 2, 3 | 640x360 | survives |
| 4 | 640x360 | survives |
| **4** | **1600x900** | **survives** |
| **5** | **640x360** | **dies at tick ~2** |
| 6 | 640x360 and 1600x900 | dies at tick ~2 |

Four cameras at full 1600x900 survive while five at a quarter of that die, so
it is **camera count, not pixels or bandwidth**. Adding the 64-channel lidar to
four cameras still survives (2000 ticks / 100 simulated seconds).

Town01 runs the full six-camera kit at 1600x900 indefinitely, so it is specific
to Town05.

**Mitigation:** trim `enabled_sensors` in the bridge's
`config/sensor_mapping.yaml`. **Consequence for results:** with cameras
reduced, traffic-light recognition degrades, so `red_light` hazard counts in
Autoware mode must not be trusted without checking.

## 1.3 Reloading the map CARLA is already running

Instant fatal error.

**Evidence** - two runs on a fresh server:

| Starting map | `load_world()` | Result |
|---|---|---|
| `Town10HD_Opt` | `Town05` | **OK**, 3.3 s, 114 actors |
| `Town05` | `Town05` | **Fatal error** |

**Why it matters in practice:** Autoware's bridge calls
`client.load_world(<map>)` during startup. Restarting Autoware while CARLA is
already on that map therefore kills CARLA. There must never be an
Autoware-only restart: always restart CARLA first, so it comes up on its
default map and Autoware's load is a genuine change.

## 1.4 `CarlaUE4.exe` is a launcher, not the server

It spawns `CarlaUE4-Win64-Shipping.exe` and exits immediately.

**Consequences observed:**

- `Ctrl+C` in its terminal does nothing; the real server keeps running.
- A `Popen` handle to it is already dead and says nothing about the server.
- Killing it and immediately launching a replacement leaves **two servers bound
  to port 2000**.

**Evidence** - `netstat -an | Select-String ":2000 "` during a restart:

```
TCP    0.0.0.0:2000    0.0.0.0:0    LISTENING
TCP    0.0.0.0:2000    0.0.0.0:0    LISTENING      <- two servers
```

confirmed by `tasklist`:

```
CarlaUE4-Win64-Shipping.e    19108
CarlaUE4-Win64-Shipping.e    53532
```

With two servers, Autoware talks to one and the runner to the other. Nothing
works and everything looks alive.

**Mitigation:** wait for zero `CarlaUE4-Win64` processes before starting a new
one, and verify only one came up.

## 1.5 A dead CARLA hangs its clients instead of erroring

The client library retries a dead connection indefinitely without raising, so
Python code blocks forever rather than failing.

**Evidence:**

```
streaming client: connection failed: Impossibile stabilire la connessione...
```
repeated indefinitely, while the process never returns.

**Mitigation:** run each execution in its own OS process with a wall-clock cap
and force-kill it. In the runner's log this appears as:

```
worker unresponsive past its wall-clock cap (likely stuck in a native call,
e.g. a dead CARLA connection retry loop) - killing it
```

---

## 1.6 The CARLA client aborts the process; it does not raise

The most damaging failure mode in this whole environment, because nothing in
Python can defend against it. Using a stale world handle - one obtained before
Autoware reloaded the map - makes `libcarla` call `abort()`. The interpreter
dies immediately: no exception, no traceback, no `try/except` anywhere in the
call stack gets a chance to run, and Windows records no Application Error
event.

**Evidence** (`faulthandler.enable()` is the only thing that reveals it):

```
Fatal Python error: Aborted

Thread 0x00008f74 (most recent call first):
  File "src/runner/follow_camera.py", line 124 in _worker
  File "threading.py", line 953 in run
```

The runner's main thread was elsewhere entirely - blocked in
`multiprocessing.Process.join()` waiting for Autoware to start - so a single
background thread took the entire pipeline down.

**Mitigation:** every component that holds a CARLA connection runs in its own
process. In this repository that is each scenario run, the Autoware-startup
tick pump, the clock health check, and the spectator camera. The camera is
additionally stopped before each restart and started again afterwards, so it
never holds a handle across a map reload.

**Diagnostic value:** if a Python process managing CARLA disappears with no
traceback and no crash event, this is the first thing to suspect. Enable
`faulthandler` and reproduce.

---

## 1.7 A "frozen" CARLA is usually a CARLA nobody is ticking

A recurring false diagnosis. In synchronous mode the simulation advances only
when a client ticks. If the tick master dies, the window stops repainting and
Windows reports `Responding=False`, so CARLA looks hung or crashed. It is
neither - the server is perfectly healthy and answers immediately.

**Evidence:** probing a CARLA that appeared frozen, with a dead runner:

```
server version: 0.9.15  (responded in 0.0s)
map: Carla/Maps/Town05
sync: True   delta: 0.05
actors: 0
frames advanced in 2s without ticking: 0
```

Instant response, correct map, synchronous mode on. `actors: 0` shows Autoware's
bridge had connected but never spawned its ego - it cannot finish its own
startup without ticks (see 2.6).

High CPU does not contradict this; the process was burning ~1.7 cores while
"frozen".

**How to tell the two apart:** connect a fresh client with a short timeout and
ask for the server version. If it answers, CARLA is fine and the problem is
whoever should be ticking it.

---

# Part 2 - Autoware

## 2.1 Teleport-induced cascade: `pose_instability_detector` then `mission_planner`

The most consistent Autoware failure. Two nodes die in the same order, minutes
apart.

**Evidence** - node deaths per launch session (`~/.ros/log/*/launch.log`),
across today's sessions:

```
22:19  autoware_pose_instability_detector_node-40  exit=-6   (SIGABRT)
       component_container_mt-58                   exit=-11  (SIGSEGV)
22:12  autoware_pose_instability_detector_node-40  exit=-6
       component_container_mt-58                   exit=-11
22:03  autoware_pose_instability_detector_node-40  exit=-6
       component_container_mt-58                   exit=-11
21:59  autoware_pose_instability_detector_node-40  exit=-6
       component_container_mt-64                   exit=-11
```

The pattern repeated in at least four separate sessions. `container_mt-58`
hosts `mission_planner` and `route_handler`.

**Timing:** the detector aborts first; the container follows roughly 3 minutes
later. In one measured case, 1788372925 -> 1788373098, i.e. 173 s apart.

**Likely cause (correlation, not proven):** every run repositions the ego by
publishing to `/initialpose`, which teleports the CARLA actor. A node whose
whole purpose is detecting sudden pose jumps aborting after repeated teleports
is a strong association, but a causal link has not been demonstrated.

**Symptom once the container is gone:** `set_route_points` stops answering -
it **times out rather than refusing**, which is the distinguishing signature:

```
set_route_points rejected the goal: SERVICE_TIMEOUT: timed out after 40.0s
```

A refused goal is normal and worth retrying; no answer at all means the node is
gone and only a restart helps.

**Practical limit:** Autoware survives roughly **3-4 runs per launch** before
routing dies.

## 2.2 `motion_planning_container` segfault

Independent of the above, and the one that silently produces stationary egos.

**Evidence:**

```
[ERROR] [component_container_mt-65]: process has died [pid 74439, exit code -11,
        cmd '.../motion_planning_container ...']
```

**Symptom:** routes are still accepted and modes still reported, but
`/planning/scenario_planning/lane_driving/trajectory` publishes nothing, so
autonomous mode never becomes available and the ego never moves. Tracing the
chain shows exactly where it breaks:

```
behavior_planning/path_with_lane_id   10.6 Hz  OK
behavior_planning/path                10.3 Hz  OK
lane_driving/trajectory               -- NOTHING --
scenario_planning/trajectory          -- NOTHING --
```

**Detection:** the runner checks whether a trajectory is being published at all
before blaming anything else, and says so explicitly rather than leaving it to
be guessed.

## 2.3 The bridge exits when CARLA dies

**Evidence:**

```
[ERROR] [autoware_carla_interface-1]: process has died [pid 7490, exit code 1]
```

Seen in four sessions, always following a CARLA crash. Autoware's remaining
nodes stay alive, so the stack *looks* healthy while being unable to drive
anything.

**Consequence:** a health check based on node registration alone returns a
false positive. Any recovery must restart both sides.

## 2.4 Camera republish nodes crash together

**Evidence** - one session lost nine nodes at once:

```
republish-5 .. republish-11   exit=-11  (SIGSEGV, seven nodes)
multi_camera_combiner-12      exit=1
autoware_carla_interface-1    exit=1
```

Consistent with the camera-related fragility in §1.2, on the Autoware side.

## 2.5 Stale DDS registrations block autonomous mode permanently

Not a crash - an accumulation, and the most misleading failure of the day.

**Evidence** - after a day of hard kills:

```
ros2 node list | wc -l          -> 290
ros2 node list | sort -u | wc -l -> 187
```

Every duplicated name appeared exactly **four times** - one live instance plus
three killed ones still advertised. Autoware's own diagnostic then blocks
autonomous mode:

```
- /autoware/modes/autonomous ERROR
    - /autoware/localization WARN          <- not blocking
    - /autoware/perception  WARN           <- not blocking
    - /autoware/system ERROR
        - /autoware/system/duplicated_node_checker ERROR   <- blocking
```

**Symptom:** localization green, routing green, trajectory drawn, and "AUTO"
greyed out. Indistinguishable from a genuine planning failure.

**Cause:** hard-killed nodes stay advertised in CycloneDDS. Our
`config/cyclonedds.xml` uses `<ParticipantIndex>none</ParticipantIndex>` - a
*Jazzy* workaround - which on Humble lets dead participants linger.

**Cure:** `wsl --shutdown`. Verified: 290/187 before, **186/186 after**, and
`autonomous_available` went from `False` to `True` immediately.

**Prevention:** shut Autoware down with SIGINT and wait, rather than `pkill`.

## 2.6 The bridge cannot start itself when external ticking is enabled

By design, but it looks like a hang.

**Evidence:** with `CARLA_EXTERNAL_TICK=1`, the bridge starts, produces **no
output at all**, and no ego ever appears. Ticking the world externally releases
it immediately:

```
before: frame 0      vehicles 0
tick 0: frame 5751   vehicles 1   roles=['ego_vehicle']
```

**Cause:** the bridge needs ticks to spawn its ego and set up sensors, and it
has been told not to tick.

**Mitigation:** pump ticks from the moment the runner takes ownership until
Scenic's loop takes over.

## 2.7 Startup hang inside `load_world`

Occasional, cause not established.

**Evidence:** the bridge connected to CARLA (`ESTAB` on port 2000, socket held
by the bridge PID) and stayed there for minutes without loading the map:

```
[INFO] [CARLA Interface] Map Path: '.../Town05' -> Loading CARLA Map: 'Town05'
[INFO] [autoware_carla_interface-1]: process started with pid [609]
    (no further output)
```

The map remained `Town10HD_Opt` after 3 minutes of external ticking. Restarting
Autoware cleared it.

## 2.8 Autoware cannot be outrun

Not a crash, but it silently invalidates results.

**Evidence:** unthrottled, Scenic ticked at **2.1x real time** and the ego moved
**0.8 m in a 20-second scenario**, peaking at 0.66 m/s. Paced to real time, the
same scenario produced 40-69 m at up to 5.7 m/s.

**Cause:** Autoware's perception, planning and control cost real wall-clock time
regardless of the simulation clock, so control commands arrive far too late.
Its own bridge paces itself with `max_real_delta_seconds`; Scenic has no such
throttle.

**Consequence:** an Autoware-mode run can never be faster than real time.

## 2.9 `max_vel` clamps runtime velocity limits

**Evidence:** with `max_vel: 4.17` and a published limit of 6.0 m/s, the ego
peaked at **4.50-4.96 m/s**. After raising the ceiling to 11.11 and publishing
6.0, it reached **5.61-5.73 m/s**.

**Consequence:** the configured value is a ceiling, not a default. Scenic suites
sample the ego at 6-11 m/s and run NPCs at 6 m/s, so at the stock 4.17 the ego
is permanently the slowest vehicle on the road and cannot complete the
overtakes the scenarios are built around.

---

# Part 3 - Open, not explained

Recorded honestly: these were investigated and **not** resolved.

## 3.1 `common21_1` never produces a trajectory

Localization initialises, the route is accepted, then no trajectory is ever
published - no green path in RViz, "AUTO" greyed out. It failed **all 5
attempts**, across five full environment restarts, and the scenario was
discarded. `common20_1` and `common30_1` complete normally on the same
environment.

Three hypotheses were measured and **eliminated**:

| Hypothesis | Measurement | Result |
|---|---|---|
| Ego spawned where there is no lanelet | Parsed `lanelet2_map.osm`, point-in-polygon over 486 lanelets | All spawns **inside** lanelets |
| Ego spawned where the point cloud is missing | Loaded all 29,689,848 points, counted within 15 m | Failing 79k-131k vs working 100k-143k - **comparable** |
| Ego spawned on a non-road lanelet subtype | Read every lanelet's subtype | **All 486 are `road`**; no other subtype exists in this map |

One unverified observation: for a failing case the start `(99.5, 28.4)` and the
goal `(49.3, 88.5)` both resolved to lanelet **7622** despite being 94 m apart.
This may indicate a degenerate route, or may be an artefact of the
point-in-polygon test (which returns the first bounding-box match and can be
wrong where lanelets overlap). **Not confirmed.**

The decisive measurement, not yet run: trace
`path_with_lane_id -> path -> lane_driving/trajectory -> scenario_planning/trajectory`
at the moment of failure, to identify which component refuses.

Caveat on the evidence: attempts 3-5 overlapped with concurrent manual testing
against the same environment, so those attempts are not clean.

## 3.2 Why Town05 specifically

Neither the five-camera limit nor the baseline crash rate is understood. Town01
does not exhibit either. No hypothesis has been tested.
