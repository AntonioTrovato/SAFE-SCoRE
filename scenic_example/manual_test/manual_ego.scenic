"""
manual_ego.scenic

Hand-written from CHN_Sha-4_6_T-1.xosc - NO converter involved.
Control experiment: if the ego drives here, the map is drivable and the
stuck-ego problem belongs to the converter's output (spawn density /
recorded routes) rather than to the map or the runner.

Values taken straight from the .xosc Init block:
  ego TeleportAction WorldPosition x=-20.356743 y=-1.051464 h=-0.0095 rad
  Scenic heading = degrees(h) - 90 = -90.54 deg
  AcquirePositionAction (goal)     x=6.722534  y=-23.095776
"""

param map = localPath('CHN_Sha-16_1_T-1.xodr')
param use2DMap = True
model scenic.simulators.carla.model

EGO_SPEED = 5          # m/s
GOAL_RADIUS = 5        # m

egoSpawnPt = new OrientedPoint at (-20.3567, -1.0515), facing -90.54 deg
egoGoalPt  = new OrientedPoint at (6.7225, -23.0958), facing -179.96 deg

behavior EgoDrive():
    # No `with speed` on the object: CARLA cannot spawn a moving actor.
    do FollowLaneBehavior(target_speed=EGO_SPEED)

ego = new Car at egoSpawnPt, facing egoSpawnPt.heading,
      with blueprint 'vehicle.lincoln.mkz_2017',
      with regionContainedIn None,
      with behavior EgoDrive()

terminate when (distance from ego to egoGoalPt) <= GOAL_RADIUS
terminate after 30 seconds
