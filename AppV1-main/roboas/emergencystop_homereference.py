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
from pymodbus.client import ModbusSerialClient

try:
    import keyboard
    HAS_KEYBOARD = True
except ImportError:
    HAS_KEYBOARD = False
    print("[WARN] 'keyboard' module not found — H/Q hotkeys disabled.")

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
# Surface/table height in robot base coordinates.
# Calibrate this by jogging the gripper to the desired contact height and using:
# TABLE_Z_M = TCP_Z - gripper_length_at_grip - object_height/2
# Start with 0.105 m because your real gripper appeared about 10.5 cm above the table.
 

# Keep planner conservative by default: assume longest possible gripper length.
GRIPPER_LENGTH = GRIPPER_LEN_CLOSED
# Active length used by floor/contact validation.
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
# Treat the lower gripper as a rotated rectangle instead of a circle/square.
# Length = long direction of the open gripper. Depth = jaw body thickness direction.
MAX_FORCE_PERCENT = 40            # cap gripping force at 40%
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
    # Numbered object catalogue.
    # length_m  = long side / longest bounding-box side
    # width_m   = first short side used for footprint/grip planning
    # breadth_m = second short side/depth used for footprint/grip planning
    # height_m  = object height above the table, used for middle-height grip Z planning
    #
    # object_orientation_deg     = default object angle on the table if no camera angle is available
    # preferred_grasp_angle_deg = desired wrist/jaw offset for that object

    "1": {
        "label": "black marker",
        "name": "Black Marker",
        "length_m": 0.134,
        "width_m": 0.02053,
        "breadth_m": 0.02053,
        "height_m": 0.02053,
        "object_orientation_deg": 90.0,
        "preferred_grasp_angle_deg": 0.0,
        "description": "Pilot Board Master black marker",
    },

    "2": {
        "label": "blue marker",
        "name": "Blue Marker",
        "length_m": 0.134,
        "width_m": 0.02053,
        "breadth_m": 0.02053,
        "height_m": 0.02053,
        "object_orientation_deg": 90.0,
        "preferred_grasp_angle_deg": 0.0,
        "description": "Pilot Board Master blue marker",
    },

    "3": {
        "label": "cube",
        "name": "Cube",
        "length_m": 0.040,
        "width_m": 0.040,
        "breadth_m": 0.040,
        "height_m": 0.040,
        "object_orientation_deg": 90.0,
        "preferred_grasp_angle_deg": 0.0,
        "description": "40 mm cube",
    },

    "4": {
        "label": "green marker",
        "name": "Green Marker",
        "length_m": 0.134,
        "width_m": 0.02053,
        "breadth_m": 0.02053,
        "height_m": 0.02053,
        "object_orientation_deg": 90.0,
        "preferred_grasp_angle_deg": 0.0,
        "description": "Pilot Board Master green marker",
    },

    "5": {
        "label": "medicine",
        "name": "Medicine",
        "length_m": 0.11572,
        "width_m": 0.05117,
        "breadth_m": 0.05117,
        "height_m": 0.01895,
        "object_orientation_deg": 90.0,
        "preferred_grasp_angle_deg": 0.0,
        "description": "Medicine item",
    },

    "6": {
        "label": "nut",
        "name": "Nut",
        "length_m": 0.0346,
        "width_m": 0.030,
        "breadth_m": 0.020,
        "height_m": 0.017,
        "object_orientation_deg": 90.0,
        "preferred_grasp_angle_deg": 0.0,
        "description": "Hexagonal nut, approximately 30 mm across flats, 17 mm tall",
    },

    "7": {
        "label": "pipe",
        "name": "Pipe",
        "length_m": 0.120,
        "width_m": 0.110,
        "breadth_m": 0.110,
        "height_m": 0.0545,
        "grasp_length_m": 0.0567,
        "grasp_width_m": 0.040,
        "grasp_breadth_m": 0.040,
        "grasp_height_m": 0.040,
        "grasp_offset_x_m": 0.0,
        "grasp_offset_y_m": -0.020,
        "grasp_offset_z_m": 0.0,
        "object_orientation_deg": 90.0,
        "preferred_grasp_angle_deg": 0.0,
        "grip_center_ratio": 0.5,
        "description": "60-degree elbow pipe with lower tube grip region",
    },

    "8": {
        "label": "sponge",
        "name": "Sponge",
        "length_m": 0.11258,
        "width_m": 0.08,
        "breadth_m": 0.08,
        "height_m": 0.01540,
        "grasp_length_m": 0.11258,
        "grasp_width_m": 0.064,
        "grasp_breadth_m": 0.064,
        "grasp_height_m": 0.01540,
        # The sponge is assumed to lie at about 90 deg on the table.
        # The jaws are angled by about 46.9 deg to match the way you have been gripping it.
        "object_orientation_deg": 90.0,
        "preferred_grasp_angle_deg": 33.54,
        "grasp_offset_x_m": 0.0,
        "grasp_offset_y_m": 0.0,
        "grasp_offset_z_m": 0.0,
        "description": "Cleaning sponge with approximately 123.54 degree angled grip",
    },
}
GRIP_EXTRA_SPACE_M = 0.000        # no extra stroke gap; grip target stays at actual object width
PRE_PICK_EXTRA_RATIO = 0.30       # open 30% wider before descending/releasing so fingers do not scrape the object
GRIP_CENTER_RATIO = 0.50          # grip from the middle height of the object
MIN_GRIP_HEIGHT_M = 0.005         # never aim lower than 5 mm above the table/floor
DROP_RELEASE_CLEARANCE_M = 0.000  # drop height is same as pickup height
PICK_HEIGHT_FINE_TUNE_M = 0      # lower pick/drop by 5 mm because latest test was still slightly high

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

HYBRID_GRIP_CONTACT_TORQUE_DELTA = 20     # <-- MAIN REACTION FORCE / CONTACT SENSITIVITY
HYBRID_GRIP_MAX_EXTRA_CLOSE_PERCENT = 8   # max extra closing if target reached but contact is weak
HYBRID_GRIP_STEP_PERCENT = 2              # close in small increments for adaptive grip
HYBRID_GRIP_STEP_DELAY_S = 0.05
HYBRID_GRIP_MIN_PERCENT = 0
HYBRID_GRIP_TORQUE_SAMPLES = 3






# Lebai Modbus RTU settings.
GRIPPER_PORT = "COM3"             # change this to the USB-RS485 COM port
GRIPPER_ADDR = 1

REG_POSITION    = 0x9C40          # 40000 - position control, 0 closed -> 100 open
REG_FORCE       = 0x9C41          # 40001 - force control, 0 -> 100
REG_CUR_POS     = 0x9C45          # 40005 - current position
REG_CUR_TORQUE  = 0x9C46          # 40006 - current torque
REG_STATUS      = 0x9C47          # 40007 - 1 completed, 0 executing
REG_HOME        = 0x9C48          # 40008 - homing/find stroke
REG_SPEED       = 0x9C4A          # 40010 - speed control
REG_AUTO_HOME   = 0x9C9A          # 40090 - auto homing disable/restore

gripper = ModbusSerialClient(
    port=GRIPPER_PORT,
    baudrate=115200,
    bytesize=8,
    parity="N",
    stopbits=1,
    timeout=2
)

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
    """Operator menu for choosing the object profile used for width/height planning."""
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

        # Accept object number
        if choice in OBJECT_CATALOGUE:
            selected = dict(OBJECT_CATALOGUE[choice])
            print(f"  Selected: {selected.get('name', choice)}")
            return selected

        # Accept typed label/name as fallback
        for obj in OBJECT_CATALOGUE.values():
            if choice in {
                str(obj.get("name", "")).lower(),
                str(obj.get("label", "")).lower(),
            }:
                selected = dict(obj)
                print(f"  Selected: {selected.get('name', choice)}")
                return selected

        print("  [INPUT ERROR] Please select one of the listed object numbers.")

def print_available_com_ports():
    print("[Gripper] Available COM ports:")
    ports = list_ports.comports()
    if not ports:
        print("  No COM ports found.")
    else:
        for p in ports:
            print(f"  {p.device} - {p.description}")

def gripper_connect():
    print_available_com_ports()
    print(f"[Gripper] Connecting on {GRIPPER_PORT}...")
    #if not gripper.connect():
        #raise RuntimeError(f"Could not connect to gripper over RS485 on {GRIPPER_PORT}.")

def gripper_write(register, value):
    result = gripper.write_register(
        address=register,
        value=int(value),
        device_id=GRIPPER_ADDR
    )
    if result.isError():
        raise RuntimeError(f"Gripper write failed: register={hex(register)}, value={value}")

def gripper_read(register):
    result = gripper.read_holding_registers(
        address=register,
        count=1,
        device_id=GRIPPER_ADDR
    )
    if result.isError():
        raise RuntimeError(f"Gripper read failed: register={hex(register)}")
    return result.registers[0]

def wait_gripper_done(timeout=10, target_percent=None, tolerance=3):
    """
    Wait for the gripper to finish. Some units do not always set the status
    register to 1, so if a target percentage is given, this also accepts the
    command as complete when current position is close enough to the target.
    """
    start = time.time()
    while time.time() - start < timeout:
        try:
            status = gripper_read(REG_STATUS)
            if status == 1:
                return True

            if target_percent is not None:
                current_pos = gripper_read(REG_CUR_POS)
                if abs(current_pos - target_percent) <= tolerance:
                    return True
        except Exception as e:
            print(f"[Gripper WARN] Status/position read failed: {e}")

        time.sleep(0.2)
    raise TimeoutError("Gripper command timeout.")

def gripper_startup():
    """Disable auto-homing, run one controlled homing cycle, then set safe force."""
    gripper_connect()

    print("[Gripper] Disabling automatic homing...")
    gripper_write(REG_AUTO_HOME, 1)
    time.sleep(0.3)

    print("[Gripper] Running one homing/find-stroke cycle...")
    gripper_write(REG_HOME, 1)
    wait_gripper_done(timeout=15)

    print(f"[Gripper] Setting force cap to {MAX_FORCE_PERCENT}%...")
    gripper_write(REG_FORCE, MAX_FORCE_PERCENT)
    time.sleep(0.3)

def gripper_set_force(force=MAX_FORCE_PERCENT):
    force = clamp_percent(min(force, MAX_FORCE_PERCENT))
    gripper_write(REG_FORCE, force)

def gripper_set_speed(speed=DEFAULT_GRIPPER_SPEED):
    speed = clamp_percent(speed)
    gripper_write(REG_SPEED, speed)

def gripper_move_percent(position_percent, force=MAX_FORCE_PERCENT, speed=DEFAULT_GRIPPER_SPEED):
    global CURRENT_GRIPPER_PERCENT
    position_percent = clamp_percent(position_percent)
    force = clamp_percent(min(force, MAX_FORCE_PERCENT))
    speed = clamp_percent(speed)

    print(
        f"[Gripper] Move to {position_percent}% open "
        f"({percent_to_commanded_opening_m(position_percent)*1000:.1f} mm calibrated target), "
        f"force={force}%, speed={speed}%"
    )

    gripper_set_force(force)
    gripper_set_speed(speed)
    gripper_write(REG_POSITION, position_percent)
    wait_gripper_done(timeout=10, target_percent=position_percent)

    CURRENT_GRIPPER_PERCENT = position_percent
    print("[Gripper] Move complete")

def gripper_open():
    gripper_move_percent(100, force=MAX_FORCE_PERCENT, speed=60)

def gripper_close():
    gripper_move_percent(0, force=MAX_FORCE_PERCENT, speed=40)

def gripper_open_for_object(object_width_m):
    pre_percent = get_pre_pick_open_percent(object_width_m)
    gripper_move_percent(pre_percent, force=MAX_FORCE_PERCENT, speed=60)


def read_gripper_torque_safe(default=0):
    """Read current gripper torque/current feedback safely."""
    try:
        return gripper_read(REG_CUR_TORQUE)
    except Exception as e:
        print(f"[Grip force WARN] Could not read torque/current feedback: {e}")
        return default


def average_gripper_torque(samples=HYBRID_GRIP_TORQUE_SAMPLES, delay_s=0.05):
    """Average torque/current readings to reduce noise."""
    values = []
    for _ in range(max(1, samples)):
        values.append(read_gripper_torque_safe(default=0))
        time.sleep(delay_s)
    return sum(values) / len(values)


def gripper_grip_object_hybrid(object_width_m):
    """
    Hybrid position + force grip.

    1. Calculate the normal object-width target.
    2. Close gradually toward that target.
    3. Stop early if torque/current rises above threshold.
    4. If target reached but contact is weak, close a little extra.
    """
    close_width_m = calibrated_close_width_for_object(
        object_width_m,
        globals().get("SELECTED_OBJECT", None),
    )
    target_percent = object_width_to_percent(close_width_m)

    if abs(close_width_m - object_width_m) > 0.0005:
        print(
            f"[Grip calibration] Commanding close width {close_width_m*1000:.1f} mm "
            f"for measured object grip width {object_width_m*1000:.1f} mm"
        )

    baseline_torque = average_gripper_torque()
    contact_threshold = baseline_torque + HYBRID_GRIP_CONTACT_TORQUE_DELTA

    print(
        f"[Hybrid grip] target={target_percent}% open, "
        f"baseline_torque={baseline_torque:.1f}, "
        f"contact_threshold={contact_threshold:.1f}"
    )
    print("[Hybrid grip tuning] Adjust HYBRID_GRIP_CONTACT_TORQUE_DELTA for reaction force.")

    current_percent = clamp_percent(CURRENT_GRIPPER_PERCENT)

    if current_percent <= target_percent:
        gripper_move_percent(target_percent, force=MAX_FORCE_PERCENT, speed=35)
        return

    percent = current_percent

    # Close toward calculated target.
    while percent > target_percent:
        next_percent = max(target_percent, percent - HYBRID_GRIP_STEP_PERCENT)
        gripper_move_percent(next_percent, force=MAX_FORCE_PERCENT, speed=30)
        percent = next_percent

        torque = average_gripper_torque()
        print(f"[Hybrid grip] percent={percent}% torque={torque:.1f}")

        if torque >= contact_threshold:
            print(
                f"[Hybrid grip] Contact detected at {percent}% "
                f"(torque {torque:.1f} >= {contact_threshold:.1f}). Holding."
            )
            return

        time.sleep(HYBRID_GRIP_STEP_DELAY_S)

    # If target reached but contact is weak, close slightly more.
    print("[Hybrid grip] Target reached; checking if extra close is needed...")
    extra_closed = 0

    while extra_closed < HYBRID_GRIP_MAX_EXTRA_CLOSE_PERCENT:
        torque = average_gripper_torque()

        if torque >= contact_threshold:
            print(
                f"[Hybrid grip] Contact confirmed after extra close at {percent}% "
                f"(torque {torque:.1f} >= {contact_threshold:.1f})."
            )
            return

        next_percent = max(HYBRID_GRIP_MIN_PERCENT, percent - HYBRID_GRIP_STEP_PERCENT)

        if next_percent == percent:
            break

        gripper_move_percent(next_percent, force=MAX_FORCE_PERCENT, speed=25)
        extra_closed += abs(percent - next_percent)
        percent = next_percent

        print(f"[Hybrid grip] Extra close to {percent}% (extra={extra_closed}%)")
        time.sleep(HYBRID_GRIP_STEP_DELAY_S)

    print("[Hybrid grip] Finished extra-close limit. Holding current grip.")


def gripper_grip_object(object_width_m):
    """
    Grip object using hybrid position + force logic when enabled.
    Falls back to normal position grip if disabled.
    """
    if HYBRID_FORCE_GRIP_ENABLED:
        gripper_grip_object_hybrid(object_width_m)
        return

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

    gripper_move_percent(close_percent, force=MAX_FORCE_PERCENT, speed=40)


def gripper_release_object(object_width_m):
    """
    Compact release inside the placement box.

    Opens to the same 30%-extra size used for pre-pick, instead of opening
    to 100% inside the box. After the robot lifts away, gripper_open() can
    fully open safely.
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
    try:
        gripper.close()
        print("[Gripper] Connection closed.")
    except Exception:
        pass

# =================================================================
# EMERGENCY STOP
# =================================================================
def emergency_stop(sig, frame):
    print("\n[EMERGENCY STOP] Ctrl+C detected!")
    try:
        r.stop()
        time.sleep(0.5)
        gripper_open()
    except Exception as e:
        print(f"  [ESTOP ERROR] {e}")
    sys.exit(0)

signal.signal(signal.SIGINT, emergency_stop)

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
# HEIGHTS & SPEED
# =================================================================
OBJECT_HEIGHT  = 0.04
PICK_Z         = OBJECT_HEIGHT + GRIPPER_LENGTH
DROP_Z         = OBJECT_HEIGHT + GRIPPER_LENGTH
SAFE_HEIGHT    = 0.30
LINEAR_SPEED   = 0.02   # m/s

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

PLACEMENT_OBJECT_GAP_M = 0.020
PLACEMENT_GRID_STEP_M = 0.020

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
PLACEMENT_ANGLE_OFFSETS_DEG = [-90, -60, -45, -30, 0, 30, 45, 60, 90]

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
HOME_Z  = 0.442998

HOME_RX = 178.4062
HOME_RY = 0.10052
HOME_RZ = 105.5




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

# =================================================================
# MULTI-OBJECT USER INPUT
# =================================================================

def input_int_with_retry(prompt, min_value=1, max_value=50):
    while True:
        try:
            value = int(input(prompt).strip())
        except ValueError:
            print("Please enter a whole number.")
            continue

        if value < min_value or value > max_value:
            print(f"Enter a number between {min_value} and {max_value}.")
            continue

        return value


def input_float_with_retry(prompt):
    while True:
        try:
            return float(input(prompt).strip())
        except ValueError:
            print("Please enter a numeric value, for example 0.437.")


def input_pick_sequence():
    """
    Ask for all pick targets before robot motion starts.
    Uses OBJECT_CATALOGUE through select_object_profile().
    """
    sequence = []

    count = input_int_with_retry(
        "\nHow many objects do you want to pick and place? ",
        min_value=1,
        max_value=50,
    )

    for idx in range(1, count + 1):
        print(f"\n=== Object {idx}/{count} ===")

        pick_x, pick_y = _input_coord_with_retry(
            f"Object {idx} pick X (m): ",
            f"Object {idx} pick Y (m): ",
            f"Object {idx} pick target",
        )

        selected_object = select_object_profile()

        sequence.append({
            "index": idx,
            "pick_x": pick_x,
            "pick_y": pick_y,
            "object": selected_object,
        })

    return sequence



def get_pick_sequence_with_valid_placement():
    """
    Ask for pick sequence, then pre-plan placement.

    If placement fails, ask the user to re-enter the pick data instead of
    crashing the program. This happens before robot power/motion begins.
    """
    while True:
        sequence = input_pick_sequence()

        try:
            preplan_all_drop_slots(sequence)
            return sequence

        except RuntimeError as e:
            print("\n" + "=" * 62)
            print("[PLACEMENT PLAN ERROR]")
            print(e)
            print("\nNo robot motion has started yet.")
            print("Please re-enter the pick sequence, choose fewer objects,")
            print("or choose objects/coordinates that can fit in the box.")
            print("=" * 62)

            ans = input("Retry entering pick sequence? (yes/no): ").strip().lower()
            if ans not in {"yes", "y"}:
                raise

# Runtime sequence input. Must stay AFTER input_pick_sequence().
PICK_SEQUENCE = []  # filled inside main() after all helper functions are defined
TRANSIT_HEIGHT = SAFE_HEIGHT

PICK_Z_DYNAMIC = PICK_Z
DROP_RELEASE_Z = DROP_Z

PRE_PICK_OPEN_PERCENT = 100
PICK_CLOSE_PERCENT = 0

OBJECT_NAME = ""
OBJECT_LENGTH_M = 0.0
OBJECT_WIDTH_M = 0.0
OBJECT_BREADTH_M = 0.0
OBJECT_HEIGHT = 0.0

GRASP_LENGTH_M = 0.0
GRASP_WIDTH_M = 0.0
GRASP_BREADTH_M = 0.0
GRASP_HEIGHT_M = 0.0

GRASP_OFFSET_X = 0.0
GRASP_OFFSET_Y = 0.0
GRASP_OFFSET_Z = 0.0

OBJECT_GRIP_WIDTH_M = 0.0
OBJECT_ORIENTATION_DEG = DEFAULT_OBJECT_ORIENTATION_DEG
PREFERRED_GRASP_ANGLE_DEG = DEFAULT_PREFERRED_GRASP_ANGLE_DEG
PLANNED_RZ_DEG = HOME_RZ if "HOME_RZ" in globals() else 0.0

SELECTED_OBJECT = {}
DROP_SLOT = {}
_RUNTIME = {}

# Editor hint only; actual value is assigned later by input_pick_sequence().
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
    Return current OUTER jaw width used for rectangular jaw collision.

    Important:
    - MAX_STROKE_M is the usable internal opening for gripping.
    - MAX_PHYSICAL_GRIPPER_WIDTH_M is the total outer physical width at full open.
    Collision/box clearance should use physical width, not internal stroke.
    """
    try:
        internal_opening = percent_to_opening_m(CURRENT_GRIPPER_PERCENT)

        # Convert internal opening into an approximate outer physical width.
        # At 100%, this becomes MAX_PHYSICAL_GRIPPER_WIDTH_M.
        # At lower openings, it still includes the jaw/body thickness around the internal gap.
        extra_body_width = max(0.0, MAX_PHYSICAL_GRIPPER_WIDTH_M - MAX_STROKE_M)
        return max(
            JAW_MIN_DYNAMIC_WIDTH_M,
            JAW_FIXED_WIDTH_M,
            internal_opening + extra_body_width,
        )
    except Exception:
        fallback_internal = percent_to_opening_m(PICK_CLOSE_PERCENT) if 'PICK_CLOSE_PERCENT' in globals() else 0.040
        extra_body_width = max(0.0, MAX_PHYSICAL_GRIPPER_WIDTH_M - MAX_STROKE_M)
        return max(JAW_MIN_DYNAMIC_WIDTH_M, JAW_FIXED_WIDTH_M, fallback_internal + extra_body_width)


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

    The lower gripping section is not treated as a circle. It is approximated as
    a yaw-rotated rectangle in XY:
      - fixed jaw body width = 50 mm,
      - dynamic opening width = current commanded opening.
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

    # Local jaw half-extents: 50 mm body dimension and dynamic jaw opening.
    hx_local = JAW_FIXED_WIDTH_M / 2.0
    hy_local = get_current_jaw_width_m() / 2.0

    # Convert the oriented rectangle into a conservative axis-aligned envelope.
    hx_world = c * hx_local + s * hy_local
    hy_world = s * hx_local + c * hy_local

    in_x = abs(tcp_x - OBS_X) < (OBS_HW + OBS_MARGIN + hx_world)
    in_y = abs(tcp_y - OBS_Y) < (OBS_HD + OBS_MARGIN + hy_world)
    return in_x and in_y

def segmented_gripper_in_extra_obs(tcp_x, tcp_y, tcp_z):
    """Segmented end-effector collision check against the optional obstacle.

    This replaces the older single-radius gripper check for manual obstacles.
    It checks:
      1) flange/body as a large cylinder,
      2) neck as a smaller cylinder,
      3) jaws as a rotated rectangle.
    """
    active_len = get_active_gripper_length()

    flange_top = 0.0
    flange_bottom = min(active_len, FLANGE_LENGTH_M)
    neck_top = flange_bottom
    neck_bottom = min(active_len, flange_bottom + NECK_LENGTH_M)

    if _circle_segment_hits_box(tcp_x, tcp_y, tcp_z, FLANGE_RADIUS_M, flange_top, flange_bottom):
        return True

    if _circle_segment_hits_box(tcp_x, tcp_y, tcp_z, NECK_RADIUS_M, neck_top, neck_bottom):
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

def gripper_in_stand(tcp_x, tcp_y, tcp_z):
    in_x = _STAND_GRP_X_MIN <= tcp_x <= _STAND_GRP_X_MAX
    in_y = _STAND_GRP_Y_MIN <= tcp_y <= _STAND_GRP_Y_MAX
    return in_x and in_y

def gripper_in_conveyor(tcp_x, tcp_y, tcp_z):
    if PLACEMENT_BOX_OVERRIDES_CONVEYOR and point_in_placement_box_xy(tcp_x, tcp_y):
        return False
    in_x = _CONV_GRP_X_MIN <= tcp_x <= _CONV_GRP_X_MAX
    in_y = _CONV_GRP_Y_MIN <= tcp_y <= _CONV_GRP_Y_MAX
    return in_x and in_y

def gripper_in_extra_obs(tcp_x, tcp_y, tcp_z):
    if not HAS_EXTRA_OBS or _BYPASS_EXTRA_OBS:
        return False
    return segmented_gripper_in_extra_obs(tcp_x, tcp_y, tcp_z)

def gripper_hits_obstacle(tcp_x, tcp_y, tcp_z):
    return (gripper_in_stand(tcp_x, tcp_y, tcp_z) or
            gripper_in_conveyor(tcp_x, tcp_y, tcp_z) or
            gripper_in_extra_obs(tcp_x, tcp_y, tcp_z) or
            carried_object_hits_extra_obs(tcp_x, tcp_y, tcp_z))

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
    return (_STAND_EFF_X_MIN <= px <= _STAND_EFF_X_MAX and
            _STAND_EFF_Y_MIN <= py <= _STAND_EFF_Y_MAX)

def point_in_conveyor(px, py):
    if PLACEMENT_BOX_OVERRIDES_CONVEYOR and point_in_placement_box_xy(px, py):
        return False
    return (_CONV_EFF_X_MIN <= px <= _CONV_EFF_X_MAX and
            _CONV_EFF_Y_MIN <= py <= _CONV_EFF_Y_MAX)

def point_in_extra_obs(px, py, pz):
    if not HAS_EXTRA_OBS:
        return False
    in_x = abs(px - OBS_X) < (OBS_HW + OBS_MARGIN)
    in_y = abs(py - OBS_Y) < (OBS_HD + OBS_MARGIN)
    in_z = pz < (OBS_H + OBS_MARGIN)
    return in_x and in_y and in_z

def point_in_obstacle(px, py, pz):
    return (point_in_stand(px, py) or
            point_in_conveyor(px, py) or
            point_in_extra_obs(px, py, pz))

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

def validate_trajectory(waypoints, label="trajectory", bypass_extra_obs=False):
    """
    Final gate before any move_linear command is issued.
    Checks Z bounds, gripper vs stand, gripper vs conveyor,
    gripper vs extra obstacle for every waypoint.
    Raises RuntimeError on first violation.
    """
    print(f"  [Validate] Checking {len(waypoints)} waypoints ({label})...")
    for i, wp in enumerate(waypoints):
        tcp_x, tcp_y, tcp_z = wp[0], wp[1], wp[2]
        tip_z = tcp_z - get_active_gripper_length()

        if not gripper_in_transit_bounds(tcp_x, tcp_y, tcp_z):
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
                f"    Segmented tool model: flange Ø{FLANGE_DIAMETER_M*1000:.1f}mm, neck Ø{NECK_DIAMETER_M*1000:.1f}mm, jaw {JAW_FIXED_WIDTH_M*1000:.1f}mm x {get_current_jaw_width_m()*1000:.1f}mm\n"
                f"  {'='*62}\n"
                f"  No motion has been sent to the robot.\n"
            )

    print(f"  [Validate] {label} clear — {len(waypoints)} waypoints OK")
    return True

# -----------------------------------------------------------------
# SECTION 2c — INPUT VALIDATION
# -----------------------------------------------------------------

def _in_workspace_xy(x, y):
    return X_MIN <= x <= X_MAX and Y_MIN <= y <= Y_MAX

def _in_stand(x, y):
    return (_STAND_EFF_X_MIN <= x <= _STAND_EFF_X_MAX and
            _STAND_EFF_Y_MIN <= y <= _STAND_EFF_Y_MAX)

def _in_conveyor(x, y):
    if PLACEMENT_BOX_OVERRIDES_CONVEYOR and point_in_placement_box_xy(x, y):
        return False
    return (_CONV_EFF_X_MIN <= x <= _CONV_EFF_X_MAX and
            _CONV_EFF_Y_MIN <= y <= _CONV_EFF_Y_MAX)

def _input_coord_with_retry(prompt_x, prompt_y, label):
    while True:
        try:
            x = float(input(prompt_x))
            y = float(input(prompt_y))
        except ValueError:
            print("\n  [INPUT ERROR] Please enter a numeric value.\n")
            continue

        if not _in_workspace_xy(x, y):
            print(f"\n  {'='*62}")
            print(f"  REJECTED — {label} (X={x:.3f}, Y={y:.3f})")
            print(f"  is outside the workspace boundary.")
            if not (X_MIN <= x <= X_MAX):
                print(f"    X={x:.3f} is outside  [{X_MIN:.3f}, {X_MAX:.3f}]")
            if not (Y_MIN <= y <= Y_MAX):
                print(f"    Y={y:.3f} is outside  [{Y_MIN:.3f}, {Y_MAX:.3f}]")
            print(f"  {'='*62}")
            print(_workspace_box_message())
            print("\n  Please try again.\n")
            continue

        if _in_stand(x, y):
            print(f"\n  {'='*62}")
            print(f"  REJECTED — {label} (X={x:.3f}, Y={y:.3f})")
            print(f"  is inside the camera stand no-go zone.")
            print(f"  This zone is permanently blocked at ALL Z heights.")
            print(f"  {'='*62}")
            print(_stand_box_message())
            print("\n  Please try again.\n")
            continue

        if _in_conveyor(x, y):
            print(f"\n  {'='*62}")
            print(f"  REJECTED — {label} (X={x:.3f}, Y={y:.3f})")
            print(f"  is inside the conveyor belt no-go zone.")
            print(f"  This zone is permanently blocked at ALL Z heights.")
            print(f"  Conveyor TCP-level zone: "
                  f"X[{_CONV_EFF_X_MIN:.3f}-{_CONV_EFF_X_MAX:.3f}]  "
                  f"Y[{_CONV_EFF_Y_MIN:.3f}-{_CONV_EFF_Y_MAX:.3f}]")
            print(f"  {'='*62}")
            print("\n  Please try again.\n")
            continue

        return x, y



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

def planned_rz_for_object(selected_object, placement_angle_deg=None):
    """
    Return TCP RZ angle for this object.
    If placement_angle_deg is given, use that for placement packing.
    """
    if placement_angle_deg is not None:
        return _normalise_angle_deg(placement_angle_deg)

    preferred = float(
        selected_object.get("preferred_grasp_angle_deg", DEFAULT_PREFERRED_GRASP_ANGLE_DEG)
    )
    return _normalise_angle_deg(HOME_RZ + preferred)


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
    Return real left/right X wall positions at a given Y.
    Handles the slanted real placement box instead of only the rectangular bounds.
    """
    y_bottom = PLACEMENT_BOX_Y_MIN
    y_top = PLACEMENT_BOX_Y_MAX

    t = (y - y_bottom) / max(y_top - y_bottom, 1e-6)
    t = max(0.0, min(1.0, t))

    left_x = 0.248 + t * (0.252 - 0.248)
    right_x = 0.586 + t * (0.516 - 0.586)

    return left_x, right_x


def candidate_inside_real_placement_box(x, y, length, width, margin_x=0.0, margin_y=0.0):
    """
    Check object footprint against the real slanted placement box.
    """
    half_l = length / 2.0
    half_w = width / 2.0

    if not (PLACEMENT_BOX_Y_MIN + margin_y + half_w <= y <= PLACEMENT_BOX_Y_MAX - margin_y - half_w):
        return False

    left_x, right_x = placement_x_limits_at_y(y)

    return left_x + margin_x + half_l <= x <= right_x - margin_x - half_l



def real_placement_wall_gaps(x, y, length, width):
    """
    Wall-gap reading against the real slanted placement box.
    """
    left_x, right_x = placement_x_limits_at_y(y)

    left_gap = (x - length / 2.0) - left_x
    right_gap = right_x - (x + length / 2.0)
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
    True only if the candidate object footprint is inside the real slanted box.
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
    Return object footprint gaps to the real placement-box walls.
    Uses slanted left/right wall interpolation instead of rectangular X min/max.
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


def _remaining_items_from_runtime(selected_object=None):
    try:
        planned_count = len(PLACED_OBJECTS)
        remaining = []
        for item in PICK_SEQUENCE[planned_count + 1:]:
            if isinstance(item, dict) and "object" in item:
                remaining.append(item["object"])
        return remaining
    except Exception:
        return []


def _future_fit_penalty(x, y, length, width, selected_object=None):
    remaining = _remaining_items_from_runtime(selected_object)
    if not remaining:
        return 0.0

    simulated = {"x": x, "y": y, "length_m": length, "width_m": width}
    penalty = 0.0

    for obj in remaining:
        can_fit = False

        for rotated in (False, True):
            test_len, test_wid = _object_footprint_for_placement(obj, rotated=rotated)

            test_x = PLACEMENT_BOX_X_MIN + test_len / 2.0 + PLACEMENT_WALL_GAP_MIN_M
            while test_x <= PLACEMENT_BOX_X_MAX - test_len / 2.0 - PLACEMENT_WALL_GAP_MIN_M:
                test_y = PLACEMENT_BOX_Y_MIN + test_wid / 2.0 + PLACEMENT_WALL_GAP_MIN_M
                while test_y <= PLACEMENT_BOX_Y_MAX - test_wid / 2.0 - PLACEMENT_WALL_GAP_MIN_M:
                    overlaps_current = _rectangles_overlap(
                        test_x, test_y, test_len, test_wid,
                        simulated["x"], simulated["y"], simulated["length_m"], simulated["width_m"],
                    )
                    overlaps_existing = _candidate_overlaps_placed(test_x, test_y, test_len, test_wid)

                    if not overlaps_current and not overlaps_existing:
                        can_fit = True
                        break

                    test_y += SMART_PLACEMENT_GRID_STEP_M

                if can_fit:
                    break

                test_x += SMART_PLACEMENT_GRID_STEP_M

            if can_fit:
                break

        if not can_fit:
            penalty += 1.0

    return penalty


def _placement_wall_gap_penalty(x, y, length, width):
    gaps = _wall_gaps_for_candidate(x, y, length, width)
    nearest_gap = min(gaps)

    if nearest_gap < PLACEMENT_WALL_GAP_MIN_M:
        return SMART_WALL_GAP_UNDER_WEIGHT * (PLACEMENT_WALL_GAP_MIN_M - nearest_gap)

    if nearest_gap > PLACEMENT_WALL_GAP_MAX_M:
        return SMART_WALL_GAP_OVER_WEIGHT * (nearest_gap - PLACEMENT_WALL_GAP_MAX_M)

    return 0.0


def _print_wall_gap_reading(prefix, x, y, length, width):
    left_gap, right_gap, bottom_gap, top_gap = _wall_gaps_for_candidate(x, y, length, width)
    print(
        f"{prefix} real/slanted wall gaps: "
        f"left-X={left_gap*1000:.1f} mm, "
        f"right-X={right_gap*1000:.1f} mm, "
        f"bottom-Y={bottom_gap*1000:.1f} mm, "
        f"top-Y={top_gap*1000:.1f} mm"
    )

def _placement_score(x, y, length, width, selected_object=None, placement_angle_deg=None):
    """
    Smart placement score. Lower score wins.

    This keeps the existing allocator structure but scores candidates by:
      - corner/wall compaction,
      - avoiding the middle,
      - leaving one large open region,
      - lightweight future-fit lookahead,
      - safe wall-gap limits.
    """
    if not SMART_PLACEMENT_ENABLED:
        object_gap_x = (x - length / 2.0) - PLACEMENT_BOX_X_MIN - BOX_WALL_THICKNESS_M
        score = abs(object_gap_x - PLACEMENT_WALL_GAP_MIN_M) * 10.0
        if object_gap_x < PLACEMENT_WALL_GAP_MIN_M:
            score += (PLACEMENT_WALL_GAP_MIN_M - object_gap_x) * 100.0
        if object_gap_x > PLACEMENT_WALL_GAP_MAX_M:
            score += (object_gap_x - PLACEMENT_WALL_GAP_MAX_M) * 15.0
        score += 0.5 * (x - PLACEMENT_BOX_X_MIN)
        score -= 0.2 * (y - PLACEMENT_BOX_Y_MIN)
        return score

    corner_score = _corner_compaction_score(x, y, length, width)
    center_penalty = _center_avoidance_score(x, y)
    open_space_score = _open_space_after_candidate_score(x, y, length, width)
    future_penalty = _future_fit_penalty(x, y, length, width, selected_object)
    wall_gap_penalty = _placement_wall_gap_penalty(x, y, length, width)

    score = 0.0
    score += SMART_CORNER_WEIGHT * corner_score
    score += SMART_CENTER_AVOID_WEIGHT * center_penalty
    score -= SMART_OPEN_SPACE_WEIGHT * open_space_score
    score += SMART_FUTURE_FIT_WEIGHT * future_penalty
    score += wall_gap_penalty

    if PLACED_OBJECTS:
        nearest_obj = min(
            ((x - obj["x"]) ** 2 + (y - obj["y"]) ** 2) ** 0.5
            for obj in PLACED_OBJECTS
        )
        score -= nearest_obj * SMART_EXISTING_OBJECT_SPREAD_WEIGHT

    return score



def find_best_drop_slot(selected_object):
    candidates = []
    
    grid_step = SMART_PLACEMENT_GRID_STEP_M

    base_angle = planned_rz_for_object(selected_object)

    for angle_offset in PLACEMENT_ANGLE_OFFSETS_DEG:
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
                        if not _candidate_overlaps_placed(x, y, length, width):
                            if not candidate_inside_real_placement_box(x, y, length, width, margin_x=margin_x, margin_y=margin_y):
                                y -= grid_step
                                continue

                            score = _placement_score(x, y, length, width, selected_object, placement_angle_deg)

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

    print(
        f"  [Packing] Reserved slot for {selected_object.get('name', selected_object.get('label', 'object'))}: "
        f"X={x:.3f}, Y={y:.3f}, rotated={rotated}, "
        f"placement_angle={placement_angle_deg:.1f} deg, "
        f"footprint={length*1000:.1f}x{width*1000:.1f} mm, "
        f"wall_gap_x={((x - length/2.0) - PLACEMENT_BOX_X_MIN - BOX_WALL_THICKNESS_M)*1000:.1f} mm, "
        f"release_gripper={gripper_physical_length_from_percent(release_percent_for_object(selected_object))*1000:.1f}x{GRIPPER_PHYSICAL_DEPTH_M*1000:.1f} mm"
    )

    return slot


def allocate_drop_slot_for_object(selected_object):
    return find_best_drop_slot(selected_object)


def print_placement_box_summary():
    print("\n=== Fixed placement box ===")
    print(f"  Outer corners: {[(round(x,3), round(y,3)) for x, y in PLACEMENT_BOX_CORNERS]}")
    print(f"  Bounding range X[{PLACEMENT_BOX_X_MIN:.3f}, {PLACEMENT_BOX_X_MAX:.3f}]  "
          f"Y[{PLACEMENT_BOX_Y_MIN:.3f}, {PLACEMENT_BOX_Y_MAX:.3f}]")
    print(f"  Wall thickness: {BOX_WALL_THICKNESS_M*1000:.1f} mm")
    print(f"  placement wall clearance: {PLACEMENT_WALL_CLEARANCE_M*1000:.1f} mm")
    print(f"  Base thickness: {BOX_BASE_THICKNESS_M*1000:.1f} mm")


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
    Pre-calculate all drop locations before any robot motion starts.
    This catches box-full or too-large-object issues before the robot moves.
    """
    print("\n=== Pre-planning all placement slots ===")

    PLACED_OBJECTS.clear()

    for seq_item in pick_sequence:
        selected_object = seq_item["object"]
        slot = reserve_drop_slot_for_object(selected_object)

        print(
            f"  Object {seq_item['index']}: "
            f"{selected_object.get('name', selected_object.get('label', 'object'))} "
            f"-> X={slot['x']:.3f}, Y={slot['y']:.3f}, "
            f"footprint={slot['length_m']*1000:.1f}x{slot['width_m']*1000:.1f} mm, "
            f"rotated={slot.get('rotated', False)}"
        )
        _print_wall_gap_reading("[Packing detail]",slot["x"],slot["y"],slot["length_m"],slot["width_m"],)

    print_placement_diagram(pick_sequence)


def print_placement_diagram(pick_sequence, cols=48, rows=22):
    """
    Print a simple top-view ASCII diagram of the placement box.
    X is horizontal, Y is vertical.
    Each object is represented by its sequence number.
    """
    print("\n=== Planned placement diagram — top view of box ===")

    grid = [["." for _ in range(cols)] for _ in range(rows)]

    x_min = PLACEMENT_BOX_X_MIN
    x_max = PLACEMENT_BOX_X_MAX
    y_min = PLACEMENT_BOX_Y_MIN
    y_max = PLACEMENT_BOX_Y_MAX

    def to_col(x):
        if x_max == x_min:
            return 0
        return int(round((x - x_min) / (x_max - x_min) * (cols - 1)))

    def to_row(y):
        if y_max == y_min:
            return 0
        return int(round((y_max - y) / (y_max - y_min) * (rows - 1)))

    for seq_item in pick_sequence:
        obj = seq_item["object"]
        slot = obj.get("_planned_drop_slot")
        if not slot:
            continue

        label = str(seq_item["index"] % 10)
        cx = slot["x"]
        cy = slot["y"]
        length = slot["length_m"]
        width = slot["width_m"]

        x0 = max(x_min, cx - length / 2.0)
        x1 = min(x_max, cx + length / 2.0)
        y0 = max(y_min, cy - width / 2.0)
        y1 = min(y_max, cy + width / 2.0)

        c0, c1 = sorted((to_col(x0), to_col(x1)))
        r0, r1 = sorted((to_row(y0), to_row(y1)))

        for rr in range(max(0, r0), min(rows, r1 + 1)):
            for cc in range(max(0, c0), min(cols, c1 + 1)):
                grid[rr][cc] = label

    print("  +-" + "-" * cols + "-+")
    for row in grid:
        print("  | " + "".join(row) + " |")
    print("  +-" + "-" * cols + "-+")
    print(f"  X range: {x_min:.3f} -> {x_max:.3f} m")
    print(f"  Y range: {y_min:.3f} -> {y_max:.3f} m")
    print("  Legend:")

    for seq_item in pick_sequence:
        obj = seq_item["object"]
        slot = obj.get("_planned_drop_slot", {})
        print(
            f"    {seq_item['index']}: {obj.get('name', obj.get('label', 'object'))} "
            f"at X={slot.get('x', 0):.3f}, Y={slot.get('y', 0):.3f}, angle={slot.get('placement_angle_deg', 0):.1f}°"
        )


# =================================================================
# STARTUP BANNER
# =================================================================
print("=" * 62)
print("  LARA 5 REAL ROBOT MODE")
print("  Emergency stop : Ctrl+C")
if HAS_KEYBOARD:
    print("  Press H        : return home")
    print("  Press Q        : quit (return home then stop)")
print("=" * 62)
print()
print("  *** REAL ROBOT — ensure work cell is clear ***")
print()
print(f"  Gripper: length {GRIPPER_LENGTH*1000:.0f} mm")
print(f"  Calibrated command stroke: {MAX_STROKE_M*1000:.0f} mm")
print(f"  Physical gripper max width: {MAX_PHYSICAL_GRIPPER_WIDTH_M*1000:.0f} mm")
print(f"  Collision model: flange Ø{FLANGE_DIAMETER_M*1000:.1f} mm, "
      f"neck Ø{NECK_DIAMETER_M*1000:.1f} mm, "
      f"jaw rectangle {JAW_FIXED_WIDTH_M*1000:.0f} mm x dynamic opening")
print(f"  Object height  : {OBJECT_HEIGHT*1000:.0f} mm above floor")
print(f"  PICK_Z / DROP_Z: {PICK_Z:.3f} m  "
      f"(fingertip will be at {OBJECT_HEIGHT:.3f} m)")
print(f"  Transit height : {SAFE_HEIGHT:.3f} m TCP  "
      f"(fingertip at {SAFE_HEIGHT - GRIPPER_LENGTH:.3f} m)")
print()
print("  Workspace box corners (metres)  [pick/drop must be inside]:")
print(f"    A (near-left)  X={X_MIN:.3f}  Y={Y_MIN:.3f}")
print(f"    B (near-right) X={X_MIN:.3f}  Y={Y_MAX:.3f}")
print(f"    C (far-right)  X={X_MAX:.3f}  Y={Y_MAX:.3f}")
print(f"    D (far-left)   X={X_MAX:.3f}  Y={Y_MIN:.3f}")
print(f"    Z range        {Z_MIN:.3f} -> {Z_MAX:.3f}  (transit Z also capped here)")
print()
print("  Camera stand no-go zone:")
print(f"    TCP-level    X {_STAND_EFF_X_MIN:.3f}->{_STAND_EFF_X_MAX:.3f}"
      f"  Y {_STAND_EFF_Y_MIN:.3f}->{_STAND_EFF_Y_MAX:.3f}")
print(f"    Gripper-body X {_STAND_GRP_X_MIN:.3f}->{_STAND_GRP_X_MAX:.3f}"
      f"  Y {_STAND_GRP_Y_MIN:.3f}->{_STAND_GRP_Y_MAX:.3f}")
print()
print("  Conveyor belt no-go zone  (physical + 50 mm margin):")
print(f"    TCP-level    X {_CONV_EFF_X_MIN:.3f}->{_CONV_EFF_X_MAX:.3f}"
      f"  Y {_CONV_EFF_Y_MIN:.3f}->{_CONV_EFF_Y_MAX:.3f}")
print(f"    Gripper-body X {_CONV_GRP_X_MIN:.3f}->{_CONV_GRP_X_MAX:.3f}"
      f"  Y {_CONV_GRP_Y_MIN:.3f}->{_CONV_GRP_Y_MAX:.3f}")
print()






# =================================================================
# OBJECT VARIABLE RESOLUTION
# =================================================================

def resolve_object_runtime_variables(selected_object, move_x, move_y, drop_slot):
    """
    Converts one selected catalogue object into all runtime variables used by the
    planner, gripper, print section, and motion poses.
    """
    object_name = selected_object.get(
        "name",
        selected_object.get("label", "object")
    )

    object_length_m = float(selected_object.get("length_m", 0.04))
    object_width_m = float(selected_object.get("width_m", 0.04))
    object_breadth_m = float(selected_object.get("breadth_m", object_width_m))
    object_height_m = float(selected_object.get("height_m", 0.04))

    grasp_length_m = float(selected_object.get("grasp_length_m", object_length_m))
    grasp_width_m = float(selected_object.get("grasp_width_m", object_width_m))
    grasp_breadth_m = float(selected_object.get("grasp_breadth_m", object_breadth_m))
    grasp_height_m = float(selected_object.get("grasp_height_m", object_height_m))

    grasp_offset_x = float(selected_object.get("grasp_offset_x_m", 0.0))
    grasp_offset_y = float(selected_object.get("grasp_offset_y_m", 0.0))
    grasp_offset_z = float(selected_object.get("grasp_offset_z_m", 0.0))

    pick_target_x = move_x + grasp_offset_x
    pick_target_y = move_y + grasp_offset_y

    object_grip_width_m = min(grasp_width_m, grasp_breadth_m) + GRIP_EXTRA_SPACE_M

    object_orientation_deg = float(selected_object.get("object_orientation_deg", 0.0))
    preferred_grasp_angle_deg = float(selected_object.get("preferred_grasp_angle_deg", 0.0))

    planned_rz_deg = HOME_RZ + preferred_grasp_angle_deg
    planned_rz_deg = _normalise_angle_deg(planned_rz_deg)

    drop_x = float(drop_slot["x"])
    drop_y = float(drop_slot["y"])

    return {
        "OBJECT_NAME": object_name,
        "OBJECT_LENGTH_M": object_length_m,
        "OBJECT_WIDTH_M": object_width_m,
        "OBJECT_BREADTH_M": object_breadth_m,
        "OBJECT_HEIGHT": object_height_m,

        "GRASP_LENGTH_M": grasp_length_m,
        "GRASP_WIDTH_M": grasp_width_m,
        "GRASP_BREADTH_M": grasp_breadth_m,
        "GRASP_HEIGHT_M": grasp_height_m,

        "GRASP_OFFSET_X": grasp_offset_x,
        "GRASP_OFFSET_Y": grasp_offset_y,
        "GRASP_OFFSET_Z": grasp_offset_z,

        "PICK_TARGET_X": pick_target_x,
        "PICK_TARGET_Y": pick_target_y,

        "OBJECT_GRIP_WIDTH_M": object_grip_width_m,
        "OBJECT_ORIENTATION_DEG": object_orientation_deg,
        "PREFERRED_GRASP_ANGLE_DEG": preferred_grasp_angle_deg,
        "PLANNED_RZ_DEG": planned_rz_deg,

        "DROP_X": drop_x,
        "DROP_Y": drop_y,
    }

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

    print(
        f"  [Multi-object obstacle] Object {nearest['index']} "
        f"temporarily treated as obstacle at "
        f"X={OBS_X:.3f}, Y={OBS_Y:.3f}, "
        f"size={OBS_W:.3f}x{OBS_D:.3f}x{OBS_H:.3f}m"
    )


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
    try:
        r.stop()
        time.sleep(0.5)
        r.power_off()
        gripper_shutdown()
        print("[System] Robot powered off.")
    except Exception as e:
        print(f"[STOP ERROR] {e}")

def ensure_robot_ready(r):
    print("[System] Switching to real mode...")
    r.switch_to_real()
    time.sleep(1)

    print("[System] Powering on — releasing brakes...")
    r.power_on()
    time.sleep(2)

    if r.get_errors():
        print("[System] Resetting errors...")
        r.reset_errors()
        time.sleep(1)

    if not r.is_robot_in_automatic_mode():
        print("[System] Switching to automatic mode...")
        r.switch_to_automatic_mode()
        time.sleep(1)

    print("[System] Initialising motion engine...")
    r.init_program()
    time.sleep(1)

    print(f"[Ready] {r.robot_name}")

def check_starting_position(r):
    pose = r.get_tcp_pose()
    tip  = pose[2] - GRIPPER_LENGTH
    print(f"[Check] Current TCP: X={pose[0]:.3f}  Y={pose[1]:.3f}  Z={pose[2]:.3f}  "
          f"(fingertip Z={tip:.3f})")
    if pose[2] < Z_MIN:
        print(f"\n  {'='*62}")
        print(f"  ABORT — TCP Z={pose[2]:.3f} is below Z_MIN={Z_MIN:.3f}")
        print(f"  Manually jog the robot above Z={Z_MIN:.3f}m before running.")
        print(f"  {'='*62}")
        power_off_robot()
        sys.exit(1)

def is_at_home(r, tol=0.01):
    try:
        c = r.get_tcp_pose()
        return (abs(c[0] - HOME_X) < tol and
                abs(c[1] - HOME_Y) < tol and
                abs(c[2] - HOME_Z) < tol)
    except Exception as e:
        print(f"  [HOME CHECK ERROR] {e}")
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
    print("\n[Emergency Home] Planning safe return...")
    current = r.get_tcp_pose()
    if is_at_home(r):
        print("[Home] Already at home")
        return
    home = get_home_pose(current)

    try:
        traj = build_full_trajectory([current, home])
        execute_trajectory(r, traj, label="Emergency return home")
        print("[Home] Reached HOME")
        return
    except Exception as e:
        print(f"\n[Home] Obstacle-aware route failed: {e}")

    if HAS_EXTRA_OBS:
        print()
        print(f"  {'='*62}")
        print(f"  WARNING — no obstacle-avoiding route found back to home.")
        print(f"  The extra obstacle is blocking the return path.")
        print(f"  Option: ignore the extra obstacle and return home directly.")
        print(f"  The camera stand and conveyor belt remain blocked.")
        print(f"  {'='*62}")
        ans = input("  Ignore extra obstacle and return home? (yes/no): ").strip().lower()
        if ans == "yes":
            print("\n[Home] Driving directly home — extra obstacle ignored...")
            _BYPASS_EXTRA_OBS = True
            try:
                traj = [current] + _density_segment(current, home) + [home]
                execute_trajectory(r, traj, label="Forced home (obstacle ignored)")
                print("[Home] Reached HOME")
            except Exception as e2:
                print(f"[Home ERROR] Direct home move failed: {e2}")
            finally:
                _BYPASS_EXTRA_OBS = False
        else:
            print()
            print("  [ABORT] Operator declined bypass.")
            print("  Turning off robot — please manually move the robot")
            print("  out of the obstacle's way, then restart the script.")
            power_off_robot()
    else:
        print("[Home ERROR] No extra obstacle defined; cannot bypass further.")

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
        print("\n[H] Home key pressed...")
        try:
            r.stop()
            gripper_open()
        except Exception as e:
            print(f"  [STOP ERROR] {e}")
        time.sleep(0.5)
        try:
            r.reset_errors()
            r.switch_to_automatic_mode()
            time.sleep(1)
        except Exception as e:
            print(f"  [RESET ERROR] {e}")
        move_to_home_emergency(r)
        home_busy = False

    def on_q():
        print("\n[Q] Quitting...")
        try:
            r.stop()
            gripper_open()
            time.sleep(0.5)
            r.reset_errors()
            r.switch_to_automatic_mode()
            time.sleep(1)
            move_to_home_emergency(r)
        except Exception as e:
            print(f"  [QUIT ERROR] {e}")
        finally:
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
        print("[Planner] Trying OVER routes")

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
    # ----------------------
    print("[Planner] Trying SIDE routes")

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
    print(f"[Linear Detour Planner] Using route (cost={best_cost:.3f})")
    return best_route

def smart_route(start, end):
    if not path_hits_obstacle(start, end):
        mid = [
            (start[0] + end[0]) / 2,
            (start[1] + end[1]) / 2,
            (start[2] + end[2]) / 2,
        ]

        if not gripper_hits_obstacle(mid[0], mid[1], mid[2]):
            return [start, end]

    detour = find_best_linear_detour_route(start, end)
    if detour is not None:
        return detour

    print("[Planner] Linear detour failed -> fallback to original planner")
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

WP_SPACING = 0.025   # metres between interpolated waypoints (25 mm)


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
        print(f"    Direct path: {cost:.3f} m")
        best_nodes, best_cost = direct, cost

    # Option 2: one via-point
    for v in pool:
        route = [start, v, end]
        if not _route_clear(route):
            continue
        cost = cost_fn(route)
        if cost < best_cost:
            best_nodes, best_cost = route, cost

    if best_nodes is not None and len(best_nodes) == 3:
        v = best_nodes[1]
        print(f"    Best 1-via : {best_cost:.3f} m  "
              f"via X={v[0]:.3f} Y={v[1]:.3f} Z={v[2]:.3f}")

    # Option 3: two via-points
    need_two = (best_nodes is None)
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
                need_two = False

    if best_nodes is not None and len(best_nodes) == 4:
        v1, v2 = best_nodes[1], best_nodes[2]
        print(f"    Best 2-via : {best_cost:.3f} m  "
              f"V1=({v1[0]:.3f},{v1[1]:.3f},{v1[2]:.3f})  "
              f"V2=({v2[0]:.3f},{v2[1]:.3f},{v2[2]:.3f})")

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
    print("\n[Pre-compute] Building optimised trajectory...")
    full_path = [checkpoints[0]]

    for i in range(len(checkpoints) - 1):
        start     = checkpoints[i]
        end       = checkpoints[i + 1]
        seg_label = f"Segment {i+1}/{len(checkpoints)-1}"
        seg_dist  = math.dist(start[:3], end[:3])

        print(f"  {seg_label}  ({seg_dist:.3f} m straight-line):")

        route = smart_route(start, end)

        for j in range(len(route) - 1):
            full_path.extend(_density_segment(route[j], route[j + 1]))
            full_path.append(route[j + 1])

        n_via = len(route) - 2
        print(f"    -> {n_via} via-point(s), "
              f"route length {_route_cost(route):.3f} m")

    total_wp = len(full_path)
    print(f"[Pre-compute] Done — {total_wp} waypoints  "
          f"(~{WP_SPACING*1000:.0f} mm spacing)")
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
    if label:
        print(f"\\n[Execute] {label} (planned transit)")

    transit_path = build_full_trajectory([start_pose, end_pose])
    execute_trajectory(r, transit_path, label=label)


def execute_trajectory(r, full_path, label="", bypass_extra_obs=False):
    """
    Execute a linear trajectory via ONE blended move_linear command.

    This avoids waypoint-by-waypoint stopping:
      - validate the whole path first,
      - prepend current TCP pose,
      - send the whole list using target_pose=trajectory,
      - enable blending.
    """
    if label:
        print(f"\\n[Execute] {label}")

    validate_trajectory(full_path, label=label, bypass_extra_obs=bypass_extra_obs)

    current = r.get_tcp_pose()
    trajectory = [current] + full_path

    print(f"  Sending {len(trajectory)} Cartesian waypoints via blended move_linear...")
    try:
        r.move_linear(
            speed=LINEAR_SPEED,
            blending=True,
            blend_radius=BLEND_RADIUS,
            controller_parameters={"control_mode": "position"},
            target_pose=trajectory,
        )
    except Exception as e:
        print(f"[ERROR] Blended trajectory failed: {e}")
        r.stop()
        raise


def power_off_robot():
    try:
        r.stop()
        time.sleep(0.5)
        r.power_off()
        gripper_shutdown()
        print("[System] Robot powered off.")
    except Exception as e:
        print(f"[STOP ERROR] {e}")

def ensure_robot_ready(r):
    print("[System] Switching to real mode...")
    r.switch_to_real()
    time.sleep(1)

    print("[System] Powering on — releasing brakes...")
    r.power_on()
    time.sleep(2)

    if r.get_errors():
        print("[System] Resetting errors...")
        r.reset_errors()
        time.sleep(1)

    if not r.is_robot_in_automatic_mode():
        print("[System] Switching to automatic mode...")
        r.switch_to_automatic_mode()
        time.sleep(1)

    print("[System] Initialising motion engine...")
    r.init_program()
    time.sleep(1)

    print(f"[Ready] {r.robot_name}")

def check_starting_position(r):
    pose = r.get_tcp_pose()
    tip  = pose[2] - GRIPPER_LENGTH
    print(f"[Check] Current TCP: X={pose[0]:.3f}  Y={pose[1]:.3f}  Z={pose[2]:.3f}  "
          f"(fingertip Z={tip:.3f})")
    if pose[2] < Z_MIN:
        print(f"\n  {'='*62}")
        print(f"  ABORT — TCP Z={pose[2]:.3f} is below Z_MIN={Z_MIN:.3f}")
        print(f"  Manually jog the robot above Z={Z_MIN:.3f}m before running.")
        print(f"  {'='*62}")
        power_off_robot()
        sys.exit(1)

def is_at_home(r, tol=0.01):
    try:
        c = r.get_tcp_pose()
        return (abs(c[0] - HOME_X) < tol and
                abs(c[1] - HOME_Y) < tol and
                abs(c[2] - HOME_Z) < tol)
    except Exception as e:
        print(f"  [HOME CHECK ERROR] {e}")
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
    print("\n[Emergency Home] Planning safe return...")
    current = r.get_tcp_pose()
    if is_at_home(r):
        print("[Home] Already at home")
        return
    home = get_home_pose(current)

    try:
        traj = build_full_trajectory([current, home])
        execute_trajectory(r, traj, label="Emergency return home")
        print("[Home] Reached HOME")
        return
    except Exception as e:
        print(f"\n[Home] Obstacle-aware route failed: {e}")

    if HAS_EXTRA_OBS:
        print()
        print(f"  {'='*62}")
        print(f"  WARNING — no obstacle-avoiding route found back to home.")
        print(f"  The extra obstacle is blocking the return path.")
        print(f"  Option: ignore the extra obstacle and return home directly.")
        print(f"  The camera stand and conveyor belt remain blocked.")
        print(f"  {'='*62}")
        ans = input("  Ignore extra obstacle and return home? (yes/no): ").strip().lower()
        if ans == "yes":
            print("\n[Home] Driving directly home — extra obstacle ignored...")
            _BYPASS_EXTRA_OBS = True
            try:
                traj = [current] + _density_segment(current, home) + [home]
                execute_trajectory(r, traj, label="Forced home (obstacle ignored)")
                print("[Home] Reached HOME")
            except Exception as e2:
                print(f"[Home ERROR] Direct home move failed: {e2}")
            finally:
                _BYPASS_EXTRA_OBS = False
        else:
            print()
            print("  [ABORT] Operator declined bypass.")
            print("  Turning off robot — please manually move the robot")
            print("  out of the obstacle's way, then restart the script.")
            power_off_robot()
    else:
        print("[Home ERROR] No extra obstacle defined; cannot bypass further.")

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
        print("\n[H] Home key pressed...")
        try:
            r.stop()
            gripper_open()
        except Exception as e:
            print(f"  [STOP ERROR] {e}")
        time.sleep(0.5)
        try:
            r.reset_errors()
            r.switch_to_automatic_mode()
            time.sleep(1)
        except Exception as e:
            print(f"  [RESET ERROR] {e}")
        move_to_home_emergency(r)
        home_busy = False

    def on_q():
        print("\n[Q] Quitting...")
        try:
            r.stop()
            gripper_open()
            time.sleep(0.5)
            r.reset_errors()
            r.switch_to_automatic_mode()
            time.sleep(1)
            move_to_home_emergency(r)
        except Exception as e:
            print(f"  [QUIT ERROR] {e}")
        finally:
            power_off_robot()
            sys.exit(0)

    keyboard.add_hotkey('h', on_h)
    keyboard.add_hotkey('q', on_q)
    keyboard.wait()

# -----------------------------------------------------------------
# SECTION 8 — MAIN
# -----------------------------------------------------------------



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
    global OBJECT_ORIENTATION_DEG, PREFERRED_GRASP_ANGLE_DEG, PLANNED_RZ_DEG
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

    print(f"\n=== Smart gripper calculation — object {cycle_index}/{total_cycles} ===")
    print(f"  Selected object          : {OBJECT_NAME}")
    print(f"  Object dimensions        : L={OBJECT_LENGTH_M*1000:.1f} x W={OBJECT_WIDTH_M*1000:.1f} x B={OBJECT_BREADTH_M*1000:.1f} x H={OBJECT_HEIGHT*1000:.1f} mm")
    print(f"  Grasp region             : L={GRASP_LENGTH_M*1000:.1f} x W={GRASP_WIDTH_M*1000:.1f} x B={GRASP_BREADTH_M*1000:.1f} x H={GRASP_HEIGHT_M*1000:.1f} mm")
    print(f"  Planned wrist RZ         : {PLANNED_RZ_DEG:.2f} deg")
    print(f"  Effective grip width     : {OBJECT_GRIP_WIDTH_M*1000:.1f} mm")
    print(f"  Calibrated command stroke: {MAX_STROKE_M*1000:.0f} mm")
    print(f"  Physical gripper width   : {MAX_PHYSICAL_GRIPPER_WIDTH_M*1000:.0f} mm")
    print(f"  Pre-pick opening         : {PRE_PICK_OPEN_PERCENT}% ({percent_to_opening_m(PRE_PICK_OPEN_PERCENT)*1000:.1f} mm)")
    print(f"  Grip/hold opening        : {PICK_CLOSE_PERCENT}% ({percent_to_opening_m(PICK_CLOSE_PERCENT)*1000:.1f} mm)")
    print(f"  Target grip height       : {TARGET_GRIP_HEIGHT*1000:.1f} mm")
    print(f"  Dynamic PICK_Z           : {PICK_Z_DYNAMIC:.3f} m")
    print(f"  Drop TCP_Z               : {DROP_RELEASE_Z:.3f} m")
    print(f"  Actual grasp target XY   : X={PICK_TARGET_X:.3f}, Y={PICK_TARGET_Y:.3f}")
    print(f"  Fixed drop slot XY       : X={DROP_X:.3f}, Y={DROP_Y:.3f}")

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
            print(f"\n  {'='*62}")
            print(f"  ABORT — {label} (X={cx:.3f}, Y={cy:.3f}) is outside the workspace boundary.")
            if is_drop_point:
                print("  Drop points are allowed outside the pick workspace only if they are inside the fixed placement box.")
            print(f"  {'='*62}")
            print(_workspace_box_message())
            if is_drop_point:
                print_placement_box_summary()
            power_off_robot()
            sys.exit(1)

        if is_drop_point and drop_is_inside_box:
            print(f"[Check] Drop-off X={cx:.3f}, Y={cy:.3f} is inside the fixed placement box; workspace XY boundary bypassed for drop.")

        if _in_stand(cx, cy):
            print(f"\n  {'='*62}")
            print(f"  ABORT — {label} (X={cx:.3f}, Y={cy:.3f}) is inside the camera stand no-go zone.")
            print(f"  {'='*62}")
            print(_stand_box_message())
            power_off_robot()
            sys.exit(1)

        if _in_conveyor(cx, cy):
            print(f"\n  {'='*62}")
            print(f"  ABORT — {label} (X={cx:.3f}, Y={cy:.3f}) is inside the conveyor belt no-go zone.")
            print(f"  {'='*62}")
            power_off_robot()
            sys.exit(1)

    if not (CAM_X_MIN <= MOVE_X <= CAM_X_MAX and CAM_Y_MIN <= MOVE_Y <= CAM_Y_MAX):
        print(f"\n  [WARN] Pick target ({MOVE_X:.3f}, {MOVE_Y:.3f}) is outside the camera scan zone — proceed with caution.")

    current = r.get_tcp_pose()
    home = get_home_pose(current)

    lift_pick_forward = copy.deepcopy(home)
    lift_pick_forward[0] = PICK_TARGET_X
    lift_pick_forward[1] = PICK_TARGET_Y
    lift_pick_forward[2] = TRANSIT_HEIGHT
    lift_pick_forward[5] = math.radians(HOME_RZ)

    lift_pick = copy.deepcopy(lift_pick_forward)
    lift_pick[5] = math.radians(PLANNED_RZ_DEG)

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
    lift_drop_grip[5] = math.radians(PLANNED_RZ_DEG)
    drop_pose_grip = copy.deepcopy(drop_pose)
    drop_pose_grip[5] = math.radians(PLANNED_RZ_DEG)

    print(f"\n  lift_pick  TCP: {[round(v,3) for v in lift_pick[:3]]} fingertip Z={TRANSIT_HEIGHT-GRIPPER_LENGTH:.3f}")
    print(f"  pick_pose  TCP: {[round(v,3) for v in pick_pose[:3]]} holding fingertip/contact Z={PICK_Z_DYNAMIC-CLOSED_GRIPPER_LENGTH:.3f}")
    print(f"  lift_drop  TCP: {[round(v,3) for v in lift_drop[:3]]} fingertip Z={TRANSIT_HEIGHT-GRIPPER_LENGTH:.3f}")
    print(f"  drop_pose  TCP: {[round(v,3) for v in drop_pose[:3]]} holding fingertip/contact Z={DROP_RELEASE_Z-CLOSED_GRIPPER_LENGTH:.3f}")

    print("\n[Pre-compute] Phase 1: wrist rotate at pick hover -> descend to pick")
    phase1_rotate = [lift_pick_forward, lift_pick]
    phase1_approach = build_full_trajectory([lift_pick, pick_pose])

    print("[Pre-compute] Phase 2: lift while holding -> rotate forward -> transit/drop")
    phase2_depart = build_full_trajectory([pick_pose, lift_pick])
    phase2_reorient = [lift_pick, lift_pick_forward_after]
    phase2_approach = build_full_trajectory([lift_drop_grip,drop_pose_grip,])

    print("[Pre-compute] Phase 3: lift away from box after release")
    phase3_depart = build_full_trajectory([drop_pose_grip,lift_drop_grip,])

    print("\n[Pre-flight] Validating all phases (gripper-volume aware)...")
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
        print(e)
        print("\n  [ABORT] No motion sent. Correct the coordinates and restart.")
        power_off_robot()
        sys.exit(1)

    print(f"\n[Pre-flight] Object {cycle_index}/{total_cycles} validated — ready to execute")
    input(f"\n  *** Press ENTER to start object {cycle_index}/{total_cycles} motion ***\n")

    current = r.get_tcp_pose()
    home = get_home_pose(current)
    correction = None
    correction_bypassed = False

    if not is_at_home(r):
        print("\n[Pre-compute] Correction: current -> home  "
              f"(robot not at home: X={current[0]:.3f} Y={current[1]:.3f} Z={current[2]:.3f})")
        try:
            correction = build_full_trajectory([current, home])
        except RuntimeError as e:
            print(f"\n[Pre-compute] Obstacle-aware correction route failed: {e}")
            if HAS_EXTRA_OBS:
                ans = input("  Ignore extra obstacle and plan correction? (yes/no): ").strip().lower()
                if ans == "yes":
                    _BYPASS_EXTRA_OBS = True
                    correction = _density_segment(current, home)
                    correction = [current] + correction + [home]
                    correction_bypassed = True
                else:
                    power_off_robot()
                    sys.exit(1)
            else:
                raise

    if correction is not None:
        validate_trajectory(correction, label="Correction (current -> home)", bypass_extra_obs=correction_bypassed)
        execute_trajectory(r, correction, label="Correction — current -> home", bypass_extra_obs=correction_bypassed)

    execute_joint_transit(r, home, lift_pick_forward, label="Phase 1 transit — Home -> lift_pick_forward")

    print("[Pause] At pick hover. Rotating wrist from forward-facing HOME_RZ to object grip angle...")
    execute_trajectory(r, phase1_rotate, label="Phase 1 wrist rotate — forward -> grip angle")
    time.sleep(0.3)

    # Open 30% larger than the object before descending.
    gripper_open_for_object(OBJECT_GRIP_WIDTH_M)

    execute_trajectory(r, phase1_approach, label="Phase 1 approach — lift_pick -> pick_pose")
    gripper_grip_object(OBJECT_GRIP_WIDTH_M)

    CARRIED_OBJECT_ENABLED = True

    execute_trajectory(r, phase2_depart, label="Phase 2 depart — pick_pose -> lift_pick")
    
    print("[Transit] Keeping object at grip angle for angled placement.")
    execute_joint_transit(r,lift_pick,lift_drop_grip,label= "Phase 2 transit — lift_pick_grip -> lift_drop_grip")

    print("[Drop] Moving down to the placement height, then releasing object.")
    execute_trajectory(r, phase2_approach, label="Phase 2 approach — lift_drop -> drop_pose")
    gripper_release_object(OBJECT_GRIP_WIDTH_M)

    CARRIED_OBJECT_ENABLED = False

    execute_trajectory(r, phase3_depart, label="Phase 3 depart — drop_pose -> lift_drop")
    print("[Reorient] Object released. Rotating wrist back to forward-facing HOME_RZ.")
    execute_trajectory(r,[lift_drop_grip, lift_drop],label="Phase 3 reorient — grip angle -> forward")

    execute_joint_transit(r, lift_drop, home, label="Phase 3 transit — lift_drop -> Home")

    print(f"\n=== Object {cycle_index}/{total_cycles} cycle complete ===")


def main():
    print("\n=== Camera Pick-and-Place — LARA 5 REAL ROBOT ===")

    global PICK_SEQUENCE
    PICK_SEQUENCE = get_pick_sequence_with_valid_placement()
    # Placement was already pre-planned during input.

    ensure_robot_ready(r)
    check_starting_position(r)
    gripper_startup()
    gripper_open()

    if HAS_KEYBOARD:
        kb_thread = threading.Thread(target=keyboard_listener, args=(r,), daemon=True)
        kb_thread.start()
        print("  Keyboard listener active — H=home  Q=quit\n")

    total_cycles = len(PICK_SEQUENCE)
    for idx, seq_item in enumerate(PICK_SEQUENCE, start=1):
        print(f"\n{'='*62}")
        print(f"  STARTING PICK-AND-PLACE OBJECT {idx}/{total_cycles}")
        print(f"{'='*62}")
        execute_one_pick_cycle(seq_item, idx, total_cycles)

    print("\n=== All requested pick-and-place cycles complete ===")
    #gripper_shutdown()
    power_off_robot()


if __name__ == "__main__":
    main()
