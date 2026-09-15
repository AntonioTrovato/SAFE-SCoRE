"""
SCENARIO: LaneChangeSimple.scenic
SOURCE FILE: LaneChangeSimple.xosc (SCTrans)
SOURCE HEADER: CARLA:LaneChangeSimple
ENTITIES: ego + 0 moving NPC(s), 2 parked, 0 pedestrian(s)
TARGET: Autoware. The ego is spawned at its recorded pose but carries NO
        Scenic behavior - Autoware owns its control. Moving NPCs still drive
        their recorded routes under Scenic.

AUTOWARE spawn_point (x, y, z, roll, pitch, yaw) - copy into the bridge config
so Autoware starts the ego at the recorded pose rather than its own default:
    [-9.4000, -152.8000, 0.0, 0.0, 0.0, 90.0000]
"""

#################################
# MAP AND MODEL                 #
#################################

param map = localPath('Town05.xodr')
param carla_map = None  # not a CARLA town - CARLA builds the world from the .xodr above
param use2DMap = True  # recorded data is 2D; no --2d needed on the CLI
param snapToGroundDefault = True  # sit actors on the road surface (the recording has no z)
param timeout = 60  # s - generating the world from OpenDRIVE outlasts the 10 s default
param real_time = 1  # 1 = play at real speed, 0 = as fast as the machine allows
model scenic.simulators.carla.model

# --------------------------------------------------------------------
# CARLA 0.9.15 spawn-height compatibility (emitted by the converter).
# Scenic places ground actors at waypoint.z + 0.5; on 0.9.15 that is
# inside the generated road mesh, so try_spawn_actor() refuses the
# spawn. Retry ONLY refused spawns, slightly higher, until they clear.
# Recorded x/y/heading are never modified. Harmless on 0.9.16, where
# the first attempt already succeeds.
import carla as _carla

_ss_orig_spawn = _carla.World.try_spawn_actor

def _ss_spawn_with_lift(world, blueprint, transform, *args, **kwargs):
    actor = _ss_orig_spawn(world, blueprint, transform, *args, **kwargs)
    if actor is not None:
        return actor
    _bp = getattr(blueprint, 'id', '') or ''
    if 'vehicle' not in _bp and 'walker' not in _bp:
        return actor
    _loc = transform.location
    for _extra in (0.3, 0.5, 0.8, 1.2):
        _t = _carla.Transform(
            _carla.Location(x=_loc.x, y=_loc.y, z=_loc.z + _extra),
            transform.rotation)
        actor = _ss_orig_spawn(world, blueprint, _t, *args, **kwargs)
        if actor is not None:
            print(f'  spawn lifted +{_extra:.2f} m to clear the road ({_bp})')
            return actor
    return None

_carla.World.try_spawn_actor = _ss_spawn_with_lift
# --------------------------------------------------------------------


# Run (CARLA server must be running):
#   scenic <this file> --simulate --2d
#   scenic <this file> --simulate --2d -p render 0 -p real_time 0   # headless, full speed
# --2d is optional (it runs without it) but ~8x faster: Scenic's 3D geometry
# checks dominate every step, and real-time playback cannot keep up without it.

#################################
# CONSTANTS                     #
#################################

SIM_DURATION = 30  # s, from the recorded speed profiles

# Cruise speeds (m/s) — mean of each recorded moving profile
EGO_SPEED = 8.00  # recorded ego speed ~0; nominal urban speed

# Autoware target: no Scenic braking parameters - the ego is driven by Autoware.

# Environment from the source file (kept for reference)
param time_of_day = 12
param weather_precipitation = 'dry'
param weather_rain_intensity = 0.00
param weather_fog_range = 100000

#################################
# SPATIAL RELATIONS             #
#################################

# Original SCTrans world coordinates and headings.
# Scenic heading 0 deg = +y axis (north), counter-clockwise;
# converted from OpenSCENARIO h (0 = +x axis / east).

egoSpawnPt = new OrientedPoint at (-9.4000, -152.8000), facing -0.00 deg
adversarySpawnPt = new OrientedPoint at (-9.4000, -71.0000), facing -0.00 deg
standingSpawnPt = new OrientedPoint at (-8.2000, 29.2000), facing -0.00 deg

#################################
# AGENT BEHAVIORS               #
#################################

#################################
# ENTITIES                      #
#################################

# 'with regionContainedIn None' keeps the exact recorded
# positions (default containment would reject off-road/junction spawns).
# 'with allowCollisions True' tolerates LGSVL's inflated
# bounding boxes, which overlap in dense recorded traffic
# (CARLA still refuses to spawn actors that intersect).

# hero: parked in the recorded data (peak speed < 0.5 m/s)
ego = new Car at egoSpawnPt, facing egoSpawnPt.heading,
      with blueprint 'vehicle.tesla.model3',
      with length 4.50, with width 2.10,
      with regionContainedIn None,
      with allowCollisions True

# adversary: parked in the recorded data (peak speed < 0.5 m/s)
adversary = new Car at adversarySpawnPt, facing adversarySpawnPt.heading,
      with blueprint 'vehicle.lincoln.mkz_2017',
      with length 4.50, with width 2.10,
      with regionContainedIn None,
      with allowCollisions True

# standing: parked in the recorded data (peak speed < 0.5 m/s)
standing = new Car at standingSpawnPt, facing standingSpawnPt.heading,
      with blueprint 'vehicle.volkswagen.t2',
      with length 4.50, with width 2.10,
      with regionContainedIn None,
      with allowCollisions True

#################################
# REAL-TIME PLAYBACK            #
#################################

# Holds each step to one timestep of wall-clock time so the run
# plays at real speed, the way the MetaDrive interface does.
# Turn it off for batch runs:  -p real_time 0
import time as _time

monitor RealTimePacing():
    paced = bool(globalParameters.real_time)
    step = float(globalParameters.timestep)
    last = _time.perf_counter()
    while True:
        if paced:
            slack = step - (_time.perf_counter() - last)
            if slack > 0:
                _time.sleep(slack)
        last = _time.perf_counter()
        wait

require monitor RealTimePacing()

#################################
# TERMINATION                   #
#################################

terminate after SIM_DURATION seconds
