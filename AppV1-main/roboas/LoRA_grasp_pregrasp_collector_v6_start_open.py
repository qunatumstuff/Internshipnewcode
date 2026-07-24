import os
import re
import json
import shutil
from time import sleep
from datetime import datetime

import cv2
import numpy as np

try:
    import pyrealsense2 as rs
except ImportError:
    rs = None

try:
    from neurapy.robot import Robot  # type: ignore
except ImportError:
    import sys
    sys.path.append(r"C:\Module-A\PythonAPI")
    from neurapy.robot import Robot  # type: ignore


# ============================================================
# LoRA / VLA GRASP + AUTO-PREGRASP DATA COLLECTOR
# Saves into the same TIEFA_Phase1_Dataset root used by the formatter
# ============================================================
#
# This combines:
#   1. Old LoRA image + instruction + pose_action.json format
#   2. OnRobot 2FG7 gripper control through external-device API
#
# Workflow:
#   1. Choose object from menu
#   2. Park robot outside camera view
#   3. Press Enter to capture image FIRST
#   4. Script auto-creates:
#        - grasp sample folder
#        - pregrasp sample folder
#        - instruction.txt for both
#   5. Move robot/gripper manually to actual grabbing pose
#   6. Press Enter to record grasp pose and optionally run gripper
#   7. Script auto-generates pregrasp pose = grasp pose + Z offset
#
# Output folder format stays compatible with your old collection format:
#   task_xxx_grasp_item/
#       image.jpg
#       instruction.txt
#       pose_action.json
#       gripper_feedback.json
#
#   task_xxx_pregrasp_item/
#       image.jpg
#       instruction.txt
#       pose_action.json
#
# ============================================================


# ==========================
# CONFIG
# ==========================

DATASET_ROOT = r"C:\Module-A\PythonAPI\TIEFA_Phase1_Dataset"

# Camera source
# Uses Intel RealSense through pyrealsense2, not the laptop webcam.
# Do not use cv2.VideoCapture(0) here because that can open the laptop camera instead.
CAMERA_BACKEND = "realsense"
REALSENSE_WIDTH = 640
REALSENSE_HEIGHT = 480
REALSENSE_FPS = 30

# Keep False so it does NOT silently fall back to laptop webcam if RealSense fails.
# Change to True only if you intentionally want webcam fallback for debugging.
ALLOW_WEBCAM_FALLBACK = False
WEBCAM_INDEX = 0

# Live preview stays open during the workflow so you can always see whether the arm/gripper is in frame.
LIVE_PREVIEW_WINDOW_NAME = "LoRA Live RealSense Preview - C capture | P pose | Y/N choices | Q quit"

PROCESS_FILE = "OnRobot2FG7_RTU_DEFAULT.json"

# IMPORTANT FORCE NOTE:
# The 2FG7 datasheet lists gripping force as roughly 20 N to 140 N.
# The API parameter "force" is a COMMAND value from 0 to 100%, not live measured force.
# The functions we found do not expose actual force feedback, so dataset records it as
# commanded_force_percent and sets actual_force_feedback_n = None.
DATASHEET_MIN_GRIP_FORCE_N = 20.0
DATASHEET_MAX_GRIP_FORCE_N = 140.0
FORCE_FEEDBACK_AVAILABLE = False
FORCE_FIELD_NOTE = (
    "force is commanded_force_percent only; actual force feedback is not available from the current JSON functions"
)

ROBOT_POSITION_SCALE_TO_METERS = 1.0
ROBOT_ROTATION_UNIT = "deg"

# Same idea as old script. Adjust if needed.
MIN_X, MAX_X = 0.20, 0.75
MIN_Y, MAX_Y = -0.50, 0.20
MIN_Z, MAX_Z = 0.02, 0.80

# Pregrasp is generated from grasp pose by increasing Z.
PREGRASP_Z_OFFSET_M = 0.080

# Instruction prompts.
# Change PREGRASP_TEMPLATE to "position flange directly above {item}" if you want
# the exact older wording.
GRASP_TEMPLATE = "grab the {item} with the gripper"
PREGRASP_TEMPLATE = "position gripper directly above {item}"


# ==========================
# ROBOT + GRIPPER SETUP
# ==========================

robot = Robot()


# Your measured calibration:
# command width -> actual jaw gap
# 90=38mm, 80=33mm, 70=24mm, 60=16.5mm, 50=7mm, 40=0mm
# Linear approx from your earlier working file:
SLOPE = 0.7871
INTERCEPT = -31.4143

WIDTH_MAX_SAFE = 85       # use close(width=85) to open safely
WIDTH_MIN_SAFE = 45       # avoid fully crushing while testing

# Startup behaviour: keep gripper open before recording so it does not start closed.
STARTUP_OPEN_WIDTH = 85
STARTUP_WIDTH_SKIP_TOLERANCE = 1.0  # if current width is already >= 84, skip open command


def mm_to_width(gap_mm):
    width = (float(gap_mm) - INTERCEPT) / SLOPE
    return max(WIDTH_MIN_SAFE, min(WIDTH_MAX_SAFE, width))


def width_to_mm(width):
    return SLOPE * float(width) + INTERCEPT


# mode:
#   "force"    -> use GraspWorkpiece, better for hard objects
#   "position" -> use close() to a fixed width, safer for soft objects
OBJECT_CATALOGUE = {
    "yellow cube": {
        "grip_mm": 25.0,
        "mode": "force",
        "force": 20,
        "speed": 15,
        "bite_mm": 4.0,
    },
    "blue cube": {
        "grip_mm": 30.0,
        "mode": "force",
        "force": 20,
        "speed": 15,
        "bite_mm": 4.0,
    },
    "green cube": {
        "grip_mm": 30.0,
        "mode": "force",
        "force": 20,
        "speed": 15,
        "bite_mm": 4.0,
    },
    "red cube": {
        "grip_mm": 30.0,
        "mode": "force",
        "force": 20,
        "speed": 15,
        "bite_mm": 4.0,
    },
    "cube": {
        "grip_mm": 30.0,
        "mode": "force",
        "force": 20,
        "speed": 15,
        "bite_mm": 4.0,
    },
    "nut": {
        "grip_mm": 30.0,
        "mode": "force",
        "force": 20,
        "speed": 10,
        "bite_mm": 4.0,
    },
    "black marker": {
        "grip_mm": 20.5,
        "mode": "force",
        "force": 12,
        "speed": 8,
        "bite_mm": 3.0,
    },
    "screwdriver": {
        "grip_mm": 18.0,
        "mode": "force",
        "force": 12,
        "speed": 8,
        "bite_mm": 3.0,
    },

    # Soft/crushable objects:
    # Do NOT rely on force detection. Use position limit and very low speed.
    "sponge": {
        "grip_mm": 30.0,
        "mode": "position",
        "hold_width": 82,   # sponge-safe: tune 85,82,80,78 only if needed
        "force": 1,         # commanded %, not actual N; physical min may still be ~20 N
        "speed": 2,
    },
    "medicine": {
        "grip_mm": 28.0,
        "mode": "position",
        "hold_width": 76,
        "force": 1,
        "speed": 3,
    },
}


# ==========================
# UTILS
# ==========================

def sanitize_name(name):
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", name.strip().lower())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned or "object"


def create_base_id():
    return datetime.now().strftime("task_%Y%m%d_%H%M%S")


def ensure_unique_folder(path):
    if not os.path.exists(path):
        return path

    base = path
    idx = 2
    while True:
        candidate = f"{base}_{idx}"
        if not os.path.exists(candidate):
            return candidate
        idx += 1


def get_arm_pose():
    pose = robot.get_tcp_pose()
    print("[RAW TCP POSE]", pose)

    return [
        float(pose[0]) * ROBOT_POSITION_SCALE_TO_METERS,
        float(pose[1]) * ROBOT_POSITION_SCALE_TO_METERS,
        float(pose[2]) * ROBOT_POSITION_SCALE_TO_METERS,
        float(pose[3]),
        float(pose[4]),
        float(pose[5]),
    ]


def make_pregrasp_pose_from_grasp(grasp_pose):
    pre = list(grasp_pose)
    pre[2] = float(pre[2]) + PREGRASP_Z_OFFSET_M
    return pre


def validate_pose_xyz(pose):
    x, y, z = pose[0], pose[1], pose[2]
    issues = []

    if not (MIN_X <= x <= MAX_X):
        issues.append(f"x={x:.4f} outside safe range {MIN_X:.2f} to {MAX_X:.2f}")

    if not (MIN_Y <= y <= MAX_Y):
        issues.append(f"y={y:.4f} outside safe range {MIN_Y:.2f} to {MAX_Y:.2f}")

    if not (MIN_Z <= z <= MAX_Z):
        issues.append(f"z={z:.4f} outside safe range {MIN_Z:.2f} to {MAX_Z:.2f}")

    return issues


def confirm_pose_if_warning(pose, pose_name):
    issues = validate_pose_xyz(pose)
    if not issues:
        return True

    print(f"\n[WARNING] {pose_name} pose has possible safety issues:")
    for issue in issues:
        print(" -", issue)

    ans = input("Save this pose anyway? (y/n): ").strip().lower()
    return ans == "y"


def save_image(frame, folder):
    image_path = os.path.join(folder, "image.jpg")
    ok = cv2.imwrite(image_path, frame)

    if not ok:
        raise RuntimeError(f"Failed to save image: {image_path}")

    return image_path


def save_instruction(folder, instruction):
    instruction_path = os.path.join(folder, "instruction.txt")

    with open(instruction_path, "w", encoding="utf-8") as f:
        f.write(instruction.strip() + "\n")

    return instruction_path



def summarize_gripper_command(gripper_plan=None, gripper_feedback=None):
    """
    Creates a clear gripper summary for the dataset.
    This avoids pretending that the recorded force is measured force.
    """
    summary = {
        "force_feedback_available": FORCE_FEEDBACK_AVAILABLE,
        "actual_force_feedback_n": None,
        "datasheet_min_grip_force_n": DATASHEET_MIN_GRIP_FORCE_N,
        "datasheet_max_grip_force_n": DATASHEET_MAX_GRIP_FORCE_N,
        "force_note": FORCE_FIELD_NOTE,
        "commanded_open": None,
        "commanded_grip": None,
        "status_feedback": None,
        "width_feedback": None,
    }

    if gripper_plan:
        open_cmd = gripper_plan.get("open_command", {})
        grip_cmd = gripper_plan.get("grip_command", {})
        summary["mode"] = gripper_plan.get("mode")
        summary["commanded_open"] = open_cmd
        summary["commanded_grip"] = grip_cmd

        # Convenience flat fields for training/debugging.
        grip_params = grip_cmd.get("params", {}) if isinstance(grip_cmd, dict) else {}
        summary["commanded_grip_width"] = grip_params.get("width")
        summary["commanded_grip_speed"] = grip_params.get("speed")
        summary["commanded_grip_force_percent"] = grip_params.get("force")

    if gripper_feedback:
        after = gripper_feedback.get("after", {})
        summary["status_feedback"] = after.get("status")
        summary["width_feedback"] = after.get("width")
        summary["grip_command_ok"] = gripper_feedback.get("grip", {}).get("ok")
        summary["overall_success"] = gripper_feedback.get("success")

    return summary

def save_pose_action(
    folder,
    session_id,
    pose,
    gripper_value,
    pose_type,
    item,
    instruction,
    linked_session_id=None,
    generated_from=None,
    gripper_plan=None,
    gripper_feedback=None,
):
    pose_path = os.path.join(folder, "pose_action.json")

    # Keep old format keys so existing formatter/dataset scripts still work.
    payload = {
        "session_id": session_id,
        "pose": pose,
        "pose_format": {
            "position": "metres",
            "rotation": ROBOT_ROTATION_UNIT,
            "order": ["x", "y", "z", "roll", "pitch", "yaw"],
        },
        "gripper": gripper_value,
        "captured_at": datetime.now().isoformat(),

        # Extra metadata for grasp dataset.
        "pose_type": pose_type,
        "target_object": item,
        "instruction": instruction,
        "linked_session_id": linked_session_id,
        "generated_from": generated_from,
        "pregrasp_z_offset_m": PREGRASP_Z_OFFSET_M if pose_type == "pregrasp" else None,
        "gripper_plan": gripper_plan,
        "gripper_feedback": gripper_feedback,

        # Clear force interpretation for the dataset.
        # Do not treat commanded_force_percent as actual force feedback.
        "gripper_command_summary": summarize_gripper_command(gripper_plan, gripper_feedback),
        "force_feedback_available": FORCE_FEEDBACK_AVAILABLE,
        "actual_force_feedback_n": None,
        "force_note": FORCE_FIELD_NOTE,
    }

    with open(pose_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    return pose_path


def save_feedback(folder, feedback):
    path = os.path.join(folder, "gripper_feedback.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(feedback, f, indent=2)
    return path


# ==========================
# GRIPPER FUNCTIONS
# ==========================

def call_gripper(function_name, params=None):
    return robot.execute_external_device_function(PROCESS_FILE, function_name, params or {})


def call_gripper_safe(function_name, params=None):
    try:
        result = call_gripper(function_name, params or {})
        return True, result
    except Exception as e:
        return False, str(e)


def recover_gripper():
    print("[GRIPPER] Recovering / clearing error state...")
    call_gripper_safe("Safety_Stop", {})
    sleep(0.5)
    call_gripper_safe("Init", {})
    sleep(1.0)


def gripper_init():
    print("[GRIPPER] Init...")
    ok, res = call_gripper_safe("Init", {})
    print("[GRIPPER] Init result:", res)
    sleep(1.0)
    return ok, res


def clear_gripper_error_soft():
    """
    Best-effort software reset.
    Note: if the LARA External Source layer is stuck, only the tablet
    disconnect/reconnect/reset may clear it. This function should not crash
    dataset collection.
    """
    print("[GRIPPER] Trying software clear of external-device error...")

    try:
        res = robot.reset_external_device_error(PROCESS_FILE)
        print("[GRIPPER] reset_external_device_error:", res)
    except Exception as e:
        print("[GRIPPER] reset_external_device_error failed:", e)

    try:
        res = robot.reset_external_device_actions()
        print("[GRIPPER] reset_external_device_actions:", res)
    except Exception as e:
        print("[GRIPPER] reset_external_device_actions failed:", e)

    sleep(0.8)


def current_width_command_from_feedback(width_feedback):
    """
    returnCurrentWidth usually returns values like:
      {'getInternalWidth': '849.000000', 'getExternalWidth': '709.000000'}
    When command width is 85, getInternalWidth is around 849,
    so command-width estimate is getInternalWidth / 10.
    """
    if not isinstance(width_feedback, dict):
        return None

    if "error" in width_feedback:
        return None

    for key in ("getInternalWidth", "getExternalWidth"):
        if key in width_feedback:
            try:
                raw = float(width_feedback[key])
                # Internal width maps cleanly to command percentage x10.
                if key == "getInternalWidth":
                    return raw / 10.0
            except Exception:
                pass

    return None


def prepare_gripper_start_open():
    """
    Startup routine:
      1. Check if gripper already looks open around width 85.
      2. If yes, skip.
      3. If not, Init then move to width 85.
      4. If external source is in error, try software clear once.
      5. If still stuck, continue dataset collection without crashing.
    """
    print("[GRIPPER] Startup check: make sure gripper is open to width 85.")

    width_feedback = read_gripper_width()
    current_cmd = current_width_command_from_feedback(width_feedback)

    if current_cmd is not None:
        print(f"[GRIPPER] Current width estimate: {current_cmd:.1f}")
        if current_cmd >= (STARTUP_OPEN_WIDTH - STARTUP_WIDTH_SKIP_TOLERANCE):
            print("[GRIPPER] Already open enough. Skipping startup open.")
            return True
    else:
        print("[GRIPPER] Could not read current width before Init:", width_feedback)

    ok_init, res_init = gripper_init()
    if not ok_init:
        print("[GRIPPER] Init failed. Trying software clear once...")
        clear_gripper_error_soft()
        ok_init, res_init = gripper_init()

    if not ok_init:
        print("[GRIPPER WARNING] Could not Init gripper. External Source may still be in error state.")
        print("[GRIPPER WARNING] Use tablet: External Devices -> disconnect/reconnect/reset -> Init.")
        print("[GRIPPER WARNING] Continuing dataset collection; skip physical gripper test until reset.")
        return False

    width_feedback = read_gripper_width()
    current_cmd = current_width_command_from_feedback(width_feedback)
    if current_cmd is not None:
        print(f"[GRIPPER] Width after Init estimate: {current_cmd:.1f}")
        if current_cmd >= (STARTUP_OPEN_WIDTH - STARTUP_WIDTH_SKIP_TOLERANCE):
            print("[GRIPPER] Already open enough after Init. Skipping open command.")
            return True

    print(f"[GRIPPER] Opening to width {STARTUP_OPEN_WIDTH}...")
    ok_open, res_open, params = move_width(STARTUP_OPEN_WIDTH, speed=15, force=1)
    print("[GRIPPER] Startup open result:", res_open)

    if not ok_open:
        print("[GRIPPER WARNING] Startup open failed. Continuing dataset collection without stopping.")
        return False

    width_feedback = read_gripper_width()
    current_cmd = current_width_command_from_feedback(width_feedback)
    if current_cmd is not None:
        print(f"[GRIPPER] Final startup width estimate: {current_cmd:.1f}")

    return True


def read_gripper_status():
    ok, res = call_gripper_safe("returnStatus", {})
    if ok and isinstance(res, dict):
        return res
    return {"error": res}


def read_gripper_width():
    ok, res = call_gripper_safe("returnCurrentWidth", {})
    if ok and isinstance(res, dict):
        return res
    return {"error": res}


def move_width(width, speed=10, force=1):
    # Important: use "close" as move-to-width.
    # Do NOT use "open" because it can physically open but throw timeout errors.
    width = int(round(max(WIDTH_MIN_SAFE, min(WIDTH_MAX_SAFE, float(width)))))
    speed = int(round(max(1, min(100, float(speed)))))
    force = int(round(max(0, min(100, float(force)))))

    params = {
        "width": width,
        "speed": speed,
        "force": force,
    }

    print(f"[GRIPPER] close/move width={width}, speed={speed}, force={force}")
    ok, res = call_gripper_safe("close", params)
    sleep(0.8)
    return ok, res, params


def open_gripper():
    return move_width(WIDTH_MAX_SAFE, speed=15, force=1)


def build_gripper_plan(item):
    spec = OBJECT_CATALOGUE[item]
    mode = spec["mode"]

    open_params = {
        "function": "close",
        "params": {
            "width": WIDTH_MAX_SAFE,
            "speed": 15,
            "force": 1,
        },
    }

    if mode == "position":
        grip_params = {
            "function": "close",
            "params": {
                "width": int(spec["hold_width"]),
                "speed": int(spec["speed"]),
                "force": int(spec["force"]),
            },
        }
    else:
        target_gap_mm = max(0.0, float(spec["grip_mm"]) - float(spec["bite_mm"]))
        target_width = int(round(mm_to_width(target_gap_mm)))

        grip_params = {
            "function": "GraspWorkpiece",
            "params": {
                "width": target_width,
                "speed": int(spec["speed"]),
                "force": int(spec["force"]),
            },
            "target_gap_mm": target_gap_mm,
        }

    return {
        "object": item,
        "mode": mode,
        "open_command": open_params,
        "grip_command": grip_params,
        "object_spec": spec,
    }


def run_grip_for_item(item, live_camera=None):
    plan = build_gripper_plan(item)

    feedback = {
        "object": item,
        "started_at": datetime.now().isoformat(),
        "plan": plan,
        "before": {
            "status": read_gripper_status(),
            "width": read_gripper_width(),
        },
        "open": None,
        "grip": None,
        "after": None,
        "success": False,
        "force_feedback_available": FORCE_FEEDBACK_AVAILABLE,
        "actual_force_feedback_n": None,
        "force_note": FORCE_FIELD_NOTE,
    }

    # Open/pre-open first.
    ok_open, res_open, open_params = move_width(
        plan["open_command"]["params"]["width"],
        plan["open_command"]["params"]["speed"],
        plan["open_command"]["params"]["force"],
    )
    feedback["open"] = {
        "ok": ok_open,
        "result": res_open,
        "params": open_params,
        "commanded_params": open_params,
        "actual_force_feedback_n": None,
    }

    if live_camera is not None:
        wait_for_live_key(
            live_camera,
            "Gripper pre-opened. Press G/ENTER/SPACE to GRIP",
            ["g", "enter", "space"],
            extra_line="Live preview stays open while waiting.",
        )
    else:
        input("\n[GRIPPER] Gripper is pre-opened. Press Enter to GRIP now...")

    grip_cmd = plan["grip_command"]
    fn = grip_cmd["function"]
    params = grip_cmd["params"]

    print(f"[GRIPPER] {fn}: {params}")
    ok_grip, res_grip = call_gripper_safe(fn, params)
    sleep(1.0)

    feedback["grip"] = {
        "ok": ok_grip,
        "function": fn,
        "params": params,
        "commanded_params": params,
        "commanded_force_percent": params.get("force") if isinstance(params, dict) else None,
        "actual_force_feedback_n": None,
        "result": res_grip,
    }

    if not ok_grip:
        print("[GRIPPER WARNING] Grip command reported an error:")
        print(res_grip)
        recover_gripper()

    feedback["after"] = {
        "status": read_gripper_status(),
        "width": read_gripper_width(),
    }

    # Basic success check.
    status_dict = feedback["after"]["status"]
    status_text = str(status_dict)
    if plan["mode"] == "force":
        feedback["success"] = ("Grip detected" in status_text)
    else:
        # For soft/position mode, we cannot confirm force. We record that command ran.
        feedback["success"] = bool(ok_grip)

    feedback["ended_at"] = datetime.now().isoformat()

    print("[GRIPPER] Feedback:")
    print(json.dumps(feedback, indent=2))

    return plan, feedback


# ==========================
# CAMERA / DATASET WORKFLOW
# ==========================

class LiveCamera:
    """
    Continuous live camera preview.
    Default backend is Intel RealSense via pyrealsense2.
    This avoids accidentally using the laptop webcam through cv2.VideoCapture(0).
    """

    def __init__(self):
        self.backend = None
        self.pipeline = None
        self.video = None
        self.latest_frame = None

    def start(self):
        if CAMERA_BACKEND.lower() == "realsense":
            if rs is None:
                if not ALLOW_WEBCAM_FALLBACK:
                    raise RuntimeError(
                        "pyrealsense2 is not installed, so Intel RealSense cannot be opened. "
                        "Install pyrealsense2 or set ALLOW_WEBCAM_FALLBACK=True only for debugging."
                    )
                print("[CAMERA WARNING] pyrealsense2 not installed. Falling back to webcam.")
                return self._start_webcam()

            try:
                self.pipeline = rs.pipeline()
                config = rs.config()
                config.enable_stream(
                    rs.stream.color,
                    REALSENSE_WIDTH,
                    REALSENSE_HEIGHT,
                    rs.format.bgr8,
                    REALSENSE_FPS,
                )
                self.pipeline.start(config)
                self.backend = "realsense"
                print(f"[CAMERA] Intel RealSense started: {REALSENSE_WIDTH}x{REALSENSE_HEIGHT}@{REALSENSE_FPS}")
                return
            except Exception as e:
                if not ALLOW_WEBCAM_FALLBACK:
                    raise RuntimeError(
                        "Could not start Intel RealSense. Laptop webcam fallback is disabled. "
                        f"Original error: {e}"
                    )
                print("[CAMERA WARNING] Could not start RealSense. Falling back to webcam:", e)
                return self._start_webcam()

        return self._start_webcam()

    def _start_webcam(self):
        self.video = cv2.VideoCapture(WEBCAM_INDEX, cv2.CAP_DSHOW)
        if not self.video.isOpened():
            self.video.release()
            self.video = cv2.VideoCapture(WEBCAM_INDEX)

        if not self.video.isOpened():
            raise RuntimeError("Could not open fallback webcam.")

        self.backend = "webcam"
        print(f"[CAMERA] Fallback webcam started at index {WEBCAM_INDEX}")

    def read(self):
        if self.backend == "realsense":
            frames = self.pipeline.wait_for_frames()
            color_frame = frames.get_color_frame()
            if not color_frame:
                return False, None
            frame = np.asanyarray(color_frame.get_data())
            self.latest_frame = frame
            return True, frame

        if self.backend == "webcam":
            ret, frame = self.video.read()
            if ret:
                self.latest_frame = frame
            return ret, frame

        return False, None

    def stop(self):
        try:
            if self.pipeline is not None:
                self.pipeline.stop()
        except Exception:
            pass

        try:
            if self.video is not None:
                self.video.release()
        except Exception:
            pass

        cv2.destroyAllWindows()
        cv2.waitKey(1)


def draw_live_overlay(frame, message, extra_line=None):
    display = frame.copy()

    cv2.putText(
        display,
        f"Camera: {CAMERA_BACKEND.upper()} / Intel RealSense target",
        (20, 30),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (0, 255, 255),
        2,
        cv2.LINE_AA,
    )

    cv2.putText(
        display,
        message,
        (20, 65),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (0, 255, 0),
        2,
        cv2.LINE_AA,
    )

    if extra_line:
        cv2.putText(
            display,
            extra_line,
            (20, 100),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )

    return display


def wait_for_live_key(live_camera, message, allowed_keys, extra_line=None, return_frame=False):
    """
    Keeps the RealSense preview alive while waiting for a key.

    allowed_keys: list of lowercase characters, e.g. ["c", "q"]
    Special names supported: "enter", "space", "esc".
    """
    allowed = set(k.lower() for k in allowed_keys)

    while True:
        ret, frame = live_camera.read()
        if not ret or frame is None:
            print("[CAMERA WARNING] Failed to read frame, retrying...")
            sleep(0.03)
            continue

        display = draw_live_overlay(frame, message, extra_line)
        cv2.imshow(LIVE_PREVIEW_WINDOW_NAME, display)
        key = cv2.waitKey(30) & 0xFF

        if key == 255:
            continue

        key_name = None
        if key in (13, 10):
            key_name = "enter"
        elif key == 32:
            key_name = "space"
        elif key == 27:
            key_name = "esc"
        else:
            key_name = chr(key).lower() if 0 <= key <= 255 else str(key)

        if key_name in allowed:
            if return_frame:
                return key_name, frame.copy()
            return key_name

        # Always allow q/esc as emergency quit if included.
        if key_name in ("q", "esc") and ("q" in allowed or "esc" in allowed):
            if return_frame:
                return key_name, frame.copy()
            return key_name


def capture_frame_from_live(live_camera):
    print("[CAMERA] Live preview is already running.")
    print("[CAMERA] Press C / ENTER / SPACE in the preview window to capture.")
    print("[CAMERA] Press Q / ESC to cancel.")

    key, frame = wait_for_live_key(
        live_camera,
        "Check arm/gripper. C/ENTER/SPACE = capture | Q/ESC = cancel",
        ["c", "enter", "space", "q", "esc"],
        extra_line="Park robot out of view before capturing the dataset image.",
        return_frame=True,
    )

    if key in ("q", "esc"):
        raise RuntimeError("Camera capture cancelled by user.")

    print("[CAMERA] Captured current RealSense frame.")
    return frame


def choose_object():
    items = list(OBJECT_CATALOGUE.keys())

    print("\nChoose item to record:")
    for i, item in enumerate(items, start=1):
        spec = OBJECT_CATALOGUE[item]
        print(f"  {i}. {item} ({spec['mode']})")

    print("  0. quit")

    while True:
        ans = input("Enter number or item name: ").strip().lower()

        if ans in ("0", "q", "quit", "exit"):
            return None

        if ans.isdigit():
            idx = int(ans)
            if 1 <= idx <= len(items):
                return items[idx - 1]

        if ans in OBJECT_CATALOGUE:
            return ans

        print("Invalid item. Try again.")


def create_sample_folders(item, frame):
    base_id = create_base_id()
    safe_item = sanitize_name(item)

    grasp_id = f"{base_id}_grasp_{safe_item}"
    pregrasp_id = f"{base_id}_pregrasp_{safe_item}"

    grasp_folder = ensure_unique_folder(os.path.join(DATASET_ROOT, grasp_id))
    pregrasp_folder = ensure_unique_folder(os.path.join(DATASET_ROOT, pregrasp_id))

    os.makedirs(grasp_folder, exist_ok=True)
    os.makedirs(pregrasp_folder, exist_ok=True)

    grasp_instruction = GRASP_TEMPLATE.format(item=item)
    pregrasp_instruction = PREGRASP_TEMPLATE.format(item=item)

    grasp_img = save_image(frame, grasp_folder)
    pregrasp_img = save_image(frame, pregrasp_folder)

    save_instruction(grasp_folder, grasp_instruction)
    save_instruction(pregrasp_folder, pregrasp_instruction)

    return {
        "base_id": base_id,
        "grasp_id": os.path.basename(grasp_folder),
        "pregrasp_id": os.path.basename(pregrasp_folder),
        "grasp_folder": grasp_folder,
        "pregrasp_folder": pregrasp_folder,
        "grasp_instruction": grasp_instruction,
        "pregrasp_instruction": pregrasp_instruction,
        "grasp_image": grasp_img,
        "pregrasp_image": pregrasp_img,
    }


def record_one_item(item, live_camera):
    print("\n" + "=" * 80)
    print(f"Recording item: {item}")
    print("=" * 80)

    print("\nStep 1: Park the robot/gripper OUTSIDE the camera view.")
    print("Arrange the object clearly in the workspace.")
    print("The RealSense preview will stay live while you check the frame.")

    frame = capture_frame_from_live(live_camera)
    folders = create_sample_folders(item, frame)

    print("\n[DATASET] Created folders:")
    print("  Grasp   :", folders["grasp_folder"])
    print("  Pregrasp:", folders["pregrasp_folder"])
    print("\n[DATASET] Instructions:")
    print("  Grasp   :", folders["grasp_instruction"])
    print("  Pregrasp:", folders["pregrasp_instruction"])

    print("\nStep 2: Manually jog/move the robot WITH GRIPPER to the actual grabbing pose.")
    print("Watch the live preview while moving, then press P in the preview window to record pose.")

    key = wait_for_live_key(
        live_camera,
        "Move gripper to GRASP pose. Press P = record pose | Q/ESC = cancel",
        ["p", "q", "esc"],
        extra_line="The preview stays live so you can see if the arm/gripper is in frame.",
    )
    if key in ("q", "esc"):
        print("[CANCELLED] Grasp pose not recorded. Delete created folders manually if not needed.")
        return

    grasp_pose = get_arm_pose()

    if not confirm_pose_if_warning(grasp_pose, "GRASP"):
        print("[CANCELLED] Grasp pose not saved. Delete the created folders manually if not needed.")
        return

    pregrasp_pose = make_pregrasp_pose_from_grasp(grasp_pose)

    if not confirm_pose_if_warning(pregrasp_pose, "AUTO-PREGRASP"):
        print("[CANCELLED] Auto-pregrasp pose not saved. Delete the created folders manually if not needed.")
        return

    # Build plan before grip so it is saved even if the physical grip is skipped.
    gripper_plan = build_gripper_plan(item)

    print("\nStep 3: Optional gripper test.")
    print("This will pre-open then grip using the object-specific setting.")
    print("For sponge/soft objects, this uses position mode with low speed.")
    print("Press Y in the preview to run gripper test, or N to skip.")

    run_key = wait_for_live_key(
        live_camera,
        "Run gripper test now? Y = yes | N = no | Q/ESC = cancel",
        ["y", "n", "q", "esc"],
        extra_line="Live RealSense preview remains open.",
    )

    if run_key in ("q", "esc"):
        print("[CANCELLED] Gripper test cancelled. Saving poses without feedback.")
        feedback = None
    elif run_key == "y":
        gripper_plan, feedback = run_grip_for_item(item, live_camera)
        save_feedback(folders["grasp_folder"], feedback)
    else:
        print("[GRIPPER] Skipped physical gripper test.")
        feedback = None

    grasp_pose_path = save_pose_action(
        folder=folders["grasp_folder"],
        session_id=folders["grasp_id"],
        pose=grasp_pose,
        gripper_value=1,
        pose_type="grasp",
        item=item,
        instruction=folders["grasp_instruction"],
        linked_session_id=folders["pregrasp_id"],
        generated_from=None,
        gripper_plan=gripper_plan,
        gripper_feedback=feedback,
    )

    pregrasp_pose_path = save_pose_action(
        folder=folders["pregrasp_folder"],
        session_id=folders["pregrasp_id"],
        pose=pregrasp_pose,
        gripper_value=0,
        pose_type="pregrasp",
        item=item,
        instruction=folders["pregrasp_instruction"],
        linked_session_id=folders["grasp_id"],
        generated_from=folders["grasp_id"],
        gripper_plan={
            "note": "Auto-generated pregrasp pose from grasp pose by adding Z offset.",
            "source_grasp_pose": grasp_pose,
            "z_offset_m": PREGRASP_Z_OFFSET_M,
        },
        gripper_feedback=None,
    )

    print("\n[COMPLETE]")
    print("Grasp pose   :", grasp_pose_path)
    print("Pregrasp pose:", pregrasp_pose_path)
    print("Grasp pose   :", grasp_pose)
    print("Pregrasp pose:", pregrasp_pose)

    if feedback is not None:
        print("\nPress Y in preview to release/open gripper, or N to leave it.")
        release_key = wait_for_live_key(
            live_camera,
            "Release/open gripper now? Y = open | N = skip",
            ["y", "n"],
            extra_line="Use this if the object is still being held.",
        )
        if release_key == "y":
            open_gripper()

    print("\nReady for next item.")


def main():
    os.makedirs(DATASET_ROOT, exist_ok=True)

    print("\nLoRA / VLA Grasp + Auto-Pregrasp Collector")
    print("Dataset root:", DATASET_ROOT)
    print("\nIMPORTANT:")
    print("- This version uses Intel RealSense through pyrealsense2, not cv2 laptop webcam.")
    print("- The live preview stays open during capture and pose recording.")
    print("- Image is captured FIRST while robot is out of camera view.")
    print("- You only manually record the actual GRASP pose.")
    print("- Pregrasp is auto-created by adding Z height to the grasp pose.")
    print("- Output keeps old image.jpg + instruction.txt + pose_action.json format.")
    print("- Force is saved as commanded_force_percent only, not actual measured force.")
    print("- For sponge, code uses width-limited position mode because minimum physical force can still be high.")
    print("- On startup, the script tries to keep the gripper open at width 85 and skips if already open.")

    live_camera = LiveCamera()
    try:
        live_camera.start()

        init_ans = input("\nPrepare/open gripper to width 85 now? (y/n, default y): ").strip().lower()
        if init_ans in ("", "y", "yes"):
            prepare_gripper_start_open()

        print("\n[CAMERA] RealSense live preview will remain active during each recording step.")
        print("[NOTE] Object selection still happens in the console.")

        while True:
            item = choose_object()
            if item is None:
                print("Bye.")
                break

            record_one_item(item, live_camera)

    finally:
        live_camera.stop()
        print("[INFO] Camera closed and dataset collection finished.")


if __name__ == "__main__":
    main()
