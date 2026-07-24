"""
pick_place_real.py  ——  LARA 5 REAL ROBOT VERSION
============================================================
Real-robot counterpart of pick_place_sim.py.

Key differences from the simulation version:
  - switch_to_real()        instead of switch_to_simulation()
  - power_on() / power_off() to release/lock physical brakes
  - Real gripper             (set_digital_output)
  - Teach-pendant must be in automatic mode before running

BEFORE RUNNING:
  1. Confirm the physical work cell matches the layout constants.
  2. Ensure the teach pendant is in AUTOMATIC mode.
  3. Confirm the safety fence is closed and e-stop is released.
  4. Verify gripper digital output pin assignments below.

═══════════════════════════════════════════════════════════
GRIPPER GEOMETRY
═══════════════════════════════════════════════════════════

GRIPPER_LENGTH = 0.16 m  (160 mm from TCP flange to fingertip)
GRIPPER_RADIUS = 0.045 m (45 mm — widest extent from centreline)

The gripper is modelled as a vertical capsule: a cylinder of
radius GRIPPER_RADIUS running from the TCP down to the fingertip
(TCP_Z - GRIPPER_LENGTH), with a hemispherical cap at the tip.

All obstacle checks sample four points along the gripper shaft
(TCP, 1/3, 2/3, and fingertip) and test each one against a
laterally-expanded version of each obstacle box (expanded by
GRIPPER_RADIUS on all XY faces). The floor check uses the
fingertip Z, not the TCP Z.

PICK / DROP Z HEIGHT:
  OBJECT_HEIGHT = 0.10 m  (target object sits 100 mm above floor)
  PICK_Z (TCP height at grip) = OBJECT_HEIGHT + GRIPPER_LENGTH
                              = 0.10 + 0.16 = 0.26 m
  This places the fingertip exactly at 0.10 m above the floor
  when gripping, matching the object height.

═══════════════════════════════════════════════════════════
BOUNDARY & OBSTACLE ENFORCEMENT — HOW IT WORKS
═══════════════════════════════════════════════════════════

LAYER 1 — Input validation (before any robot motion):
  Every coordinate is checked against the workspace box before
  being accepted. If outside, the operator sees all four corner
  coordinates and is asked to try again. The robot never starts.
  Both pick AND drop are also checked against the camera stand
  no-go zone. If either lands inside, the operator is rejected
  and must re-enter.

LAYER 2 — Pre-flight trajectory validation:
  All three phases are computed before any motion starts.
  Every waypoint is checked — for the full gripper volume —
  against the workspace, camera stand, and extra obstacle.
  If any waypoint fails, execution is refused entirely.
  No motion has been sent to the robot at this point.

LAYER 3 — Global optimal path planning:
  For each segment, ALL valid via-point candidates are collected
  from every obstacle face upfront.  Three route types are then
  evaluated and ranked by total arc length (shortest wins):
    • Direct path           start → end
    • One via-point         start → V → end  (all candidates)
    • Two via-points        start → V1 → V2 → end  (all pairs)
  Every sub-leg of every route is validated with the full
  gripper-volume obstacle check before it can be accepted.

LAYER 4 — Stand hard-abort in main():
  main() re-checks both pick and drop even when coordinates come
  from the camera feed (bypassing the input loop).

TRANSIT MOTION:
  Transit legs (home↔lift_pick, lift_pick↔lift_drop,
  lift_drop↔home) use move_linear for smooth organic motion.
  Pick/drop approach legs use move_linear for precise vertical
  control.

CAMERA STAND NO-GO ZONE (physical + 30 mm margin + gripper radius):
  TCP checks use X 0.640-0.860  Y -0.580--0.420 (30 mm margin).
  Gripper-volume checks expand these further by GRIPPER_RADIUS.

WORKSPACE BOX / PICK-DROP ZONE (camera vision, corners, metres):
  Corner A (near-left) : X=0.250  Y=-0.370
  Corner B (near-right): X=0.250  Y= 0.000
  Corner C (far-right) : X=0.585  Y= 0.000
  Corner D (far-left)  : X=0.585  Y=-0.370
  Z range              : 0.050 -> 0.850
  Pick and drop coordinates must fall inside this box.

ARM TRANSIT:
  The arm may travel outside the camera vision box during transit
  but Z is still capped at Z_MAX (0.850 m) to protect joint limits.
  There is no hard XY cap on transit beyond the conveyor no-go zone.

CONVEYOR BELT NO-GO ZONE (physical + 50 mm safety margin):
  Physical footprint : X -0.800->0.800   Y 0.200->0.800
  + 50 mm margin     : X -0.850->0.850   Y 0.150->0.850
  Blocked at ALL Z heights (same treatment as camera stand).
  Gripper-body checks expand these further by GRIPPER_RADIUS.
═══════════════════════════════════════════════════════════
"""

import sys
import time
import copy
import math
import signal
import threading
# pymodbus/pyserial no longer used -- gripper now goes through
# r.execute_external_device_function(), same as the LoRA collector script.

try:
    import keyboard
    HAS_KEYBOARD = True
except ImportError:
    HAS_KEYBOARD = False
    pass  # print removed for MCP quiet operation

try:
    from neurapy.robot import Robot
except ImportError:
    sys.path.append(r"C:\Module-A\PythonAPI")
    from neurapy.robot import Robot

r = Robot()

EMERGENCY_STOP_ACTIVE = False

# =================================================================
# ONROBOT 2FG7 GRIPPER GEOMETRY -- REAL MEASURED MODEL
# =================================================================
# Rewritten from direct physical measurements (reference photo showing
# the three segmented zones, and a real jog-to-table-contact test).
# This REPLACES the earlier estimated/guessed geometry entirely.
#
# TABLE_Z_M: measured by jogging the BARE robot flange (no tool length
# applied -- "purely TCP") straight down until it touched the table.
# The Z reading at that contact point, in the base reference frame, was:
#   X=420.7974mm  Y=-0.15297mm  Z=-20.11582mm
# Only Z matters here -- this defines where the table surface actually
# sits in robot base coordinates, independent of gripper length.
TABLE_Z_M = 0 #-0.02011582

GRIPPER_SAFETY_LENGTH = 0.000

# -----------------------------------------------------------------
# THREE-SEGMENT STACK, measured downward from TCP (Z=0 = TCP origin)
# -----------------------------------------------------------------
# 1) FLANGE -- topmost, the Quick Changer itself (datasheet-confirmed
#    Ø71mm), sitting directly below the robot's TCP/flange face.
# 2) NECK   -- middle section, modelled as a box (not a cylinder --
#    corrected from the old model, since the real cross-section here
#    is not round).
# 3) JAW    -- bottommost, the fingers. This is the ONLY segment whose
#    horizontal footprint changes as the gripper opens/closes -- its
#    Z-height is fixed (a parallel 2-finger gripper's fingertips do not
#    move up/down as it opens, only side to side).

PICK_OFFSET_X_M = 0    # 0mm in X
PICK_OFFSET_Y_M = 0   # -0mm in Y

# --- FLANGE (Quick Changer) ---
FLANGE_DIAMETER_M = 0.071          # 71 mm, datasheet-confirmed Quick Changer diameter
FLANGE_RADIUS_M = FLANGE_DIAMETER_M / 2.0
FLANGE_LENGTH_M = 0.02843          # 28.43 mm, measured (matches QC Tool Side datasheet: 28.40mm)

# --- NECK (box, not cylinder) ---
NECK_LENGTH_DIM_M = 0.090          # 90 mm, box "length" (long horizontal axis)
NECK_THICKNESS_M  = 0.060          # 60 mm, box "thickness" (short horizontal axis)
NECK_LENGTH_M     = 0.06092        # 60.92 mm, vertical extent along Z (kept this name --
                                    # existing collision code already treats it as a pure
                                    # Z-offset, so no call-site changes needed)
NECK_CLEARANCE_M  = 0.005          # 5 mm safety margin added around the neck box

# Worst-case scalar "radius" for the neck, used only where older code
# needs a single conservative number (not an oriented box check) --
# e.g. placement-box wall clearance. Half of the longer box dimension.
NECK_RADIUS_M = NECK_LENGTH_DIM_M / 2.0   # 45 mm, conservative

# --- JAW (fingers) ---
JAW_HEIGHT_M = 0.0423               # 42.3 mm, total jaw height along Z (FIXED regardless of opening)
JAW_MAX_OPENING_M = 0.039           # 39 mm max internal opening (inward-grip finger config, per datasheet)
JAW_MIN_OPENING_M = 0.000           # 0 mm, fully closed
JAW_THICKNESS_PAD_M = 0.007         # 7 mm added on EACH side for real jaw material thickness
JAW_WIDTH_M = 0.0265                 # 27 mm, fixed width of a single jaw (perpendicular to opening direction)
JAW_CLEARANCE_M = 0.001             # 1 mm safety margin added around the jaw box
JAW_FIXED_WIDTH_M = JAW_WIDTH_M     # kept as an alias -- older code elsewhere in this file
                                     # (placement clearance, diagnostics) already reads this name
JAW_MIN_DYNAMIC_WIDTH_M = 2 * JAW_THICKNESS_PAD_M  # 14 mm floor = fully closed jaw box length

# Total flange-to-fingertip length. UNLIKE the old model, this does NOT
# change between "open" and "closed" -- the real jaw height is fixed;
# only its horizontal footprint (see get_current_jaw_width_m) changes.
GRIPPER_LENGTH = FLANGE_LENGTH_M + NECK_LENGTH_M + JAW_HEIGHT_M + 0.025 # = 0.13165 m
GRIPPER_LEN_OPEN = GRIPPER_LENGTH     # kept for compatibility with any old call sites
GRIPPER_LEN_CLOSED = GRIPPER_LENGTH   # both now identical -- see note above
ACTIVE_GRIPPER_LENGTH = GRIPPER_LENGTH

# Carried object collision model
CARRIED_OBJECT_ENABLED = False
CARRIED_OBJECT_HEIGHT_M = 0.0
CARRIED_OBJECT_WIDTH_M = 0.0
CARRIED_OBJECT_DEPTH_M = 0.0
CARRIED_OBJECT_BELOW_GRIP_M = 0.0

# Keep a single worst-case scalar radius for fixed stand/conveyor zone
# expansion and other places that just need one conservative number
# rather than a full oriented-box check.
GRIPPER_RADIUS = max(FLANGE_RADIUS_M, NECK_RADIUS_M, (JAW_MAX_OPENING_M + 2 * JAW_THICKNESS_PAD_M) / 2.0)
END_EFFECTOR_MAX_RADIUS = GRIPPER_RADIUS

# NOTE: MAX_STROKE_M, GRIPPER_PERCENT_SCALE, and GRIPPER_PERCENT_OFFSET
# are defined once, further down in the "ONROBOT 2FG7 GRIPPER CONTROL"
# section, using the real 2FG7 datasheet stroke and this hardware's
# measured width-command calibration.

# IMPORTANT:
# Jaw stroke/opening is NOT the same as total physical gripper width.
MAX_PHYSICAL_GRIPPER_WIDTH_M = JAW_MAX_OPENING_M + 2 * JAW_THICKNESS_PAD_M  # 39 + 14 = 53 mm
GRIPPER_PHYSICAL_CLOSED_LENGTH_M = 2 * JAW_THICKNESS_PAD_M                  # 14 mm, fully closed
GRIPPER_PHYSICAL_OPEN_LENGTH_M = MAX_PHYSICAL_GRIPPER_WIDTH_M               # 53 mm, fully open
GRIPPER_PHYSICAL_DEPTH_M = JAW_WIDTH_M                                      # 27 mm

MAX_PHYSICAL_GRIPPER_HALF_WIDTH_M = MAX_PHYSICAL_GRIPPER_WIDTH_M / 2.0
JAW_MAX_PHYSICAL_WIDTH_M = MAX_PHYSICAL_GRIPPER_WIDTH_M


# Placement-footprint model for the lower gripper/jaw region.
# Treat the lower gripper as a rotated rect
#  instead of a circle/square.
# Length = long direction of the open gripper. Depth = jaw body thickness direction.
MAX_FORCE_PERCENT = 20            # cap gripping force at 20%
DEFAULT_GRIPPER_SPEED = 50

# =================================================================
# OBJECT-BASED WRIST ORIENTATION MODEL
# =================================================================
# The gripper has one calibrated forward-facing home orientation.
# From the teach pendant, the forward-facing c angle is about 105.5 deg.
# This value is used as the base TCP yaw/RZ for all picks.
#
# Each object can then define:
#   object_orientation_deg        -> expected object angle on table when no camera angle is available
#   preferred_grasp_angle_deg    -> desired jaw angle relative to that object orientation
#
# If later you pass a camera-measured angle, the same function can use it instead
# of the catalogue default. This avoids adding a manual angle-selection menu.
DEFAULT_OBJECT_ORIENTATION_DEG = 90.0
DEFAULT_PREFERRED_GRASP_ANGLE_DEG = 0.0
CAMERA_ANGLE_OFFSET_DEG = 0

# Object selection catalogue. Add more objects here later.
# Dimensions are in metres.
OBJECT_CATALOGUE = {
    # Numbered object catalogue.
    # length_m  = long side / longest bounding-box side
    # width_m   = first short side used for footprint/grip planning
    # breadth_m = second short side/depth used for footprint/grip planning
    # height_m  = object height above the table, used for middle-height grip Z planning
    #
    # object_orientation_deg     = default object angle on the table if no camera angle is available
    # preferred_grasp_angle_deg = desired wrist/jaw offset for that object

    "1": {
        "label": "yellow cube",
        "name": "Yellow Cube",
        "length_m": 0.025,
        "width_m": 0.025,
        "breadth_m": 0.025,
        "height_m": 0.025,
        "object_orientation_deg": 90.0,
        "preferred_grasp_angle_deg": 0.0,
        "grasp_symmetry_deg": 90.0,   # cube gripped face-to-face: 4 equivalent angles
        "description": "Yellow cube",
    },

    "2": {
        "label": "blue cube",
        "name": "Blue Cube",
        "length_m": 0.03,
        "width_m": 0.03,
        "breadth_m": 0.03,
        "height_m": 0.03,
        "object_orientation_deg": 90.0,
        "preferred_grasp_angle_deg": 0.0,
        "grasp_symmetry_deg": 90.0,
        "description": "Blue cube",
    },

    "3": {
        "label": "green cube",
        "name": "Green Cube",
        "length_m": 0.03,
        "width_m": 0.03,
        "breadth_m": 0.03,
        "height_m": 0.03,
        "object_orientation_deg": 90.0,
        "preferred_grasp_angle_deg": 0.0,
        "grasp_symmetry_deg": 90.0,
        "description": "Green cube",
    },

    "4": {
        "label": "red cube",
        "name": "Red Cube",
        "length_m": 0.030,
        "width_m": 0.030,
        "breadth_m": 0.030,
        "height_m": 0.030,
        "object_orientation_deg": 90.0,
        "preferred_grasp_angle_deg": 0.0,
        "grasp_symmetry_deg": 90.0,
        "description": "Red cube",
    },

    "5": {
        "label": "nut",
        "name": "Nut",
        "length_m": 0.0346,
        "width_m": 0.03,
        "breadth_m": 0.03,
        "height_m": 0.017,
        "object_orientation_deg": 90.0,
        "preferred_grasp_angle_deg": 0.0,
        "grasp_symmetry_deg": 60.0,   # hex nut gripped flat-to-flat: 6 equivalent angles
        "description": "Nut",
    },

    "6": {
        "label": "black marker",
        "name": "Black Marker",
        "length_m": 0.134,
        "width_m": 0.02053,
        "breadth_m": 0.02053,
        "height_m": 0.02053,
        "object_orientation_deg": 90.0,
        "preferred_grasp_angle_deg": 0.0,
        "description": "Black marker",
    },

    "7": {
        "label": "medicine",
        "name": "Medicine",
        "length_m": 0.112,
        "width_m": 0.028,
        "breadth_m": 0.028,
        "height_m": 0.023,
        "object_orientation_deg": 90.0,
        "preferred_grasp_angle_deg": 0.0,
        "description": "Medicine",
    },

    "8": {
        "label": "sponge",
        "name": "Sponge",
        "length_m": 0.075,
        "width_m": 0.03,
        "breadth_m": 0.03,
        "height_m": 0.015,
        "object_orientation_deg": 90.0,
        "preferred_grasp_angle_deg": 0.0,
        "description": "Sponge",
    },

    "9":{
    "label": "screwdriver",
    "name": "Screwdriver",

    # Overall object footprint
    "length_m": 0.104,
    "width_m": 0.0244,      # thickest end / maximum width
    "breadth_m": 0.0244,
    "height_m": 0.0244,

    # Grasping info
    "grasp_width_m": 0.0181,        # narrower midpoint grasp area
    "preferred_grasp_region": "middle",
    "object_orientation_deg": 90.0,
    "preferred_grasp_angle_deg": 0.0,

    "description": "Asymmetric screwdriver; maximum width at thick end is 24.4 mm, preferred grasp region is the narrower middle section at around 18.1 mm."
    }
}
GRIP_EXTRA_SPACE_M = 0.000        # no extra stroke gap; grip target stays at actual object width
PRE_PICK_EXTRA_RATIO = 0.30       # open 30% wider before descending/releasing so fingers do not scrape the object
GRIP_CENTER_RATIO = 0.50          # grip from the middle height of the object
MIN_GRIP_HEIGHT_M = 0.005         # never aim lower than 5 mm above the table/floor
DROP_RELEASE_CLEARANCE_M = 0.000  # drop height is same as pickup height
PICK_HEIGHT_FINE_TUNE_M = 0      # lower pick/drop by 5 mm because latest test was still slightly high


# =================================================================
# MCP VISION INPUT + DYNAMIC AVOIDANCE SETTINGS
# =================================================================
# MCP z is NOT treated as final robot TCP Z.
# It is treated as a vision-measured object height/top/center hint.
# The robot still calculates TCP pick height using gripper length and object profile.
DEFAULT_OBJECT_HEIGHT_M = 0.040
MIN_VALID_MCP_OBJECT_Z_M = 0.003

# When the camera sends all detected objects, the chosen object is picked while
# the rest can be treated as dynamic obstacles during planning.
MCP_DYNAMIC_OBJECT_AVOIDANCE_ENABLED = True
MCP_DYNAMIC_OBJECT_AVOIDANCE_MODE = "3d"  # "xy" or "3d"

# Extra safety around detected non-target objects.
MCP_DYNAMIC_OBJECT_MARGIN_XY_M = 0.015
MCP_DYNAMIC_OBJECT_MARGIN_Z_M = 0.010

# Runtime storage for non-target detected objects.
MCP_DYNAMIC_OBSTACLES = []

# =================================================================
# MCP CAMERA PLACEMENT OCCUPANCY + DIAGNOSTIC PRINTS
# =================================================================
# Objects detected inside the placement box are treated as already placed.
# They are NOT valid pick targets, but they reserve area inside the box so
# the drop planner avoids overlapping them.
MCP_PLACEMENT_BOX_DETECTIONS = []

# Keep normal MCP operation quiet. Only these two diagnostics are printed:
#   1) first pickup coordinate
#   2) planned placement coordinate + placement-box coordinates/area
MCP_DIAGNOSTIC_PRINTS_ENABLED = True



# =================================================================
# OBJECT-SPECIFIC GRIP COMMAND CALIBRATION
# =================================================================
# Some objects need a slightly smaller commanded opening than their measured
# outer width because the real jaw opening appears wider than the calculated
# command, or because the object shape needs firmer side contact.
#
# This only affects the CLOSE/HOLD command.
# It does NOT change:
#   - MAX_STROKE_M = 0.090 command stroke reference
#   - the 6 cm -> 15 cm physical placement footprint
#   - pre-pick/release clearance opening
OBJECT_GRIP_COMMAND_SCALE = {
    "yellow cube": 0.88,
    "blue cube": 0.88,
    "green cube": 0.88,
    "red cube": 0.88,
    "medicine": 0.88,
    "screwdriver": 0.88,
    "nut": 0.88,
    "sponge": 0.934,
    "black marker": 0.88,

}

OBJECT_GRIP_COMMAND_MIN_M = {
    "yellow cube": 0.0024,
    "blue cube": 0.0029,
    "green cube": 0.0029,
    "red cube": 0.0029,
    "medicine": 0.0027,
    "screwdriver" : 0.00151, 
    "nut": 0.0029,
    "black marker" : 0.001953, 
    "sponge": 0.027,
}
# =================================================================
# HYBRID POSITION + FORCE GRIP TUNING
# =================================================================
# HYBRID_FORCE_GRIP_ENABLED is forced to False (see the full explanation
# in the "ONROBOT 2FG7 GRIPPER CONTROL" section below) -- real force/
# torque feedback is not available on this hardware, so hybrid
# contact-detection cannot work. These tuning constants are kept
# (rather than deleted) purely because average_gripper_torque() and
# gripper_grip_object_hybrid() still reference them as safe no-op
# defaults -- they have no real effect while hybrid mode is disabled.
HYBRID_GRIP_CONTACT_TORQUE_DELTA    = 20
HYBRID_GRIP_MAX_EXTRA_CLOSE_PERCENT = 8
HYBRID_GRIP_STEP_PERCENT            = 2
HYBRID_GRIP_STEP_DELAY_S            = 0.05
HYBRID_GRIP_MIN_PERCENT             = 0
HYBRID_GRIP_TORQUE_SAMPLES          = 3

# =================================================================
# CAMERA STAND — PERMANENT NO-GO ZONE
# =================================================================
STAND_X_MIN  = 0.67
STAND_X_MAX  = 0.83
STAND_Y_MIN  = -0.55
STAND_Y_MAX  = -0.45
FIXED_MARGIN = 0.03   # 30 mm structural safety buffer

_STAND_EFF_X_MIN = STAND_X_MIN - FIXED_MARGIN         # 0.640
_STAND_EFF_X_MAX = STAND_X_MAX + FIXED_MARGIN          # 0.860
_STAND_EFF_Y_MIN = STAND_Y_MIN - FIXED_MARGIN          # -0.580
_STAND_EFF_Y_MAX = STAND_Y_MAX + FIXED_MARGIN          # -0.420

_STAND_GRP_X_MIN = _STAND_EFF_X_MIN - GRIPPER_RADIUS
_STAND_GRP_X_MAX = _STAND_EFF_X_MAX + GRIPPER_RADIUS
_STAND_GRP_Y_MIN = _STAND_EFF_Y_MIN - GRIPPER_RADIUS
_STAND_GRP_Y_MAX = _STAND_EFF_Y_MAX + GRIPPER_RADIUS

# =================================================================
# CONVEYOR BELT — PERMANENT NO-GO ZONE
# =================================================================
CONV_X_MIN    = -0.800
CONV_X_MAX    =  0.800
CONV_Y_MIN    =  0.200
CONV_Y_MAX    =  0.800

_CONV_EFF_X_MIN = CONV_X_MIN - FIXED_MARGIN
_CONV_EFF_X_MAX = CONV_X_MAX + FIXED_MARGIN
_CONV_EFF_Y_MIN = CONV_Y_MIN - FIXED_MARGIN
_CONV_EFF_Y_MAX = CONV_Y_MAX + FIXED_MARGIN

_CONV_GRP_X_MIN = _CONV_EFF_X_MIN - GRIPPER_RADIUS
_CONV_GRP_X_MAX = _CONV_EFF_X_MAX + GRIPPER_RADIUS
_CONV_GRP_Y_MIN = _CONV_EFF_Y_MIN - GRIPPER_RADIUS
_CONV_GRP_Y_MAX = _CONV_EFF_Y_MAX + GRIPPER_RADIUS

# =================================================================
# CAMERA SCAN ZONE  (informational)
# =================================================================
CAM_X_MIN = 0.25
CAM_X_MAX = 0.60
CAM_Y_MIN = -0.37
CAM_Y_MAX =  0.03

# =================================================================
# WORKSPACE LIMITS
# =================================================================
X_MIN, X_MAX = 0.250,  0.585
Y_MIN, Y_MAX = -0.370,  0.000
Z_MIN, Z_MAX =  0.010,  0.850

# =================================================================
# ONROBOT 2FG7 GRIPPER CONTROL (via NeuraPy execute_external_device_function)
# =================================================================
# REPLACES the old Lebai/Modbus-RTU direct-serial control block.
#
# Why this section was rewritten:
#   The original code below this comment talked to a Lebai gripper
#   directly over raw Modbus RTU (pymodbus, holding registers like
#   REG_POSITION / REG_FORCE / REG_CUR_TORQUE). That is NOT the
#   hardware on this robot -- this robot uses an OnRobot 2FG7,
#   connected through the LARA5 controller's own External Devices
#   interface, not a raw serial link from this PC.
#
#   All gripper communication now goes through:
#       r.execute_external_device_function(PROCESS_FILE, function_name, params)
#   exactly like the working LoRA data-collection script
#   (LoRA_grasp_pregrasp_collector_v6_start_open.py) already does.
#
# IMPORTANT -- every function name below is kept IDENTICAL to the old
# version (clamp_percent, object_width_to_percent, gripper_move_percent,
# gripper_open, gripper_close, gripper_grip_object, etc.). Every other
# part of this file (trajectory planning, collision checks, pick/place
# sequencing) calls these same names and does not need to change.
#
# HONEST LIMITATION -- read before using hybrid force grip:
#   The 2FG7, as currently configured/queried through this API, does
#   NOT expose a working real force/torque readback (getMaxForce and
#   similar calls reliably return 0 -- this was verified directly on
#   this hardware). That means the old "hybrid position + force" grip
#   logic, which depended on reading rising torque to detect contact,
#   CANNOT actually detect contact on this hardware. Rather than leave
#   that logic silently doing nothing useful (or worse, always closing
#   all the way down with no real contact check -- a crush risk for
#   soft objects like the sponge), HYBRID_FORCE_GRIP_ENABLED is forced
#   to False here, and a one-time warning is printed. Normal
#   position-based gripping (gripper_grip_object) is calibrated instead
#   using the 2FG7's own GraspWorkpiece contact-stop behavior, which
#   IS real and does work (verified on this hardware).

PROCESS_FILE = "OnRobot2FG7_RTU_DEFAULT.json"

# Calibration: converts a desired real jaw GAP (mm) into the raw "width"
# command value this specific 2FG7 + RTU config actually expects.
#
# CORRECTED -- the previous values here (0.7871 / -31.4143) were an
# error: they were never actually derived from real data, and produced
# a NEGATIVE (clamped-to-0 = fully CLOSED) command at 100% open, which
# is backwards. These corrected values are a real 2-point linear fit
# from your actual logged gripper_feedback.json results:
#   screwdriver: target_gap_mm=15.0 -> commanded width=59  (real log)
#   sponge:      target_gap_mm=28.0 -> commanded width=75  (real log)
# This gives ~87.3 at full 38mm stroke, closely matching the known
# real "open" command (~85) used elsewhere in your working scripts.
# NOTE: this is still only a 2-point fit -- if you get a chance to log
# a 3rd real data point (ideally near full-open or full-closed), it's
# worth re-checking this stays linear across the whole range rather
# than assuming it does.
GRIPPER_WIDTH_CMD_SLOPE = 1.230769
GRIPPER_WIDTH_CMD_INTERCEPT = 40.538462

# Safety cap: real logged timeouts traced to commanding 100% open
# (width_cmd=87.3), which extrapolates beyond our validated calibration
# data (real logged points only go up to 28mm gap -> width 75). Capping
# ordinary opens at 80% keeps commands close to/within the validated
# range (80% -> ~78, near the confirmed-working 75).
GRIPPER_MAX_SAFE_OPEN_PERCENT = 80

# Default "resting/traveling" open percent -- used while moving between
# positions (not actively gripping a specific object), instead of
# leaving the gripper at whatever percent it was last commanded to, or
# jumping straight to a risky 100%.
GRIPPER_TRAVEL_OPEN_PERCENT = 80

HYBRID_FORCE_GRIP_ENABLED = False  # forced off -- see note above; real force
                                    # feedback is not available on this hardware
_HYBRID_WARNING_PRINTED = False

MAX_STROKE_M = 0.038              # 2FG7 datasheet: total stroke = 38 mm
GRIPPER_PERCENT_SCALE = 1
GRIPPER_PERCENT_OFFSET = 0.0

CURRENT_GRIPPER_PERCENT = 100
CURRENT_GRIPPER_FORCE = 20        # last commanded force %, mirrors MAX_FORCE_PERCENT default
CURRENT_GRIPPER_SPEED = 50        # last commanded speed %, mirrors DEFAULT_GRIPPER_SPEED


def clamp_percent(value):
    return int(max(0, min(100, round(value))))


def object_width_to_percent(object_width_m):
    """
    Convert desired real jaw opening width into a 0-100 "percent open" value,
    using the SAME percent convention the rest of this file already expects
    (0 = fully closed, 100 = fully open, scaled against MAX_STROKE_M).
    This function's math is UNCHANGED from the original -- only the
    hardware call that actually executes a move (gripper_move_percent)
    was rewritten below.
    """
    raw_percent = (object_width_m / MAX_STROKE_M) * 100.0
    calibrated_percent = raw_percent * GRIPPER_PERCENT_SCALE + GRIPPER_PERCENT_OFFSET
    return clamp_percent(calibrated_percent)


def select_object_profile_by_name(object_name):
    """
    Select object profile from OBJECT_CATALOGUE using MCP object name.
    UNCHANGED from original -- no hardware dependency here.
    """
    if object_name is None:
        raise ValueError("MCP object_name is required.")

    name = str(object_name).strip().lower()

    aliases = {
        "yellow": "yellow cube",
        "blue": "blue cube",
        "green": "green cube",
        "red": "red cube",
        "marker": "black marker",
        "black": "black marker",
        "med": "medicine",
        "medicine box": "medicine",
        "hex nut": "nut",
        "hexagonal nut": "nut",
    }
    name = aliases.get(name, name)

    match name:
        case "yellow cube":
            target_labels = {"yellow cube"}
        case "blue cube":
            target_labels = {"blue cube"}
        case "green cube":
            target_labels = {"green cube"}
        case "red cube":
            target_labels = {"red cube"}
        case "cube":
            target_labels = {"yellow cube", "blue cube", "green cube", "red cube"}
        case "nut":
            target_labels = {"nut"}
        case "black marker":
            target_labels = {"black marker"}
        case "medicine":
            target_labels = {"medicine"}
        case "sponge":
            target_labels = {"sponge"}
        case "screwdriver":
            target_labels = {"screwdriver"}
        case _:
            target_labels = {name}

    for obj in OBJECT_CATALOGUE.values():
        label = str(obj.get("label", "")).strip().lower()
        display = str(obj.get("name", "")).strip().lower()

        if label in target_labels or display in target_labels:
            return dict(obj)

    raise ValueError(f"Unsupported MCP object_name: {object_name!r}")


def percent_to_commanded_opening_m(percent):
    """Display/debug helper. UNCHANGED math from original."""
    p = (clamp_percent(percent) - GRIPPER_PERCENT_OFFSET) / max(GRIPPER_PERCENT_SCALE, 1e-6)
    return (p / 100.0) * MAX_STROKE_M


def percent_to_opening_m(percent):
    return (clamp_percent(percent) / 100.0) * MAX_STROKE_M


def gripper_length_from_percent(percent):
    """
    Vertical gripper length from flange/TCP to fingertip.

    CORRECTED from the old model: the real measured jaw section has a
    FIXED Z-height (42.3mm) regardless of how open/closed the jaws
    are -- this matches a standard parallel 2-finger gripper, where
    the fingertips move sideways as it opens/closes, not up/down.
    GRIPPER_LEN_OPEN and GRIPPER_LEN_CLOSED are now identical
    (both equal GRIPPER_LENGTH), so this always returns the same
    value -- kept as a function (rather than removed) purely so every
    existing call site elsewhere in this file keeps working unchanged.
    """
    clamp_percent(percent)  # validated for compatibility, even though unused in the result
    return GRIPPER_LENGTH


def get_object_grip_label(selected_object=None):
    """Return stable lowercase label/name for object-specific grip calibration."""
    try:
        obj = selected_object if selected_object is not None else globals().get("SELECTED_OBJECT", {})
        return str(obj.get("label", obj.get("name", ""))).strip().lower()
    except Exception:
        return ""


def calibrated_close_width_for_object(object_width_m, selected_object=None):
    """
    Return command width used for CLOSE/HOLD only. UNCHANGED math from original.
    """
    label = get_object_grip_label(selected_object)
    scale = OBJECT_GRIP_COMMAND_SCALE.get(label, 1.0)
    min_width = OBJECT_GRIP_COMMAND_MIN_M.get(label, 0.0)

    calibrated = object_width_m * scale
    calibrated = max(min_width, calibrated)

    return min(object_width_m, calibrated)


def get_pre_pick_open_percent(object_width_m):
    """
    Opening before descending. Modified to keep the gripper at a constant max
    safe opening (GRIPPER_MAX_SAFE_OPEN_PERCENT) at all times before picking,
    as requested by the user, instead of hugging the object closely.
    """
    return GRIPPER_MAX_SAFE_OPEN_PERCENT


def get_pick_close_percent(object_width_m):
    """Target gripper opening for gripping the object. UNCHANGED math from original."""
    close_width_m = calibrated_close_width_for_object(
        object_width_m,
        globals().get("SELECTED_OBJECT", None),
    )
    return object_width_to_percent(close_width_m)


def select_object_profile():
    """Operator menu for choosing the object profile. UNCHANGED from original."""
    print("\n=== Object selection ===")
    for key, obj in OBJECT_CATALOGUE.items():
        print(
            f"  {key}. {obj.get('name', key)} — {obj.get('description', '')} "
            f"(L={obj.get('length_m', 0)*1000:.1f} mm, "
            f"W={obj.get('width_m', 0)*1000:.1f} mm, "
            f"B={obj.get('breadth_m', obj.get('width_m', 0))*1000:.1f} mm, "
            f"H={obj.get('height_m', 0)*1000:.1f} mm)"
        )

    while True:
        choice = input("Select object to pick: ").strip().lower()

        if choice in OBJECT_CATALOGUE:
            selected = dict(OBJECT_CATALOGUE[choice])
            print(f"  Selected: {selected.get('name', choice)}")
            return selected

        for obj in OBJECT_CATALOGUE.values():
            if choice in {
                str(obj.get("name", "")).lower(),
                str(obj.get("label", "")).lower(),
            }:
                selected = dict(obj)
                print(f"  Selected: {selected.get('name', choice)}")
                return selected

        print("  [INPUT ERROR] Please select one of the listed object numbers.")


def _gap_mm_to_width_command(gap_mm):
    """
    Convert a desired real jaw GAP (mm) into the raw 'width' command value
    this OnRobot2FG7_RTU_DEFAULT.json config actually expects, using the
    real calibration measured on this hardware.
    """
    width_cmd = GRIPPER_WIDTH_CMD_SLOPE * gap_mm + GRIPPER_WIDTH_CMD_INTERCEPT
    return max(0.0, min(100.0, width_cmd))


def gripper_connect():
    """
    No separate serial connection needed -- the OnRobot 2FG7 is reached
    through the LARA5 controller's own connection (the global `r` Robot
    object), not a direct PC-to-gripper link.
    """
    print(f"[Gripper] Using OnRobot 2FG7 via {PROCESS_FILE} through the robot controller.")


def _call_gripper_function(function_name, params=None):
    """Thin wrapper around execute_external_device_function with consistent error printing."""
    try:
        return r.execute_external_device_function(PROCESS_FILE, function_name, params or {})
    except Exception as e:
        print(f"[Gripper ERROR] '{function_name}' failed: {e}")
        raise


def wait_gripper_done(timeout=10, target_percent=None, tolerance=3):
    """
    Poll the 2FG7's own status readback until it reports done.

    FIXED (two real bugs found from actual hardware logs):

    1. The old width-comparison fallback compared current_width
       (returnCurrentWidth) against target_width_cmd directly. Real
       logged data shows returnCurrentWidth reports on the device's own
       raw internal encoder scale (~821, ~847 for commands of 82, 85 --
       roughly 10x larger), while target_width_cmd is on our small
       calibrated command scale (~59-87). These can NEVER be within
       `tolerance` of each other -- this branch was dead code that
       could never trigger true. Removed rather than guess an unverified
       scale factor (we got burned once already assuming an
       unverified calibration -- not repeating that here).

    2. This used to raise TimeoutError on timeout, which propagated all
       the way up and aborted the ENTIRE pick_and_place_object call --
       even though `close` moves have real logged evidence of
       physically succeeding even when status-based completion
       detection can't confirm it (status often just reads "Idle" both
       before and during a plain positional close, unlike
       GraspWorkpiece's real "Grip detected" event). A DETECTION
       failure should not be treated as a MOVE failure. Now returns
       False instead of raising -- the caller logs a warning and
       continues, trusting the command was sent successfully (no
       earlier exception from the actual `close`/`GraspWorkpiece`
       call itself).
    """
    start = time.time()
    while time.time() - start < timeout:
        try:
            result = _call_gripper_function("returnStatus")
            status = str(result.get("getStatus", "")).strip().lower()
            if status in ("grip detected", "idle", "completed"):
                return True
        except Exception as e:
            print(f"[Gripper WARN] Status read failed: {e}")

        time.sleep(0.2)

    print("[Gripper WARN] Could not confirm completion via status within "
          f"{timeout}s -- continuing anyway (command itself did not error).")
    return False


def gripper_startup():
    """
    Initialise the gripper. Uses the device's own Init function if
    available; wrapped defensively since not every process JSON exposes
    the same function set, and startup should not hard-crash the whole
    robot script over a best-effort init call.
    """
    gripper_connect()
    print("[Gripper] Attempting device Init...")
    try:
        _call_gripper_function("Init")
    except Exception as e:
        print(f"[Gripper] Init call failed or unsupported, continuing anyway: {e}")
    time.sleep(0.3)

    print(f"[Gripper] Setting default force cap to {MAX_FORCE_PERCENT}%...")
    gripper_set_force(MAX_FORCE_PERCENT)

    gripper_self_test()


def gripper_set_force(force=MAX_FORCE_PERCENT):
    """
    The 2FG7's API takes force as a parameter on each move call, not as a
    standalone register write. This just stores the value to be used by
    the next gripper_move_percent call, preserving the old call pattern
    used elsewhere in this file.
    """
    global CURRENT_GRIPPER_FORCE
    CURRENT_GRIPPER_FORCE = clamp_percent(min(force, MAX_FORCE_PERCENT))


def gripper_set_speed(speed=DEFAULT_GRIPPER_SPEED):
    """Same as gripper_set_force -- stored for the next move call."""
    global CURRENT_GRIPPER_SPEED
    CURRENT_GRIPPER_SPEED = clamp_percent(speed)


def gripper_move_percent(position_percent, force=MAX_FORCE_PERCENT, speed=DEFAULT_GRIPPER_SPEED):
    """
    Real hardware call: converts the requested percent-open into the
    calibrated width command, then calls the 2FG7's 'close' function
    (position-mode move -- no contact-detection wait, matches the
    behaviour verified safe for this hardware, including soft objects).
    """
    global CURRENT_GRIPPER_PERCENT
    position_percent = clamp_percent(position_percent)
    force = clamp_percent(min(force, MAX_FORCE_PERCENT))
    speed = clamp_percent(speed)

    gap_mm = percent_to_commanded_opening_m(position_percent) * 1000
    width_cmd = _gap_mm_to_width_command(gap_mm)

    print(
        f"[Gripper] Move to {position_percent}% open "
        f"({gap_mm:.1f} mm calibrated gap -> width_cmd={width_cmd:.1f}), "
        f"force={force}%, speed={speed}%"
    )

    _call_gripper_function("close", {"width": width_cmd, "speed": speed, "force": force})
    wait_gripper_done(timeout=10, target_percent=position_percent)

    CURRENT_GRIPPER_PERCENT = position_percent
    print("[Gripper] Move complete")


def gripper_open():
    """
    Opens to GRIPPER_TRAVEL_OPEN_PERCENT (80%), NOT 100%. Real logged
    timeouts traced to commanding 100% open (width_cmd=87.3), which
    extrapolates beyond validated calibration data. 80% stays close to
    the confirmed-working real data point (75 at 28mm gap).
    """
    gripper_move_percent(GRIPPER_TRAVEL_OPEN_PERCENT, force=MAX_FORCE_PERCENT, speed=60)


def gripper_self_test():
    """
    One-time open -> close -> open cycle, meant to be run once at
    program startup (after gripper_startup()). Purpose:
      1. Confirms the gripper actually responds before real operations
         start, rather than discovering a communication problem
         mid-pick.
      2. "Wakes up"/primes the device with real motion commands right
         after Init, in case a fresh Init alone isn't enough for the
         first real move to respond promptly.
    Not wrapped in a blanket try/except -- if this fails, you want to
    know immediately at startup, not partway through a real pick.
    """
    print("[Gripper] Running startup self-test (open -> close -> open)...")
    gripper_open()
    time.sleep(0.3)
    gripper_close()
    time.sleep(0.3)
    gripper_open()
    print("[Gripper] Self-test complete.")


def gripper_close():
    gripper_move_percent(0, force=MAX_FORCE_PERCENT, speed=40)


def gripper_open_for_object(object_width_m):
    pre_percent = get_pre_pick_open_percent(object_width_m)
    gripper_move_percent(pre_percent, force=MAX_FORCE_PERCENT, speed=60)


def gripper_grip_object_plain(object_width_m):
    """Plain position-based grip -- the real, working grip path on this hardware."""
    close_width_m = calibrated_close_width_for_object(
        object_width_m,
        globals().get("SELECTED_OBJECT", None),
    )
    close_percent = object_width_to_percent(close_width_m)

    if abs(close_width_m - object_width_m) > 0.0005:
        print(
            f"[Grip calibration] Commanding close width {close_width_m*1000:.1f} mm "
            f"for measured object grip width {object_width_m*1000:.1f} mm"
        )

    #gripper_move_percent(close_percent, force=MAX_FORCE_PERCENT, speed=40)
    gap_mm = percent_to_commanded_opening_m(close_percent) * 1000
    
    # Apply dynamic gap reduction for retries (if requested by Velkan/agent)
    selected_object = globals().get("SELECTED_OBJECT", {})
    if selected_object:
        reduction_percent = float(selected_object.get("_gap_mm_reduction_percent", 0.0))
        if reduction_percent > 0.0:
            original_gap = gap_mm
            gap_mm = gap_mm * (1.0 - (reduction_percent / 100.0))
            print(f"[Grip Retrying] Applying {reduction_percent}% reduction to gap_mm: {original_gap:.1f}mm -> {gap_mm:.1f}mm")

    width_cmd = _gap_mm_to_width_command(gap_mm)
    try:
        #NEW: proactively check for a lingering error before attempting,
        # rather than only discovering it via a failed call.
        if r.is_external_device_in_error(PROCESS_FILE, ignore_no_connection=True):
            print("[Grip] Device already in error state -- clearing before attempting grip.")
            r.reset_external_device_error(PROCESS_FILE, ignore_no_connection=True)

        result = _call_gripper_function(
            "GraspWorkpiece",
            {"width": width_cmd, "speed": 40, "force": MAX_FORCE_PERCENT},
            )
        status = str(result.get("getStatus", "")).strip().lower()
        print(f"[Grip] GraspWorkpiece result status: {status!r}")
        return status == "grip detected"
    except Exception as e:
        print(f"[Grip WARNING] GraspWorkpiece did not confirm contact: {e}")
        try:
            r.reset_errors()
            print("[Grip Recovery] Step 1/2: reset_errors() OK")

            device_reset = r.reset_external_device_error(PROCESS_FILE, ignore_no_connection=True)
            print(f"[Grip Recovery] Step 2/2: reset_external_device_error() -> {device_reset}")

        except Exception as reset_e:
            print(f"[Grip WARNING] Full recovery failed partway through: {reset_e}")
        return False


def gripper_grip_object(object_width_m):
    """Grip object -- always uses the plain grip path with real
    GraspWorkpiece contact detection (hybrid force-based grip removed;
    real force/torque feedback was never available on this hardware)."""
    return gripper_grip_object_plain(object_width_m)


def gripper_release_object(object_width_m):
    """
    Compact release inside the placement box. UNCHANGED logic from
    original -- only the underlying move call was rewritten.
    """
    release_percent = object_width_to_percent(
        object_width_m * (1.0 + PRE_PICK_EXTRA_RATIO)
    )

    gripper_move_percent(
        release_percent,
        force=MAX_FORCE_PERCENT,
        speed=60
    )


def gripper_shutdown():
    """
    No separate connection to close -- the OnRobot 2FG7 is reached
    through the robot controller's own connection, not a direct serial
    link owned by this script.
    """
    print("[Gripper] No separate gripper connection to close (using robot controller link).")

# =================================================================
# EMERGENCY STOP
# =================================================================
def _workspace_box_message():
    return (
        "\n  Workspace boundary corners (metres):\n"
        f"    Corner A (near-left)  X={X_MIN:.3f}  Y={Y_MIN:.3f}\n"
        f"    Corner B (near-right) X={X_MIN:.3f}  Y={Y_MAX:.3f}\n"
        f"    Corner C (far-right)  X={X_MAX:.3f}  Y={Y_MAX:.3f}\n"
        f"    Corner D (far-left)   X={X_MAX:.3f}  Y={Y_MIN:.3f}\n"
        f"    Z range               {Z_MIN:.3f} -> {Z_MAX:.3f}\n"
        f"  Pick coordinates must fall INSIDE this box.\n"
        f"  Auto drop coordinates may be outside this box if they are inside the fixed placement box."
    )


def _stand_box_message():
    return (
        "\n  Camera stand no-go zone (physical + 30 mm safety margin):\n"
        f"    TCP-level:    X {_STAND_EFF_X_MIN:.3f} -> {_STAND_EFF_X_MAX:.3f}"
        f"   Y {_STAND_EFF_Y_MIN:.3f} -> {_STAND_EFF_Y_MAX:.3f}\n"
        f"    Gripper-body: X {_STAND_GRP_X_MIN:.3f} -> {_STAND_GRP_X_MAX:.3f}"
        f"   Y {_STAND_GRP_Y_MIN:.3f} -> {_STAND_GRP_Y_MAX:.3f}\n"
        f"    Blocked at ALL Z heights.\n"
        f"  Your coordinates must fall OUTSIDE the TCP-level zone."
    )



# =================================================================
# SHARED COLLISION ZONE HELPERS
# =================================================================
# These helpers remove duplicated "x_min <= x <= x_max and y_min <= y <= y_max"
# logic while keeping the old public function names below.
# Old names such as point_in_stand(), gripper_in_stand(), point_in_conveyor(),
# and gripper_in_conveyor() are kept as wrappers so existing planner code
# continues to work unchanged.

def _point_in_rect_xy(x, y, x_min, x_max, y_min, y_max):
    """Generic XY rectangle inclusion check."""
    return x_min <= x <= x_max and y_min <= y <= y_max


def _stand_zone_contains_xy(x, y, expanded_for_gripper=False):
    """
    Stand-zone check.

    expanded_for_gripper=False:
        Uses TCP-level stand zone.
    expanded_for_gripper=True:
        Uses gripper-body expanded stand zone.
    """
    if expanded_for_gripper:
        return _point_in_rect_xy(
            x, y,
            _STAND_GRP_X_MIN, _STAND_GRP_X_MAX,
            _STAND_GRP_Y_MIN, _STAND_GRP_Y_MAX,
        )

    return _point_in_rect_xy(
        x, y,
        _STAND_EFF_X_MIN, _STAND_EFF_X_MAX,
        _STAND_EFF_Y_MIN, _STAND_EFF_Y_MAX,
    )


def _conveyor_zone_contains_xy(x, y, expanded_for_gripper=False):
    """
    Conveyor-zone check.

    expanded_for_gripper=False:
        Uses TCP-level conveyor zone.
    expanded_for_gripper=True:
        Uses gripper-body expanded conveyor zone.
    """
    if PLACEMENT_BOX_OVERRIDES_CONVEYOR and point_in_placement_box_xy(x, y):
        return False

    if expanded_for_gripper:
        return _point_in_rect_xy(
            x, y,
            _CONV_GRP_X_MIN, _CONV_GRP_X_MAX,
            _CONV_GRP_Y_MIN, _CONV_GRP_Y_MAX,
        )

    return _point_in_rect_xy(
        x, y,
        _CONV_EFF_X_MIN, _CONV_EFF_X_MAX,
        _CONV_EFF_Y_MIN, _CONV_EFF_Y_MAX,
    )

# =================================================================
# HEIGHTS & SPEED
# =================================================================
DEFAULT_OBJECT_HEIGHT = DEFAULT_OBJECT_HEIGHT_M
OBJECT_HEIGHT  = DEFAULT_OBJECT_HEIGHT  # fallback only; MCP object height is applied per selected object
PICK_Z         = OBJECT_HEIGHT + GRIPPER_LENGTH
DROP_Z         = OBJECT_HEIGHT + GRIPPER_LENGTH
SAFE_HEIGHT    = 0.30
LINEAR_SPEED          = 0.05   # m/s  (was 0.1)
LINEAR_ACCELERATION   = 0.05   # m/s^2
LINEAR_JERK           = 500.0
ROTATION_SPEED        = 0.90   # rad/s
ROTATION_ACCELERATION = 1.57
ROTATION_JERK         = 500.0
ROBOT_SPEED_OVERRIDE  = 0.80   # 0-1, floor for robot override
TRANSIT_HEIGHT = SAFE_HEIGHT

# =================================================================
# OPTIONAL EXTRA OBSTACLE
# =================================================================
HAS_EXTRA_OBS = False
OBS_X = OBS_Y = OBS_W = OBS_D = OBS_H = 0.0
OBS_HW = OBS_HD = 0.0
OBS_MARGIN = 0.005

_BYPASS_EXTRA_OBS = False

# =================================================================
# PATH PLANNER SETTINGS
# =================================================================
DETOUR_CLEARANCE = 0.01   # metres

# =================================================================
# FIXED PLACEMENT BOX / SMART DROP-ZONE ALLOCATOR
# =================================================================
PLACEMENT_BOX_ENABLED = True
PLACEMENT_BOX_SHAPE = "rectangle"
PLACEMENT_BOX_OVERRIDES_CONVEYOR = True

PLACEMENT_BOX_CORNERS = [
    (0.586, 0.055),
    (0.516, 0.28),
    (0.252, 0.28),
    (0.248, 0.055),
]

PLACEMENT_BOX_X_MIN = min(p[0] for p in PLACEMENT_BOX_CORNERS)
PLACEMENT_BOX_X_MAX = max(p[0] for p in PLACEMENT_BOX_CORNERS)
PLACEMENT_BOX_Y_MIN = min(p[1] for p in PLACEMENT_BOX_CORNERS)
PLACEMENT_BOX_Y_MAX = max(p[1] for p in PLACEMENT_BOX_CORNERS)

BOX_WALL_THICKNESS_M = 0.005
PLACEMENT_WALL_CLEARANCE_M = 0.010  # object gap from wall target minimum = 10 mm
BOX_BASE_THICKNESS_M = 0.005

# Physical box height above the base/table.
BOX_WALL_HEIGHT_M = 0.070

# Extra safety after checking which gripper segment can reach the box wall.
SEGMENTED_BOX_WALL_MARGIN_M = 0.010

# Minimum clearance even if only the jaws/fingers are near the wall.
MIN_BOX_GRIPPER_SIDE_CLEARANCE_M = 0.008

# For placement slot allocation, do not shrink the box by the full physical
# 150 mm gripper width. The gripper does not permanently occupy the box.
# This cap keeps planning practical while still leaving wall clearance.
MAX_PLACEMENT_SEGMENT_CLEARANCE_M = 0.035

# Separate caps for placement allocation.
# X cap is smaller so objects can be placed closer to the lower-X wall.
MAX_PLACEMENT_SEGMENT_CLEARANCE_X_M = 0.010
MAX_PLACEMENT_SEGMENT_CLEARANCE_Y_M = 0.020


PLACEMENT_INNER_MARGIN_M = BOX_WALL_THICKNESS_M + PLACEMENT_WALL_CLEARANCE_M
PLACEMENT_INNER_X_MIN = PLACEMENT_BOX_X_MIN + PLACEMENT_INNER_MARGIN_M
PLACEMENT_INNER_X_MAX = PLACEMENT_BOX_X_MAX - PLACEMENT_INNER_MARGIN_M
PLACEMENT_INNER_Y_MIN = PLACEMENT_BOX_Y_MIN + PLACEMENT_INNER_MARGIN_M
PLACEMENT_INNER_Y_MAX = PLACEMENT_BOX_Y_MAX - PLACEMENT_INNER_MARGIN_M

PLACEMENT_OBJECT_GAP_M = 0.008
PLACEMENT_GRID_STEP_M = 0.010

# Placement packing target:
# Keep object footprint about 1-2 cm from the wall when packing near the wall.
PLACEMENT_WALL_GAP_MIN_M = 0.010
PLACEMENT_WALL_GAP_MAX_M = 0.020


# =================================================================
# SMART PLACEMENT LOOKAHEAD SETTINGS
# =================================================================
SMART_PLACEMENT_ENABLED = True
SMART_PLACEMENT_GRID_STEP_M = 0.005

SMART_CORNER_WEIGHT = 8.0
SMART_CENTER_AVOID_WEIGHT = 0.04
SMART_OPEN_SPACE_WEIGHT = 2.5
SMART_FUTURE_FIT_WEIGHT = 12.0
SMART_WALL_GAP_UNDER_WEIGHT = 100.0
SMART_WALL_GAP_OVER_WEIGHT = 8.0
SMART_EXISTING_OBJECT_SPREAD_WEIGHT = 0.05

# Try rotating the wrist/gripper for better placement packing.
# These are relative offsets from the object's preferred grasp angle.
PLACEMENT_ANGLE_OFFSETS_DEG = [-30, 0, 30]
PLACEMENT_ANGLE_DEVIATION_WEIGHT = 0.002   # penalty per degree away from the object's natural angle

# Permanent occupied-object footprint margin.
# Keep this small. The gripper release opening is temporary and should not
# permanently consume packing space.
PLACEMENT_FOOTPRINT_MARGIN_M = 0.005

# Extra wall safety is defined once above before PLACEMENT_INNER_MARGIN_M.

# Approximate horizontal safety space needed by the lower gripper body while lowering/releasing.
PLACEMENT_GRIPPER_SIDE_CLEARANCE_M = (JAW_FIXED_WIDTH_M / 2.0) + 0.010

# Reserve space for object portion hanging below the grasp point.
PLACEMENT_CARRIED_OBJECT_MARGIN_M = 0.005

PLACED_OBJECTS = []

# Gripper release opening is temporary and is handled by gripper_release_object().
# It is not permanently reserved by the box-packing footprint.

# =================================================================
# HOME POSITION
# =================================================================
HOME_X  = 0.419999
HOME_Y  = 0.0
HOME_Z  = 0.48

HOME_RX = 179.93608
HOME_RY = 0.04243
HOME_RZ = -158.16
GRIPPER_RZ_OFFSET = 0  # Offset to make physical gripper reach desired angle




# =================================================================
# RUNTIME DEFAULTS FOR STATIC ANALYSIS / EDITOR WARNINGS
# =================================================================
# These values are placeholders so VS Code/Pylance knows the names exist.
# The real values are overwritten for each object inside set_active_pick_item().

MOVE_X = 0.0
MOVE_Y = 0.0
DROP_X = 0.0
DROP_Y = 0.0

PICK_TARGET_X = 0.0
PICK_TARGET_Y = 0.0



# -----------------------------------------------------------------
# SECTION 1 — BASIC HELPERS
# -----------------------------------------------------------------

def clamp(val, lo, hi):
    return max(min(val, hi), lo)

def get_active_gripper_length():
    """Return the gripper length currently used for floor/contact validation."""
    return ACTIVE_GRIPPER_LENGTH

def interpolate_waypoints(start, end, n):
    wps = []
    for i in range(1, n):
        t  = i / n
        wp = [
            start[0] + t * (end[0] - start[0]),
            start[1] + t * (end[1] - start[1]),
            start[2] + t * (end[2] - start[2]),
        ] + list(start[3:])
        wps.append(wp)
    return wps

def _normalise_angle_deg(angle):
    """Keep angle in [-180, 180] so orientation math stays stable."""
    while angle > 180:
        angle -= 360
    while angle < -180:
        angle += 360
    return angle


# -----------------------------------------------------------------
# SECTION 1b — COMPATIBILITY ALIASES
# -----------------------------------------------------------------
# Some earlier versions used different helper names. These wrappers keep
# the code stable if a call site uses an older name.

def _compat_missing_helper_error(name):
    raise RuntimeError(
        f"Required helper function '{name}' is missing. "
        "Check that the trajectory planning section was not deleted."
    )


def get_current_tool_rz_deg():
    """Return the TCP yaw/RZ currently used by the planner.

    This script normally locks TCP orientation to HOME_RZ. If you later add
    auto-orientation again, set PLANNED_RZ_DEG and this function will use it.
    """
    return globals().get("PLANNED_RZ_DEG", HOME_RZ)

def get_current_jaw_width_m():
    """
    Return the current jaw box's dynamic dimension (the direction the
    jaws open/close along), used for the jaw's oriented-rectangle
    collision check.

    New exact formula (replacing the old rough physical-width
    extrapolation): current internal opening + a fixed 7mm padding on
    EACH side for real jaw material thickness (14mm total), clamped to
    the real max opening (39mm) + padding.
    """
    try:
        internal_opening = percent_to_opening_m(CURRENT_GRIPPER_PERCENT)
    except Exception:
        internal_opening = percent_to_opening_m(PICK_CLOSE_PERCENT) if 'PICK_CLOSE_PERCENT' in globals() else 0.020

    internal_opening = max(JAW_MIN_OPENING_M, min(JAW_MAX_OPENING_M, internal_opening))
    return internal_opening + (2 * JAW_THICKNESS_PAD_M)


def _circle_segment_hits_box(tcp_x, tcp_y, tcp_z, radius_m, z_top_offset_m, z_bottom_offset_m):
    """Check one vertical circular tool segment against the extra obstacle.

    z_top_offset_m / z_bottom_offset_m are measured downward from TCP.
    Example: flange from TCP to TCP-0.030 m.
    """
    if z_bottom_offset_m <= z_top_offset_m:
        return False

    seg_top_z = tcp_z - z_top_offset_m
    seg_bottom_z = tcp_z - z_bottom_offset_m
    obs_top_z = OBS_H + OBS_MARGIN

    # If the whole segment is above the obstacle, it cannot hit it.
    if seg_bottom_z > obs_top_z:
        return False

    in_x = abs(tcp_x - OBS_X) < (OBS_HW + OBS_MARGIN + radius_m)
    in_y = abs(tcp_y - OBS_Y) < (OBS_HD + OBS_MARGIN + radius_m)
    return in_x and in_y

def _oriented_jaw_hits_box(tcp_x, tcp_y, tcp_z):
    """Check lower rectangular jaws against the extra obstacle.

    Rewritten with the real measured jaw dimensions:
      - fixed jaw width = 27 mm (JAW_WIDTH_M), perpendicular to opening
      - dynamic opening dimension = current commanded opening + 14 mm
        total padding (7 mm each side, real jaw material thickness)
      - 1 mm clearance (JAW_CLEARANCE_M) added around the whole box
      - fixed Z-height = 42.3 mm (JAW_HEIGHT_M), does NOT change with opening
    The rectangle is converted into its world-frame AABB extents for a fast,
    conservative collision check against the obstacle box.
    """
    active_len = get_active_gripper_length()
    jaw_start_offset = min(active_len, FLANGE_LENGTH_M + NECK_LENGTH_M)
    jaw_end_offset = active_len

    # No lower jaw section available if dimensions are not physically possible.
    if jaw_end_offset <= jaw_start_offset:
        return False

    jaw_top_z = tcp_z - jaw_start_offset
    jaw_bottom_z = tcp_z - jaw_end_offset
    obs_top_z = OBS_H + OBS_MARGIN

    # If the complete jaw section is above the obstacle, it cannot collide.
    if jaw_bottom_z > obs_top_z:
        return False

    rz = math.radians(get_current_tool_rz_deg())
    c = abs(math.cos(rz))
    s = abs(math.sin(rz))

    # Local jaw half-extents: fixed 27mm width, dynamic opening dimension,
    # both padded by the 1mm jaw clearance.
    hx_local = (JAW_WIDTH_M / 2.0) + JAW_CLEARANCE_M
    hy_local = (get_current_jaw_width_m() / 2.0) + JAW_CLEARANCE_M

    # Convert the oriented rectangle into a conservative axis-aligned envelope.
    hx_world = c * hx_local + s * hy_local
    hy_world = s * hx_local + c * hy_local

    in_x = abs(tcp_x - OBS_X) < (OBS_HW + OBS_MARGIN + hx_world)
    in_y = abs(tcp_y - OBS_Y) < (OBS_HD + OBS_MARGIN + hy_world)
    return in_x and in_y


def _oriented_neck_hits_box(tcp_x, tcp_y, tcp_z):
    """Check the neck section against the extra obstacle.

    NEW: the neck is now modelled as a rectangular box (90mm x 60mm),
    not a cylinder -- corrected from the old model, since the real
    cross-section here is not round. Uses the same yaw-rotated
    rectangle -> conservative AABB approach as the jaw check, plus a
    5mm clearance margin (NECK_CLEARANCE_M).
    """
    active_len = get_active_gripper_length()
    neck_start_offset = min(active_len, FLANGE_LENGTH_M)
    neck_end_offset = min(active_len, FLANGE_LENGTH_M + NECK_LENGTH_M)

    if neck_end_offset <= neck_start_offset:
        return False

    neck_top_z = tcp_z - neck_start_offset
    neck_bottom_z = tcp_z - neck_end_offset
    obs_top_z = OBS_H + OBS_MARGIN

    if neck_bottom_z > obs_top_z:
        return False

    rz = math.radians(get_current_tool_rz_deg())
    c = abs(math.cos(rz))
    s = abs(math.sin(rz))

    hx_local = (NECK_LENGTH_DIM_M / 2.0) + NECK_CLEARANCE_M
    hy_local = (NECK_THICKNESS_M / 2.0) + NECK_CLEARANCE_M

    hx_world = c * hx_local + s * hy_local
    hy_world = s * hx_local + c * hy_local

    in_x = abs(tcp_x - OBS_X) < (OBS_HW + OBS_MARGIN + hx_world)
    in_y = abs(tcp_y - OBS_Y) < (OBS_HD + OBS_MARGIN + hy_world)
    return in_x and in_y


def segmented_gripper_in_extra_obs(tcp_x, tcp_y, tcp_z):
    """Segmented end-effector collision check against the optional obstacle.

    Checks:
      1) flange as a circle (Ø71mm, the real Quick Changer diameter),
      2) neck as a yaw-rotated rectangle (90mm x 60mm box, NOT a cylinder
         -- corrected from the old model),
      3) jaws as a yaw-rotated rectangle (27mm fixed x dynamic opening+14mm).
    """
    active_len = get_active_gripper_length()

    flange_top = 0.0
    flange_bottom = min(active_len, FLANGE_LENGTH_M)

    if _circle_segment_hits_box(tcp_x, tcp_y, tcp_z, FLANGE_RADIUS_M, flange_top, flange_bottom):
        return True

    if _oriented_neck_hits_box(tcp_x, tcp_y, tcp_z):
        return True

    if _oriented_jaw_hits_box(tcp_x, tcp_y, tcp_z):
        return True

    return False

def carried_object_hits_extra_obs(tcp_x, tcp_y, tcp_z):
    """
    Collision check for the object being carried after gripping.

    If the cube is gripped at its middle, the lower half of the cube still hangs
    below the gripper contact point. This function checks that carried cube
    volume against the optional obstacle.
    """
    if not HAS_EXTRA_OBS or not CARRIED_OBJECT_ENABLED or _BYPASS_EXTRA_OBS:
        return False

    # Grip/contact height in world Z
    grip_contact_z = tcp_z - get_active_gripper_length()

    # Carried object extends below and above the grip point
    obj_bottom_z = grip_contact_z - CARRIED_OBJECT_BELOW_GRIP_M
    obj_top_z = obj_bottom_z + CARRIED_OBJECT_HEIGHT_M

    # If object is fully above obstacle, no collision
    if obj_bottom_z > OBS_H + OBS_MARGIN:
        return False

    # Simple box model for the carried cube/object
    obj_half_x = CARRIED_OBJECT_WIDTH_M / 2.0
    obj_half_y = CARRIED_OBJECT_DEPTH_M / 2.0

    in_x = abs(tcp_x - OBS_X) < (OBS_HW + OBS_MARGIN + obj_half_x)
    in_y = abs(tcp_y - OBS_Y) < (OBS_HD + OBS_MARGIN + obj_half_y)
    in_z = obj_top_z > TABLE_Z_M and obj_bottom_z < (OBS_H + OBS_MARGIN)

    return in_x and in_y and in_z
# -----------------------------------------------------------------
# SECTION 2 — GRIPPER-VOLUME OBSTACLE & WORKSPACE CHECKS
# -----------------------------------------------------------------

def _gripper_shaft_z_samples(tcp_z):
    return [
        tcp_z,
        tcp_z - get_active_gripper_length() / 3,
        tcp_z - 2 * get_active_gripper_length() / 3,
        tcp_z - get_active_gripper_length(),
    ]

def gripper_in_stand(tcp_x, tcp_y, tcp_z):  # tcp_z unused: stand blocked at ALL heights
    return _stand_zone_contains_xy(tcp_x, tcp_y, expanded_for_gripper=True)

def gripper_in_conveyor(tcp_x, tcp_y, tcp_z):  # tcp_z unused: conyeor blocked at ALL heights
    return _conveyor_zone_contains_xy(tcp_x, tcp_y, expanded_for_gripper=True)

def gripper_in_extra_obs(tcp_x, tcp_y, tcp_z):
    if not HAS_EXTRA_OBS or _BYPASS_EXTRA_OBS:
        return False
    return segmented_gripper_in_extra_obs(tcp_x, tcp_y, tcp_z)

def _mcp_dynamic_obstacle_half_extents(obstacle):
    """
    Return conservative half extents for a detected non-target object.
    """
    length = float(obstacle.get("length_m", 0.04))
    width = float(obstacle.get("width_m", 0.04))
    breadth = float(obstacle.get("breadth_m", width))

    half_x = max(length, width) / 2.0 + MCP_DYNAMIC_OBJECT_MARGIN_XY_M
    half_y = max(width, breadth) / 2.0 + MCP_DYNAMIC_OBJECT_MARGIN_XY_M

    return half_x, half_y


def mcp_point_in_dynamic_obstacle(px, py, pz=None):
    """
    Dynamic obstacle check from MCP camera detections.

    Mode:
        xy  -> avoid detected objects by XY footprint only.
        3d  -> avoid detected objects by XY footprint + height range.
    """
    if not MCP_DYNAMIC_OBJECT_AVOIDANCE_ENABLED:
        return False

    if not MCP_DYNAMIC_OBSTACLES:
        return False

    for obstacle in MCP_DYNAMIC_OBSTACLES:
        ox = float(obstacle.get("x", 0.0))
        oy = float(obstacle.get("y", 0.0))
        half_x, half_y = _mcp_dynamic_obstacle_half_extents(obstacle)

        in_xy = (
            abs(px - ox) <= half_x
            and abs(py - oy) <= half_y
        )

        if not in_xy:
            continue

        if MCP_DYNAMIC_OBJECT_AVOIDANCE_MODE == "xy":
            return True

        # 3D mode: object blocks only near its real physical height.
        if pz is None:
            return True

        obj_height = float(obstacle.get("height_m", DEFAULT_OBJECT_HEIGHT_M))
        bottom_z = TABLE_Z_M
        top_z = TABLE_Z_M + obj_height + MCP_DYNAMIC_OBJECT_MARGIN_Z_M

        if bottom_z <= pz <= top_z:
            return True

    return False


def gripper_hits_obstacle(tcp_x, tcp_y, tcp_z):
    return (
        gripper_in_stand(tcp_x, tcp_y, tcp_z)
        or gripper_in_conveyor(tcp_x, tcp_y, tcp_z)
        or gripper_in_extra_obs(tcp_x, tcp_y, tcp_z)
        or carried_object_hits_extra_obs(tcp_x, tcp_y, tcp_z)
        or mcp_point_in_dynamic_obstacle(tcp_x, tcp_y, tcp_z)
    )


def gripper_in_workspace(tcp_x, tcp_y, tcp_z):
    tcp_ok = (X_MIN <= tcp_x <= X_MAX and
              Y_MIN <= tcp_y <= Y_MAX and
              Z_MIN <= tcp_z <= Z_MAX)
    fingertip_ok = (tcp_z - get_active_gripper_length()) >= (TABLE_Z_M + 0.002)
    return tcp_ok and fingertip_ok

def gripper_in_transit_bounds(tcp_x, tcp_y, tcp_z):
    z_ok = Z_MIN <= tcp_z <= Z_MAX
    fingertip_ok = (tcp_z - get_active_gripper_length()) >= (TABLE_Z_M + 0.002)
    return z_ok and fingertip_ok

def point_in_stand(px, py):
    return _stand_zone_contains_xy(px, py, expanded_for_gripper=False)

def point_in_conveyor(px, py):
    return _conveyor_zone_contains_xy(px, py, expanded_for_gripper=False)


def _axis_aligned_box_contains_xy(px, py, cx, cy, half_x, half_y, margin=0.0):
    """Generic centre/half-size XY box inclusion check."""
    return (
        abs(px - cx) < (half_x + margin) and
        abs(py - cy) < (half_y + margin)
    )

def point_in_extra_obs(px, py, pz):
    if not HAS_EXTRA_OBS:
        return False
    in_xy = _axis_aligned_box_contains_xy(px, py, OBS_X, OBS_Y, OBS_HW, OBS_HD, OBS_MARGIN)
    in_z = pz < (OBS_H + OBS_MARGIN)
    return in_xy and in_z


def point_in_obstacle(px, py, pz):
    return (
        point_in_stand(px, py)
        or point_in_conveyor(px, py)
        or point_in_extra_obs(px, py, pz)
        or mcp_point_in_dynamic_obstacle(px, py, pz)
    )

# Only Z is checked here intentionally — via-point candidates may be generated
# outside the pick workspace XY box (e.g. lateral detours around the conveyor).
# To also enforce XY bounds, use gripper_in_workspace() instead.
def point_in_workspace(px, py, pz):
    return Z_MIN <= pz <= Z_MAX

def path_hits_obstacle(start, end, num_checks=50):
    for i in range(num_checks + 1):
        t  = i / num_checks
        px = start[0] + t * (end[0] - start[0])
        py = start[1] + t * (end[1] - start[1])
        pz = start[2] + t * (end[2] - start[2])
        if pz > Z_MAX or pz < Z_MIN:
            return True
        if gripper_hits_obstacle(px, py, pz):
            return True
    return False

def point_hits_obstacle(point):
    return point_in_obstacle(point[0], point[1], point[2])

def is_in_workspace(pose):
    return point_in_workspace(pose[0], pose[1], pose[2])

# -----------------------------------------------------------------
# SECTION 2b — PRE-FLIGHT TRAJECTORY VALIDATION
# -----------------------------------------------------------------

def validate_kinematics(waypoints, label="trajectory"):
    """
    Attempt to use neurapy's IK to validate reachability before moving.
    If the API supports calculate_ik or inverse_kinematics, it will raise an error here rather than mid-motion.
    """
    global r
    if r is None:
        return True
    
    ik_func = getattr(r, 'inverse_kinematics', getattr(r, 'calculate_ik', getattr(r, 'get_inverse_kinematics', None)))

    if ik_func is None:
        return True

    for i, wp in enumerate(waypoints):
        try:
            res = ik_func(wp)
            # Some APIs return (angles, is_reachable) tuple
            if isinstance(res, tuple) and len(res) > 1 and isinstance(res[1], bool):
                if not res[1]:
                    raise RuntimeError("IK Returned Unreachable Status")
        except Exception as e:
            raise RuntimeError(
                f"\n  {'='*62}\n"
                f"  PRE-FLIGHT IK ABORT\n"
                f"  Waypoint {i} in {label} is unreachable (IK Failed).\n"
                f"    Target Pose: {wp}\n"
                f"    Reason: {e}\n"
                f"  {'='*62}\n"
                f"  No motion has been sent to the robot.\n"
            )
    return True


def validate_trajectory(waypoints, label="trajectory", bypass_extra_obs=False):
    """
    Final gate before any move_linear command is issued.
    Checks Z bounds, gripper vs stand, gripper vs conveyor,
    gripper vs extra obstacle for every waypoint.
    Raises RuntimeError on first violation.
    """
    for i, wp in enumerate(waypoints):
        tcp_x, tcp_y, tcp_z = wp[0], wp[1], wp[2]
        tip_z = tcp_z - get_active_gripper_length()

        if not gripper_in_transit_bounds(tcp_x, tcp_y, tcp_z):
            if i == 0:
                print(
                    f"WARNING: Waypoint 0 in {label} violates Z limits (Tip Z={tip_z:.3f}). "
                    f"Bypassing because it is the starting pose."
                )
            else:
                raise RuntimeError(
                    f"\n  {'='*62}\n"
                    f"  PRE-FLIGHT ABORT\n"
                    f"  Waypoint {i} in {label} violates Z limits.\n"
                    f"    TCP   X={tcp_x:.3f}  Y={tcp_y:.3f}  Z={tcp_z:.3f}\n"
                    f"    Fingertip Z={tip_z:.3f}  (must be >= {Z_MIN:.3f})\n"
                    f"    Validation gripper length={get_active_gripper_length():.4f} m\n"
                    f"    Z range: [{Z_MIN:.3f} -> {Z_MAX:.3f}]\n"
                    f"  {'='*62}\n"
                    f"  No motion has been sent to the robot.\n"
                )

        if gripper_in_stand(tcp_x, tcp_y, tcp_z):
            raise RuntimeError(
                f"\n  {'='*62}\n"
                f"  PRE-FLIGHT ABORT\n"
                f"  Waypoint {i} in {label}: gripper body enters camera stand zone.\n"
                f"    TCP   X={tcp_x:.3f}  Y={tcp_y:.3f}  Z={tcp_z:.3f}\n"
                f"    (Gripper-body exclusion zone expands stand by {GRIPPER_RADIUS:.3f}m)\n"
                f"  {'='*62}\n"
                f"  No motion has been sent to the robot.\n"
                + _stand_box_message()
            )

        if gripper_in_conveyor(tcp_x, tcp_y, tcp_z):
            raise RuntimeError(
                f"\n  {'='*62}\n"
                f"  PRE-FLIGHT ABORT\n"
                f"  Waypoint {i} in {label}: gripper body enters conveyor belt zone.\n"
                f"    TCP   X={tcp_x:.3f}  Y={tcp_y:.3f}  Z={tcp_z:.3f}\n"
                f"    (Gripper-body exclusion zone expands conveyor by {GRIPPER_RADIUS:.3f}m)\n"
                f"    Conveyor TCP-level zone: "
                f"X[{_CONV_EFF_X_MIN:.3f}-{_CONV_EFF_X_MAX:.3f}]  "
                f"Y[{_CONV_EFF_Y_MIN:.3f}-{_CONV_EFF_Y_MAX:.3f}]\n"
                f"  {'='*62}\n"
                f"  No motion has been sent to the robot.\n"
            )

        if not bypass_extra_obs and gripper_in_extra_obs(tcp_x, tcp_y, tcp_z):
            raise RuntimeError(
                f"\n  {'='*62}\n"
                f"  PRE-FLIGHT ABORT\n"
                f"  Waypoint {i} in {label}: gripper body enters extra obstacle.\n"
                f"    TCP   X={tcp_x:.3f}  Y={tcp_y:.3f}  Z={tcp_z:.3f}\n"
                f"    Fingertip Z={tip_z:.3f}\n"
                f"    Obstacle centre ({OBS_X:.3f}, {OBS_Y:.3f})  H={OBS_H:.3f}m\n"
                f"    Segmented tool model: flange Ø{FLANGE_DIAMETER_M*1000:.1f}mm, neck {NECK_LENGTH_DIM_M*1000:.0f}x{NECK_THICKNESS_M*1000:.0f}mm box, jaw {JAW_FIXED_WIDTH_M*1000:.1f}mm x {get_current_jaw_width_m()*1000:.1f}mm\n"
                f"  {'='*62}\n"
                f"  No motion has been sent to the robot.\n"
            )
            
    # Final kinematic reachability check
    validate_kinematics(waypoints, label)
    return True

# -----------------------------------------------------------------
# SECTION 2c — INPUT VALIDATION
# -----------------------------------------------------------------

def _in_workspace_xy(x, y):
    return X_MIN <= x <= X_MAX and Y_MIN <= y <= Y_MAX

def _in_stand(x, y):
    return _stand_zone_contains_xy(x, y, expanded_for_gripper=False)

def _in_conveyor(x, y):
    return _conveyor_zone_contains_xy(x, y, expanded_for_gripper=False)

# -----------------------------------------------------------------
# SECTION 2d — FIXED PLACEMENT BOX HELPERS
# -----------------------------------------------------------------


def estimate_drop_tcp_z_for_object(selected_object):
    """
    Estimate TCP Z used when placing this object in the box.
    Used only for pre-planning clearance.
    """
    object_height = float(selected_object.get("height_m", 0.04))
    grasp_height = float(selected_object.get("grasp_height_m", object_height))
    grasp_width = float(selected_object.get("grasp_width_m", selected_object.get("width_m", 0.04)))
    grasp_breadth = float(selected_object.get("grasp_breadth_m", selected_object.get("breadth_m", grasp_width)))
    object_grip_width_m = min(grasp_width, grasp_breadth) + GRIP_EXTRA_SPACE_M

    close_percent = get_pick_close_percent(object_grip_width_m)
    closed_gripper_length = gripper_length_from_percent(close_percent)

    object_grip_center_ratio = float(
        selected_object.get("grip_center_ratio", GRIP_CENTER_RATIO)
    )
    target_grip_height = max(MIN_GRIP_HEIGHT_M, grasp_height * object_grip_center_ratio)

    raw_pick_z = TABLE_Z_M + closed_gripper_length + target_grip_height + PICK_HEIGHT_FINE_TUNE_M
    min_safe_pick_z = TABLE_Z_M + closed_gripper_length + MIN_GRIP_HEIGHT_M
    pick_z_dynamic = max(raw_pick_z, min_safe_pick_z)

    return pick_z_dynamic + BOX_BASE_THICKNESS_M + DROP_RELEASE_CLEARANCE_M


def gripper_side_clearance_at_box_wall(drop_tcp_z, selected_object):
    """
    Clearance based on which gripper segment is low enough to touch the box wall.

    ELI5:
    The internal opening might be 90 mm, but the whole gripper can still be
    about 150 mm wide. For gripping we use the 90 mm stroke. For box-wall
    collision, we use the physical outer width.
    """
    box_wall_top_z = TABLE_Z_M + BOX_BASE_THICKNESS_M + BOX_WALL_HEIGHT_M

    grasp_width = float(selected_object.get("grasp_width_m", selected_object.get("width_m", 0.04)))
    grasp_breadth = float(selected_object.get("grasp_breadth_m", selected_object.get("breadth_m", grasp_width)))
    object_grip_width_m = min(grasp_width, grasp_breadth) + GRIP_EXTRA_SPACE_M

    # Internal opening used for release command.
    release_internal_opening_m = object_grip_width_m * (1.0 + PRE_PICK_EXTRA_RATIO)

    # Physical width used for wall clearance.
    # Adds body/jaw thickness around the internal opening.
    extra_body_width = max(0.0, MAX_PHYSICAL_GRIPPER_WIDTH_M - MAX_STROKE_M)
    release_physical_width_m = min(
        MAX_PHYSICAL_GRIPPER_WIDTH_M,
        release_internal_opening_m + extra_body_width,
    )

    clearance = max(
        MIN_BOX_GRIPPER_SIDE_CLEARANCE_M,
        JAW_FIXED_WIDTH_M / 2.0,
        release_physical_width_m / 2.0,
    )

    flange_bottom_z = drop_tcp_z - FLANGE_LENGTH_M
    neck_bottom_z = drop_tcp_z - (FLANGE_LENGTH_M + NECK_LENGTH_M)

    if neck_bottom_z <= box_wall_top_z:
        clearance = max(clearance, NECK_RADIUS_M)

    if flange_bottom_z <= box_wall_top_z:
        clearance = max(clearance, FLANGE_RADIUS_M)

    return clearance + SEGMENTED_BOX_WALL_MARGIN_M


def _effective_inner_margin_for_object(selected_object):
    """
    Box inner margin for this specific object/drop height.

    The full gripper physical width is useful for collision checks, but using
    the full value to shrink the permanent placement area makes the box look
    falsely full. So for drop-slot allocation, the segmented gripper clearance
    is capped to MAX_PLACEMENT_SEGMENT_CLEARANCE_M.
    """
    drop_tcp_z = estimate_drop_tcp_z_for_object(selected_object)
    segment_clearance = gripper_side_clearance_at_box_wall(drop_tcp_z, selected_object)

    capped_segment_clearance = min(
        segment_clearance,
        MAX_PLACEMENT_SEGMENT_CLEARANCE_M
    )

    return PLACEMENT_INNER_MARGIN_M + capped_segment_clearance










def gripper_physical_length_from_percent(percent):
    """
    Estimate OUTER physical lower-gripper length from opening percentage.

    Important:
    This is NOT the internal jaw stroke.
    Internal jaw stroke stays MAX_STROKE_M = 0.090 m.

    This function only estimates how much physical space the gripper body/jaws
    may occupy for placement clearance:
      0% open   -> 6 cm x 3.5 cm
      100% open -> 15 cm x 3.5 cm
    """
    p = clamp_percent(percent) / 100.0
    return (
        GRIPPER_PHYSICAL_CLOSED_LENGTH_M
        + p * (GRIPPER_PHYSICAL_OPEN_LENGTH_M - GRIPPER_PHYSICAL_CLOSED_LENGTH_M)
    )


def release_percent_for_object(selected_object):
    """
    Estimate compact release opening percentage for placement clearance.

    This uses the real jaw-stroke conversion:
      object width -> percent of 90 mm internal stroke.

    It does NOT make the gripper open to 15 cm.
    """
    grasp_width = float(selected_object.get("grasp_width_m", selected_object.get("width_m", 0.04)))
    grasp_breadth = float(selected_object.get("grasp_breadth_m", selected_object.get("breadth_m", grasp_width)))
    object_grip_width_m = min(grasp_width, grasp_breadth) + GRIP_EXTRA_SPACE_M

    release_width_m = object_grip_width_m * (1.0 + PRE_PICK_EXTRA_RATIO)
    return object_width_to_percent(release_width_m)

def _nearest_symmetric_angle(physical_angle_deg, reference_angle_deg, symmetry_period_deg):
    """
    Generalizes the old fixed +/-180 flip logic to ANY N-fold rotational
    symmetry.

    Some objects have more than 2 physically-equivalent grasp angles:
      - a plain rectangular block/marker/screwdriver: 2 equivalent angles,
        180 deg apart (the original behaviour -- pass symmetry_period_deg=180)
      - a cube gripped face-to-face: 4 equivalent angles, 90 deg apart
      - a hex nut gripped flat-to-flat: 6 equivalent angles, 60 deg apart

    Given a target physical_angle_deg and a reference_angle_deg (where the
    arm currently is / was last at), this returns whichever of the
    physically-equivalent angles (spaced symmetry_period_deg apart) is
    CLOSEST to reference_angle_deg -- minimizing wrist rotation, exactly
    like the old +/-180 check did, just generalized to any period.

    symmetry_period_deg <= 0 means "no symmetry, use the literal angle".
    """
    if symmetry_period_deg <= 0:
        return _normalise_angle_deg(physical_angle_deg)

    diff = _normalise_angle_deg(physical_angle_deg - reference_angle_deg)
    half_period = symmetry_period_deg / 2.0

    # Reduce diff into (-half_period, half_period] by stepping in
    # symmetry_period_deg increments -- this picks the closest
    # equivalent rotation instead of insisting on the literal angle.
    while diff > half_period:
        diff -= symmetry_period_deg
    while diff <= -half_period:
        diff += symmetry_period_deg

    return _normalise_angle_deg(reference_angle_deg + diff)


def planned_rz_for_object(selected_object, placement_angle_deg=None, reference_angle_deg=None):
    """
    Return TCP RZ angle for this object.
    If placement_angle_deg is given, use that for placement packing.
    reference_angle_deg is used to choose the nearest symmetric rotation.

    The number of equivalent grasp angles is now configurable per object
    via "grasp_symmetry_deg" in OBJECT_CATALOGUE (default 180, matching
    the original behaviour for every object that doesn't specify it):
      180 -> rectangular block, 2 equivalent angles (old default/behaviour)
       90 -> cube gripped face-to-face, 4 equivalent angles
       60 -> hex nut gripped flat-to-flat, 6 equivalent angles
    """
    if placement_angle_deg is not None:
        angle = _normalise_angle_deg(placement_angle_deg)
    else:
        preferred = float(
            selected_object.get("preferred_grasp_angle_deg", DEFAULT_PREFERRED_GRASP_ANGLE_DEG)
        )
        angle = _normalise_angle_deg(HOME_RZ + preferred)

    # 1. Apply the hardware mounting offset to get the true physical target angle
    offset = globals().get("GRIPPER_RZ_OFFSET", 0.0)
    physical_angle = _normalise_angle_deg(angle + offset)

    # 2. Compare against the reference angle to pick the shortest rotation
    base = HOME_RZ if reference_angle_deg is None else reference_angle_deg

    # 3. Find whichever symmetric-equivalent angle is closest to the
    #    current physical position, using this object's own symmetry period.
    symmetry_period_deg = float(selected_object.get("grasp_symmetry_deg", 180.0))
    physical_angle = _nearest_symmetric_angle(physical_angle, base, symmetry_period_deg)

    return physical_angle


def rotated_rectangle_half_extents(length_m, width_m, angle_deg):
    theta = math.radians(angle_deg)
    c = abs(math.cos(theta))
    s = abs(math.sin(theta))

    half_l = length_m / 2.0
    half_w = width_m / 2.0

    half_x = c * half_l + s * half_w
    half_y = s * half_l + c * half_w

    return half_x, half_y


def rotated_gripper_half_extents_for_object(selected_object, placement_angle_deg=None):
    """
    Lower gripper rectangular physical footprint for placement.

    This uses the outer physical footprint, NOT jaw stroke:
      release percent 0%   -> 6 cm x 3.5 cm
      release percent 100% -> 15 cm x 3.5 cm

    The release percent itself is calculated from the real 90 mm internal stroke.
    """
    rz_deg = planned_rz_for_object(selected_object, placement_angle_deg)

    release_percent = release_percent_for_object(selected_object)
    length_m = gripper_physical_length_from_percent(release_percent)
    depth_m = GRIPPER_PHYSICAL_DEPTH_M

    return rotated_rectangle_half_extents(length_m, depth_m, rz_deg)


def _effective_xy_margins_for_object(selected_object, placement_angle_deg=None):
    """
    Separate X/Y margins for placement.

    Use wall thickness + desired wall gap, plus only the rotated jaw rectangle
    clearance when it matters for the wall approach.
    """
    base_margin = BOX_WALL_THICKNESS_M + PLACEMENT_WALL_GAP_MIN_M

    grip_half_x, grip_half_y = rotated_gripper_half_extents_for_object(
        selected_object,
        placement_angle_deg,
    )

    # Limit gripper clearance for packing so the object can still approach walls.
    # The physical rectangle is considered by angle scoring, but permanent object
    # placement should not reserve the whole gripper forever.
    add_x = min(grip_half_x, MAX_PLACEMENT_SEGMENT_CLEARANCE_X_M)
    add_y = min(grip_half_y, MAX_PLACEMENT_SEGMENT_CLEARANCE_Y_M)

    return base_margin + add_x, base_margin + add_y




def placement_x_limits_at_y(y):
    """
    Rectangular placement-box version.

    Kept for compatibility with the existing planner.
    Since the physical box is now rectangular, X limits do not change with Y.
    """
    return PLACEMENT_BOX_X_MIN, PLACEMENT_BOX_X_MAX



def candidate_inside_real_placement_box(x, y, length, width, margin_x=0.0, margin_y=0.0):
    """
    Rectangular placement-box containment check.

    Kept under the same function name so the existing planner can continue
    calling it without changes.
    """
    half_l = length / 2.0
    half_w = width / 2.0

    return (
        PLACEMENT_BOX_X_MIN + margin_x + half_l <= x <= PLACEMENT_BOX_X_MAX - margin_x - half_l
        and
        PLACEMENT_BOX_Y_MIN + margin_y + half_w <= y <= PLACEMENT_BOX_Y_MAX - margin_y - half_w
    )




def real_placement_wall_gaps(x, y, length, width):
    """
    Wall-gap reading against the rectangular placement box.

    Returns:
        left_gap, right_gap, bottom_gap, top_gap
    """
    left_gap = (x - length / 2.0) - PLACEMENT_BOX_X_MIN
    right_gap = PLACEMENT_BOX_X_MAX - (x + length / 2.0)
    bottom_gap = (y - width / 2.0) - PLACEMENT_BOX_Y_MIN
    top_gap = PLACEMENT_BOX_Y_MAX - (y + width / 2.0)

    return left_gap, right_gap, bottom_gap, top_gap


def point_in_placement_box_xy(x, y):
    if not PLACEMENT_BOX_ENABLED:
        return False
    return (
        PLACEMENT_BOX_X_MIN <= x <= PLACEMENT_BOX_X_MAX and
        PLACEMENT_BOX_Y_MIN <= y <= PLACEMENT_BOX_Y_MAX
    )


def point_in_placement_inner_xy(x, y, half_x=0.0, half_y=0.0):
    if not point_in_placement_box_xy(x, y):
        return False
    return (
        PLACEMENT_INNER_X_MIN + half_x <= x <= PLACEMENT_INNER_X_MAX - half_x and
        PLACEMENT_INNER_Y_MIN + half_y <= y <= PLACEMENT_INNER_Y_MAX - half_y
    )


def _rectangles_overlap(cx1, cy1, l1, w1, cx2, cy2, l2, w2, clearance=PLACEMENT_OBJECT_GAP_M):
    return (
        abs(cx1 - cx2) < ((l1 + l2) / 2.0 + clearance) and
        abs(cy1 - cy2) < ((w1 + w2) / 2.0 + clearance)
    )


def _object_footprint_for_placement(selected_object, rotated=False):
    """
    Return the permanent/safety footprint for box packing.

    This represents the released object plus a small safety margin.
    It does not permanently reserve full gripper opening, because the gripper
    only opens compactly during release and then lifts away.
    """
    object_length = float(
        selected_object.get("length_m", selected_object.get("grasp_length_m", 0.04))
    )

    object_width = float(
        selected_object.get("breadth_m", selected_object.get("width_m", 0.04))
    )

    object_height = float(selected_object.get("height_m", 0.04))
    grasp_height = float(selected_object.get("grasp_height_m", object_height))

    object_grip_center_ratio = float(
        selected_object.get("grip_center_ratio", GRIP_CENTER_RATIO)
    )

    # Estimate how much of the object hangs below the grasp point.
    # More hanging material means more conservative placement near walls.
    target_grip_height = max(MIN_GRIP_HEIGHT_M, grasp_height * object_grip_center_ratio)
    below_grip_m = max(0.0, object_height - target_grip_height)

    carried_margin = below_grip_m * 0.25 + PLACEMENT_CARRIED_OBJECT_MARGIN_M

    footprint_length = object_length + PLACEMENT_FOOTPRINT_MARGIN_M + carried_margin
    footprint_width = object_width + PLACEMENT_FOOTPRINT_MARGIN_M + carried_margin

    if rotated:
        return footprint_width, footprint_length

    return footprint_length, footprint_width


def _candidate_inside_placement_box(x, y, length, width, margin_x=0.0, margin_y=0.0):
    """
    True only if the candidate object footprint is inside the real rectangular box.
    """
    return candidate_inside_real_placement_box(
        x, y, length, width,
        margin_x=margin_x,
        margin_y=margin_y,
    )



def _candidate_overlaps_placed(x, y, length, width):
    for obj in PLACED_OBJECTS:
        if _rectangles_overlap(
            x, y, length, width,
            obj["x"], obj["y"], obj["length_m"], obj["width_m"],
        ):
            return True
    return False



def _wall_gaps_for_candidate(x, y, length, width):
    """
    Return object footprint gaps to the rectangular placement-box walls.
    """
    return real_placement_wall_gaps(x, y, length, width)




def _corner_compaction_score(x, y, length, width):
    left_gap, right_gap, bottom_gap, top_gap = _wall_gaps_for_candidate(x, y, length, width)
    return min(
        left_gap + bottom_gap,
        left_gap + top_gap,
        right_gap + bottom_gap,
        right_gap + top_gap,
    )


def _center_avoidance_score(x, y):
    center_x = (PLACEMENT_BOX_X_MIN + PLACEMENT_BOX_X_MAX) / 2.0
    center_y = (PLACEMENT_BOX_Y_MIN + PLACEMENT_BOX_Y_MAX) / 2.0
    dist_from_center = math.hypot(x - center_x, y - center_y)
    return 1.0 / max(dist_from_center, 0.001)


def _open_space_after_candidate_score(x, y, length, width):
    left_gap, right_gap, bottom_gap, top_gap = _wall_gaps_for_candidate(x, y, length, width)
    box_w = PLACEMENT_BOX_X_MAX - PLACEMENT_BOX_X_MIN
    box_h = PLACEMENT_BOX_Y_MAX - PLACEMENT_BOX_Y_MIN
    return max(
        max(0.0, left_gap) * box_h,
        max(0.0, right_gap) * box_h,
        max(0.0, bottom_gap) * box_w,
        max(0.0, top_gap) * box_w,
    )



def _placement_wall_gap_penalty(x, y, length, width):
    gaps = _wall_gaps_for_candidate(x, y, length, width)
    nearest_gap = min(gaps)

    if nearest_gap < PLACEMENT_WALL_GAP_MIN_M:
        return SMART_WALL_GAP_UNDER_WEIGHT * (PLACEMENT_WALL_GAP_MIN_M - nearest_gap)

    if nearest_gap > PLACEMENT_WALL_GAP_MAX_M:
        return SMART_WALL_GAP_OVER_WEIGHT * (nearest_gap - PLACEMENT_WALL_GAP_MAX_M)

    return 0.0


def _placement_score(x, y, length, width, selected_object=None, placement_angle_deg=None, base_angle_deg=None):# base_angle_deg check wit initial ts
    """
    Smart placement score. Lower score wins.

    This keeps the existing allocator structure but scores candidates by:
      - corner/wall compaction,
      - avoiding the middle,
      - leaving one large open region,
      - lightweight future-fit lookahead,
      - safe wall-gap limits.
    """

    angle_penalty = 0.0# angle penalty to make it not do uncessary shi 
    if placement_angle_deg is not None and base_angle_deg is not None:
        angle_deviation = abs(_normalise_angle_deg(placement_angle_deg - base_angle_deg))
        angle_penalty = angle_deviation * PLACEMENT_ANGLE_DEVIATION_WEIGHT

    if not SMART_PLACEMENT_ENABLED:
        object_gap_x = (x - length / 2.0) - PLACEMENT_BOX_X_MIN - BOX_WALL_THICKNESS_M
        score = abs(object_gap_x - PLACEMENT_WALL_GAP_MIN_M) * 10.0
        if object_gap_x < PLACEMENT_WALL_GAP_MIN_M:
            score += (PLACEMENT_WALL_GAP_MIN_M - object_gap_x) * 100.0
        if object_gap_x > PLACEMENT_WALL_GAP_MAX_M:
            score += (object_gap_x - PLACEMENT_WALL_GAP_MAX_M) * 15.0
        score += 0.5 * (x - PLACEMENT_BOX_X_MIN)
        score -= 0.2 * (y - PLACEMENT_BOX_Y_MIN)
        return score + angle_penalty # added angle penalty :)

    corner_score = _corner_compaction_score(x, y, length, width)
    center_penalty = _center_avoidance_score(x, y)
    open_space_score = _open_space_after_candidate_score(x, y, length, width)
    wall_gap_penalty = _placement_wall_gap_penalty(x, y, length, width)

    score = 0.0
    score += SMART_CORNER_WEIGHT * corner_score
    score += SMART_CENTER_AVOID_WEIGHT * center_penalty
    score -= SMART_OPEN_SPACE_WEIGHT * open_space_score
    score += wall_gap_penalty

    if PLACED_OBJECTS:
        # Calculate minimum edge-to-edge distance for spreading
        nearest_edge_dist = 999.0
        for obj in PLACED_OBJECTS:
            center_dist = math.hypot(x - obj["x"], y - obj["y"])
            # Approximate the radius of both candidate and placed object
            cand_radius = math.hypot(length, width) / 2.0
            obj_radius = math.hypot(obj["length_m"], obj["width_m"]) / 2.0
            
            edge_dist = center_dist - cand_radius - obj_radius
            if edge_dist < nearest_edge_dist:
                nearest_edge_dist = edge_dist
                
        score -= nearest_edge_dist * SMART_EXISTING_OBJECT_SPREAD_WEIGHT

    return score + angle_penalty



def find_best_drop_slot(selected_object):
    candidates = []
    
    grid_step = SMART_PLACEMENT_GRID_STEP_M

    # Base angle is always HOME_RZ so objects are placed squarely relative to the box (robot POV)
    # completely ignoring the slanted angle they had on the table when picked up.
    base_angle = HOME_RZ

    # Only test strictly square placement angles (0 and 90 degrees relative to box)
    for angle_offset in [0, 90]:
        placement_angle_deg = _normalise_angle_deg(base_angle + angle_offset)

        for rotated in (False, True):
            length, width = _object_footprint_for_placement(selected_object, rotated=rotated)

            margin_x, margin_y = _effective_xy_margins_for_object(selected_object, placement_angle_deg)
            x_min = PLACEMENT_BOX_X_MIN + margin_x
            x_max = PLACEMENT_BOX_X_MAX - margin_x
            y_min = PLACEMENT_BOX_Y_MIN + margin_y
            y_max = PLACEMENT_BOX_Y_MAX - margin_y

            # Candidate starts as close as possible to lower-X wall.
            x = x_min + length / 2.0
            while x <= x_max - length / 2.0 + 1e-9:
                y = y_max - width / 2.0  # start higher Y first
                while y >= y_min + width / 2.0 - 1e-9:
                    if _candidate_inside_placement_box(x, y, length, width, margin_x=margin_x, margin_y=margin_y):
                        if _candidate_overlaps_placed(x, y, length, width):
                                y -= grid_step
                                continue
                        
                        score = _placement_score(x, y, length, width, selected_object, placement_angle_deg,base_angle_deg=base_angle)# added base_angle_deg=base_angle for angle check
                        # Prefer angles where the long gripper rectangle wastes less X margin.
                        grip_half_x, grip_half_y = rotated_gripper_half_extents_for_object(
                            selected_object,
                            placement_angle_deg,
                            )
                        score += min(grip_half_x, MAX_PLACEMENT_SEGMENT_CLEARANCE_X_M) * 0.5
                        
                        candidates.append((
                                score,
                                x,
                                y,
                                length,
                                width,
                                rotated,
                                placement_angle_deg,
                            ))
                    y -= PLACEMENT_GRID_STEP_M
                x += PLACEMENT_GRID_STEP_M

    if not candidates:
        raise RuntimeError(
            "No free placement slot found inside the box. "
            "The box may be full, object footprint too large, or margins too conservative."
        )

    candidates.sort(key=lambda item: item[0])
    _, x, y, length, width, rotated, placement_angle_deg = candidates[0]

    slot = {
        "x": x,
        "y": y,
        "length_m": length,
        "width_m": width,
        "rotated": rotated,
        "placement_angle_deg": placement_angle_deg,
    }

    PLACED_OBJECTS.append(slot)
    return slot


def allocate_drop_slot_for_object(selected_object):
    return find_best_drop_slot(selected_object)





# -----------------------------------------------------------------
# SECTION 2e — PRE-PLANNED PLACEMENT SUMMARY / DIAGRAM
# -----------------------------------------------------------------

def reserve_drop_slot_for_object(selected_object):
    """Allocate and store a drop slot inside the selected object dictionary."""
    if selected_object.get("_planned_drop_slot") is not None:
        return selected_object["_planned_drop_slot"]

    slot = allocate_drop_slot_for_object(selected_object)
    selected_object["_planned_drop_slot"] = slot
    return slot



def preplan_all_drop_slots(pick_sequence):
    """
    Pre-calculate all drop locations before robot motion starts.

    For MCP/camera mode, PLACED_OBJECTS is first seeded with any objects that
    the camera already sees inside the placement box, PLUS any objects we 
    already successfully placed in previous voice commands (PERSISTENT_PLACED_OBJECTS).
    """
    PLACED_OBJECTS.clear()
    


    # 2. Load any newly detected objects physically inside the box
    _load_mcp_placement_occupancy_into_planner()

    for seq_item in pick_sequence:
        selected_object = seq_item["object"]
        slot = reserve_drop_slot_for_object(selected_object)
        selected_object["_planned_drop_slot"] = slot




# =================================================================
# MULTI-OBJECT PICK SEQUENCE HELPERS
# =================================================================

def add_future_pick_objects_as_obstacles(sequence, current_index):
    """
    Treat not-yet-picked objects as temporary obstacles.

    This prevents the arm/gripper from sweeping through other objects that are
    still sitting in the pick area while executing the current object's path.

    The current object is NOT added as an obstacle because the robot must be
    allowed to descend to it.
    """
    global HAS_EXTRA_OBS, OBS_X, OBS_Y, OBS_W, OBS_D, OBS_H, OBS_HW, OBS_HD

    # This codebase supports one manual extra obstacle through OBS_*.
    # For multi-object runs, the safest simple behaviour is:
    #   - keep the manual obstacle if the user entered one,
    #   - but if no manual obstacle is active, use the nearest future object
    #     as a temporary obstacle during this pick cycle.
    #
    # More advanced future version:
    #   support a list of dynamic obstacles instead of one OBS_* object.

    if HAS_EXTRA_OBS:
        return

    future = [item for item in sequence if item["index"] > current_index]
    if not future:
        return

    # Use the closest future object to the current pick as the temporary obstacle.
    current = sequence[current_index - 1]
    cx, cy = current["pick_x"], current["pick_y"]

    def dist2(item):
        return (item["pick_x"] - cx) ** 2 + (item["pick_y"] - cy) ** 2

    nearest = min(future, key=dist2)
    obj = nearest["object"]

    obs_len = float(obj.get("length_m", obj.get("grasp_length_m", 0.04)))
    obs_wid = float(obj.get("width_m", obj.get("grasp_width_m", 0.04)))
    obs_brd = float(obj.get("breadth_m", obs_wid))
    obs_hgt = float(obj.get("height_m", obj.get("grasp_height_m", 0.04)))

    # Use the larger horizontal size as X-width and the other as Y-depth.
    # This is conservative because future objects may be rotated.
    OBS_X = nearest["pick_x"]
    OBS_Y = nearest["pick_y"]
    OBS_W = max(obs_len, obs_wid)
    OBS_D = max(obs_brd, min(obs_len, obs_wid))
    OBS_H = obs_hgt
    OBS_HW = OBS_W / 2.0
    OBS_HD = OBS_D / 2.0
    HAS_EXTRA_OBS = True


def clear_temporary_future_object_obstacle(was_manual_obstacle):
    """
    Clear temporary future-object obstacle after each object cycle if it was not
    originally a user/manual obstacle.
    """
    global HAS_EXTRA_OBS, OBS_X, OBS_Y, OBS_W, OBS_D, OBS_H, OBS_HW, OBS_HD

    if was_manual_obstacle:
        return

    HAS_EXTRA_OBS = False
    OBS_X = OBS_Y = OBS_W = OBS_D = OBS_H = 0.0
    OBS_HW = OBS_HD = 0.0


# -----------------------------------------------------------------
# SECTION 6 — ROBOT STARTUP + HOME
# -----------------------------------------------------------------

def power_off_robot():
    global _MCP_ROBOT_READY
    try:
        r.stop()
        time.sleep(0.5)
        r.power_off()
        gripper_shutdown()
        _MCP_ROBOT_READY = False
    except Exception as e:
        _MCP_ROBOT_READY = False
        return


def ensure_robot_ready(r):
 
    r.switch_to_real()
    time.sleep(1)

    r.power_on()
    time.sleep(2)

    if r.get_errors():
        r.reset_errors()
        time.sleep(1)

    if not r.is_robot_in_automatic_mode():
        r.switch_to_automatic_mode()
        time.sleep(1)

    
    time.sleep(1)
    # Settle delay + discard-first-reading: only runs ONCE per program
    # (guarded by _MCP_ROBOT_READY), matching the reported symptom that
    # the dive only happens on the very first home activation, manual
    # or auto. Hypothesis: the very first pose reading right after
    # power_on/init_program may not yet reflect the arm's true settled
    # position.
    time.sleep(1.0)
    _ = r.get_tcp_pose()   # discard — may be stale immediately after init
    time.sleep(0.3)
    print(f"[Startup] Settled pose confirmed: {r.get_tcp_pose()}")

    # Enforce minimum robot speed override
    try:
        if r.get_override() < ROBOT_SPEED_OVERRIDE:
            r.set_override(ROBOT_SPEED_OVERRIDE)
            time.sleep(0.2)
    except Exception:
        pass


def check_starting_position(r):
    pose = r.get_tcp_pose()
    tip  = pose[2] - GRIPPER_LENGTH
    if tip < Z_MIN:
        power_off_robot()
        sys.exit(1)

def is_at_home(r, tol=0.01):
    try:
        c = r.get_tcp_pose()
        return (abs(c[0] - HOME_X) < tol and
                abs(c[1] - HOME_Y) < tol and
                abs(c[2] - HOME_Z) < tol)
    except Exception as e:
        return False

def get_home_pose(current):
    home    = copy.deepcopy(current)
    home[0] = HOME_X
    home[1] = HOME_Y
    home[2] = HOME_Z
    home[3] = math.radians(HOME_RX)
    home[4] = math.radians(HOME_RY)
    home[5] = math.radians(HOME_RZ)  # always return to forward-facing home angle
    return home

def move_to_home_emergency(r):
    global _BYPASS_EXTRA_OBS
    current = r.get_tcp_pose()
    if is_at_home(r):
        return
    home = get_home_pose(current)

    traj = build_full_trajectory([current, home])
    execute_trajectory(r, traj, label="Emergency return home")
    # No try/except — let exceptions propagate so on_h knows if it failed

MCP_INTENTIONAL_STOP = False

def mcp_return_home():
    """Callable from robot_mcp to safely stop and return home."""
    global MCP_INTENTIONAL_STOP, EMERGENCY_STOP_ACTIVE

    if EMERGENCY_STOP_ACTIVE:
        print("[SAFETY] Emergency stop is still latched — refusing to auto-recover or move. "
              "Call clear_emergency_stop explicitly before attempting return_home.")
        raise RuntimeError("Robot is in Emergency Stop state. Clear it before commanding.")
    
    import time
    try:
        MCP_INTENTIONAL_STOP = True
        r.stop()
        time.sleep(0.5)
        r.reset_errors()              # clear ROBOT-level errors

        # IMPORTANT: power must be restored BEFORE any attempt to talk to
        # the gripper. On this hardware, the OnRobot 2FG7's power/RS485
        # comms are routed through the robot's own safety-rated power
        # system -- trying to reach the gripper before power_on() has
        # actually restored it causes a real Modbus write failure
        # ("Please reset emergency stop, power on robot and retry!").
        # This was confirmed directly from real logs on this hardware.
        r.power_on()
        r.switch_to_automatic_mode()  # must be in auto before any motion
        time.sleep(1.5)               # give the tool power/comms line time to actually come back

        # Disconnect/reconnect the external device -- this replicates the
        # manual pendant fix (External Devices -> disconnect -> reconnect)
        # that reliably cleared a stuck gripper earlier in testing on this
        # exact hardware. Confirmed real signatures via r.get_doc():
        #   disconnect_external_device(file_path, ignore_no_connection=False) -> bool
        #   connect_external_device(file_path, ignore_no_connection=False) -> bool
        # disconnect uses ignore_no_connection=True defensively, since the
        # device may already be disconnected/in a bad state at this point
        # and we don't want that alone to abort the whole recovery.
        try:
            r.disconnect_external_device(PROCESS_FILE, ignore_no_connection=True)
            time.sleep(0.5)
            reconnected = r.connect_external_device(PROCESS_FILE)
            print(f"[Gripper] Reconnected to external device: {reconnected}")
        except Exception as e:
            print(f"[Gripper WARN] disconnect/reconnect cycle failed: {e}")

        # CRITICAL SAFETY FIX: gripper_open() is now its own try/except,
        # SEPARATE from the outer one. Previously, if gripper_open() threw
        # (e.g. "Gripper command timeout"), the whole function jumped to
        # the outer except block and move_to_home_emergency(r) NEVER RAN
        # -- meaning a gripper problem silently prevented the arm itself
        # from returning home. That's backwards for a safety routine: the
        # arm should always attempt to get home, with or without a
        # working gripper.
        try:
            gripper_open()
        except Exception as e:
            print(f"[Gripper WARN] gripper_open() failed during return-home, "
                  f"continuing to move the arm home anyway: {e}")

        move_to_home_emergency(r)     # always attempt this, regardless of gripper state
    except Exception as e:
        print(f"Error returning home: {e}")
    finally:
        MCP_INTENTIONAL_STOP = False

    
        

# -----------------------------------------------------------------
# SECTION 7 — KEYBOARD LISTENER
# -----------------------------------------------------------------

def keyboard_listener(r):
    if not HAS_KEYBOARD:
        return
    
    home_busy = False
    def on_h():
        nonlocal home_busy
        if home_busy:
            return
        home_busy = True
        try:
            r.stop()
            time.sleep(0.5)
            r.reset_errors()              # clear errors before doing anything else
            r.switch_to_automatic_mode()  # must be in auto before any motion
            time.sleep(1)
            gripper_open()                # now safe to open gripper
            move_to_home_emergency(r)     # then go home
        except Exception:
            pass
        
        finally:
            home_busy = False

    def on_q():
        try:
            r.stop()
            gripper_open()
            time.sleep(0.5)
            time.sleep(1)
            move_to_home_emergency(r)
        except Exception:
            pass
         # Step 2: try to recover to home — best effort, do NOT power off mid-move if this fails
        
        try:
            r.reset_errors()
            r.switch_to_automatic_mode()
            time.sleep(1)
            move_to_home_emergency(r)
        except Exception:
            pass  # could not reach home — power off in current position
        
        # Step 3: always power off and exit
        power_off_robot()
        sys.exit(0)

    keyboard.add_hotkey('h', on_h)
    keyboard.add_hotkey('q', on_q)
    keyboard.wait()

# -----------------------------------------------------------------
# SECTION 8 — MAIN
# -----------------------------------------------------------------



# ==============================
# CURVE PATH GENERATOR
# ==============================
def generate_curve_waypoints(nodes, steps=20):
    start, control, end = nodes
    path = []
    for i in range(steps + 1):
        t = i / steps
        x = (1-t)**2 * start[0] + 2*(1-t)*t*control[0] + t**2 * end[0]
        y = (1-t)**2 * start[1] + 2*(1-t)*t*control[1] + t**2 * end[1]
        z = (1-t)**2 * start[2] + 2*(1-t)*t*control[2] + t**2 * end[2]
        wp = [x, y, z] + list(start[3:])
        path.append(wp)
    return path

def is_valid_path(path):
    for wp in path:
        if gripper_hits_obstacle(wp[0], wp[1], wp[2]):
            return False
    return True

def find_best_linear_detour_route(start, end):
    """
    Alternative to the Bézier arc planner:
    - For low obstacles (< 0.4 m): try linear UP -> ACROSS -> DOWN routes
    - For taller obstacles: try linear side-detour routes
    Returns a node list (not dense waypoints).
    """
    candidates = []
    ori = list(start[3:])
    base_z = max(start[2], end[2])

    use_over = HAS_EXTRA_OBS and OBS_H < 0.4

    # Check if obstacle is actually between start and end
    obs_between_x = min(start[0], end[0]) - 0.02 <= OBS_X <= max(start[0], end[0]) + 0.02
    obs_between_y = min(start[1], end[1]) - 0.02 <= OBS_Y <= max(start[1], end[1]) + 0.02
    use_over = use_over and obs_between_x and obs_between_y

    # ----------------------
    # 1. TRY OVER ROUTES
    # ----------------------
    if use_over:

        clearance = 0.02  # target clearance above object

        heights = [
            min(Z_MAX - 0.05, OBS_H + GRIPPER_LENGTH + clearance),
            min(Z_MAX - 0.05, OBS_H + GRIPPER_LENGTH + clearance + 0.01),
            min(Z_MAX - 0.05, OBS_H + GRIPPER_LENGTH + clearance + 0.02),
        ]

        for h in heights:
            mid_x = (start[0] + end[0]) / 2
            mid_y = (start[1] + end[1]) / 2

            # keep the high section nearer the obstacle, not across the full move
            via1_x = start[0] + 0.35 * (mid_x - start[0])
            via1_y = start[1] + 0.35 * (mid_y - start[1])

            via2_x = end[0] + 0.35 * (mid_x - end[0])
            via2_y = end[1] + 0.35 * (mid_y - end[1])

            route = [
                start,
                [via1_x, via1_y, h] + ori,
                [via2_x, via2_y, h] + ori,
                end
            ]

            if not _route_clear(route):
                continue

            # slight penalty for higher routes so lower valid ones are preferred
            cost = _route_cost(route) + (h - base_z) * 0.1
            candidates.append((cost, route))

    # ----------------------
    # 2. TRY SIDE ROUTES
    # ---------------------- ------------------------------------------ here

    offsets = [-0.06, -0.03, 0.03, 0.06]
    detour_z = min(Z_MAX - 0.05, base_z + 0.03)

    dx = end[1] - start[1]
    dy = -(end[0] - start[0])
    norm = math.hypot(dx, dy)

    if norm != 0:
        dx /= norm
        dy /= norm

        for offset in offsets:
            via1 = [start[0] + dx * offset, start[1] + dy * offset, detour_z] + ori
            via2 = [end[0] + dx * offset, end[1] + dy * offset, detour_z] + ori
            route = [start, via1, via2, end]

            if not _route_clear(route):
                continue

            candidates.append((_route_cost(route), route))

    if not candidates:
        return None

    best_cost, best_route = min(candidates, key=lambda x: x[0])
    return best_route

def smart_route(start, end):
    if not path_hits_obstacle(start, end):
        return [start, end]

    detour = find_best_linear_detour_route(start, end)
    if detour is not None:
        return detour

    return find_optimal_route(start, end)

def plan_best_route(start_pose, end_pose):
    """
    Compatibility wrapper for older call sites.
    The actual restored planner is smart_route().
    """
    return smart_route(start_pose, end_pose)


# -----------------------------------------------------------------
# SECTION 3 — GLOBAL OPTIMAL PATH PLANNER
# -----------------------------------------------------------------

WP_SPACING = 0.04   # metres between interpolated waypoints (25 mm)


def _density_segment(start, end):
    length = math.dist(start[:3], end[:3])
    n      = max(2, round(length / WP_SPACING))
    return interpolate_waypoints(start, end, n)

def _collect_via_candidates(start, end, ori):
    clearance = DETOUR_CLEARANCE + GRIPPER_RADIUS
    z_lateral = sorted({start[2], end[2]})

    def _face_samples(lo, hi, n=7):
        return [lo + (hi - lo) * i / (n - 1) for i in range(n)]

    raw = []

    # Camera stand face candidates (lateral only)
    sx_min = _STAND_EFF_X_MIN
    sx_max = _STAND_EFF_X_MAX
    sy_min = _STAND_EFF_Y_MIN
    sy_max = _STAND_EFF_Y_MAX
    s_y_samples = _face_samples(sy_min, sy_max)
    s_x_samples = _face_samples(sx_min, sx_max)

    for pz in z_lateral:
        for py in s_y_samples:
            raw.append((sx_max + clearance, py, pz))
            raw.append((sx_min - clearance, py, pz))
        for px in s_x_samples:
            raw.append((px, sy_max + clearance, pz))
            raw.append((px, sy_min - clearance, pz))

    # Conveyor belt face candidates (lateral only)
    cy_min = _CONV_EFF_Y_MIN
    cy_max = _CONV_EFF_Y_MAX
    cx_min = _CONV_EFF_X_MIN
    cx_max = _CONV_EFF_X_MAX
    cx_sample_lo = min(start[0], end[0]) - clearance
    cx_sample_hi = max(start[0], end[0]) + clearance
    c_x_samples  = _face_samples(cx_sample_lo, cx_sample_hi)

    for pz in z_lateral:
        for px in c_x_samples:
            raw.append((px, cy_min - clearance, pz))
        c_y_samples = _face_samples(cy_min, cy_max)
        for py in c_y_samples:
            raw.append((cx_max + clearance, py, pz))
            raw.append((cx_min - clearance, py, pz))

    # Extra obstacle face candidates (lateral + over-top)
    if HAS_EXTRA_OBS:
        ebx_min = OBS_X - OBS_HW - OBS_MARGIN
        ebx_max = OBS_X + OBS_HW + OBS_MARGIN
        eby_min = OBS_Y - OBS_HD - OBS_MARGIN
        eby_max = OBS_Y + OBS_HD + OBS_MARGIN
        ebz_top = OBS_H + OBS_MARGIN + GRIPPER_LENGTH + clearance

        e_y_samples = _face_samples(eby_min, eby_max)
        e_x_samples = _face_samples(ebx_min, ebx_max)

        for pz in z_lateral:
            for py in e_y_samples:
                raw.append((ebx_max + clearance, py, pz))
                raw.append((ebx_min - clearance, py, pz))
            for px in e_x_samples:
                raw.append((px, eby_max + clearance, pz))
                raw.append((px, eby_min - clearance, pz))

            for py in (start[1], end[1]):
                raw.append((ebx_max + clearance, py, pz))
                raw.append((ebx_min - clearance, py, pz))
            for px in (start[0], end[0]):
                raw.append((px, eby_max + clearance, pz))
                raw.append((px, eby_min - clearance, pz))

        over_x_samples = _face_samples(
            min(start[0], end[0], ebx_min) - clearance,
            max(start[0], end[0], ebx_max) + clearance,
            n=5
        )
        over_y_samples = _face_samples(eby_min, eby_max, n=5)
        for px in over_x_samples:
            for py in over_y_samples:
                raw.append((px, py, ebz_top))
        raw.append((start[0], start[1], ebz_top))
        raw.append((end[0],   end[1],   ebz_top))

    # Filter: Z cap + self-clearance
    pool = []
    seen = set()
    for (px, py, pz) in raw:
        key = (round(px, 4), round(py, 4), round(pz, 4))
        if key in seen:
            continue
        seen.add(key)
        cand = [px, py, pz] + list(ori)
        if not is_in_workspace(cand):
            continue
        if point_hits_obstacle(cand):
            continue
        pool.append(cand)

    return pool

def _route_cost(nodes):
    return sum(math.dist(nodes[i][:3], nodes[i+1][:3])
               for i in range(len(nodes) - 1))

def _route_clear(nodes):
    for i in range(len(nodes) - 1):
        if path_hits_obstacle(nodes[i], nodes[i + 1]):
            return False
    return True


def find_optimal_route(start, end):
    """
    Find the shortest safe route from start to end.
    Evaluates direct, 1-via, and 2-via routes.
    Returns node list. Raises RuntimeError if no safe route found.
    """
    ori     = start[3:]
    pool    = _collect_via_candidates(start, end, ori)
    cost_fn = _route_cost

    best_nodes = None
    best_cost  = math.inf

    # Option 1: direct
    direct = [start, end]
    if _route_clear(direct):
        cost = cost_fn(direct)
        best_nodes, best_cost = direct, cost

    # Option 2: one via-point
    for v in pool:
        route = [start, v, end]
        if not _route_clear(route):
            continue
        cost = cost_fn(route)
        if cost < best_cost:
            best_nodes, best_cost = route, cost
        

    # Option 3: two via-points
    for i, v1 in enumerate(pool):
        for v2 in pool[i+1:]:
            low_bound = (math.dist(start[:3], v1[:3]) +
                         math.dist(v1[:3],    v2[:3]) +
                         math.dist(v2[:3],    end[:3]))
            if low_bound >= best_cost:
                continue
            route = [start, v1, v2, end]
            if not _route_clear(route):
                continue
            cost = cost_fn(route)
            if cost < best_cost:
                best_nodes, best_cost = route, cost
                

    if best_nodes is None:
        raise RuntimeError(
            f"[BLOCKED] No safe route found from "
            f"({start[0]:.3f},{start[1]:.3f},{start[2]:.3f}) to "
            f"({end[0]:.3f},{end[1]:.3f},{end[2]:.3f}).\n"
            f"  Tried direct + {len(pool)} single via-points "
            f"+ {len(pool)*(len(pool)-1)//2} via-pairs.\n"
            f"  Check that pick/drop coordinates are not too close "
            f"to an obstacle, or reduce obstacle size."
        )

    return best_nodes

# -----------------------------------------------------------------
# SECTION 4 — TRAJECTORY BUILDER  (density-scaled, linear only)
# -----------------------------------------------------------------

def build_full_trajectory(checkpoints):
    """
    Build a fully interpolated trajectory through all checkpoints.
    Used for linear pick/drop approach and depart legs only.
    """
    
    full_path = [checkpoints[0]]

    for i in range(len(checkpoints) - 1):
        start     = checkpoints[i]
        end       = checkpoints[i + 1]
        seg_label = f"Segment {i+1}/{len(checkpoints)-1}"
        seg_dist  = math.dist(start[:3], end[:3])

        route = smart_route(start, end)

        for j in range(len(route) - 1):
            full_path.extend(_density_segment(route[j], route[j + 1]))
            full_path.append(route[j + 1])

        n_via = len(route) - 2
        

    total_wp = len(full_path)
    return full_path

# -----------------------------------------------------------------
# SECTION 5 — EXECUTION
# -----------------------------------------------------------------
SHORTERSIDE_SIDE = min(OBS_W, OBS_D) if HAS_EXTRA_OBS else 0.05
BLEND_RADIUS = SHORTERSIDE_SIDE * 0.1
BLEND_RADIUS = max(0.005, min(BLEND_RADIUS, 0.05))

def execute_joint_transit(r, start_pose, end_pose, label=""):
    """
    Transit uses full dynamic planner + blended Cartesian linear movement.
    """
    transit_path = build_full_trajectory([start_pose, end_pose])
    execute_trajectory(r, transit_path, label=label)


def execute_trajectory(r, full_path, label="", bypass_extra_obs=False, custom_speed=None, is_blending=True):
    """
    Execute a linear trajectory via ONE blended move_linear command.

    This avoids waypoint-by-waypoint stopping:
      - validate the whole path first,
      - prepend current TCP pose,
      - send the whole list using target_pose=trajectory,
      - enable blending.
    """
    validate_trajectory(full_path, label=label, bypass_extra_obs=bypass_extra_obs)
    current = r.get_tcp_pose()
    trajectory = [current] + full_path
    
    apply_speed = custom_speed if custom_speed is not None else LINEAR_SPEED
    apply_acceleration = LINEAR_ACCELERATION
    apply_rotation_speed = ROTATION_SPEED
    apply_rotation_acceleration = ROTATION_ACCELERATION
    
    try:
        print(f"[Motion] Starting execute_trajectory: '{label}' with {len(full_path)} waypoints...")
        r.move_linear(
            speed=apply_speed,
            acceleration=apply_acceleration,
            jerk=LINEAR_JERK,
            rotation_speed=apply_rotation_speed,
            rotation_acceleration=apply_rotation_acceleration,
            rotation_jerk=ROTATION_JERK,
            enable_blending=is_blending,
            blend_radius=BLEND_RADIUS if is_blending else 0.0,
            controller_parameters={"control_mode": "position"},
            target_pose=trajectory,
            )
        print(f"[Motion] Finished execute_trajectory: '{label}'")
    except Exception as e:
        print(f"[Motion] FAILED execute_trajectory '{label}': {e}")
        r.stop()
        raise




# -----------------------------------------------------------------
# SECTION 7 — KEYBOARD LISTENER
# -----------------------------------------------------------------

def keyboard_listener(r):
    if not HAS_KEYBOARD:
        return
    home_busy = False

    def on_h():
        nonlocal home_busy
        if home_busy:
            return
        home_busy = True #------------------------------------fix
        
        try:
            r.stop()
            gripper_open()
        except Exception as e:
            return
        time.sleep(0.5)
        try:
            r.reset_errors()
            r.switch_to_automatic_mode()
            time.sleep(1)
        except Exception as e:
            return
        move_to_home_emergency(r)
        home_busy = False

    def on_q():
        try:
            r.stop()
            gripper_open()
            time.sleep(0.5)
            r.reset_errors()
            r.switch_to_automatic_mode()
            time.sleep(1)
            move_to_home_emergency(r)
        except Exception as e:
           return
        finally:
            power_off_robot()
            sys.exit(0)

    keyboard.add_hotkey('h', on_h)
    keyboard.add_hotkey('q', on_q)
    keyboard.wait()

# -----------------------------------------------------------------
# SECTION 8 — MAIN
# -----------------------------------------------------------------



def resolve_object_runtime_variables(selected_object, move_x, move_y, drop_slot):
    """
    Convert one selected object + one MCP pick coordinate + one planned drop slot
    into the runtime variables used by execute_one_pick_cycle().

    This keeps the old global-variable execution structure, but makes the values
    come from MCP/camera/object-profile data instead of manual user input.
    """
    object_name = str(
        selected_object.get("name", selected_object.get("label", "object"))
    )

    object_length = float(selected_object.get("length_m", 0.04))
    object_width = float(selected_object.get("width_m", 0.04))
    object_breadth = float(selected_object.get("breadth_m", object_width))
    object_height = float(selected_object.get("height_m", DEFAULT_OBJECT_HEIGHT_M))

    grasp_length = float(selected_object.get("grasp_length_m", object_length))
    grasp_width = float(selected_object.get("grasp_width_m", object_width))
    grasp_breadth = float(selected_object.get("grasp_breadth_m", object_breadth))
    grasp_height = float(selected_object.get("grasp_height_m", object_height))

    grasp_offset_x = float(selected_object.get("grasp_offset_x_m", 0.0))
    grasp_offset_y = float(selected_object.get("grasp_offset_y_m", 0.0))
    grasp_offset_z = float(selected_object.get("grasp_offset_z_m", 0.0))

    pick_target_x = float(move_x) + grasp_offset_x
    pick_target_y = float(move_y) + grasp_offset_y

    object_grip_width_m = min(grasp_width, grasp_breadth) + GRIP_EXTRA_SPACE_M

    object_orientation_deg = float(
        selected_object.get("object_orientation_deg", DEFAULT_OBJECT_ORIENTATION_DEG)
    )
    preferred_grasp_angle_deg = float(
        selected_object.get("preferred_grasp_angle_deg", DEFAULT_PREFERRED_GRASP_ANGLE_DEG)
    )

    # Use the planned placement angle when the packing planner provides one.
    # Otherwise use the normal object grasp angle.
    placement_angle_deg = None
    if isinstance(drop_slot, dict):
        placement_angle_deg = drop_slot.get("placement_angle_deg")

    current_wrist_angle_deg = math.degrees(r.get_tcp_pose()[5])
    
    pick_rz_deg = planned_rz_for_object(
        selected_object,
        placement_angle_deg=None,
        reference_angle_deg=current_wrist_angle_deg,
        )
    
    drop_rz_deg = planned_rz_for_object(
        selected_object,
        placement_angle_deg=placement_angle_deg,
        reference_angle_deg=pick_rz_deg,
    )

    if not isinstance(drop_slot, dict):
        raise RuntimeError("Missing planned drop slot for selected object.")

    drop_x = float(drop_slot["x"])
    drop_y = float(drop_slot["y"])

    return {
        "OBJECT_NAME": object_name,
        "OBJECT_LENGTH_M": object_length,
        "OBJECT_WIDTH_M": object_width,
        "OBJECT_BREADTH_M": object_breadth,
        "OBJECT_HEIGHT": object_height,

        "GRASP_LENGTH_M": grasp_length,
        "GRASP_WIDTH_M": grasp_width,
        "GRASP_BREADTH_M": grasp_breadth,
        "GRASP_HEIGHT_M": grasp_height,

        "GRASP_OFFSET_X": grasp_offset_x,
        "GRASP_OFFSET_Y": grasp_offset_y,
        "GRASP_OFFSET_Z": grasp_offset_z,

        "PICK_TARGET_X": pick_target_x,
        "PICK_TARGET_Y": pick_target_y,

        "OBJECT_GRIP_WIDTH_M": object_grip_width_m,
        "OBJECT_ORIENTATION_DEG": object_orientation_deg,
        "PREFERRED_GRASP_ANGLE_DEG": preferred_grasp_angle_deg,
        "PLANNED_RZ_DEG": pick_rz_deg,
        "PICK_RZ_DEG": pick_rz_deg,
        "DROP_RZ_DEG": drop_rz_deg,

        "DROP_X": drop_x,
        "DROP_Y": drop_y,
    }

def set_active_pick_item(seq_item, cycle_index=1, total_cycles=1):
    """
    Configure all object/grasp/drop runtime variables for one pick cycle.
    This restores the missing runtime setup helper.
    """
    global MOVE_X, MOVE_Y, SELECTED_OBJECT, DROP_SLOT, _RUNTIME
    global OBJECT_NAME, OBJECT_LENGTH_M, OBJECT_WIDTH_M, OBJECT_BREADTH_M, OBJECT_HEIGHT
    global GRASP_LENGTH_M, GRASP_WIDTH_M, GRASP_BREADTH_M, GRASP_HEIGHT_M
    global GRASP_OFFSET_X, GRASP_OFFSET_Y, GRASP_OFFSET_Z
    global PICK_TARGET_X, PICK_TARGET_Y, OBJECT_GRIP_WIDTH_M
    global OBJECT_ORIENTATION_DEG, PREFERRED_GRASP_ANGLE_DEG, PLANNED_RZ_DEG, PICK_RZ_DEG, DROP_RZ_DEG
    global DROP_X, DROP_Y
    global PRE_PICK_OPEN_PERCENT, PICK_CLOSE_PERCENT
    global PRE_PICK_GRIPPER_LENGTH, CLOSED_GRIPPER_LENGTH, ACTIVE_GRIPPER_LENGTH
    global TARGET_GRIP_HEIGHT
    global CARRIED_OBJECT_HEIGHT_M, CARRIED_OBJECT_WIDTH_M, CARRIED_OBJECT_DEPTH_M, CARRIED_OBJECT_BELOW_GRIP_M
    global PICK_Z_DYNAMIC_RAW, MIN_SAFE_PICK_Z, PICK_Z_DYNAMIC
    global DROP_RELEASE_Z_RAW, DROP_RELEASE_Z

    MOVE_X = seq_item["pick_x"]
    MOVE_Y = seq_item["pick_y"]
    SELECTED_OBJECT = seq_item["object"]

    DROP_SLOT = SELECTED_OBJECT.get("_planned_drop_slot") or allocate_drop_slot_for_object(SELECTED_OBJECT)

    _RUNTIME = resolve_object_runtime_variables(
        SELECTED_OBJECT,
        MOVE_X,
        MOVE_Y,
        DROP_SLOT,
    )

    OBJECT_NAME = _RUNTIME["OBJECT_NAME"]
    OBJECT_LENGTH_M = _RUNTIME["OBJECT_LENGTH_M"]
    OBJECT_WIDTH_M = _RUNTIME["OBJECT_WIDTH_M"]
    OBJECT_BREADTH_M = _RUNTIME["OBJECT_BREADTH_M"]
    OBJECT_HEIGHT = _RUNTIME["OBJECT_HEIGHT"]

    GRASP_LENGTH_M = _RUNTIME["GRASP_LENGTH_M"]
    GRASP_WIDTH_M = _RUNTIME["GRASP_WIDTH_M"]
    GRASP_BREADTH_M = _RUNTIME["GRASP_BREADTH_M"]
    GRASP_HEIGHT_M = _RUNTIME["GRASP_HEIGHT_M"]

    GRASP_OFFSET_X = _RUNTIME["GRASP_OFFSET_X"]
    GRASP_OFFSET_Y = _RUNTIME["GRASP_OFFSET_Y"]
    GRASP_OFFSET_Z = _RUNTIME["GRASP_OFFSET_Z"]

    PICK_TARGET_X = _RUNTIME["PICK_TARGET_X"]
    PICK_TARGET_Y = _RUNTIME["PICK_TARGET_Y"]

    OBJECT_GRIP_WIDTH_M = _RUNTIME["OBJECT_GRIP_WIDTH_M"]
    OBJECT_ORIENTATION_DEG = _RUNTIME["OBJECT_ORIENTATION_DEG"]
    PREFERRED_GRASP_ANGLE_DEG = _RUNTIME["PREFERRED_GRASP_ANGLE_DEG"]
    PLANNED_RZ_DEG = _RUNTIME["PLANNED_RZ_DEG"]
    PICK_RZ_DEG = _RUNTIME["PICK_RZ_DEG"]
    DROP_RZ_DEG = _RUNTIME["DROP_RZ_DEG"]

    DROP_X = _RUNTIME["DROP_X"]
    DROP_Y = _RUNTIME["DROP_Y"]

    if OBJECT_GRIP_WIDTH_M > MAX_STROKE_M:
        raise RuntimeError(
            f"Selected object requires {OBJECT_GRIP_WIDTH_M*1000:.1f} mm opening, "
            f"but the usable gripper stroke is {MAX_STROKE_M*1000:.1f} mm."
        )

    PRE_PICK_OPEN_PERCENT = get_pre_pick_open_percent(OBJECT_GRIP_WIDTH_M)
    PICK_CLOSE_PERCENT = get_pick_close_percent(OBJECT_GRIP_WIDTH_M)
    PRE_PICK_GRIPPER_LENGTH = gripper_length_from_percent(PRE_PICK_OPEN_PERCENT)
    CLOSED_GRIPPER_LENGTH = gripper_length_from_percent(PICK_CLOSE_PERCENT)

    ACTIVE_GRIPPER_LENGTH = CLOSED_GRIPPER_LENGTH

    object_grip_center_ratio = float(
        SELECTED_OBJECT.get("grip_center_ratio", GRIP_CENTER_RATIO)
    )

    TARGET_GRIP_HEIGHT = max(
        MIN_GRIP_HEIGHT_M,
        GRASP_HEIGHT_M * object_grip_center_ratio
    )

    CARRIED_OBJECT_HEIGHT_M = OBJECT_HEIGHT
    CARRIED_OBJECT_WIDTH_M = OBJECT_WIDTH_M
    CARRIED_OBJECT_DEPTH_M = OBJECT_BREADTH_M
    CARRIED_OBJECT_BELOW_GRIP_M = max(0.0, OBJECT_HEIGHT - TARGET_GRIP_HEIGHT)

    PICK_Z_DYNAMIC_RAW = (
        TABLE_Z_M
        + CLOSED_GRIPPER_LENGTH
        + TARGET_GRIP_HEIGHT
        + GRASP_OFFSET_Z
        + PICK_HEIGHT_FINE_TUNE_M
    )

    MIN_SAFE_PICK_Z = TABLE_Z_M + CLOSED_GRIPPER_LENGTH + MIN_GRIP_HEIGHT_M
    PICK_Z_DYNAMIC = max(PICK_Z_DYNAMIC_RAW, MIN_SAFE_PICK_Z)

    DROP_RELEASE_Z_RAW = PICK_Z_DYNAMIC + BOX_BASE_THICKNESS_M + DROP_RELEASE_CLEARANCE_M
    DROP_RELEASE_Z = max(DROP_RELEASE_Z_RAW, MIN_SAFE_PICK_Z)

def execute_one_pick_cycle(seq_item, cycle_index, total_cycles):
    """Execute one full pick-and-place cycle using the selected sequence item."""
    global CARRIED_OBJECT_ENABLED, _BYPASS_EXTRA_OBS

    set_active_pick_item(seq_item, cycle_index, total_cycles)

    # Pick coordinates must stay inside the original pick workspace.
    # Drop coordinates may bypass the pick workspace only if inside the placement box.
    for label, cx, cy in [("Pick target / grasp region", PICK_TARGET_X, PICK_TARGET_Y),
                           ("Drop-off", DROP_X, DROP_Y)]:
        is_drop_point = label.startswith("Drop-off")
        drop_is_inside_box = is_drop_point and point_in_placement_box_xy(cx, cy)

        if not _in_workspace_xy(cx, cy) and not drop_is_inside_box:
            if is_drop_point:
                power_off_robot()
                sys.exit(1)

        if is_drop_point and drop_is_inside_box:
            continue

        if _in_stand(cx, cy):
            power_off_robot()
            sys.exit(1)

        if _in_conveyor(cx, cy):
            power_off_robot()
            sys.exit(1)

    if not (CAM_X_MIN <= MOVE_X <= CAM_X_MAX and CAM_Y_MIN <= MOVE_Y <= CAM_Y_MAX):
        raise RuntimeError(
            f"Pick target is outside camera scan zone: "
            f"X={MOVE_X:.3f}, Y={MOVE_Y:.3f}. "
            f"Allowed camera zone: X[{CAM_X_MIN:.3f},{CAM_X_MAX:.3f}], "
            f"Y[{CAM_Y_MIN:.3f},{CAM_Y_MAX:.3f}]."
        )

    current = r.get_tcp_pose()
    home = get_home_pose(current)

    lift_pick_forward = copy.deepcopy(home)
    lift_pick_forward[0] = PICK_TARGET_X
    lift_pick_forward[1] = PICK_TARGET_Y
    lift_pick_forward[2] = TRANSIT_HEIGHT
    lift_pick_forward[5] = math.radians(HOME_RZ)

    lift_pick = copy.deepcopy(lift_pick_forward)
    lift_pick[5] = math.radians(PICK_RZ_DEG)

    pick_pose = copy.deepcopy(lift_pick)
    pick_pose[2] = PICK_Z_DYNAMIC

    lift_pick_forward_after = copy.deepcopy(lift_pick_forward)

    lift_drop = copy.deepcopy(home)
    lift_drop[0] = DROP_X
    lift_drop[1] = DROP_Y
    lift_drop[2] = TRANSIT_HEIGHT
    lift_drop[5] = math.radians(HOME_RZ)

    drop_pose = copy.deepcopy(lift_drop)
    drop_pose[2] = DROP_RELEASE_Z
    lift_drop_grip = copy.deepcopy(lift_drop)
    lift_drop_grip[5] = math.radians(DROP_RZ_DEG)
    drop_pose_grip = copy.deepcopy(drop_pose)
    drop_pose_grip[5] = math.radians(DROP_RZ_DEG)

    phase1_rotate = [lift_pick_forward, lift_pick]
    phase1_approach = build_full_trajectory([lift_pick, pick_pose])

 
    phase2_depart = build_full_trajectory([pick_pose, lift_pick])
    phase2_reorient = [lift_pick, lift_pick_forward_after]
    phase2_approach = build_full_trajectory([lift_drop_grip,drop_pose_grip,])


    phase3_depart = build_full_trajectory([drop_pose_grip,lift_drop_grip,])

    try:
        _BYPASS_EXTRA_OBS = False
        validate_trajectory(phase1_rotate, label="Phase 1 rotate to grip angle")
        validate_trajectory(phase1_approach, label="Phase 1 approach")
        validate_trajectory(phase2_depart, label="Phase 2 depart")
        validate_trajectory(phase2_reorient, label="Phase 2 reorient forward")
        validate_trajectory(phase2_approach, label="Phase 2 approach")
        validate_trajectory(phase3_depart, label="Phase 3 depart")
    except RuntimeError as e:
        _BYPASS_EXTRA_OBS = False
        power_off_robot()
        sys.exit(1)

    # MCP/camera mode must not pause for terminal input.
    # Motion begins immediately after pre-flight validation.

    if not is_at_home(r):
        move_to_home_emergency(r)
    home = get_home_pose(r.get_tcp_pose())   # always fresh, using the REAL current position
    
    execute_joint_transit(r, home, lift_pick_forward, label="Phase 1 transit — Home -> lift_pick_forward")
    
    execute_trajectory(r, phase1_rotate, label="Phase 1 wrist rotate — forward -> grip angle", custom_speed=0.5, is_blending=False)
    time.sleep(0.3)

    # Open 30% larger than the object before descending.
    gripper_open_for_object(OBJECT_GRIP_WIDTH_M)

    execute_trajectory(r, phase1_approach, label="Phase 1 approach — lift_pick -> pick_pose")
    gripper_grip_object(OBJECT_GRIP_WIDTH_M)

    CARRIED_OBJECT_ENABLED = True

    execute_trajectory(r, phase2_depart, label="Phase 2 depart — pick_pose -> lift_pick")
    
    execute_trajectory(r, phase2_reorient, label="Phase 2 reorient — pick angle -> forward", custom_speed=0.5, is_blending=False)
    execute_joint_transit(r, lift_pick_forward_after, lift_drop, label="Phase 2 transit — pick_forward -> drop_forward")
    execute_trajectory(r, [lift_drop, lift_drop_grip], label="Phase 2 reorient — forward -> drop angle", custom_speed=0.5, is_blending=False)
    
    execute_trajectory(r, phase2_approach, label="Phase 2 approach — lift_drop -> drop_pose")
    gripper_release_object(OBJECT_GRIP_WIDTH_M)

    CARRIED_OBJECT_ENABLED = False

    execute_trajectory(r, phase3_depart, label="Phase 3 depart — drop_pose -> lift_drop")
    
    execute_trajectory(r,[lift_drop_grip, lift_drop],label="Phase 3 reorient — grip angle -> forward", custom_speed=0.5, is_blending=False)

    if MCP_IS_RELOCATING:
        execute_joint_transit(r, lift_drop, home, label="Phase 3 transit — lift_drop -> Home")
        if ROBOT_EVENT_CALLBACK:
            ROBOT_EVENT_CALLBACK("relocate_placed")
        return

    execute_joint_transit(r, lift_drop, home, label="Phase 3 transit — lift_drop -> Home")

    if not MCP_IS_RELOCATING:
        if ROBOT_EVENT_CALLBACK:
            ROBOT_EVENT_CALLBACK("pick_and_place_completed")

    



# =================================================================
# MCP COMPATIBILITY HELPERS
# =================================================================
# These helpers let the MCP server send object_name + x/y/z directly.
# They do not replace the existing planner, placement display, gripper logic,
# or trajectory execution code. They only bypass the manual terminal prompts.

MCP_NO_UI_MODE = False
_MCP_ROBOT_READY = False
AUTO_MCP_ROBOT_STARTUP = True  # Set True if this file should initialise robot/gripper inside run_mcp_pick_and_place().
MCP_MIN_VALID_Z_M = 0.005
ROBOT_EVENT_CALLBACK = None
MCP_IS_RELOCATING = False


def mcp_find_object_profile(object_name):
    """Resolve an MCP object name into the existing OBJECT_CATALOGUE profile."""
    name = str(object_name or "").strip().lower()

    match name:
        case "yellow cube" | "yellow":
            keys = ["1"]
        case "blue cube" | "blue":
            keys = ["2"]
        case "green cube" | "green":
            keys = ["3"]
        case "red cube" | "red":
            keys = ["4"]
        case "cube" | "box" | "unknown_blocker":
            keys = ["1", "2", "3", "4"]
        case "nut" | "hex nut" | "hexagonal nut":
            keys = ["5"]
        case "black marker" | "black" | "marker":
            keys = ["6"]
        case "medicine" | "medicine box" | "med":
            keys = ["7"]
        case "sponge":
            keys = ["8"]
        case "screwdriver":
            keys = ["9"]
        case _:
            keys = []

    # First use match-case result.
    for key in keys:
        if key in OBJECT_CATALOGUE:
            return dict(OBJECT_CATALOGUE[key])

    # Fallback: search catalogue labels/names.
    for obj in OBJECT_CATALOGUE.values():
        if name in {
            str(obj.get("label", "")).strip().lower(),
            str(obj.get("name", "")).strip().lower(),
        }:
            return dict(obj)

    raise ValueError(
        f"Unsupported object_name={object_name!r}. "
        "Use yellow cube, blue cube, green cube, red cube, nut, black marker, medicine, sponge, screwdriver, or unknown_blocker."
    )


def _mcp_object_height_from_z(selected_object, mcp_z):
    """
    Convert MCP z into an object-height hint.

    Important:
    - MCP z is NOT final TCP Z.
    - If MCP z is valid, it can override the catalogue height for this detection.
    - If MCP z is too low/invalid, the catalogue object height is used.
    """
    fallback_height = float(selected_object.get("height_m", DEFAULT_OBJECT_HEIGHT_M))

    try:
        z_value = float(mcp_z)
    except (TypeError, ValueError):
        return fallback_height

    if z_value < MIN_VALID_MCP_OBJECT_Z_M:
        return fallback_height

    # Clamp to avoid one bad depth reading making the gripper aim too high.
    # Most objects in the current catalogue are below 12 cm.
    return max(MIN_GRIP_HEIGHT_M, min(z_value, 0.120))


def _mcp_normalize_detection(raw, default_index=1):
    """
    Normalize one MCP detection into a consistent dict.

    Accepted keys:
        name/object_name/label/class
        x, y, z
        angle_deg/angle/yaw/rotation  (object yaw in robot base frame, from OBB RPY decomposition)
    """
    if raw is None:
        raise ValueError("Empty MCP detection received.")

    name = (
        raw.get("object_name")
        or raw.get("name")
        or raw.get("label")
        or raw.get("class")
        or raw.get("class_name")
    )

    if name is None:
        raise ValueError(f"MCP detection missing object name: {raw!r}")

    # Accept angle under several common key names from different camera pipelines.
    raw_angle = (
        raw.get("angle_deg")
        or raw.get("angle")
        or raw.get("yaw")
        or raw.get("rotation")
    )

    return {
        "index": int(raw.get("index", default_index)),
        "object_name": str(name).strip().lower(),
        "x": float(raw["x"]),
        "y": float(raw["y"]),
        "z": float(raw.get("z", 0.0)),
        "angle_deg": float(raw_angle) if raw_angle is not None else None,
        "w_px": float(raw.get("w_px", 0.0)),#using data from camera to confirm orientation
        "h_px": float(raw.get("h_px", 0.0)),
    }


def _mcp_detection_inside_placement_box(det):
    """
    Return True if a camera/MCP detection centre is already inside the placement box.
    Uses an inward tolerance so objects placed slightly near the edge still count.
    """
    INBOX_TOLERANCE_M = 0.05
    try:
        x, y = float(det["x"]), float(det["y"])
        return (
            PLACEMENT_BOX_X_MIN - INBOX_TOLERANCE_M <= x <= PLACEMENT_BOX_X_MAX + INBOX_TOLERANCE_M and
            PLACEMENT_BOX_Y_MIN - INBOX_TOLERANCE_M <= y <= PLACEMENT_BOX_Y_MAX + INBOX_TOLERANCE_M
        )
    except Exception:
        return False


def _mcp_make_placement_occupancy(det):
    """
    Convert one detected object inside the placement box into a PLACED_OBJECTS slot.

    The placement planner already avoids entries inside PLACED_OBJECTS, so this
    lets camera-detected objects inside the box reduce the available placement area.
    """
    obj = select_object_profile_by_name(det["object_name"])
    detected_height = _mcp_object_height_from_z(obj, det.get("z", 0.0))
    obj = dict(obj)
    obj["height_m"] = detected_height

    length, width = _object_footprint_for_placement(obj, rotated=False)

    return {
        "x": float(det["x"]),
        "y": float(det["y"]),
        "length_m": length,
        "width_m": width,
        "rotated": False,
        "placement_angle_deg": planned_rz_for_object(obj),
        "source": "mcp_camera_placement_box",
        "name": det["object_name"],
    }


def _load_mcp_placement_occupancy_into_planner():
    """
    Seed PLACED_OBJECTS with camera-detected objects that are already in the box.

    This runs before planning the new drop slot.
    """
    for slot in MCP_PLACEMENT_BOX_DETECTIONS:
        PLACED_OBJECTS.append(dict(slot))


def _placement_box_area_m2():
    return max(0.0, PLACEMENT_BOX_X_MAX - PLACEMENT_BOX_X_MIN) * max(0.0, PLACEMENT_BOX_Y_MAX - PLACEMENT_BOX_Y_MIN)


def _placement_occupied_area_m2():
    area = 0.0
    for slot in PLACED_OBJECTS:
        area += max(0.0, float(slot.get("length_m", 0.0))) * max(0.0, float(slot.get("width_m", 0.0)))
    return area


def _placement_available_area_m2():
    return max(0.0, _placement_box_area_m2() - _placement_occupied_area_m2())


def diagnostic_print_first_pick(sequence):
    """
    Diagnostic print 1/2:
    Shows the first MCP pickup coordinate after filtering out objects already
    inside the placement box.
    """
    if not MCP_DIAGNOSTIC_PRINTS_ENABLED or not sequence:
        return

    first = sequence[0]
    obj = first.get("object", {})
    print(
        "[DIAGNOSTIC] First pickup: "
        f"object={first.get('object_name', obj.get('label', obj.get('name', 'object')))}, "
        f"X={first['pick_x']:.3f}, Y={first['pick_y']:.3f}, "
        f"MCP_Z={float(first.get('pick_z', 0.0)):.3f}, "
        f"height_used={float(obj.get('mcp_height_used_m', obj.get('height_m', 0.0))):.3f}"
    )


def diagnostic_print_placement_and_box(sequence):
    """
    Diagnostic print 2/2:
    Shows the planned placement coordinate, placement-box bounds, and estimated
    remaining rectangular area after accounting for camera-detected box objects.
    """
    if not MCP_DIAGNOSTIC_PRINTS_ENABLED or not sequence:
        return

    obj = sequence[0].get("object", {})
    slot = obj.get("_planned_drop_slot", {})

    print(
        "[DIAGNOSTIC] Placement: "
        f"X={float(slot.get('x', 0.0)):.3f}, Y={float(slot.get('y', 0.0)):.3f}, "
        f"footprint={float(slot.get('length_m', 0.0))*1000:.1f}x{float(slot.get('width_m', 0.0))*1000:.1f}mm, "
        f"box_X=[{PLACEMENT_BOX_X_MIN:.3f},{PLACEMENT_BOX_X_MAX:.3f}], "
        f"box_Y=[{PLACEMENT_BOX_Y_MIN:.3f},{PLACEMENT_BOX_Y_MAX:.3f}], "
        f"available_area={_placement_available_area_m2():.4f}m^2"
    )


def mcp_build_pick_sequence(target_object_name=None, x=None, y=None, z=0.0, angle=None, detections=None, grasp_label=None, gap_mm_reduction_percent=0.0):
    """
    Build the internal pick_sequence from MCP data.

    Camera/MCP can send all detected objects. Objects already inside the
    placement box are NOT valid pickup targets; they are stored as placement
    occupancy so the smart drop planner avoids them.

    MCP z is treated as an object-height hint, not final robot TCP Z.
    MCP angle is the object yaw in robot base frame from YOLOv11 OBB RPY decomposition.
    If angle is None, the catalogue preferred_grasp_angle_deg is used instead.

    grasp_label — for the pipe: which end the segmentation model selected
    (grasp_A or grasp_B). When provided, x/y/z already point to that exact
    end and catalogue offsets are confirmed zero so nothing shifts the position.
    """
    global MCP_DYNAMIC_OBSTACLES, MCP_PLACEMENT_BOX_DETECTIONS

    MCP_DYNAMIC_OBSTACLES = []
    MCP_PLACEMENT_BOX_DETECTIONS = []

    normalized = []

    if detections:
        for i, det in enumerate(detections, start=1):
            normalized.append(_mcp_normalize_detection(det, default_index=i))

    if normalized:
        target_key = str(target_object_name or normalized[0]["object_name"]).strip().lower()

        pickable = []

        for det in normalized:
            if _mcp_detection_inside_placement_box(det):
                MCP_PLACEMENT_BOX_DETECTIONS.append(_mcp_make_placement_occupancy(det))
                continue

            pickable.append(det)

        target_candidates = [
            det for det in pickable
            if det["object_name"] == target_key
        ]

        if not target_candidates:
            raise ValueError(
                f"Target object {target_key!r} was not found as a pickable object. "
                "It may already be inside the placement box or missing from camera detections."
            )

        target = target_candidates[0]

        for det in pickable:
            if det is target:
                continue

            obstacle_obj = select_object_profile_by_name(det["object_name"])
            obstacle_height = _mcp_object_height_from_z(obstacle_obj, det.get("z", 0.0))

            MCP_DYNAMIC_OBSTACLES.append({
                "name": det["object_name"],
                "x": det["x"],
                "y": det["y"],
                "z": det.get("z", 0.0),
                "height_m": obstacle_height,
                "length_m": float(obstacle_obj.get("length_m", obstacle_obj.get("width_m", 0.04))),
                "width_m": float(obstacle_obj.get("width_m", 0.04)),
                "breadth_m": float(obstacle_obj.get("breadth_m", obstacle_obj.get("width_m", 0.04))),
            })
    else:
        if target_object_name is None:
            raise ValueError("target_object_name is required when detections are not provided.")
        if x is None or y is None:
            raise ValueError("x and y are required when detections are not provided.")

        target = {
            "index": 1,
            "object_name": str(target_object_name).strip().lower(),
            "x": float(x),
            "y": float(y),
            "z": float(z),
            "angle_deg": float(angle) if angle is not None else None,
        }

        if _mcp_detection_inside_placement_box(target):
            raise ValueError(
                f"Target object {target['object_name']!r} is already inside the placement box, "
                "so it is not treated as a pickable object."
            )

    selected_object = select_object_profile_by_name(target["object_name"])

    catalogue_height_m = float(selected_object.get("height_m", 0.03))
    raw_detected_z = float(target.get("z", 0.0))
    Z_PLAUSIBILITY_MARGIN_M = 0.05

    if not (-Z_PLAUSIBILITY_MARGIN_M <= raw_detected_z <= catalogue_height_m + Z_PLAUSIBILITY_MARGIN_M):
        raise ValueError(
            f"Detected Z ({raw_detected_z*1000:.1f}mm) for object "
            f"{target['object_name']!r} is outside the plausible range "
            f"for its known height ({catalogue_height_m*1000:.1f}mm) -- "
            f"refusing to trust this detection."
        )

    detected_height = _mcp_object_height_from_z(selected_object, target.get("z", 0.0))

    selected_object = dict(selected_object)
    selected_object["height_m"] = detected_height
    selected_object["mcp_detected_z_m"] = float(target.get("z", 0.0))
    selected_object["mcp_height_used_m"] = detected_height

    # Apply camera angle if provided, overriding the catalogue preferred_grasp_angle_deg.
    # The camera gives absolute yaw in robot base frame. Convert to a gripper offset
    # relative to HOME_RZ so planned_rz_for_object produces the correct absolute TCP RZ.
    camera_angle = target.get("angle_deg")
    if camera_angle is not None:
        #selected_object["preferred_grasp_angle_deg"] = float(camera_angle) + CAMERA_ANGLE_OFFSET_DEG - HOME_RZ
        
        w_px = float(target.get("w_px", 0.0))
        h_px = float(target.get("h_px", 0.0))
        # Only apply the dynamic perpendicular correction for genuinely elongated
        # # objects (long/width ratio significant) -- symmetric objects like cubes
        # # don't need this and shouldn't have their angle flipped by pixel noise.
        length_m = float(selected_object.get("length_m", 0.0))
        width_m = float(selected_object.get("width_m", 0.0))
        is_elongated = width_m > 0 and (length_m / width_m) > 1.5
        
        perpendicular_correction = 0.0
        if is_elongated and w_px > 0 and h_px > 0:
            if h_px > w_px :
                perpendicular_correction = 90.0   # long axis aligned with reported angle -> need perpendicular
                 # else: h_px > w_px means angle_deg already points along the short axis -- no correction needed
        selected_object["preferred_grasp_angle_deg"] = float(camera_angle) + perpendicular_correction + CAMERA_ANGLE_OFFSET_DEG - HOME_RZ
        print(f"[Grasp Angle Debug] w_px={w_px:.1f} h_px={h_px:.1f} is_elongated={is_elongated} "
              f"correction={perpendicular_correction} final_preferred={selected_object['preferred_grasp_angle_deg']:.2f}")
        selected_object["mcp_camera_angle_deg"] = float(camera_angle)
    else:
        selected_object["mcp_camera_angle_deg"] = None  # catalogue default will be used

    # For the pipe, when vision segmentation provides a grasp_label the x/y/z
    # coordinates already point to the chosen pipe end. Confirm offsets are zero
    # so nothing shifts the segmentation-computed position, and store the label
    # for diagnostics only.
    if selected_object.get("label") == "pipe" and grasp_label:
        selected_object["grasp_offset_x_m"] = 0.0
        selected_object["grasp_offset_y_m"] = 0.0
        selected_object["grasp_offset_z_m"] = 0.0
        selected_object["_chosen_grasp_label"] = grasp_label

    selected_object["_gap_mm_reduction_percent"] = gap_mm_reduction_percent

    sequence = [{
        "index": 1,
        "pick_x": float(target["x"]) + PICK_OFFSET_X_M,
        "pick_y": float(target["y"]) + PICK_OFFSET_Y_M,
        "pick_z": float(target.get("z", 0.0)),
        "object_name": target["object_name"],
        "object": selected_object,
        "mcp_detections": normalized,
    }]

    return sequence

def mcp_robot_startup_once():
    """Run real robot startup once for MCP server lifetime. Gripper skipped in NO_GRIPPER_VERSION."""
    global _MCP_ROBOT_READY
    if _MCP_ROBOT_READY:
        return

    ensure_robot_ready(r)
    check_starting_position(r)
    # NO_GRIPPER_VERSION: gripper_startup() and gripper_open() skipped

    if HAS_KEYBOARD:
        kb_thread = threading.Thread(target=keyboard_listener, args=(r,), daemon=True)
        kb_thread.start()

    _MCP_ROBOT_READY = True




def run_mcp_pick_and_place(object_name=None, x=None, y=None, z=0.0, angle=None, detections=None, grasp_label=None, gap_mm_reduction_percent=0.0):
    """
    MCP entry point for robot execution.

    The chosen target is picked. Other detected objects outside the placement
    box become dynamic obstacles. Detected objects already inside the placement
    box reserve placement area and are not pickable.

    MCP z is used only as an object-height hint; TCP Z is calculated by this robot code.
    MCP angle is the object yaw in degrees in robot base frame, from YOLOv11 OBB RPY
    decomposition. If None, the catalogue preferred_grasp_angle_deg is used instead.

    grasp_label — for the pipe: which end vision segmentation selected
    (grasp_A or grasp_B). Stored for diagnostics. x/y/z already point to
    the correct end when this is provided.
    """
    global MCP_NO_UI_MODE, MCP_IS_RELOCATING
    MCP_NO_UI_MODE = True
    MCP_IS_RELOCATING = False

    try:
        if AUTO_MCP_ROBOT_STARTUP:
            mcp_robot_startup_once()

        # Proactively clear any lingering controller errors (e.g. [3104]) 
        # and ensure automatic mode before starting the pick sequence.
        import time
        r.reset_errors()
        time.sleep(0.1)
        r.switch_to_automatic_mode()

        sequence = mcp_build_pick_sequence(
            target_object_name=object_name,
            x=x,
            y=y,
            z=z,
            angle=angle,
            detections=detections,
            grasp_label=grasp_label,
            gap_mm_reduction_percent=gap_mm_reduction_percent,
        )

        preplan_all_drop_slots(sequence)

        diagnostic_print_first_pick(sequence)
        diagnostic_print_placement_and_box(sequence)

        for seq_item in sequence:
            set_active_pick_item(seq_item, 1, 1)
            execute_one_pick_cycle(seq_item, 1, 1)
    except Exception as e:
        if not MCP_INTENTIONAL_STOP and ROBOT_EVENT_CALLBACK:
            ROBOT_EVENT_CALLBACK("error", str(e))
        if not MCP_INTENTIONAL_STOP:
            raise

    return {
        "status":                       "ok",
        "picked_object":                sequence[0].get("object_name", object_name),
        "pick_x":                       sequence[0]["pick_x"],
        "pick_y":                       sequence[0]["pick_y"],
        "mcp_detected_z":               sequence[0].get("pick_z", z),
        "mcp_camera_angle_deg":         sequence[0]["object"].get("mcp_camera_angle_deg"),
        "chosen_grasp":                 sequence[0]["object"].get("_chosen_grasp_label"),
        "object_height_used_m":         sequence[0]["object"].get("mcp_height_used_m"),
        "dynamic_obstacle_count":       len(MCP_DYNAMIC_OBSTACLES),
        "placement_box_detected_count": len(MCP_PLACEMENT_BOX_DETECTIONS),
        "avoidance_mode":               MCP_DYNAMIC_OBJECT_AVOIDANCE_MODE,
        "available_placement_area_m2":  _placement_available_area_m2(),
        "drop_slot":                    sequence[0]["object"].get("_planned_drop_slot", {}),
    }


def _find_relocation_spot(obstacle_name, obstacle_x, obstacle_y, detections, target_name=None, obstacle_angle=None):
    """
    Finds a safe spot to drop the obstacle.
    - Stay inside the camera scan zone (CAM_X_MIN/MAX, CAM_Y_MIN/MAX) so
      YOLO can see it on the next scan.
    - Stay away from other objects by at least RELOCATION_CLEARANCE_M.
    - Stay away from the target object specifically by at least TARGET_CLEARANCE_M.
    - Stay away from the conveyor and camera stand no-go zones.
    - Stay away from the current obstacle position itself.

    Returns [x, y] or raises RuntimeError if no spot found.
    """
    import cv2
    import math

    RELOCATION_CLEARANCE_M = 0.01   # minimum edge-to-edge gap from other objects
    TARGET_CLEARANCE_M     = 0.01   # extra edge-to-edge clearance from target specifically
    GRID_STEP_M            = 0.02   # search grid resolution (finer grid)
    BORDER_M               = 0.02   # minimum distance from workspace edge

    def get_obb(obj_name, cx, cy, angle_deg, extra_clearance=0.0):
        info = OBJECT_CATALOGUE.get(obj_name, {})
        l = float(info.get("length_m", 0.04))
        w = float(info.get("breadth_m", 0.04))
        return ((float(cx), float(cy)), (l + extra_clearance * 2, w + extra_clearance * 2), float(angle_deg))

    obs_angle = obstacle_angle if obstacle_angle is not None else 0.0
    if obstacle_angle is None:
        for det in (detections or []):
            if det.get("object_name") == obstacle_name:
                obs_angle = float(det.get("angle_deg", 0.0))
                break

    avoid_rects = []
    target_rects = []

    # The obstacle's original position is a no-go zone
    avoid_rects.append(get_obb(obstacle_name, obstacle_x, obstacle_y, obs_angle, RELOCATION_CLEARANCE_M))

    for det in (detections or []):
        det_name = det.get("object_name")
        det_x = float(det.get("x", 0))
        det_y = float(det.get("y", 0))
        det_angle = float(det.get("angle_deg", 0.0))
        
        if target_name and det_name == target_name:
            target_rects.append(get_obb(det_name, det_x, det_y, det_angle, TARGET_CLEARANCE_M))
        else:
            avoid_rects.append(get_obb(det_name, det_x, det_y, det_angle, RELOCATION_CLEARANCE_M))

    best_score = -1
    best_xy = None

    x = CAM_X_MIN + BORDER_M
    while x <= CAM_X_MAX - BORDER_M:
        y = CAM_Y_MIN + BORDER_M
        while y <= CAM_Y_MAX - BORDER_M:
            # Skip conveyor and stand no-go zones.
            if gripper_in_conveyor(x, y, Z_MIN):
                y += GRID_STEP_M
                continue
            if gripper_in_stand(x, y, Z_MIN):
                y += GRID_STEP_M
                continue

            cand_rect = get_obb(obstacle_name, x, y, obs_angle, 0.0)

            too_close = False
            for rect in avoid_rects:
                ret, _ = cv2.rotatedRectangleIntersection(cand_rect, rect)
                if ret != cv2.INTERSECT_NONE:
                    too_close = True
                    break

            if not too_close:
                for rect in target_rects:
                    ret, _ = cv2.rotatedRectangleIntersection(cand_rect, rect)
                    if ret != cv2.INTERSECT_NONE:
                        too_close = True
                        break

            if not too_close:
                # Score based on distance to nearest object center
                min_edge_dist = min(
                    (math.hypot(x - r[0][0], y - r[0][1]) for r in avoid_rects),
                    default=999.0
                )
                target_min_edge_dist = min(
                    (math.hypot(x - r[0][0], y - r[0][1]) for r in target_rects),
                    default=999.0
                )
                score = min(min_edge_dist, target_min_edge_dist)
                if score > best_score:
                    best_score = score
                    best_xy = [x, y]

            y += GRID_STEP_M
        x += GRID_STEP_M

    if best_xy:
        return best_xy

    raise RuntimeError(
        f"No safe relocation spot found for {obstacle_name!r} in pick workspace. "
        "Workspace may be too crowded."
    )


def run_mcp_relocate_object(
    obstacle_name,
    obstacle_x,
    obstacle_y,
    obstacle_z=0.0,
    obstacle_angle=None,
    detections=None,
    target_name=None,
):
    """
    MCP entry point: pick an obstacle object and drop it at a safe empty
    spot within the pick workspace (camera scan zone), then signal the MCP
    server to trigger a fresh YOLO photo before returning to Qwen.

    This is NOT a placement-box drop. The object stays in the pick workspace
    so YOLO can re-detect the updated scene.

    Flow:
      1. Find a safe relocation XY within the camera scan zone.
      2. Build a single-object pick sequence targeting the obstacle.
      3. Override the planned drop slot to the relocation XY instead of the
         placement box.
      4. Execute the pick-and-drop cycle.
      5. Return status + relocation coordinates so the MCP server knows to
         trigger a fresh camera detection before passing control back to Qwen.
    """
    global MCP_NO_UI_MODE, MCP_IS_RELOCATING
    MCP_NO_UI_MODE = True
    MCP_IS_RELOCATING = True

    try:
        if AUTO_MCP_ROBOT_STARTUP:
            mcp_robot_startup_once()

        # Step 1: find a safe drop spot in the workspace.
        reloc_xy = _find_relocation_spot(
            obstacle_name, obstacle_x, obstacle_y, detections, target_name=target_name, obstacle_angle=obstacle_angle
        )
        reloc_x, reloc_y = reloc_xy

        # Step 2: build a pick sequence for the obstacle.
        # Pass all detections so other objects become dynamic obstacles during planning.
        sequence = mcp_build_pick_sequence(
            target_object_name=obstacle_name,
            x=obstacle_x,
            y=obstacle_y,
            z=obstacle_z,
            angle=obstacle_angle,
            detections=detections,
        )

        selected_object = sequence[0]["object"]

        # Step 3: override the planned drop slot to the relocation position.
        # estimate_drop_tcp_z_for_object gives the correct TCP Z for a table-level drop.
        reloc_z = estimate_drop_tcp_z_for_object(selected_object)

        reloc_slot = {
            "x":        reloc_x,
            "y":        reloc_y,
            "z":        reloc_z,
            "angle_deg": float(obstacle_angle) if obstacle_angle is not None else HOME_RZ,
            "length_m": float(selected_object.get("length_m", selected_object.get("width_m", 0.04))),
            "width_m":  float(selected_object.get("width_m", 0.04)),
        }

        selected_object["_planned_drop_slot"] = reloc_slot

        # Step 4: execute the pick-and-drop.
        # preplan_all_drop_slots is NOT called here — the slot is already set above
        # and we do not want to allocate placement-box space for a workspace relocation.
        set_active_pick_item(sequence[0], 1, 1)
        execute_one_pick_cycle(sequence[0], 1, 1)
    except Exception as e:
        if not MCP_INTENTIONAL_STOP and ROBOT_EVENT_CALLBACK:
            ROBOT_EVENT_CALLBACK("error", str(e))
        if not MCP_INTENTIONAL_STOP:
            raise

    # Step 5: return status including relocation coordinates.
    # The MCP server uses "requires_redetection": True to trigger a fresh
    # YOLO photo before passing the updated scene back to Qwen.
    return {
        "status":               "ok",
        "action":               "relocate",
        "relocated_object":     obstacle_name,
        "original_x":           obstacle_x,
        "original_y":           obstacle_y,
        "relocation_x":         reloc_x,
        "relocation_y":         reloc_y,
        "requires_redetection": True,   # MCP server must trigger fresh YOLO photo
    }


def main():
    """
    Direct terminal execution is disabled for this MCP/camera version.

    Start the MCP server and call run_mcp_pick_and_place(...) with camera
    detections instead of using manual multi-pick input.
    """
    raise RuntimeError("Run through MCP server. Manual terminal main() is disabled.")



if __name__ == "__main__":
    main()
