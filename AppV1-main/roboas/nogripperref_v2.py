"""
pick_place_real.py  ——  LARA 5 REAL ROBOT VERSION
============================================================
Real-robot counterpart of pick_place_sim.py.

Key differences from the simulation version:
  - switch_to_real()        instead of switch_to_simulation()
  - power_on() / power_off() to release/lock physical brakes
  - init_program()           to sync the motion engine
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
from serial.tools import list_ports
# NO_GRIPPER_VERSION: pymodbus not imported — gripper is disabled

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

# =================================================================
# SMART LEBAI GRIPPER GEOMETRY + MODBUS CONTROL
# =================================================================
# Physical gripper model, measured from flange/TCP origin to lowest fingertip point.
# No extra protection layer here; these are the real flange/TCP-to-contact lengths you measured.
GRIPPER_SAFETY_LENGTH = 0.000
TABLE_Z_M = -0.0198

GRIPPER_LEN_OPEN = 0.2009
GRIPPER_LEN_CLOSED = 0.2259

GRIPPER_LENGTH = GRIPPER_LEN_CLOSED
# Keep planner conservative by default: assume longest possible gripper length.
# Surface/table height in robot base coordinates.
# Calibrate this by jogging the gripper to the desired contact height and using:
# TABLE_Z_M = TCP_Z - gripper_length_at_grip - object_height/2
# Start with 0.105 m because your real gripper appeared about 10.5 cm above the table.
 
# This will be updated after object selection to match the actual holding opening.
ACTIVE_GRIPPER_LENGTH = GRIPPER_LENGTH
# -----------------------------------------------------------------
# SEGMENTED END-EFFECTOR COLLISION MODEL
# -----------------------------------------------------------------
# Old code treated the whole gripper as one long 45 mm radius cylinder.
# That is safe, but very conservative. The new model splits the tool into:
#   1) flange/body circular section,
#   2) neck circular section,
#   3) lower jaw rectangular section.
# This lets the planner see that the lower gripping part is not a huge circle.

CIRCULAR_EXTRA_DIAMETER_M = 0.005   # extra 0.5 cm added only to circular collision partsS

FLANGE_DIAMETER_M = 0.087 + CIRCULAR_EXTRA_DIAMETER_M  # 87 mm CAD diameter + 5 mm allowance
FLANGE_RADIUS_M = FLANGE_DIAMETER_M / 2.0              # 46 mm

NECK_LENGTH_M = 0.079964                               # fixed section between your two CAD planes
NECK_DIAMETER_M = 0.068 + CIRCULAR_EXTRA_DIAMETER_M    # 68 mm CAD diameter + 5 mm allowance
NECK_RADIUS_M = NECK_DIAMETER_M / 2.0                  # 36.5 mm

# Approximate length of the top flange/body section along TCP-Z.
# Adjust this if you later measure the flange axial thickness directly.
FLANGE_LENGTH_M = 0.030

# Lower jaw collision model. The jaws are better treated as a rectangular box
# rather than a circular radius. 50 mm comes from your CAD front-view measurement.
JAW_FIXED_WIDTH_M = 0.050
JAW_MIN_DYNAMIC_WIDTH_M = 0.010

# Carried object collision model
CARRIED_OBJECT_ENABLED = False
CARRIED_OBJECT_HEIGHT_M = 0.0
CARRIED_OBJECT_WIDTH_M = 0.0
CARRIED_OBJECT_DEPTH_M = 0.0
CARRIED_OBJECT_BELOW_GRIP_M = 0.0

# Keep the old single radius as a worst-case radius for fixed stand/conveyor zones
# and planner clearance generation. This should be the largest circular radius.
GRIPPER_RADIUS = max(FLANGE_RADIUS_M, NECK_RADIUS_M)
END_EFFECTOR_MAX_RADIUS = GRIPPER_RADIUS

# Jaw stroke model.
# NOTE:
# The gripper physically may be advertised/estimated as 90 mm, but your latest
# test showed the commanded percentage produced a larger real opening
# (~55 mm when you wanted ~40 mm). That means the Modbus 0-100% scale must be
# calibrated to the REAL measured jaw opening, not only the nominal datasheet value.
#
# If the gripper still opens too wide, tune this value using:
#   EFFECTIVE_COMMAND_STROKE_M = measured_opening_m / (command_percent / 100)
# Example: if full-open internal stroke is measured as 90 mm, use 0.090 m.
MAX_STROKE_M = 0.090              # actual usable internal jaw stroke/opening; 100% = 90 mm
# =================================================================
# GRIPPER PERCENTAGE CALIBRATION
# =================================================================
# The real gripper appears to open wider than the simple linear
# MAX_STROKE_M calculation predicts.
#
# Example from test:
#   desired cube opening ≈ 40 mm
#   real opening was ≈ 45 mm
#   scale = 40 / 45 ≈ 0.88
#
# This only scales the command percentage.
# It does NOT change MAX_STROKE_M, object dimensions, or placement footprint.
GRIPPER_PERCENT_SCALE = 1
GRIPPER_PERCENT_OFFSET = 0.0



# IMPORTANT:
# Jaw stroke/opening is NOT the same as total physical gripper width.
# Your measured full-open stroke/opening is about 90 mm, but the whole gripper
# can physically occupy about 150 mm including jaw thickness/body protrusion.
MAX_PHYSICAL_GRIPPER_WIDTH_M = 0.150
# Physical lower-gripper footprint used only for placement/collision clearance.
# This is NOT jaw stroke. It estimates the outer size that could touch the box/object.
GRIPPER_PHYSICAL_CLOSED_LENGTH_M = 0.060  # 0% open -> 6 cm outer footprint
GRIPPER_PHYSICAL_OPEN_LENGTH_M = 0.150    # 100% open -> 15 cm outer footprint
GRIPPER_PHYSICAL_DEPTH_M = 0.035          # constant 3.5 cm depth

MAX_PHYSICAL_GRIPPER_HALF_WIDTH_M = MAX_PHYSICAL_GRIPPER_WIDTH_M / 2.0

# Approximate lower-jaw outer width when the jaws are open.
# Used only for collision/placement clearance, not for gripping conversion.
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

# Object selection catalogue. Add more objects here later.
# Dimensions are in metres.
OBJECT_CATALOGUE = {
    "1": {
        "label": "yellow cube",
        "name": "Yellow Cube",
        "length_m": 0.0,
        "width_m": 0.0,
        "breadth_m": 0.0,
        "height_m": 0.0,
        "object_orientation_deg": 90.0,
        "preferred_grasp_angle_deg": 0.0,
        "description": "Yellow cube",
    },
    "2": {
        "label": "blue cube",
        "name": "Blue Cube",
        "length_m": 0.0,
        "width_m": 0.0,
        "breadth_m": 0.0,
        "height_m": 0.0,
        "object_orientation_deg": 90.0,
        "preferred_grasp_angle_deg": 0.0,
        "description": "Blue cube",
    },
    "3": {
        "label": "green cube",
        "name": "Green Cube",
        "length_m": 0.0,
        "width_m": 0.0,
        "breadth_m": 0.0,
        "height_m": 0.0,
        "object_orientation_deg": 90.0,
        "preferred_grasp_angle_deg": 0.0,
        "description": "Green cube",
    },
    "4": {
        "label": "red cube",
        "name": "Red Cube",
        "length_m": 0.0,
        "width_m": 0.0,
        "breadth_m": 0.0,
        "height_m": 0.0,
        "object_orientation_deg": 90.0,
        "preferred_grasp_angle_deg": 0.0,
        "description": "Red cube",
    },
    "5": {
        "label": "nut",
        "name": "Nut",
        "length_m": 0.0,
        "width_m": 0.0,
        "breadth_m": 0.0,
        "height_m": 0.0,
        "object_orientation_deg": 90.0,
        "preferred_grasp_angle_deg": 0.0,
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
        "length_m": 0.0,
        "width_m": 0.0,
        "breadth_m": 0.0,
        "height_m": 0.0,
        "object_orientation_deg": 90.0,
        "preferred_grasp_angle_deg": 0.0,
        "description": "Medicine",
    },
    "8": {
        "label": "sponge",
        "name": "Sponge",
        "length_m": 0.0,
        "width_m": 0.0,
        "breadth_m": 0.0,
        "height_m": 0.0,
        "object_orientation_deg": 90.0,
        "preferred_grasp_angle_deg": 0.0,
        "description": "Sponge",
    },
    "9": {
        "label": "screwdriver",
        "name": "Screwdriver",
        "length_m": 0.0,
        "width_m": 0.0,
        "breadth_m": 0.0,
        "height_m": 0.0,
        "object_orientation_deg": 90.0,
        "preferred_grasp_angle_deg": 0.0,
        "description": "Screwdriver",
    },
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
    "cube": 0.92,       # 40 mm cube -> command about 36.8 mm equivalent
    "medicine": 0.85,   # 51.2 mm medicine -> command about 43.5 mm equivalent
}

OBJECT_GRIP_COMMAND_MIN_M = {
    "cube": 0.034,
    "medicine": 0.040,
}
# =================================================================
# HYBRID POSITION + FORCE GRIP TUNING
# =================================================================
# CHANGE THIS FIRST:
#   HYBRID_GRIP_CONTACT_TORQUE_DELTA
#
# Larger value  = gripper squeezes harder before stopping.
# Smaller value = gripper stops earlier / gentler grip.
HYBRID_FORCE_GRIP_ENABLED = True

# =================================================================
# NO_GRIPPER_VERSION — all gripper hardware constants stubbed out
# =================================================================
HYBRID_GRIP_CONTACT_TORQUE_DELTA    = 20
HYBRID_GRIP_MAX_EXTRA_CLOSE_PERCENT = 8
HYBRID_GRIP_STEP_PERCENT            = 2
HYBRID_GRIP_STEP_DELAY_S            = 0.05
HYBRID_GRIP_MIN_PERCENT             = 0
HYBRID_GRIP_TORQUE_SAMPLES          = 3

GRIPPER_PORT = "NONE"
GRIPPER_ADDR = 1

REG_POSITION   = 0x9C40
REG_FORCE      = 0x9C41
REG_CUR_POS    = 0x9C45
REG_CUR_TORQUE = 0x9C46
REG_STATUS     = 0x9C47
REG_HOME       = 0x9C48
REG_SPEED      = 0x9C4A
REG_AUTO_HOME  = 0x9C9A

# No physical gripper client — all writes/reads are no-ops
gripper = None

CURRENT_GRIPPER_PERCENT = 100

def clamp_percent(value):
    return int(max(0, min(100, round(value))))

def object_width_to_percent(object_width_m):
    """
    Convert desired real jaw opening width into Lebai 0-100% command.

    Uses a global percentage calibration because the real gripper opening is
    larger than the simple linear MAX_STROKE_M model predicted.
    """
    raw_percent = (object_width_m / MAX_STROKE_M) * 100.0
    calibrated_percent = raw_percent * GRIPPER_PERCENT_SCALE + GRIPPER_PERCENT_OFFSET
    return clamp_percent(calibrated_percent)



def percent_to_commanded_opening_m(percent):
    """
    Display/debug helper showing calibrated target opening estimate.
    """
    p = (clamp_percent(percent) - GRIPPER_PERCENT_OFFSET) / max(GRIPPER_PERCENT_SCALE, 1e-6)
    return (p / 100.0) * MAX_STROKE_M

def percent_to_opening_m(percent):
    return (clamp_percent(percent) / 100.0) * MAX_STROKE_M

def gripper_length_from_percent(percent):
    """
    Dynamic vertical gripper length from flange/TCP to fingertip.
    0% open   = fully closed = longest effective length.
    100% open = fully open   = shortest effective length.
    Includes the 20 mm protection layer.
    """
    percent = clamp_percent(percent)
    return GRIPPER_LEN_CLOSED - (percent / 100.0) * (GRIPPER_LEN_CLOSED - GRIPPER_LEN_OPEN)


def get_object_grip_label(selected_object=None):
    """Return stable lowercase label/name for object-specific grip calibration."""
    try:
        obj = selected_object if selected_object is not None else globals().get("SELECTED_OBJECT", {})
        return str(obj.get("label", obj.get("name", ""))).strip().lower()
    except Exception:
        return ""


def calibrated_close_width_for_object(object_width_m, selected_object=None):
    """
    Return command width used for CLOSE/HOLD only.

    The robot can still pre-open/release wider using the real object width.
    This avoids medicine/cube being held too loosely without changing placement.
    """
    label = get_object_grip_label(selected_object)
    scale = OBJECT_GRIP_COMMAND_SCALE.get(label, 1.0)
    min_width = OBJECT_GRIP_COMMAND_MIN_M.get(label, 0.0)

    calibrated = object_width_m * scale
    calibrated = max(min_width, calibrated)

    # Never command wider than the original object width here.
    return min(object_width_m, calibrated)


def get_pre_pick_open_percent(object_width_m):
    """
    Opening before descending. Opens slightly wider than the target grip width before descending.
    PRE_PICK_EXTRA_RATIO = 0.30 means 30% wider than the object grip width.
    """
    return object_width_to_percent(object_width_m * (1.0 + PRE_PICK_EXTRA_RATIO))

def get_pick_close_percent(object_width_m):
    """Target gripper opening for gripping the object, with object-specific calibration."""
    close_width_m = calibrated_close_width_for_object(
        object_width_m,
        globals().get("SELECTED_OBJECT", None),
    )
    return object_width_to_percent(close_width_m)


def select_object_profile():
    """
    Manual object selection is disabled in MCP/camera mode.

    Use select_object_profile_by_name(object_name) through run_mcp_pick_and_place().
    """
    raise RuntimeError("Manual object selection is disabled. Use MCP object_name input.")


def select_object_profile_by_name(object_name):
    """
    Select object profile from OBJECT_CATALOGUE using MCP object name.

    This replaces manual user selection for MCP mode while preserving the
    original OBJECT_CATALOGUE dimensions, grip calibration, and placement logic.
    """
    if object_name is None:
        raise ValueError(
        f"Unsupported object_name={object_name!r}. "
        "Use yellow cube, blue cube, green cube, red cube, nut, black marker, medicine, sponge, or screwdriver."
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
        raise ValueError(
        f"Unsupported object_name={object_name!r}. "
        "Use yellow cube, blue cube, green cube, red cube, nut, black marker, medicine, sponge, or screwdriver."
    )

    if name is None:
        raise ValueError(
        f"Unsupported object_name={object_name!r}. "
        "Use yellow cube, blue cube, green cube, red cube, nut, black marker, medicine, sponge, or screwdriver."
    )

    return {
        "index": int(raw.get("index", default_index)),
        "object_name": str(name).strip().lower(),
        "x": float(raw["x"]),
        "y": float(raw["y"]),
        "z": float(raw.get("z", 0.0)),
        "angle_deg": float(raw_angle) if raw_angle is not None else None,
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


def mcp_build_pick_sequence(target_object_name=None, x=None, y=None, z=0.0, angle=None, detections=None, grasp_label=None):
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
        f"Unsupported object_name={object_name!r}. "
        "Use yellow cube, blue cube, green cube, red cube, nut, black marker, medicine, sponge, or screwdriver."
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
            raise ValueError(
        f"Unsupported object_name={object_name!r}. "
        "Use yellow cube, blue cube, green cube, red cube, nut, black marker, medicine, sponge, or screwdriver."
    )

    selected_object = select_object_profile_by_name(target["object_name"])
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
        selected_object["preferred_grasp_angle_deg"] = float(camera_angle) - HOME_RZ
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

    sequence = [{
        "index": 1,
        "pick_x": float(target["x"]),
        "pick_y": float(target["y"]),
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




def run_mcp_pick_and_place(object_name=None, x=None, y=None, z=0.0, angle=None, detections=None, grasp_label=None):
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

        sequence = mcp_build_pick_sequence(
            target_object_name=object_name,
            x=x,
            y=y,
            z=z,
            angle=angle,
            detections=detections,
            grasp_label=grasp_label,
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


def _find_relocation_spot(obstacle_name, obstacle_x, obstacle_y, detections, target_name=None):
    """
    Find a safe XY drop position for an obstacle being relocated within the
    pick workspace (NOT the placement box).

    Strategy:
      - Stay inside the camera scan zone (CAM_X_MIN/MAX, CAM_Y_MIN/MAX) so
        YOLO can re-detect the object after relocation.
      - Stay away from all other detected objects by at least RELOCATION_CLEARANCE_M.
      - Stay away from the target object specifically by at least TARGET_CLEARANCE_M.
      - Stay away from the conveyor and camera stand no-go zones.
      - Stay away from the current obstacle position itself.

    Returns [x, y] or raises RuntimeError if no spot found.
    """
    RELOCATION_CLEARANCE_M = 0.08   # minimum gap from other objects (reduced from 0.12 to find more central options)
    TARGET_CLEARANCE_M     = 0.15   # extra clearance from target specifically (prevents overlap warning trigger)
    GRID_STEP_M            = 0.02   # search grid resolution (finer grid)
    BORDER_M               = 0.07   # minimum distance from workspace edge (increased from 0.04 to avoid boundary singularities/joint limits)

    # Build list of positions to avoid: all detections + obstacle's own position.
    avoid = []
    target_positions = []
    for det in (detections or []):
        det_name = det.get("object_name")
        det_x = float(det.get("x", 0))
        det_y = float(det.get("y", 0))
        if target_name and det_name == target_name:
            target_positions.append((det_x, det_y))
        else:
            avoid.append((det_x, det_y))
    avoid.append((float(obstacle_x), float(obstacle_y)))

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

            # Check clearance from all regular objects.
            too_close = False
            for (ox, oy) in avoid:
                if math.hypot(x - ox, y - oy) < RELOCATION_CLEARANCE_M:
                    too_close = True
                    break

            # Check clearance from target specifically.
            if not too_close:
                for (tx, ty) in target_positions:
                    if math.hypot(x - tx, y - ty) < TARGET_CLEARANCE_M:
                        too_close = True
                        break

            if not too_close:
                min_dist = min(
                    (math.hypot(x - ox, y - oy) for ox, oy in avoid),
                    default=999.0
                )
                target_min_dist = min(
                    (math.hypot(x - tx, y - ty) for tx, ty in target_positions),
                    default=999.0
                )
                score = min(min_dist, target_min_dist)
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
            obstacle_name, obstacle_x, obstacle_y, detections, target_name=target_name
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
