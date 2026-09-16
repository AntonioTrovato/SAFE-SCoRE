# Daily procedure — CARLA + Autoware

Starting and stopping the environment by hand, once it is installed.

> **You may not need this.** `python -m src.runner.run_experiment --engine autoware`
> starts CARLA and Autoware itself, checks them, and restarts them on a crash.
> This procedure is for driving the stack manually — debugging, demos, or
> exploring in RViz.

`[WIN]` = Windows PowerShell · `[WSL]` = Ubuntu terminal in WSL2

---

## What survives what

This table is why some steps below are conditional.

| | Closing terminals | WSL restart | Windows reboot | Re-cloning Autoware |
|---|---|---|---|---|
| `~/.bashrc` exports (CUDA, DDS, ccache) | survives | survives | survives | survives |
| `~/cyclonedds.xml`, maps | survives | survives | survives | survives |
| Autoware `build/` + `install/` | survives | survives | survives | **lost — full rebuild** |
| TensorRT engines (`~/autoware_data`) | survives | survives | survives | survives |
| Source patches (host IP, `/launch/` path, tick) | survives | survives | survives | **lost — reapply** |
| `sysctl` + `lo multicast` | **lost** | **lost** | **lost** | n/a |
| Windows host IP value | usually same | **can change** | **can change** | n/a |

**The WSL VM restarts when** you run `wsl --shutdown`, Windows reboots, **or you
close every Ubuntu terminal and leave it idle for a few minutes** — WSL2
auto-stops the VM. That last one catches people out: closing all terminals is
not harmless.

When in doubt, do the full cold start. Steps 3–5 are cheap and safe to repeat.

---

## Cold start

### 1. Check for leftovers `[WIN]`

```powershell
Get-Process CarlaUE4* -ErrorAction SilentlyContinue
```

Anything listed must be stopped first — two CARLA servers on port 2000 is a
failure mode that looks like everything working.

```powershell
Get-Process CarlaUE4*,CrashReportClient -ErrorAction SilentlyContinue | Stop-Process -Force
```

### 2. Start CARLA `[WIN]`

```powershell
& "<CARLA_PATH>\CarlaUE4.exe" -prefernvidia -quality-level=Low
```

Wait until the window has finished loading before continuing.

### 3. Check the Windows host IP `[WSL]`

WSL2 reaches Windows on a virtual adapter whose address **can change after a
reboot**. Compare the live value with what the bridge is configured to use:

```bash
ip route show default | awk '{print $3}'
```

```bash
grep -n 'name="host"' ~/autoware/src/universe/autoware_universe/simulator/autoware_carla_interface/launch/autoware_carla_interface.launch.xml
```

If they differ, edit that line to the live address and rebuild just that
package:

```bash
cd ~/autoware && colcon build --packages-select autoware_carla_interface
```

### 4. Kernel and network settings `[WSL]`

**Lost on every WSL restart.** Asks for your sudo password.

```bash
sudo ip link set lo multicast on && sudo sysctl -w net.core.rmem_max=2147483647 && sudo sysctl -w net.ipv4.ipfrag_time=3 && sudo sysctl -w net.ipv4.ipfrag_high_thresh=134217728
```

Verify — if `rmem_max` still reads `212992`, they did not apply:

```bash
sysctl net.core.rmem_max net.ipv4.ipfrag_time net.ipv4.ipfrag_high_thresh
```

### 5. Confirm CARLA is reachable `[WSL]`

Five seconds here beats launching the whole stack blind.

```bash
timeout 3 bash -c "</dev/tcp/$(ip route show default | awk '{print $3}')/2000" && echo OK || echo BLOCKED
```

`BLOCKED` means CARLA has not finished loading (wait and retry) or a firewall is
in the way.

### 6. Launch Autoware `[WSL]`

```bash
cd ~/autoware && source install/setup.bash && ros2 launch autoware_launch e2e_simulator.launch.xml map_path:=$HOME/autoware/autoware_map/Town05 vehicle_model:=sample_vehicle sensor_model:=carla_sensor_kit simulator_type:=carla
```

RViz opens after 10–30 s. **Leave this terminal running — it is the whole
stack.**

> If you exported `CARLA_EXTERNAL_TICK=1`, Autoware will start, print little,
> and spawn no ego until something ticks CARLA. That is expected: with the tick
> patch active, Autoware cannot complete its own startup unaided.

### 7. Drive it from RViz

1. **Localization** panel → *Init by GNSS*, until the status reads `INITIALIZED`
2. **2D Goal Pose** → click and drag on the map to set a destination; a route
   appears
3. **AUTO** → the car drives

---

## Shutdown

**Order matters, and a clean stop matters.**

### 1. Stop Autoware `[WSL]`

Press `Ctrl+C` in the launch terminal and let it finish.

Do **not** skip straight to killing it. Force-killing Autoware leaves stale DDS
node registrations behind, and on the next start its duplicate-node checker
refuses autonomous mode permanently — curable only by `wsl --shutdown`. See
[KNOWN_INSTABILITIES.md](KNOWN_INSTABILITIES.md) §2.5.

Confirm nothing survived:

```bash
ps -eo comm --no-headers | grep -c component_container
```

Only if that is non-zero:

```bash
pkill -9 -f '[c]omponent_container'; pkill -9 -f '[r]viz2'
```

> The bracket around the first letter is deliberate — it stops the pattern
> matching the shell that is running it, which otherwise reports processes that
> do not exist.

### 2. Stop CARLA `[WIN]`

```powershell
Get-Process CarlaUE4*,CrashReportClient -ErrorAction SilentlyContinue | Stop-Process -Force
```

### 3. Release the machine `[WIN]` — only if others need the RAM

```powershell
wsl --shutdown
```

---

## Restarting soon after

If the WSL VM never stopped (an Ubuntu terminal stayed open), you can skip
steps 3 and 4 — the IP and sysctl settings are still in place. Everything else
is the same.

If every terminal was closed, assume the VM stopped and do the full cold start.

---

## Diagnostics

| Symptom | Cause | Fix |
|---|---|---|
| CARLA window frozen, "Not Responding" | Nobody is ticking it — in synchronous mode the world only advances on a tick | Not a crash. Check whether the client that owns the clock is still alive |
| Autoware starts, no car appears | `CARLA_EXTERNAL_TICK=1` with nothing ticking | Expected; something must tick CARLA |
| AUTO greyed out | No route, localization not converged, vehicle not stopped, or stale DDS nodes | Check the RViz panels in order; if all look right, `wsl --shutdown` |
| Bridge exits immediately | CARLA died underneath it | Restart **both** |
| `FileNotFoundError` on launch | The `/launch/` include-path patch is missing | Reapply (README, Part 1) |
| Nothing connects, no errors | Host IP changed | Redo step 3 |

A CARLA that answers a client is healthy, however frozen its window looks:

```bash
python -c "import carla; print(carla.Client('127.0.0.1',2000).get_server_version())"
```

---

## After a reinstall or re-clone

Re-cloning Autoware silently discards the local patches. Verify all four:

```bash
grep -n "CARLA_EXTERNAL_TICK" ~/autoware/src/universe/autoware_universe/simulator/autoware_carla_interface/src/autoware_carla_interface/carla_autoware.py
grep -n "max_vel:" ~/autoware/src/launcher/autoware_launch/autoware_launch/config/planning/scenario_planning/common/common.param.yaml
grep -n 'name="host"' ~/autoware/src/universe/autoware_universe/simulator/autoware_carla_interface/launch/autoware_carla_interface.launch.xml
grep -n "autoware_carla_interface.launch.xml" ~/autoware/src/launcher/autoware_launch/autoware_launch/launch/e2e_simulator.launch.xml
```

Three of the four fail silently when missing: the stack starts, RViz looks
healthy, and the car simply never drives.
