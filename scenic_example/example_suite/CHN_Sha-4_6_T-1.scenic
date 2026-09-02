"""
SCENARIO: CHN_Sha-4_6_T-1.scenic
SOURCE FILE: CHN_Sha-4_6_T-1.xosc (SCTrans)
SOURCE HEADER: myscenario (author: trial)
ENTITIES: ego + 11 moving NPC(s), 1 parked, 0 pedestrian(s)
TARGET: Autoware. The ego is spawned at its recorded pose but carries NO
        Scenic behavior - Autoware owns its control. Moving NPCs still drive
        their recorded routes under Scenic.

AUTOWARE spawn_point (x, y, z, roll, pitch, yaw) - copy into the bridge config
so Autoware starts the ego at the recorded pose rather than its own default:
    [-20.3567, -1.0515, 0.0, 0.0, 0.0, -0.5443]
AUTOWARE goal pose (x, y, yaw):
    [6.7225, -23.0958, -89.9594]
"""

#################################
# MAP AND MODEL                 #
#################################

param map = localPath('CHN_Sha-16_1_T-1.xodr')
param carla_map = None  # not a CARLA town - CARLA builds the world from the .xodr above
param use2DMap = True  # recorded data is 2D; no --2d needed on the CLI
param snapToGroundDefault = True  # sit actors on the road surface (the recording has no z)
param timeout = 60  # s - generating the world from OpenDRIVE outlasts the 10 s default
param real_time = 1  # 1 = play at real speed, 0 = as fast as the machine allows
model scenic.simulators.carla.model

# Run (CARLA server must be running):
#   scenic <this file> --simulate --2d
#   scenic <this file> --simulate --2d -p render 0 -p real_time 0   # headless, full speed
# --2d is optional (it runs without it) but ~8x faster: Scenic's 3D geometry
# checks dominate every step, and real-time playback cannot keep up without it.

#################################
# CONSTANTS                     #
#################################

SIM_DURATION = 165  # s, from the recorded speed profiles

# Cruise speeds (m/s) — mean of each recorded moving profile
EGO_SPEED = 2.69  # recorded
NPC30_SPEED = 6.51  # recorded
NPC33_SPEED = 2.95  # recorded
NPC34_SPEED = 2.80  # recorded
NPC35_SPEED = 2.25  # recorded
NPC36_SPEED = 2.87  # recorded
NPC37_SPEED = 12.78  # recorded
NPC38_SPEED = 2.32  # recorded
NPC39_SPEED = 2.41  # recorded
NPC310_SPEED = 2.32  # recorded
NPC312_SPEED = 1.07  # recorded
NPC313_SPEED = 2.28  # recorded

# Autoware target: no Scenic braking parameters - the ego is driven by Autoware.
EGO_GOAL_RADIUS = 5  # m, counts as arrival at the destination

# Environment from the source file (kept for reference)
param time_of_day = 12
param weather_precipitation = 'rain'
param weather_rain_intensity = 0.50
param weather_fog_range = 10000

#################################
# SPATIAL RELATIONS             #
#################################

# Original SCTrans world coordinates and headings.
# Scenic heading 0 deg = +y axis (north), counter-clockwise;
# converted from OpenSCENARIO h (0 = +x axis / east).

egoSpawnPt = new OrientedPoint at (-20.3567, -1.0515), facing -90.54 deg
egoGoalPt = new OrientedPoint at (6.7225, -23.0958), facing -179.96 deg  # recorded destination
npc30SpawnPt = new OrientedPoint at (-8.2956, -1.1664), facing -90.54 deg
npc33SpawnPt = new OrientedPoint at (-38.0547, 1.4251), facing 90.28 deg
npc34SpawnPt = new OrientedPoint at (-25.8881, 1.4714), facing 89.99 deg
npc35SpawnPt = new OrientedPoint at (-11.8923, 1.3613), facing 89.46 deg
npc36SpawnPt = new OrientedPoint at (-34.0457, -1.0488), facing -89.72 deg
npc37SpawnPt = new OrientedPoint at (6.7274, -29.8669), facing -179.93 deg
npc38SpawnPt = new OrientedPoint at (-44.3283, -1.0986), facing -89.72 deg
npc39SpawnPt = new OrientedPoint at (0.4320, 1.3984), facing 93.01 deg
npc310SpawnPt = new OrientedPoint at (-54.1674, -1.1464), facing -89.72 deg
npc311SpawnPt = new OrientedPoint at (6.9223, 8.6684), facing 178.60 deg
npc312SpawnPt = new OrientedPoint at (7.1586, 18.3304), facing 178.59 deg
npc313SpawnPt = new OrientedPoint at (-63.3967, -1.1912), facing -89.72 deg

# Recorded route waypoints (x, y, heading_rad)
npc30Waypoints = [
    (-8.00, -1.17, -1.580), (-7.42, -1.17, -1.580), (-6.85, -1.18, -1.580),
    (-6.28, -1.19, -1.580), (-5.71, -1.19, -1.580), (-5.13, -1.20, -1.580),
    (-4.56, -1.20, -1.580), (-3.98, -1.21, -1.580), (-3.40, -1.21, -1.580),
    (-2.84, -1.22, -1.580), (-2.27, -1.23, -1.583), (-1.65, -1.25, -1.589),
    (-0.96, -1.28, -1.598), (-0.19, -1.34, -1.620), (0.63, -1.44, -1.657),
    (1.50, -1.59, -1.715), (2.41, -1.82, -1.809), (3.29, -2.17, -1.944),
    (4.12, -2.66, -2.131), (4.86, -3.30, -2.369), (5.51, -4.12, -2.640),
    (5.80, -4.63, -2.769), (6.08, -5.20, -2.886), (6.32, -5.83, -2.983),
    (6.49, -6.50, -3.053), (6.61, -7.21, -3.101), (6.68, -7.95, -3.129),
    (6.71, -8.71, -3.141), (6.71, -9.49, -3.141), (6.71, -10.29, -3.141),
    (6.71, -11.11, -3.141), (6.71, -11.95, -3.141), (6.71, -12.81, -3.141),
    (6.71, -13.70, -3.141), (6.71, -14.59, -3.141), (6.71, -15.51, -3.141),
    (6.72, -16.45, -3.141), (6.72, -17.41, -3.141), (6.72, -18.39, -3.141),
    (6.72, -19.40, -3.141), (6.72, -20.43, -3.141), (6.72, -21.48, -3.141),
    (6.72, -22.55, -3.141), (6.72, -23.64, -3.141), (6.72, -24.76, -3.140),
    (6.72, -25.90, -3.140), (6.72, -27.06, -3.140), (6.73, -28.24, -3.140),
    (6.73, -29.44, -3.140), (6.73, -30.66, -3.140), (6.73, -31.91, -3.140),
    (6.73, -33.18, -3.140), (6.73, -34.47, -3.140), (6.73, -35.78, -3.140),
    (6.74, -37.11, -3.140),
]
npc33Waypoints = [
    (-38.35, 1.42, 1.576), (-38.95, 1.42, 1.576), (-39.53, 1.42, 1.576),
    (-40.12, 1.41, 1.576), (-40.70, 1.41, 1.576), (-41.29, 1.41, 1.576),
    (-41.89, 1.41, 1.576), (-42.48, 1.40, 1.576), (-43.05, 1.40, 1.576),
    (-43.64, 1.40, 1.576), (-44.23, 1.40, 1.576), (-44.83, 1.39, 1.576),
    (-45.41, 1.39, 1.576), (-46.01, 1.39, 1.576), (-46.60, 1.38, 1.576),
    (-47.18, 1.38, 1.576), (-47.78, 1.38, 1.576), (-48.37, 1.37, 1.576),
    (-48.96, 1.37, 1.576), (-49.54, 1.37, 1.576), (-50.13, 1.37, 1.576),
    (-50.70, 1.36, 1.576), (-51.29, 1.36, 1.576), (-51.88, 1.36, 1.576),
    (-52.47, 1.36, 1.576), (-53.06, 1.35, 1.576), (-53.65, 1.35, 1.576),
    (-54.24, 1.35, 1.576), (-54.82, 1.34, 1.576), (-55.42, 1.34, 1.576),
    (-56.00, 1.34, 1.576), (-56.60, 1.34, 1.576), (-57.18, 1.33, 1.576),
    (-57.77, 1.33, 1.576), (-58.36, 1.33, 1.576), (-58.94, 1.32, 1.576),
    (-59.54, 1.32, 1.576), (-60.12, 1.32, 1.576), (-60.71, 1.32, 1.576),
    (-61.30, 1.31, 1.576), (-61.89, 1.31, 1.576), (-62.46, 1.31, 1.576),
    (-63.06, 1.30, 1.576), (-63.64, 1.30, 1.576), (-64.22, 1.30, 1.576),
    (-64.80, 1.30, 1.576), (-65.40, 1.29, 1.576),
]
npc34Waypoints = [
    (-26.16, 1.47, 1.571), (-26.72, 1.47, 1.573), (-27.27, 1.47, 1.575),
    (-28.40, 1.47, 1.576), (-28.96, 1.47, 1.576), (-29.51, 1.47, 1.576),
    (-30.07, 1.46, 1.576), (-30.63, 1.46, 1.576), (-31.18, 1.46, 1.576),
    (-32.29, 1.45, 1.576), (-32.85, 1.45, 1.576), (-33.40, 1.45, 1.576),
    (-33.97, 1.44, 1.576), (-34.53, 1.44, 1.576), (-35.65, 1.44, 1.576),
    (-36.21, 1.43, 1.576), (-36.77, 1.43, 1.576), (-37.34, 1.43, 1.576),
    (-37.90, 1.43, 1.576), (-39.02, 1.42, 1.576), (-39.58, 1.42, 1.576),
    (-40.14, 1.41, 1.576), (-40.70, 1.41, 1.576), (-41.25, 1.41, 1.576),
    (-41.80, 1.41, 1.576), (-42.92, 1.40, 1.576), (-43.47, 1.40, 1.576),
    (-44.03, 1.40, 1.576), (-44.59, 1.39, 1.576), (-45.15, 1.39, 1.576),
    (-46.27, 1.39, 1.576), (-46.82, 1.38, 1.576), (-47.38, 1.38, 1.576),
    (-47.94, 1.38, 1.576), (-48.50, 1.37, 1.576), (-49.62, 1.37, 1.576),
    (-50.17, 1.37, 1.576), (-50.72, 1.36, 1.576), (-51.29, 1.36, 1.576),
    (-51.84, 1.36, 1.576), (-52.40, 1.36, 1.576), (-53.52, 1.35, 1.576),
    (-54.08, 1.35, 1.576), (-54.63, 1.34, 1.576), (-55.19, 1.34, 1.576),
    (-55.74, 1.34, 1.576), (-56.86, 1.33, 1.576), (-57.41, 1.33, 1.576),
    (-57.98, 1.33, 1.576), (-58.53, 1.33, 1.576), (-59.09, 1.32, 1.576),
    (-60.22, 1.32, 1.576), (-60.78, 1.31, 1.576), (-61.33, 1.31, 1.576),
    (-61.90, 1.31, 1.576), (-62.47, 1.31, 1.576), (-63.03, 1.30, 1.576),
    (-64.15, 1.30, 1.576), (-64.71, 1.30, 1.576), (-65.26, 1.29, 1.576),
]
npc35Waypoints = [
    (-12.12, 1.36, 1.561), (-12.78, 1.37, 1.561), (-13.45, 1.38, 1.561),
    (-14.13, 1.38, 1.561), (-14.80, 1.39, 1.561), (-15.48, 1.40, 1.561),
    (-16.15, 1.40, 1.561), (-16.84, 1.41, 1.561), (-17.52, 1.41, 1.561),
    (-18.20, 1.42, 1.561), (-18.88, 1.43, 1.561), (-19.54, 1.43, 1.561),
    (-20.21, 1.44, 1.561), (-20.88, 1.45, 1.561), (-21.56, 1.45, 1.561),
    (-22.24, 1.46, 1.561), (-22.91, 1.46, 1.562), (-23.58, 1.47, 1.564),
    (-24.25, 1.47, 1.566), (-24.92, 1.47, 1.568), (-25.59, 1.47, 1.570),
    (-26.26, 1.47, 1.572), (-26.93, 1.47, 1.574), (-27.60, 1.48, 1.576),
    (-28.27, 1.47, 1.576), (-28.95, 1.47, 1.576), (-29.63, 1.47, 1.576),
    (-30.31, 1.46, 1.576), (-30.98, 1.46, 1.576), (-31.67, 1.46, 1.576),
    (-32.35, 1.45, 1.576), (-33.02, 1.45, 1.576), (-33.68, 1.45, 1.576),
    (-34.35, 1.44, 1.576), (-35.02, 1.44, 1.576), (-35.70, 1.44, 1.576),
    (-36.37, 1.43, 1.576), (-37.04, 1.43, 1.576), (-37.71, 1.43, 1.576),
    (-38.38, 1.42, 1.576), (-39.07, 1.42, 1.576), (-39.75, 1.42, 1.576),
    (-40.43, 1.41, 1.576), (-41.09, 1.41, 1.576), (-41.77, 1.41, 1.576),
    (-42.44, 1.40, 1.576), (-43.11, 1.40, 1.576), (-43.78, 1.40, 1.576),
    (-44.46, 1.39, 1.576), (-45.13, 1.39, 1.576), (-45.80, 1.39, 1.576),
    (-46.48, 1.38, 1.576), (-47.16, 1.38, 1.576), (-47.83, 1.38, 1.576),
]
npc36Waypoints = [
    (-33.80, -1.05, -1.566), (-33.08, -1.04, -1.566), (-32.36, -1.04, -1.566),
    (-31.64, -1.04, -1.566), (-30.91, -1.03, -1.566), (-30.20, -1.03, -1.566),
    (-29.48, -1.03, -1.566), (-28.77, -1.02, -1.566), (-28.06, -1.02, -1.566),
    (-27.33, -1.02, -1.567), (-25.90, -1.02, -1.571), (-25.17, -1.02, -1.573),
    (-24.46, -1.03, -1.575), (-23.75, -1.03, -1.577), (-23.04, -1.03, -1.579),
    (-22.32, -1.03, -1.580), (-21.60, -1.04, -1.580), (-20.88, -1.05, -1.580),
    (-20.16, -1.05, -1.580), (-19.44, -1.06, -1.580), (-18.73, -1.07, -1.580),
    (-18.01, -1.07, -1.580), (-17.29, -1.08, -1.580), (-16.57, -1.09, -1.580),
    (-15.85, -1.09, -1.580), (-15.14, -1.10, -1.580), (-14.42, -1.11, -1.580),
    (-13.69, -1.11, -1.580), (-12.98, -1.12, -1.580), (-12.26, -1.13, -1.580),
    (-10.81, -1.14, -1.580), (-10.08, -1.15, -1.580), (-9.36, -1.16, -1.580),
    (-8.65, -1.16, -1.580), (-7.92, -1.17, -1.580), (-7.21, -1.18, -1.580),
    (-6.51, -1.18, -1.580), (-5.80, -1.19, -1.580), (-5.07, -1.20, -1.580),
    (-4.34, -1.20, -1.580), (-3.62, -1.21, -1.580), (-2.89, -1.22, -1.580),
    (-2.15, -1.23, -1.584), (-1.58, -1.25, -1.589), (-0.92, -1.28, -1.599),
    (-0.17, -1.34, -1.621), (0.64, -1.44, -1.657), (1.51, -1.59, -1.716),
    (2.43, -1.83, -1.813), (2.91, -2.00, -1.875), (4.59, -3.04, -2.275),
    (5.24, -3.75, -2.527), (5.54, -4.16, -2.653), (5.81, -4.64, -2.772),
    (6.07, -5.17, -2.881), (6.29, -5.76, -2.973), (6.46, -6.40, -3.042),
    (6.59, -7.06, -3.092), (6.67, -7.75, -3.126), (6.70, -8.46, -3.137),
]
npc37Waypoints = [
    (6.73, -31.11, -3.140), (6.73, -32.37, -3.140), (6.73, -33.64, -3.140),
    (6.73, -34.93, -3.140), (6.73, -36.24, -3.140),
]
npc38Waypoints = [
    (-44.10, -1.10, -1.566), (-43.41, -1.09, -1.566), (-42.72, -1.09, -1.566),
    (-42.02, -1.09, -1.566), (-41.31, -1.08, -1.566), (-40.62, -1.08, -1.566),
    (-39.92, -1.08, -1.566), (-39.23, -1.07, -1.566), (-38.54, -1.07, -1.566),
    (-37.85, -1.07, -1.566), (-37.16, -1.06, -1.566), (-36.47, -1.06, -1.566),
    (-35.77, -1.06, -1.566), (-35.08, -1.05, -1.566), (-34.39, -1.05, -1.566),
    (-33.68, -1.05, -1.566), (-32.99, -1.04, -1.566), (-32.30, -1.04, -1.566),
    (-31.59, -1.04, -1.566), (-30.90, -1.03, -1.566), (-30.21, -1.03, -1.566),
    (-29.51, -1.03, -1.566), (-28.81, -1.02, -1.566), (-28.11, -1.02, -1.566),
    (-27.42, -1.02, -1.567), (-26.72, -1.02, -1.569), (-26.03, -1.02, -1.571),
    (-25.33, -1.02, -1.573), (-24.63, -1.02, -1.575), (-23.94, -1.03, -1.577),
    (-23.25, -1.03, -1.579), (-22.56, -1.03, -1.580), (-21.87, -1.04, -1.580),
    (-21.18, -1.04, -1.580), (-20.49, -1.05, -1.580), (-19.79, -1.06, -1.580),
    (-19.09, -1.06, -1.580), (-18.40, -1.07, -1.580), (-17.71, -1.08, -1.580),
    (-17.02, -1.08, -1.580), (-16.33, -1.09, -1.580), (-15.64, -1.10, -1.580),
    (-14.95, -1.10, -1.580), (-14.26, -1.11, -1.580), (-13.56, -1.12, -1.580),
    (-12.86, -1.12, -1.580), (-12.18, -1.13, -1.580), (-11.49, -1.14, -1.580),
    (-10.79, -1.14, -1.580), (-10.09, -1.15, -1.580), (-9.40, -1.16, -1.580),
    (-8.71, -1.16, -1.580), (-8.01, -1.17, -1.580), (-7.32, -1.18, -1.580),
]
npc39Waypoints = [
    (0.18, 1.37, 1.609), (-0.57, 1.32, 1.587), (-1.09, 1.29, 1.572),
    (-1.60, 1.28, 1.568), (-2.11, 1.28, 1.564), (-2.62, 1.27, 1.561),
    (-3.12, 1.28, 1.561), (-3.63, 1.28, 1.561), (-4.64, 1.29, 1.561),
    (-5.16, 1.30, 1.561), (-5.66, 1.30, 1.561), (-6.17, 1.31, 1.561),
    (-6.67, 1.31, 1.561), (-7.18, 1.32, 1.561), (-7.69, 1.32, 1.561),
    (-8.21, 1.33, 1.561), (-8.72, 1.33, 1.561), (-9.23, 1.34, 1.561),
    (-9.98, 1.34, 1.561), (-10.49, 1.35, 1.561), (-10.99, 1.35, 1.561),
    (-11.50, 1.36, 1.561), (-12.00, 1.36, 1.561), (-13.01, 1.37, 1.561),
    (-13.52, 1.38, 1.561), (-14.03, 1.38, 1.561), (-14.54, 1.39, 1.561),
    (-15.29, 1.39, 1.561), (-15.81, 1.40, 1.561), (-16.32, 1.40, 1.561),
    (-16.82, 1.41, 1.561), (-17.33, 1.41, 1.561), (-17.83, 1.42, 1.561),
    (-18.33, 1.42, 1.561), (-19.07, 1.43, 1.561), (-19.80, 1.44, 1.561),
    (-20.52, 1.44, 1.561), (-21.93, 1.46, 1.561), (-22.63, 1.46, 1.561),
    (-23.32, 1.47, 1.563), (-24.01, 1.47, 1.565), (-24.71, 1.47, 1.567),
    (-25.40, 1.47, 1.569), (-26.08, 1.47, 1.571), (-26.75, 1.47, 1.573),
    (-27.42, 1.48, 1.575), (-28.11, 1.47, 1.576), (-28.78, 1.47, 1.576),
    (-29.46, 1.47, 1.576), (-30.14, 1.46, 1.576), (-30.83, 1.46, 1.576),
    (-31.49, 1.46, 1.576), (-32.85, 1.45, 1.576), (-33.52, 1.45, 1.576),
    (-34.20, 1.44, 1.576), (-34.88, 1.44, 1.576), (-35.55, 1.44, 1.576),
    (-36.22, 1.43, 1.576), (-36.90, 1.43, 1.576), (-37.57, 1.43, 1.576),
]
npc310Waypoints = [
    (-53.94, -1.15, -1.566), (-53.26, -1.14, -1.566), (-52.56, -1.14, -1.566),
    (-51.88, -1.14, -1.566), (-51.19, -1.13, -1.566), (-50.49, -1.13, -1.566),
    (-49.80, -1.13, -1.566), (-49.12, -1.12, -1.566), (-48.43, -1.12, -1.566),
    (-47.73, -1.12, -1.566), (-47.03, -1.11, -1.566), (-46.34, -1.11, -1.566),
    (-45.64, -1.11, -1.566), (-44.96, -1.10, -1.566), (-44.27, -1.10, -1.566),
    (-43.56, -1.09, -1.566), (-42.88, -1.09, -1.566), (-42.19, -1.09, -1.566),
    (-41.50, -1.08, -1.566), (-40.79, -1.08, -1.566), (-40.10, -1.08, -1.566),
    (-39.41, -1.07, -1.566), (-38.70, -1.07, -1.566), (-38.00, -1.07, -1.566),
    (-37.30, -1.06, -1.566), (-36.59, -1.06, -1.566), (-35.91, -1.06, -1.566),
    (-35.21, -1.05, -1.566), (-34.51, -1.05, -1.566), (-33.81, -1.05, -1.566),
    (-33.12, -1.04, -1.566), (-32.42, -1.04, -1.566), (-31.73, -1.04, -1.566),
    (-31.04, -1.03, -1.566), (-30.36, -1.03, -1.566), (-29.67, -1.03, -1.566),
    (-28.97, -1.02, -1.566), (-28.28, -1.02, -1.566), (-27.58, -1.02, -1.566),
    (-26.89, -1.02, -1.568), (-26.20, -1.02, -1.570), (-25.51, -1.02, -1.572),
    (-24.83, -1.02, -1.574), (-24.15, -1.03, -1.576), (-23.45, -1.03, -1.578),
    (-22.75, -1.03, -1.580), (-22.05, -1.04, -1.580), (-21.36, -1.04, -1.580),
    (-20.67, -1.05, -1.580), (-19.98, -1.06, -1.580), (-19.29, -1.06, -1.580),
    (-18.60, -1.07, -1.580), (-17.89, -1.07, -1.580), (-17.20, -1.08, -1.580),
]
npc312Waypoints = [
    (7.15, 18.15, 3.117), (7.14, 17.53, 3.117), (7.13, 17.01, 3.117),
    (7.11, 16.50, 3.117),
]
npc313Waypoints = [
    (-63.21, -1.19, -1.566), (-62.63, -1.19, -1.566), (-62.03, -1.18, -1.566),
    (-61.41, -1.18, -1.566), (-60.78, -1.18, -1.566), (-60.13, -1.18, -1.566),
    (-59.48, -1.17, -1.566), (-58.83, -1.17, -1.566), (-58.16, -1.17, -1.566),
    (-57.50, -1.16, -1.566), (-56.81, -1.16, -1.566), (-56.14, -1.16, -1.566),
    (-55.46, -1.15, -1.566), (-54.78, -1.15, -1.566), (-54.10, -1.15, -1.566),
    (-53.42, -1.14, -1.566), (-52.72, -1.14, -1.566), (-52.04, -1.14, -1.566),
    (-51.35, -1.13, -1.566), (-50.66, -1.13, -1.566), (-49.97, -1.13, -1.566),
    (-49.27, -1.12, -1.566), (-48.58, -1.12, -1.566), (-47.89, -1.12, -1.566),
    (-47.19, -1.11, -1.566), (-46.48, -1.11, -1.566), (-45.79, -1.11, -1.566),
    (-45.09, -1.10, -1.566), (-44.40, -1.10, -1.566), (-43.69, -1.10, -1.566),
    (-43.00, -1.09, -1.566), (-42.30, -1.09, -1.566), (-41.60, -1.09, -1.566),
    (-40.91, -1.08, -1.566), (-40.22, -1.08, -1.566), (-39.54, -1.08, -1.566),
    (-38.84, -1.07, -1.566), (-38.14, -1.07, -1.566), (-37.44, -1.07, -1.566),
    (-36.75, -1.06, -1.566), (-36.07, -1.06, -1.566), (-35.38, -1.06, -1.566),
    (-34.70, -1.05, -1.566), (-34.02, -1.05, -1.566), (-33.33, -1.05, -1.566),
    (-32.64, -1.04, -1.566), (-31.95, -1.04, -1.566), (-31.24, -1.04, -1.566),
    (-30.55, -1.03, -1.566), (-29.85, -1.03, -1.566), (-29.16, -1.03, -1.566),
    (-28.48, -1.02, -1.566), (-27.79, -1.02, -1.566), (-27.09, -1.02, -1.567),
]

import math as _math

def bestLaneAt(x, y, heading):
    """Containing lane whose direction best matches the recorded heading
    (several lanes can overlap near junctions)."""
    pt = Vector(x, y)
    candidates = [l for l in network.lanes if l.containsPoint(pt)]
    if not candidates:
        return network.laneAt(pt)
    def _hdiff(l):
        value = l.orientation.value(pt)
        yaw = value.yaw if hasattr(value, 'yaw') else float(value)
        d = abs(yaw - heading) % (2 * _math.pi)
        return min(d, 2 * _math.pi - d)
    return min(candidates, key=_hdiff)

def lanesFromWaypoints(waypoints):
    """Ordered lane sequence traversed by a recorded (x, y, heading) route."""
    lanes = []
    for x, y, h in waypoints:
        lane = bestLaneAt(x, y, h)
        if lane is not None and (not lanes or lanes[-1] is not lane):
            lanes.append(lane)
    return lanes

def connectedSegments(lanes, gap=1.0):
    """Split a lane route wherever junction connectivity is missing,
    so each piece can be followed with FollowTrajectoryBehavior."""
    segments = []
    for lane in lanes:
        if segments:
            pe = segments[-1][-1].centerline.points[-1]
            ls = lane.centerline.points[0]
            if _math.hypot(ls[0] - pe[0], ls[1] - pe[1]) <= gap:
                segments[-1].append(lane)
                continue
        segments.append([lane])
    return segments

def lanesTowardGoal(spawnPt, spawnHeading, goalPt, maxHops=14):
    """Greedy lane chaining toward the recorded destination; stitches across
    junctions with missing connectivity."""
    from shapely.geometry import Point as _ShPoint
    goal = _ShPoint(goalPt.position.x, goalPt.position.y)
    lane = bestLaneAt(spawnPt.position.x, spawnPt.position.y, spawnHeading)
    if lane is None:
        return []
    route = [lane]
    visited = {lane.uid}
    for _ in range(maxHops):
        cur = route[-1]
        if cur.polygon.distance(goal) <= 1.0:
            break
        nxt = []
        for m in cur.maneuvers:
            step = m.connectingLane if m.connectingLane is not None else m.endLane
            if step is not None and step.uid not in visited:
                nxt.append(step)
        succ = getattr(cur, '_successor', None)
        if not nxt and succ is not None and succ.uid not in visited:
            nxt.append(succ)
        if not nxt:
            ex, ey = cur.centerline.points[-1][0], cur.centerline.points[-1][1]
            for l in network.lanes:
                if l.uid in visited:
                    continue
                ls = l.centerline.points[0]
                if _math.hypot(ls[0] - ex, ls[1] - ey) <= 4.0:
                    nxt.append(l)
        if not nxt:
            break
        best = min(nxt, key=lambda l: l.polygon.distance(goal))
        route.append(best)
        visited.add(best.uid)
    return route

egoRouteLanes = lanesTowardGoal(egoSpawnPt, egoSpawnPt.heading, egoGoalPt)
npc30RouteLanes = lanesFromWaypoints(npc30Waypoints)
npc33RouteLanes = lanesFromWaypoints(npc33Waypoints)
npc34RouteLanes = lanesFromWaypoints(npc34Waypoints)
npc35RouteLanes = lanesFromWaypoints(npc35Waypoints)
npc36RouteLanes = lanesFromWaypoints(npc36Waypoints)
npc37RouteLanes = lanesFromWaypoints(npc37Waypoints)
npc38RouteLanes = lanesFromWaypoints(npc38Waypoints)
npc39RouteLanes = lanesFromWaypoints(npc39Waypoints)
npc310RouteLanes = lanesFromWaypoints(npc310Waypoints)
npc312RouteLanes = lanesFromWaypoints(npc312Waypoints)
npc313RouteLanes = lanesFromWaypoints(npc313Waypoints)

#################################
# AGENT BEHAVIORS               #
#################################

behavior Npc30Behavior():
    # CARLA spawns at rest; restore the recorded speed on the first step
    take SetSpeedAction(self.initialSpeed)
    if npc30RouteLanes:
        for seg in connectedSegments(npc30RouteLanes):
            do FollowTrajectoryBehavior(target_speed=NPC30_SPEED, trajectory=seg)
        # recorded route complete — stop
        while True:
            take SetBrakeAction(1.0)
    else:
        # route did not map onto network lanes
        do FollowLaneBehavior(target_speed=NPC30_SPEED)

behavior Npc33Behavior():
    # CARLA spawns at rest; restore the recorded speed on the first step
    take SetSpeedAction(self.initialSpeed)
    if npc33RouteLanes:
        for seg in connectedSegments(npc33RouteLanes):
            do FollowTrajectoryBehavior(target_speed=NPC33_SPEED, trajectory=seg)
        # recorded route complete — stop
        while True:
            take SetBrakeAction(1.0)
    else:
        # route did not map onto network lanes
        do FollowLaneBehavior(target_speed=NPC33_SPEED)

behavior Npc34Behavior():
    # CARLA spawns at rest; restore the recorded speed on the first step
    take SetSpeedAction(self.initialSpeed)
    if npc34RouteLanes:
        for seg in connectedSegments(npc34RouteLanes):
            do FollowTrajectoryBehavior(target_speed=NPC34_SPEED, trajectory=seg)
        # recorded route complete — stop
        while True:
            take SetBrakeAction(1.0)
    else:
        # route did not map onto network lanes
        do FollowLaneBehavior(target_speed=NPC34_SPEED)

behavior Npc35Behavior():
    # CARLA spawns at rest; restore the recorded speed on the first step
    take SetSpeedAction(self.initialSpeed)
    if npc35RouteLanes:
        for seg in connectedSegments(npc35RouteLanes):
            do FollowTrajectoryBehavior(target_speed=NPC35_SPEED, trajectory=seg)
        # recorded route complete — stop
        while True:
            take SetBrakeAction(1.0)
    else:
        # route did not map onto network lanes
        do FollowLaneBehavior(target_speed=NPC35_SPEED)

behavior Npc36Behavior():
    # CARLA spawns at rest; restore the recorded speed on the first step
    take SetSpeedAction(self.initialSpeed)
    if npc36RouteLanes:
        for seg in connectedSegments(npc36RouteLanes):
            do FollowTrajectoryBehavior(target_speed=NPC36_SPEED, trajectory=seg)
        # recorded route complete — stop
        while True:
            take SetBrakeAction(1.0)
    else:
        # route did not map onto network lanes
        do FollowLaneBehavior(target_speed=NPC36_SPEED)

behavior Npc37Behavior():
    # CARLA spawns at rest; restore the recorded speed on the first step
    take SetSpeedAction(self.initialSpeed)
    if npc37RouteLanes:
        for seg in connectedSegments(npc37RouteLanes):
            do FollowTrajectoryBehavior(target_speed=NPC37_SPEED, trajectory=seg)
        # recorded route complete — stop
        while True:
            take SetBrakeAction(1.0)
    else:
        # route did not map onto network lanes
        do FollowLaneBehavior(target_speed=NPC37_SPEED)

behavior Npc38Behavior():
    # CARLA spawns at rest; restore the recorded speed on the first step
    take SetSpeedAction(self.initialSpeed)
    if npc38RouteLanes:
        for seg in connectedSegments(npc38RouteLanes):
            do FollowTrajectoryBehavior(target_speed=NPC38_SPEED, trajectory=seg)
        # recorded route complete — stop
        while True:
            take SetBrakeAction(1.0)
    else:
        # route did not map onto network lanes
        do FollowLaneBehavior(target_speed=NPC38_SPEED)

behavior Npc39Behavior():
    # CARLA spawns at rest; restore the recorded speed on the first step
    take SetSpeedAction(self.initialSpeed)
    if npc39RouteLanes:
        for seg in connectedSegments(npc39RouteLanes):
            do FollowTrajectoryBehavior(target_speed=NPC39_SPEED, trajectory=seg)
        # recorded route complete — stop
        while True:
            take SetBrakeAction(1.0)
    else:
        # route did not map onto network lanes
        do FollowLaneBehavior(target_speed=NPC39_SPEED)

behavior Npc310Behavior():
    # CARLA spawns at rest; restore the recorded speed on the first step
    take SetSpeedAction(self.initialSpeed)
    if npc310RouteLanes:
        for seg in connectedSegments(npc310RouteLanes):
            do FollowTrajectoryBehavior(target_speed=NPC310_SPEED, trajectory=seg)
        # recorded route complete — stop
        while True:
            take SetBrakeAction(1.0)
    else:
        # route did not map onto network lanes
        do FollowLaneBehavior(target_speed=NPC310_SPEED)

behavior Npc312Behavior():
    # CARLA spawns at rest; restore the recorded speed on the first step
    take SetSpeedAction(self.initialSpeed)
    if npc312RouteLanes:
        for seg in connectedSegments(npc312RouteLanes):
            do FollowTrajectoryBehavior(target_speed=NPC312_SPEED, trajectory=seg)
        # recorded route complete — stop
        while True:
            take SetBrakeAction(1.0)
    else:
        # route did not map onto network lanes
        do FollowLaneBehavior(target_speed=NPC312_SPEED)

behavior Npc313Behavior():
    # CARLA spawns at rest; restore the recorded speed on the first step
    take SetSpeedAction(self.initialSpeed)
    if npc313RouteLanes:
        for seg in connectedSegments(npc313RouteLanes):
            do FollowTrajectoryBehavior(target_speed=NPC313_SPEED, trajectory=seg)
        # recorded route complete — stop
        while True:
            take SetBrakeAction(1.0)
    else:
        # route did not map onto network lanes
        do FollowLaneBehavior(target_speed=NPC313_SPEED)

#################################
# ENTITIES                      #
#################################

# 'with regionContainedIn None' keeps the exact recorded
# positions (default containment would reject off-road/junction spawns).
# 'with allowCollisions True' tolerates LGSVL's inflated
# bounding boxes, which overlap in dense recorded traffic
# (CARLA still refuses to spawn actors that intersect).

ego = new Car at egoSpawnPt, facing egoSpawnPt.heading,
      with blueprint 'vehicle.lincoln.mkz_2017',
      with length 5.00, with width 2.00,
      with regionContainedIn None,
      with allowCollisions True,
      with rolename 'ego_vehicle'  # Autoware drives this actor; no Scenic behavior

npc30 = new Car at npc30SpawnPt, facing npc30SpawnPt.heading,
      with blueprint 'vehicle.tesla.model3',
      with length 5.00, with width 2.00,
      with regionContainedIn None,
      with allowCollisions True,
      with initialSpeed 2.8600,  # recorded initial speed (m/s)
      with behavior Npc30Behavior()

npc33 = new Car at npc33SpawnPt, facing npc33SpawnPt.heading,
      with blueprint 'vehicle.tesla.model3',
      with length 5.00, with width 2.00,
      with regionContainedIn None,
      with allowCollisions True,
      with initialSpeed 3.0063,  # recorded initial speed (m/s)
      with behavior Npc33Behavior()

npc34 = new Car at npc34SpawnPt, facing npc34SpawnPt.heading,
      with blueprint 'vehicle.tesla.model3',
      with length 5.00, with width 2.00,
      with regionContainedIn None,
      with allowCollisions True,
      with initialSpeed 2.8115,  # recorded initial speed (m/s)
      with behavior Npc34Behavior()

npc35 = new Car at npc35SpawnPt, facing npc35SpawnPt.heading,
      with blueprint 'vehicle.tesla.model3',
      with length 5.00, with width 2.00,
      with regionContainedIn None,
      with allowCollisions True,
      with initialSpeed 2.3055,  # recorded initial speed (m/s)
      with behavior Npc35Behavior()

npc36 = new Car at npc36SpawnPt, facing npc36SpawnPt.heading,
      with blueprint 'vehicle.tesla.model3',
      with length 5.00, with width 2.00,
      with regionContainedIn None,
      with allowCollisions True,
      with initialSpeed 2.3535,  # recorded initial speed (m/s)
      with behavior Npc36Behavior()

npc37 = new Car at npc37SpawnPt, facing npc37SpawnPt.heading,
      with blueprint 'vehicle.tesla.model3',
      with length 5.00, with width 2.00,
      with regionContainedIn None,
      with allowCollisions True,
      with initialSpeed 12.2142,  # recorded initial speed (m/s)
      with behavior Npc37Behavior()

npc38 = new Car at npc38SpawnPt, facing npc38SpawnPt.heading,
      with blueprint 'vehicle.tesla.model3',
      with length 5.00, with width 2.00,
      with regionContainedIn None,
      with allowCollisions True,
      with initialSpeed 2.2730,  # recorded initial speed (m/s)
      with behavior Npc38Behavior()

npc39 = new Car at npc39SpawnPt, facing npc39SpawnPt.heading,
      with blueprint 'vehicle.tesla.model3',
      with length 5.00, with width 2.00,
      with regionContainedIn None,
      with allowCollisions True,
      with initialSpeed 2.5634,  # recorded initial speed (m/s)
      with behavior Npc39Behavior()

npc310 = new Car at npc310SpawnPt, facing npc310SpawnPt.heading,
      with blueprint 'vehicle.tesla.model3',
      with length 5.00, with width 2.00,
      with regionContainedIn None,
      with allowCollisions True,
      with initialSpeed 2.2824,  # recorded initial speed (m/s)
      with behavior Npc310Behavior()

# Npc311: parked in the recorded data (peak speed < 0.5 m/s)
npc311 = new Car at npc311SpawnPt, facing npc311SpawnPt.heading,
      with blueprint 'vehicle.tesla.model3',
      with length 5.00, with width 2.00,
      with regionContainedIn None,
      with allowCollisions True

npc312 = new Car at npc312SpawnPt, facing npc312SpawnPt.heading,
      with blueprint 'vehicle.tesla.model3',
      with length 5.00, with width 2.00,
      with regionContainedIn None,
      with allowCollisions True,
      with initialSpeed 1.9519,  # recorded initial speed (m/s)
      with behavior Npc312Behavior()

npc313 = new Car at npc313SpawnPt, facing npc313SpawnPt.heading,
      with blueprint 'vehicle.tesla.model3',
      with length 5.00, with width 2.00,
      with regionContainedIn None,
      with allowCollisions True,
      with initialSpeed 1.8451,  # recorded initial speed (m/s)
      with behavior Npc313Behavior()

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

terminate when (distance from ego to egoGoalPt) <= EGO_GOAL_RADIUS
terminate after SIM_DURATION seconds
