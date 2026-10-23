# Daily procedure — running Mode 2 (CARLA + Autoware)

What to do at the start and at the end of each working session, once SAFE-SCoRE, CARLA and Autoware are installed (README, Installation). SAFE-SCoRE starts, checks and restarts CARLA and Autoware itself, so you never launch them: you only prepare the machine, then run the command.

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
| Source patches (host IP, `/launch/` path, tick, `max_vel`) | survives | survives | survives | **lost — reapply** |
| `sysctl` + `lo multicast` | **lost** | **lost** | **lost** | n/a |
| Windows host IP value | usually same | **can change** | **can change** | n/a |

**The WSL VM restarts when** you run `wsl --shutdown`, Windows reboots, **or you
close every Ubuntu terminal and leave it idle for a few minutes** — WSL2
auto-stops the VM. That last one catches people out: closing all terminals is
not harmless.

When in doubt, do all the steps below. They are cheap and safe to repeat.

---

## Start of the session

### 1. Check for leftovers `[WIN]`

Two CARLA servers on port 2000 is a failure mode that looks like everything working, so nothing of a previous session may be running.

```powershell
Get-Process CarlaUE4*,CrashReportClient -ErrorAction SilentlyContinue
```

Anything listed must be stopped:

```powershell
Get-Process CarlaUE4*,CrashReportClient -ErrorAction SilentlyContinue | Stop-Process -Force
```

Then check that no Autoware is left in WSL `[WSL]`. The result must be `0`:

```bash
ps -eo comm --no-headers | grep -c component_container
```

If it is not, stop it cleanly with `Ctrl+C` in the terminal that launched it. Only if there is none, force it:

```bash
pkill -9 -f '[c]omponent_container'; pkill -9 -f '[r]viz2'
```

> The bracket around the first letter is deliberate — it stops the pattern
> matching the shell that is running it, which otherwise reports processes that
> do not exist.

Force-killing Autoware leaves stale DDS node registrations behind, and on the next start its duplicate-node checker refuses autonomous mode permanently — curable only by `wsl --shutdown`. See [KNOWN_INSTABILITIES.md](KNOWN_INSTABILITIES.md) §2.5.

### 2. Check the Windows host IP `[WSL]`

WSL2 reaches Windows on a virtual adapter whose address **can change after a
reboot**. Compare the live value with what the bridge is configured to use:

```bash
ip route show default | awk '{print $3}'
```

```bash
grep -n 'name="host"' ~/autoware/src/universe/autoware_universe/simulator/autoware_carla_interface/launch/autoware_carla_interface.launch.xml
```

If they differ, set that line to the live address (this replaces the value of the
`default` or `value` attribute on the `name="host"` line with the address printed
by the first command):

```bash
sed -i -E "/name=\"host\"/ s/(default|value)=\"[^\"]*\"/\1=\"$(ip route show default | awk '{print $3}')\"/" ~/autoware/src/universe/autoware_universe/simulator/autoware_carla_interface/launch/autoware_carla_interface.launch.xml
```

Check that the line now shows the live address (rerun the `grep` above), then
rebuild just that package:

```bash
cd ~/autoware && colcon build --packages-select autoware_carla_interface
```

### 3. Kernel and network settings `[WSL]`

**Lost on every WSL restart.** Asks for your sudo password.

Paste these **one line at a time** — as a single `&&` chain the terminal can
split them, silently leaving most unapplied.

```bash
sudo ip link set lo multicast on
```

```bash
sudo sysctl -w net.core.rmem_max=2147483647
```

```bash
sudo sysctl -w net.ipv4.ipfrag_time=3
```

```bash
sudo sysctl -w net.ipv4.ipfrag_high_thresh=134217728
```

**Keep this terminal open for the whole session** — if every Ubuntu
terminal closes, the VM stops and these settings are lost.

Verify — if `rmem_max` still reads `212992`, they did not apply:

```bash
sysctl net.core.rmem_max net.ipv4.ipfrag_time net.ipv4.ipfrag_high_thresh
```

### 4. Run `[WIN]`

From the repository root, with the virtual environment active. SAFE-SCoRE starts CARLA and Autoware (RViz opens by itself), runs the suite, and stops them at the end:

```powershell
python -m src.runner.run_experiment --input_dir scenic_example/suite --output_folder my_run --num_runs 10 --driver autoware --follow_camera behind --carla_exe "C:\path\to\CARLA_0.9.15\WindowsNoEditor\CarlaUE4.exe" --carla_launch_args="-prefernvidia -quality-level=Low" --autoware_map_path /home/<your-linux-user>/autoware/autoware_map/Town05
```

The options are described in the README (Running).

---

## End of the session

SAFE-SCoRE stops CARLA and Autoware when the suite ends. If you interrupted it, or it crashed, check as in Start, step 1 that nothing is left. Prefer a clean `Ctrl+C` to a kill for Autoware.

To release the machine, only if others need the RAM `[WIN]`:

```powershell
wsl --shutdown
```

---

## Diagnostics

| Symptom | Cause | Fix |
|---|---|---|
| `pre-flight: Autoware nodes are missing`, repeated | The kernel settings are lost (WSL restarted), or Autoware cannot start | Redo step 3, then launch Autoware by hand once to read the real error (README, Mode 2) |
| Nothing connects, no errors | Host IP changed | Redo step 2 |
| `FileNotFoundError` on Autoware launch | The `/launch/` include-path patch is missing | Reapply (README, Step 4.3) |
| Autonomous mode never available, localization and routing look fine | Stale DDS registrations | `wsl --shutdown`, then redo steps 3 and 4 |
| CARLA window frozen, "Not Responding" | Nobody is ticking it — in synchronous mode the world only advances on a tick | Not a crash. Check whether the client that owns the clock (the runner) is still alive |
| Bridge exits immediately | CARLA died underneath it | SAFE-SCoRE restarts **both** by itself |

A CARLA that answers a client is healthy, however frozen its window looks:

```powershell
python -c "import carla; print(carla.Client('127.0.0.1',2000).get_server_version())"
```

---

## After a reinstall or re-clone

Re-cloning Autoware silently discards the local patches. Verify all four:

```bash
grep -n "CARLA_EXTERNAL_TICK" ~/autoware/src/universe/autoware_universe/simulator/autoware_carla_interface/src/autoware_carla_interface/carla_autoware.py
grep -n "max_vel:" ~/autoware/src/launcher/autoware_launch/autoware_launch/config/planning/scenario_planning/common/common.param.yaml
grep -n 'name="host"' ~/autoware/src/universe/autoware_universe/simulator/autoware_carla_interface/launch/autoware_carla_interface.launch.xml
grep -n "launch/autoware_carla_interface.launch.xml" ~/autoware/src/launcher/autoware_launch/autoware_launch/launch/e2e_simulator.launch.xml
```

Three of the four fail silently when missing: the stack starts, RViz looks
healthy, and the car simply never drives.
