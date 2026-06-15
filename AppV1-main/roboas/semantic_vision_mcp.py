import asyncio
import time
import logging
import json
import urllib.request
import os
import math
import threading
import base64
import re
import traceback

import cv2
import numpy as np
import pyrealsense2 as rs
from ultralytics import YOLO

from mcp.server import Server
from mcp.types import Tool, TextContent
from mcp.server.sse import SseServerTransport
# Starlette removed - using pure ASGI routing for maximum robustness
import uvicorn

# Import semantic_camera.py — hardware layer.
# Provides: current_rgb_frame, get_camera_snapshot(), vision_loop(), get_object_crops()
import semantic_camera as camera

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vision-mcp")

# ==========================================
# CONFIGURATION
# ==========================================
# ==========================================
# OLLAMA AUTO-DISCOVERY
# ==========================================
def auto_detect_ollama():
    """Scans for an active Ollama instance and automatically selects the best vision model."""
    ips_to_try = [
        os.environ.get("LAPTOP_A_IP"),
        "127.0.0.1",
        "192.168.2.99",
        "192.168.2.13" # Or whatever Laptop A's IP is
    ]
    
    for ip in ips_to_try:
        if not ip: continue
        try:
            req = urllib.request.Request(f"http://{ip}:11434/api/tags")
            with urllib.request.urlopen(req, timeout=3) as response:
                data = json.loads(response.read().decode("utf-8"))
                models = [m["name"] for m in data.get("models", [])]
                logger.info(f"Ollama found at {ip}! Models: {models}")
                
                # Priority 1: Any Qwen Vision model
                for m in models:
                    if "qwen" in m.lower() and "vl" in m.lower():
                        return ip, m
                
                # Priority 2: LLaVA or other vision models
                for m in models:
                    if "llava" in m.lower() or "vision" in m.lower() or "pixtral" in m.lower():
                        return ip, m
                
                # Fallback: Just return the first available model
                if models:
                    return ip, models[0]
        except Exception:
            continue
            
    logger.error("Could not find any Ollama instance running!")
    return "127.0.0.1", "qwen3-vl:2b"

OLLAMA_IP, QWEN_MODEL = auto_detect_ollama()

print("="*60)
print("USING OLLAMA IP:", OLLAMA_IP)
print("USING MODEL:", QWEN_MODEL)
print("="*60)

logger.info(f"Using Ollama at {OLLAMA_IP} with model {QWEN_MODEL}")

ROBOT_MCP_URL = os.environ.get("ROBOT_MCP_URL", "http://localhost:8002/messages")
MAX_PLANNING_ITERATIONS = 5

# ==========================================
# OBJECT CATALOGUE
# height_m used for elevation analysis —
# Qwen compares detected Z against known height
# to determine if an object is stacked on another.
# ==========================================
OBJECT_CATALOGUE = {
    "black marker": {"size": "134 x 20.53 x 20.53 mm", "height_m": 0.02053,
                     "length_m": 0.134,   "breadth_m": 0.02053},
    "blue marker":  {"size": "134 x 20.53 x 20.53 mm", "height_m": 0.02053,
                     "length_m": 0.134,   "breadth_m": 0.02053},
    "cube":         {"size": "40 x 40 x 40 mm",         "height_m": 0.040,
                     "length_m": 0.040,   "breadth_m": 0.040},
    "green marker": {"size": "134 x 20.53 x 20.53 mm",  "height_m": 0.02053,
                     "length_m": 0.134,   "breadth_m": 0.02053},
    "medicine":     {"size": "115.72 x 51.17 x 18.95 mm","height_m": 0.01895,
                     "length_m": 0.11572, "breadth_m": 0.05117},
    "nut":          {"size": "34.6 x 30 x 17 mm",        "height_m": 0.017,
                     "length_m": 0.0346,  "breadth_m": 0.030},
    "pipe":         {"size": "120 x 110 x 54.5 mm",      "height_m": 0.0545,
                     "length_m": 0.120,   "breadth_m": 0.110,
                     "notes": "Smart grasp via segmentation mask"},
    "sponge":       {"size": "112.58 x 80 x 15.4 mm",    "height_m": 0.01540,
                     "length_m": 0.11258, "breadth_m": 0.080,
                     "notes": "Angled grasp configuration"},
}

# Camera-to-robot base frame transformation matrix.
# Calibrated to the physical D435i mounting position.
CAM_TO_ROBOT_T = np.array([
    [ 0.7337634310,  0.6126652048, -0.2936538341,  0.7173839756],
    [ 0.6785283256, -0.6388791698,  0.3625365054, -0.4903506740],
    [ 0.0345041846, -0.4652684744, -0.8844968672,  0.7880605490],
    [ 0.0,           0.0,           0.0,            1.0         ],
], dtype=np.float64)

# Z offset applied to every detection — compensates for the camera
# viewing objects from above, which causes the depth reading to land
# on the top surface of the object rather than its centre.
# 25mm raises the robot's approach height so the gripper doesn't
# dig into the object on descent.
Z_OFFSET_M = 0.025

server = Server("vision-mcp-server")

# ==========================================
# DETECTION — runs on demand using camera frame
# ==========================================
def _rotation_matrix_to_euler(R):
    """Decompose 3x3 rotation matrix into roll, pitch, yaw (radians)."""
    sy = math.sqrt(R[0, 0] ** 2 + R[1, 0] ** 2)
    singular = sy < 1e-6
    if not singular:
        roll  = math.atan2( R[2, 1],  R[2, 2])
        pitch = math.atan2(-R[2, 0],  sy)
        yaw   = math.atan2( R[1, 0],  R[0, 0])
    else:
        roll  = math.atan2(-R[1, 2],  R[1, 1])
        pitch = math.atan2(-R[2, 0],  sy)
        yaw   = 0.0
    return roll, pitch, yaw


def _pixel_to_robot(cx_px, cy_px, angle_rad, depth_frame, intrinsics):
    """
    Convert a pixel centre + OBB angle into robot-frame XYZ and yaw.
    Returns dict {x, y, z, angle_deg} or None if depth is invalid.
    """
    distance = depth_frame.get_distance(int(cx_px), int(cy_px))
    if distance <= 0.0:
        return None

    cam_pt  = rs.rs2_deproject_pixel_to_point(intrinsics, [cx_px, cy_px], distance)
    robot_pt = CAM_TO_ROBOT_T @ np.array([cam_pt[0], cam_pt[1], cam_pt[2], 1.0])

    R_cam_to_robot   = CAM_TO_ROBOT_T[:3, :3]
    R_obj_in_camera  = np.array([
        [ math.cos(angle_rad), -math.sin(angle_rad), 0],
        [ math.sin(angle_rad),  math.cos(angle_rad), 0],
        [ 0,                    0,                   1],
    ])
    _, _, yaw = _rotation_matrix_to_euler(R_cam_to_robot @ R_obj_in_camera)

    return {
        "x":         round(float(robot_pt[0]), 4),
        "y":         round(float(robot_pt[1]), 4),
        "z":         round(float(robot_pt[2]) + Z_OFFSET_M, 4),  # +25mm height offset
        "angle_deg": round(math.degrees(yaw),  2),
    }


def _compute_pipe_grasp(mask_binary, depth_frame, intrinsics, angle_rad):
    """
    Use the pipe segmentation mask to find the two physical pipe ends,
    deproject both through depth, and return the one needing least wrist
    rotation from home (angle = 0 deg robot frame).

    Returns a detection dict with grasp_label, or None if mask is too small.
    """
    contours, _ = cv2.findContours(
        mask_binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    if not contours:
        return None

    largest = max(contours, key=cv2.contourArea)
    if cv2.contourArea(largest) < 500:
        return None

    rect = cv2.minAreaRect(largest)
    box  = cv2.boxPoints(rect).astype(np.int32)
    w, h = rect[1]

    # Short sides of the rectangle = the two physical pipe ends
    if w < h:
        end_a_px = ((box[0] + box[3]) / 2).astype(int)
        end_b_px = ((box[1] + box[2]) / 2).astype(int)
    else:
        end_a_px = ((box[0] + box[1]) / 2).astype(int)
        end_b_px = ((box[2] + box[3]) / 2).astype(int)

    candidates = []
    for label, px in [("grasp_A", end_a_px), ("grasp_B", end_b_px)]:
        coords = _pixel_to_robot(px[0], px[1], angle_rad, depth_frame, intrinsics)
        if coords is None:
            continue
        coords["label"] = label
        candidates.append(coords)

    if not candidates:
        return None

    # Pick the end requiring least wrist rotation from home (0 deg)
    best = min(candidates, key=lambda c: abs(c["angle_deg"]))
    return best


def run_yolo_detection(color_image, depth_frame, intrinsics):
    """
    Run detection on all catalogue objects using a two-pass approach:

    Pass 1 — OBB model (best16.pt) on the full image:
        Detects all objects and extracts their trained orientation angle.
        For non-pipe/sponge objects this also gives the final centre position.
        For pipe and sponge, we keep only the OBB angle here and use
        segmentation for their centre/endpoint positions in Pass 2.

    Pass 2 — Segmentation model (best13.pt) for pipe and sponge:
        Gets accurate mask-based centre/endpoint positions.
        Uses the OBB angle from Pass 1 for orientation (more reliable than
        minAreaRect on a complex L-shaped or flat contour).
        Falls back to minAreaRect angle if OBB did not detect that class.

    All coordinates include the +25mm Z offset (Z_OFFSET_M).
    """
    detections   = []

    # ── Pass 1: OBB — run on full image, collect angles and non-seg detections ─
    # Build angle lookup {class_name: angle_rad} from OBB results so
    # segmentation pass can use the trained orientation angle.
    obb_angles = {}   # class_name → best OBB angle_rad (highest confidence)

    if camera.inference_lock.acquire(timeout=2.0):
        try:
            obb_results = camera.model(
            color_image, verbose=False, agnostic_nms=False, iou=0.35, conf=0.35
        )
        finally:
            camera.inference_lock.release()
    else:
        logger.warning("Could not acquire inference lock for OBB, skipping detection")
        return []

    for result in obb_results:
        if result.obb is None:
            continue
        for idx, obb in enumerate(result.obb):
            cls_id   = int(obb.cls[0])
            cls_name = camera.model.names[cls_id].lower()
            conf     = float(obb.conf[0])

            if cls_name not in OBJECT_CATALOGUE:
                continue

            angle_rad = float(obb.xywhr[0][4])

            # Store the highest-confidence OBB angle per class
            if cls_name not in obb_angles or conf > obb_angles[cls_name]["conf"]:
                obb_angles[cls_name] = {"angle_rad": angle_rad, "conf": conf}

            # Pipe and sponge: keep angle only, position comes from segmentation
            if cls_name in ("pipe", "sponge"):
                continue

            cx_px = float(obb.xywhr[0][0])
            cy_px = float(obb.xywhr[0][1])

            coords = _pixel_to_robot(cx_px, cy_px, angle_rad, depth_frame, intrinsics)
            if coords is None:
                continue

            detections.append({
                "id":          f"{cls_name}_{idx}",
                "object_name": cls_name,
                "x":           coords["x"],
                "y":           coords["y"],
                "z":           coords["z"],
                "angle_deg":   coords["angle_deg"],
                "confidence":  round(conf, 3),
            })

    # ── Pass 2: Segmentation — pipe and sponge centre/endpoint positions ────────
    if camera.inference_lock.acquire(timeout=2.0):
        try:
            seg_results = camera.segment(
            color_image, verbose=False, agnostic_nms=False, iou=0.35, conf=0.35
        )
        finally:
            camera.inference_lock.release()
    else:
        logger.warning("Could not acquire inference lock for segmentation, skipping")
        seg_results = []

    for seg_result in seg_results:
        if seg_result.masks is None:
            continue
        masks     = seg_result.masks.data.cpu().numpy()
        class_ids = seg_result.boxes.cls.cpu().numpy().astype(int)
        confs     = seg_result.boxes.conf.cpu().numpy()

        for mask, class_id, conf in zip(masks, class_ids, confs):
            cls_name = camera.model.names[class_id].lower()
            if cls_name not in ("pipe", "sponge"):
                continue

            mask_bin = cv2.resize(mask, (color_image.shape[1], color_image.shape[0]))
            mask_bin = ((mask_bin > 0.5) * 255).astype(np.uint8)

            contours, _ = cv2.findContours(
                mask_bin, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            if not contours:
                continue
            largest      = max(contours, key=cv2.contourArea)
            rect         = cv2.minAreaRect(largest)
            cx_px, cy_px = rect[0]

            # Use OBB angle if available (more reliable than minAreaRect on complex shapes)
            # Fall back to minAreaRect angle if OBB didn't detect this class
            if cls_name in obb_angles:
                angle_rad = obb_angles[cls_name]["angle_rad"]
                logger.info(f"[{cls_name}] Using OBB angle: {math.degrees(angle_rad):.1f}deg")
            else:
                angle_rad = math.radians(rect[2])
                logger.info(f"[{cls_name}] OBB angle not available, using minAreaRect: {rect[2]:.1f}deg")

            if cls_name == "pipe":
                # Compute two physical pipe ends, select the one needing least rotation
                best = _compute_pipe_grasp(mask_bin, depth_frame, intrinsics, angle_rad)
                if best is None:
                    continue
                detections.append({
                    "object_name": "pipe",
                    "x":           best["x"],
                    "y":           best["y"],
                    "z":           best["z"],
                    "angle_deg":   best["angle_deg"],
                    "confidence":  round(float(conf), 3),
                    "grasp_label": best["label"],
                })
            else:
                # Sponge — mask centre deprojected through depth with OBB angle
                coords = _pixel_to_robot(cx_px, cy_px, angle_rad, depth_frame, intrinsics)
                if coords is None:
                    continue
                detections.append({
                    "object_name": "sponge",
                    "x":           coords["x"],
                    "y":           coords["y"],
                    "z":           coords["z"],
                    "angle_deg":   coords["angle_deg"],
                    "confidence":  round(float(conf), 3),
                })

    logger.info(
        f"Detection: {len(detections)} object(s) — "
        f"{[d['object_name'] for d in detections]}"
    )
    return detections


# RealSense pipeline — shared across tool calls
def get_realsense_depth_and_intrinsics():
    """
    Return the current aligned depth frame and colour intrinsics from camera.py
    shared in-memory space.
    """
    return camera.current_depth_frame, camera.camera_intrinsics


def get_current_detections():
    """
    Capture a fresh detection pass using camera.py's current RGB frame
    and the Vision MCP's own depth pipeline.

    Returns list of detection dicts, empty list on failure.
    """
    color_image = camera.current_rgb_frame
    if color_image is None:
        logger.warning("No RGB frame from camera yet.")
        return []

    depth_frame, intrinsics = get_realsense_depth_and_intrinsics()
    if depth_frame is None:
        logger.warning("No depth frame available.")
        return []

    return run_yolo_detection(color_image, depth_frame, intrinsics)


def get_frame_as_base64():
    """
    Return camera.py's current frame as a base64 JPEG string.
    Strips the data URL prefix if present.
    """
    raw = camera.get_camera_snapshot()
    if raw.startswith("Error"):
        return None
    if raw.startswith("data:"):
        parts = raw.split(",")
        return parts[1] if len(parts) > 1 else None
    return raw


# ==========================================
# QWEN COMMUNICATION
# ==========================================
async def ask_qwen_vision(prompt: str, base64_image: str) -> str:
    """Send image + prompt to Qwen3-VL via Ollama API."""
    logger.info(f"Connecting to Qwen at {OLLAMA_IP} with model {QWEN_MODEL}...")

    raw_b64 = base64_image
    if raw_b64.startswith("data:"):
        parts = raw_b64.split(",")
        if len(parts) > 1:
            raw_b64 = parts[1]

    # Removed /no_think because it caused qwen3-vl:2b to return empty responses.
    prompt_with_directive = prompt

    payload = {
        "model": QWEN_MODEL,
        "prompt": prompt_with_directive,
        "stream": False,
        "think": False,   # Stops Qwen3 burning all tokens on 'thinking' before the answer
        "images": [raw_b64],
        "options": {
            "temperature": 0.1,
            "num_predict": 1024
        }
    }

    print("IMAGE SIZE:", len(raw_b64))

    url = f"http://{OLLAMA_IP}:11434/api/generate"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    loop = asyncio.get_running_loop()
    def fetch():
        logger.info("SENDING TO QWEN...")
        with urllib.request.urlopen(req, timeout=300) as response:
            print("Qwen http received")
            return json.loads(response.read().decode("utf-8"))
    try:
        result = await loop.run_in_executor(None, fetch)
        logger.info("QWEN RESPONSE RECEIVED")
        print("RAW OLLAMA RESULT:", result)

        if "error" in result:
           traceback.print_exc()
           print("FULL OLLAMA ERROR RESULT:", json.dumps(result, indent=2))
           print(f"Ollama API Error: {result['error']}")
           return f"Ollama API Error: {result['error']}"
        
        print("RESULT KEYS:", result.keys())

        response_text = result.get("response", "")
        
        # Qwen3 thinking mode fallback: Ollama splits output into
        # "thinking" + "response". If the model spent all tokens
        # thinking, "response" is empty but the answer may be
        # inside the "thinking" field.
        if not response_text.strip():
            thinking_text = result.get("thinking", "")
            if thinking_text.strip():
                logger.warning("Qwen 'response' empty but 'thinking' has content — using it.")
                print("QWEN THINKING FIELD:", thinking_text[:500])
                response_text = thinking_text
            else:
                logger.error("Ollama returned empty in both 'response' and 'thinking'.")
                print("FULL OLLAMA RESULT KEYS:", list(result.keys()))
                print("FULL OLLAMA RESULT:", json.dumps(result, indent=2)[:2000])
                return "Ollama API Error: Model returned empty string."
            
        logger.info(f"[Qwen] {response_text[:120]}")
        return response_text
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Qwen network error: {e}")
        return f"Ollama API Error: {e}"


# ==========================================
# QWEN PLANNING
# ==========================================
def extract_qwen_json(raw: str) -> dict:
    raw = raw.strip()

    # Remove XML-style thinking
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()

    # Try extracting from ```json blocks first
    json_match = re.search(r"```json\s*(.*?)\s*```", raw, re.DOTALL)
    if json_match:
        json_text = json_match.group(1).strip()
    else:
        # Fallback to finding the first { and last }
        start = raw.find("{")
        end = raw.rfind("}")

        if start == -1 or end == -1 or end <= start:
            raise ValueError(f"No JSON object found in Qwen response: {raw[:300]}")

        json_text = raw[start:end + 1].strip()

    return json.loads(json_text)

def parse_qwen_action(raw: str) -> dict:
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()

    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    final = lines[-1].upper() if lines else ""

    if final.startswith("PICK"):
        return {
            "next_action": "pick",
            "obstacle_name": None,
            "reasoning": "Qwen chose PICK"
        }

    if final.startswith("RELOCATE:"):
        obstacle = final.split(":", 1)[1].strip().lower()
        return {
            "next_action": "relocate",
            "obstacle_name": obstacle,
            "reasoning": f"Qwen chose to relocate {obstacle}"
        }

    if final.startswith("ABORT:"):
        reason = final.split(":", 1)[1].strip()
        return {
            "next_action": "abort",
            "obstacle_name": None,
            "reasoning": reason
        }

    return {
        "next_action": "pick",
        "obstacle_name": None,
        "reasoning": f"Could not parse Qwen output — sensor analysis clear, defaulting to pick. Raw: {raw[:100]}"
    }

def is_inside_placement_box(det: dict) -> bool:
    """
    Return True if the detection is inside the virtual placement box.
    Uses coordinate bounds matching nogripperref.py with 5cm tolerance.
    """
    INBOX_TOLERANCE_M = 0.05
    try:
        x = float(det["x"])
        y = float(det["y"])
        return (
            0.248 - INBOX_TOLERANCE_M <= x <= 0.586 + INBOX_TOLERANCE_M and
            0.055 - INBOX_TOLERANCE_M <= y <= 0.280 + INBOX_TOLERANCE_M
        )
    except Exception:
        return False


def check_overlap_obb(d1: dict, d2: dict, clearance: float = 0.0) -> bool:
    """Check if two oriented bounding boxes overlap in XY plane using Separating Axis Theorem (SAT)."""
    # Retrieve catalogue sizes
    info1 = OBJECT_CATALOGUE.get(d1.get("object_name", ""), {})
    info2 = OBJECT_CATALOGUE.get(d2.get("object_name", ""), {})

    l1 = float(info1.get("length_m", 0.04))
    b1 = float(info1.get("breadth_m", 0.04))
    l2 = float(info2.get("length_m", 0.04))
    b2 = float(info2.get("breadth_m", 0.04))

    # Parse angles
    a1_val = d1.get("angle_deg")
    a2_val = d2.get("angle_deg")
    a1_deg = float(a1_val) if a1_val is not None else 0.0
    a2_deg = float(a2_val) if a2_val is not None else 0.0

    r1 = math.radians(a1_deg)
    r2 = math.radians(a2_deg)

    cos1, sin1 = math.cos(r1), math.sin(r1)
    cos2, sin2 = math.cos(r2), math.sin(r2)

    # Separation axes (perpendicular to rect edges)
    axes = [
        (cos1, sin1),
        (-sin1, cos1),
        (cos2, sin2),
        (-sin2, cos2)
    ]

    # Corners of rect 1
    cx1, cy1 = float(d1["x"]), float(d1["y"])
    hl1, hb1 = l1 / 2.0, b1 / 2.0
    corners1 = []
    for s1 in (-1.0, 1.0):
        for s2 in (-1.0, 1.0):
            corners1.append((
                cx1 + s1 * hl1 * cos1 - s2 * hb1 * sin1,
                cy1 + s1 * hl1 * sin1 + s2 * hb1 * cos1
            ))

    # Corners of rect 2
    cx2, cy2 = float(d2["x"]), float(d2["y"])
    hl2, hb2 = l2 / 2.0, b2 / 2.0
    corners2 = []
    for s1 in (-1.0, 1.0):
        for s2 in (-1.0, 1.0):
            corners2.append((
                cx2 + s1 * hl2 * cos2 - s2 * hb2 * sin2,
                cy2 + s1 * hl2 * sin2 + s2 * hb2 * cos2
            ))

    # Project corners onto each axis to find overlaps
    for ax, ay in axes:
        length = math.hypot(ax, ay)
        if length < 1e-6:
            continue
        ax, ay = ax / length, ay / length

        # Project corners1
        p1 = [c[0] * ax + c[1] * ay for c in corners1]
        min1, max1 = min(p1), max(p1)

        # Project corners2
        p2 = [c[0] * ax + c[1] * ay for c in corners2]
        min2, max2 = min(p2), max(p2)

        # Check for gap along the projected axis
        if max1 + clearance < min2 or max2 + clearance < min1:
            return False

    return True


def compute_scene_analysis(target: str, detections: list[dict]) -> str:
    """Deterministic spatial analysis using YOLO coordinates + catalogue dimensions.
    Distinguishes between:
      - TARGET elevated (sitting on something) → still accessible, safe to pick
      - BLOCKER elevated by target's height → likely on top of target, must relocate
    """
    lines = []
    # Find the target detection, excluding any that are already in the placement box
    target_det = next((d for d in detections if d["object_name"] == target and not is_inside_placement_box(d)), None)

    # 1. Analyze Elevation (Z-axis)
    lines.append("ELEVATION ANALYSIS:")
    elevation_found = False
    for d in detections:
        # Ignore any objects that are inside the placement box
        if is_inside_placement_box(d):
            continue

        known_h = OBJECT_CATALOGUE.get(d["object_name"], {}).get("height_m", None)
        if known_h is None:
            continue
        expected_z = known_h / 2
        excess = d["z"] - expected_z
        if excess <= 0.020:
            continue

        elevation_found = True
        implied_height = excess * 2
        MATCH_TOLERANCE = 0.015
        plausible = []
        for obj_name, obj_info in OBJECT_CATALOGUE.items():
            if obj_name == d["object_name"]:
                continue
            h = obj_info["height_m"]
            if abs(h - implied_height) <= MATCH_TOLERANCE:
                plausible.append(obj_name)

        if d["object_name"] == target:
            # TARGET has an anomalously high surface Z.
            lines.append(
                f"  - CAUTION: Target '{target}' has an anomalously high surface (Z={d['z']*1000:.0f}mm). "
                f"Look at the image carefully. If you see a smaller object (like a 'cube') resting on top of OR inside/blocking the {target}, you MUST output 'relocate' for that object."
            )
        else:
            # NON-TARGET is elevated — check if it might be sitting ON the target
            # Only flag as block if it is physically close/overlapping in XY to the target
            dist = 9999.0
            if target_det:
                dist = math.hypot(d["x"] - target_det["x"], d["y"] - target_det["y"])

            if dist < 0.080 and target in plausible:
                lines.append(
                    f"  - WARNING: {d['object_name']} is elevated (Z={d['z']*1000:.0f}mm) "
                    f"and is likely sitting ON TOP OF target '{target}'. "
                    f"MUST relocate {d['object_name']} first."
                )
            elif plausible:
                lines.append(
                    f"  - INFO: {d['object_name']} is elevated (Z={d['z']*1000:.0f}mm). "
                    f"Likely resting on: {', '.join(plausible)}."
                )
            else:
                lines.append(
                    f"  - Note: {d['object_name']} is elevated but no catalogue "
                    f"object matches the implied support height."
                )

    if not elevation_found:
        lines.append("  - All detected objects are flat on the table.")

    lines.append("")

    # 2. Analyze XY Overlap
    lines.append("OVERLAP ANALYSIS (XY):")
    overlap_found = False

    if target_det:
        target_info = OBJECT_CATALOGUE.get(target, {})
        target_radius = math.hypot(
            target_info.get("length_m", 0.04),
            target_info.get("breadth_m", 0.04),
        ) / 2

        for d in detections:
            if d["object_name"] == target or is_inside_placement_box(d):
                continue
            if check_overlap_obb(d, target_det, clearance=0.015):
                overlap_found = True
                lines.append(f"  - WARNING: {d['object_name']} overlaps with target {target}.")

    if not target_det:
        lines.append(f"  - Target '{target}' not currently detected by YOLO.")
    elif not overlap_found:
        lines.append(f"  - No objects physically overlap with '{target}'.")

    return "\n".join(lines)


async def qwen_plan_next_action(
    target: str,
    base64_image: str,
    detections: list[dict],
    action_history: list[str],
    user_context: str = "",
) -> dict:
    """
    Ask Qwen to decide ONE next action based on the current scene.
    Called in a loop — after each robot action the scene is re-read
    and Qwen is asked again.
    """
    scene_analysis = compute_scene_analysis(target, detections)
    catalogue_list = ", ".join(OBJECT_CATALOGUE.keys())
    
    history_summary = (
        "No actions taken yet."
        if not action_history
        else "Actions already taken:\n" + "\n".join(
            f"  {i+1}. {a}" for i, a in enumerate(action_history)
        )
    )
    
    user_context_section = ""
    if user_context:
        user_context_section = (
            f"USER INSTRUCTION CONTEXT:\n"
            f"  The user said: \"{user_context}\"\n"
            f"  Use this as a hint when the scene is ambiguous.\n\n"
        )

    prompt = (
        f"You are the visual safety gate for a robotic arm.\n\n"
        f"GOAL: Pick up the '{target}'\n\n"
        f"{user_context_section}"
        f"KNOWN OBJECT CATALOGUE: {catalogue_list}\n\n"
        f"PYTHON SENSOR ANALYSIS (Depth Elevation & XY Overlap):\n"
        f"{scene_analysis}\n\n"
        f"HISTORY:\n{history_summary}\n\n"
        f"INSTRUCTIONS:\n"
        f"  1. Look at the image AND read the Python sensor analysis.\n"
        f"  2. If the analysis shows a WARNING that another object is ON TOP of or overlapping with the target, output 'relocate' for that blocking object.\n"
        f"  3. If the analysis shows CAUTION that the target has an anomalously high surface, look for an object sitting ON TOP OF or INSIDE/BLOCKING it. If you see one (like a 'cube'), output 'relocate' for that object. If it is clear, output 'pick'.\n"
        f"  4. If the analysis says the target is clear, verify visually. If it looks clear and nothing is inside/blocking it, output 'pick'.\n"
        f"  5. If you see an unknown object (not in catalogue) blocking the target, output 'abort'.\n"
        f"  6. Do not re-relocate already moved objects (check history).\n\n"
        f"AVAILABLE ACTIONS:\n"
        f"  - relocate: move one blocking object to a safe spot.\n"
        f"  - pick: pick the target.\n"
        f"  - abort: cannot safely reach the target.\n\n"
        f"CRITICAL INSTRUCTION: You may think first, but your final output MUST be a valid JSON block enclosed in '```json' and '```' markers.\n"
        f"NO EXPLANATIONS AFTER THE JSON. ONLY OUTPUT JSON AS THE FINAL RESULT.\n\n"
        f"Pick format:\n"
        f'{{"next_action":"pick","obstacle_name":null,"reasoning":"target is visible and safe"}}\n\n'
        f"Relocate format:\n"
        f'{{"next_action":"relocate","obstacle_name":"[name_of_blocking_object]","reasoning":"[name] is blocking the target"}}\n\n'
        f"Abort format:\n"
        f'{{"next_action":"abort","obstacle_name":null,"reasoning":"target cannot be safely reached"}}'
    )
    raw = await ask_qwen_vision(prompt, base64_image)
    print("RAW QWEN PLAN:", repr(raw))
    try:
        plan = extract_qwen_json(raw)
    except Exception as e:
        logger.error(f"Failed to parse Qwen JSON: {e}")
        
        # Clean fallback message for the UI
        clean_reason = raw[:100] if "Ollama API Error" in raw else "JSON Parse Error"
        
        # Default to PICK not abort — the Python sensor analysis (overlap +
        # elevation) has already gated this point. If sensors said the target
        # was blocked, the relocate would have been forced earlier. Aborting
        # because the LLM rambled is the wrong failure mode.
        plan = {
            "next_action": "pick",
            "obstacle_name": None,
            "reasoning": f"Qwen unparseable ({clean_reason}) — sensor analysis clear, proceeding with direct pick."
        }
        
    print("PARSED QWEN ACTION:", plan)
    plan["raw_output"]=raw
    
    return plan

# ==========================================
# ROBOT MCP COMMUNICATION
# ==========================================
async def call_robot_tool(tool_name: str, arguments: dict) -> dict:
    """Send a tool call to the Robot MCP server."""
    payload = {
        "jsonrpc": "2.0",
        "method":  "tools/call",
        "params":  {"name": tool_name, "arguments": arguments},
        "id":      1,
    }
    req = urllib.request.Request(
        ROBOT_MCP_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    loop = asyncio.get_running_loop()
    def fetch():
        with urllib.request.urlopen(req, timeout=900) as response:
            return json.loads(response.read().decode("utf-8"))
    try:
        result = await loop.run_in_executor(None, fetch)
        logger.info(f"Robot MCP [{tool_name}]: {str(result)[:120]}")
        return result
    except Exception as e:
        logger.error(f"Robot MCP call failed [{tool_name}]: {e}")
        return {"error": str(e)}


# ==========================================
# MCP TOOLS
# ==========================================
@server.list_tools()
async def handle_list_tools() -> list[Tool]:
    return [
        Tool(
            name="locate_object",
            description="Uses the robotic vision camera to identify an object and get its coordinates. Implements Qwen safety gate.",
            inputSchema={
                "type": "object",
                "properties": {
                    "target_name": {
                        "type": "string",
                        "description": "Name of the object to locate.",
                        "enum": list(OBJECT_CATALOGUE.keys())
                    },
                    "user_context": {
                        "type": "string",
                        "description": "Optional context about the object's visual properties (e.g., 'milk sticker'). If provided, the vision server will cross-reference cropped images with the VLM to pick the precise object matching this context."
                    }
                },
                "required": ["target_name"]
            }
        ),

        Tool(
            name="analyze_scene_semantics",
            description="Crops all currently detected objects from the YOLO vision model and passes them to the local VLM to generate a semantic description mapping. Returns a JSON list of objects with visual descriptions.",
            inputSchema={"type": "object", "properties": {}},
        ),

        Tool(
            name="capture_and_detect",
            description=(
                "Run a fresh YOLO detection pass on the current camera frame and "
                "return all detected objects with robot-frame coordinates, angles, "
                "and Z heights. Pipe returns grasp endpoint coordinates."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),

        Tool(
            name="analyse_surroundings",
            description=(
                "Capture the current camera frame and send it to Qwen-VL with a "
                "custom prompt for a free-text description of the workspace. "
                "Use for open-ended scene questions."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "prompt": {
                        "type":        "string",
                        "description": "Custom analysis instruction for Qwen.",
                    }
                },
            },
        ),

        Tool(
            name="get_camera_snapshot",
            description=(
                "Return the current camera frame as a base64 image. "
                "Optionally provide a question for Qwen-VL to answer about the image."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "question": {
                        "type":        "string",
                        "description": "Optional question for Qwen-VL about the image.",
                    }
                },
            },
        ),
    ]


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict | None) -> list[TextContent]:
    args = arguments or {}

    # ── capture_and_detect ────────────────────────────────────────────────────
    if name == "capture_and_detect":
        detections = get_current_detections()
        frame_b64  = get_frame_as_base64()
        return [TextContent(type="text", text=json.dumps({
            "status":     "ok",
            "detections": detections,
            "has_frame":  frame_b64 is not None,
        }))]

    # ── analyze_scene_semantics ───────────────────────────────────────────────
    if name == "analyze_scene_semantics":
        try:
            crops = camera.get_object_crops()
            if not crops:
                return [TextContent(type="text", text=json.dumps({"status": "failed", "message": "No objects currently detected to analyze."}))]
            
            results = []
            for crop in crops:
                prompt = f"Briefly describe this {crop['class']}, specifically noting any visible stickers, brands, or distinct visual markings. Keep it under 5 words."
                description = await ask_qwen_vision(prompt, crop['crop_b64'])
                results.append({
                    "id": crop['id'],
                    "class": crop['class'],
                    "description": description.strip()
                })
                
            return [TextContent(type="text", text=json.dumps({
                "status": "success",
                "semantics": results
            }))]
        except Exception as e:
            logger.error(f"Semantic analysis failed: {e}")
            return [TextContent(type="text", text=json.dumps({"status": "error", "message": str(e)}))]

    # ── analyse_surroundings ──────────────────────────────────────────────────
    if name == "analyse_surroundings":
        prompt    = args.get("prompt") or (
            "Describe all objects in the workspace, their positions relative to each "
            "other, and whether any appear stacked or blocking others."
        )
        frame_b64 = get_frame_as_base64()
        if frame_b64 is None:
            return [TextContent(type="text", text="Error: Camera frame not ready.")]
        result = await ask_qwen_vision(prompt, frame_b64)
        return [TextContent(type="text", text=result)]

    # ── get_camera_snapshot ───────────────────────────────────────────────────
    if name == "get_camera_snapshot":
        question  = args.get("question")
        frame_b64 = get_frame_as_base64()
        if frame_b64 is None:
            return [TextContent(type="text", text="Error: Camera frame not ready.")]
        if question:
            result = await ask_qwen_vision(question, frame_b64)
            return [TextContent(type="text", text=result)]
        return [TextContent(type="text", text=frame_b64)]

    # ── locate_object ─────────────────────────────────────────────────────────
        # ── locate_object ─────────────────────────────────────────────────────────
    if name == "locate_object":
        target = (args.get("target_name") or "").strip().lower()
        user_context = args.get("user_context", "").strip()

        if target not in OBJECT_CATALOGUE:
            return [TextContent(type="text", text=json.dumps({
                "status": "REJECTED",
                "message": f"'{target}' not in catalogue.",
            }))]

        action_history = []
        iteration = 0

        while iteration < MAX_PLANNING_ITERATIONS:
            iteration += 1
            camera.current_target_class = target
            await asyncio.sleep(1.0)

            detections = get_current_detections()
            frame_b64 = get_frame_as_base64()

            if not detections:
                return [TextContent(type="text", text=json.dumps({
                    "status": "FAILED",
                    "message": "No objects detected.",
                    "history": action_history,
                }))]

            target_detections = [
                d for d in detections
                if d.get("object_name") == target and not is_inside_placement_box(d)
            ]

            if target_detections and user_context:
                logger.info(f"User context provided: '{user_context}'. Cross-referencing VLM...")
                crops = camera.get_object_crops()
                target_crops = [c for c in crops if c["class"] == target]
                
                if target_crops:
                    # Ask VLM to pick the best match
                    prompt = f"The user requested an object matching this context: '{user_context}'. Based on the visual appearance of these {target}s, which ID best matches the request? Reply ONLY with the exact ID string (e.g. {target_crops[0]['id']}). Do not include any other words."
                    
                    # For simplicity, if multiple crops exist we could query each individually or stitch them. 
                    # Qwen-VL handles multiple images. We pass them all.
                    b64_images = [c["crop_b64"] for c in target_crops]
                    
                    try:
                        # Construct a multi-image payload for Qwen-VL via local Ollama
                        req_payload = {
                            "model": QWEN_MODEL,
                            "prompt": prompt,
                            "stream": False,
                            "images": b64_images,
                            "options": {"temperature": 0.1, "num_predict": 100}
                        }
                        url = f"http://{OLLAMA_IP}:11434/api/generate"
                        req = urllib.request.Request(url, data=json.dumps(req_payload).encode("utf-8"), headers={"Content-Type": "application/json"})
                        with urllib.request.urlopen(req, timeout=15) as response:
                            ans = json.loads(response.read().decode("utf-8"))
                            vlm_choice = ans.get("response", "").strip()
                            logger.info(f"VLM selected crop ID: {vlm_choice}")
                            
                            # Filter target detections down to just the VLM's choice
                            filtered_dets = [d for d in target_detections if d.get("id") == vlm_choice]
                            if filtered_dets:
                                target_detections = filtered_dets
                    except Exception as e:
                        logger.error(f"VLM cross-reference failed: {e}")

            if not target_detections:
                # ── Hidden-target inference ──────────────────────────────
                # Target not visible. Check if any detected object is
                # elevated by approximately the target's height — if so,
                # the target is likely hidden underneath that object.
                target_height = OBJECT_CATALOGUE.get(target, {}).get("height_m", None)
                inferred_blocker = None
                if target_height:
                    for d in detections:
                        if is_inside_placement_box(d):
                            continue
                        known_h = OBJECT_CATALOGUE.get(d["object_name"], {}).get("height_m", None)
                        if known_h is None:
                            continue
                        expected_z = known_h / 2
                        excess = d["z"] - expected_z
                        implied_support = excess * 2
                        if excess > 0.020 and abs(implied_support - target_height) <= 0.015:
                            inferred_blocker = d
                            break

                if inferred_blocker is None:
                    return [TextContent(type="text", text=json.dumps({
                        "status": "FAILED",
                        "message": f"'{target}' not found.",
                        "detected_objects": [d.get("object_name") for d in detections if not is_inside_placement_box(d)],
                        "history": action_history,
                    }))]

                # Target is hidden — skip Qwen, go straight to relocate
                blocker_name = inferred_blocker["object_name"]
                logger.info(
                    f"Target '{target}' not visible but '{blocker_name}' is elevated "
                    f"by ~{target_height*1000:.0f}mm — inferring target is hidden underneath."
                )
                plan = {
                    "next_action": "relocate",
                    "obstacle_name": blocker_name,
                    "reasoning": (
                        f"Target '{target}' not visible. '{blocker_name}' is elevated, "
                        f"matching target height. Target likely hidden underneath."
                    ),
                    "raw_output": "Python inference — target not visible, Qwen not consulted",
                }
            else:
                # ── Deterministic obstacle detection ─────────────────────
                target_info = OBJECT_CATALOGUE.get(target, {})
                expected_h = target_info.get("height_m")
                sensor_obstacle = None
                sensor_reasoning = ""

                # Check 1: Z-axis elevation mismatch (Stacked target)
                if expected_h is not None and target_detections:
                    expected_z = expected_h / 2
                    excess = target_detections[0]["z"] - expected_z
                    if excess > 0.020:
                        # Find overlapping or closest detected obstacle to target
                        target_x = target_detections[0]["x"]
                        target_y = target_detections[0]["y"]
                        best_obstacle = None
                        min_dist = 9999.0
                        for d in detections:
                            if d is target_detections[0] or is_inside_placement_box(d):
                                continue
                            dx = d["x"] - target_x
                            dy = d["y"] - target_y
                            dist = (dx*dx + dy*dy)**0.5
                            if dist < 0.080 and dist < min_dist:
                                min_dist = dist
                                best_obstacle = d.get("object_name")
                        sensor_obstacle = best_obstacle if best_obstacle else "cube"
                        sensor_reasoning = f"Depth sensor override: Target '{target}' surface is {excess*1000:.0f}mm higher than expected, indicating overlapping object '{sensor_obstacle}' is on top."

                # Check 2: XY overlap
                if not sensor_obstacle and target_detections:
                    target_det = target_detections[0]
                    target_radius = math.hypot(
                        target_info.get("length_m", 0.04),
                        target_info.get("breadth_m", 0.04),
                    ) / 2

                    overlapping_obstacle = None
                    min_overlap_dist = 9999.0

                    for d in detections:
                        if d["object_name"] == target or is_inside_placement_box(d):
                            continue
                        if check_overlap_obb(d, target_det, clearance=0.015):
                            dist = math.hypot(d["x"] - target_det["x"], d["y"] - target_det["y"])
                            if dist < min_overlap_dist:
                                min_overlap_dist = dist
                                overlapping_obstacle = d["object_name"]

                    if overlapping_obstacle:
                        sensor_obstacle = overlapping_obstacle
                        sensor_reasoning = f"XY overlap override: Target '{target}' is physically overlapping with '{overlapping_obstacle}' (distance {min_overlap_dist*1000:.1f}mm), requiring relocation."

                if sensor_obstacle:
                    logger.warning(f"SENSOR OVERRIDE: Bypassing Qwen due to deterministic block '{sensor_obstacle}'.")
                    plan = {
                        "next_action": "relocate",
                        "obstacle_name": sensor_obstacle,
                        "reasoning": sensor_reasoning,
                        "raw_output": "Sensor detection override — bypassed Qwen",
                    }
                else:
                    # ── Normal flow: Qwen safety gate ────────────────────────
                    print("BEFORE QWEN")
                    plan = await qwen_plan_next_action(
                        target,
                        frame_b64,
                        detections,
                        action_history,
                        user_context,
                    ) if frame_b64 else {
                        "next_action": "abort",
                        "obstacle_name": None,
                        "reasoning": "No camera frame — aborting for safety.",
                        "raw_output": "No camera frame available, did not run qwen",
                    }
                print("AFTER QWEN/SENSOR PLAN:", plan)

                if plan.get("next_action") == "pick":
                    # Failsafe: If Qwen hallucinated that the path is clear, but depth sensor knows there is an anomaly.
                    target_info = OBJECT_CATALOGUE.get(target, {})
                    expected_h = target_info.get("height_m")
                    if expected_h is not None and target_detections:
                        expected_z = expected_h / 2
                        excess = target_detections[0]["z"] - expected_z
                        if excess > 0.020:
                            # Find overlapping or closest detected obstacle to target
                            target_x = target_detections[0]["x"]
                            target_y = target_detections[0]["y"]
                            best_obstacle = None
                            min_dist = 9999.0
                            for d in detections:
                                if d is target_detections[0] or is_inside_placement_box(d):
                                    continue
                                dx = d["x"] - target_x
                                dy = d["y"] - target_y
                                dist = (dx*dx + dy*dy)**0.5
                                if dist < 0.080 and dist < min_dist:
                                    min_dist = dist
                                    best_obstacle = d.get("object_name")
                            obstacle_name = best_obstacle if best_obstacle else "cube"

                            logger.warning(f"QWEN FAILSAFE OVERRIDE: Target '{target}' Z is anomalously high (+{excess*1000:.0f}mm). Forcing relocate of '{obstacle_name}'.")
                            plan = {
                                "next_action": "relocate",
                                "obstacle_name": obstacle_name,
                                "reasoning": f"Depth sensor failsafe: Target '{target}' surface is {excess*1000:.0f}mm higher than expected, indicating an undetected or overlapping object '{obstacle_name}' is inside or on top of it.",
                                "raw_output": plan.get("raw_output", "") + f"\n\n(OVERRIDDEN BY DEPTH SENSOR FAILSAFE: Relocating {obstacle_name})"
                            }

                    # XY Overlap Failsafe: If Qwen hallucinated that it is clear, but YOLO detects an overlap
                    if plan.get("next_action") == "pick" and target_detections:
                        target_det = target_detections[0]
                        target_info = OBJECT_CATALOGUE.get(target, {})
                        target_radius = math.hypot(
                            target_info.get("length_m", 0.04),
                            target_info.get("breadth_m", 0.04),
                        ) / 2

                        overlapping_obstacle = None
                        min_overlap_dist = 9999.0

                        for d in detections:
                            if d["object_name"] == target or is_inside_placement_box(d):
                                continue
                            if check_overlap_obb(d, target_det, clearance=0.015):
                                dist = math.hypot(d["x"] - target_det["x"], d["y"] - target_det["y"])
                                # Overlaps! Find the closest overlapping object
                                if dist < min_overlap_dist:
                                    min_overlap_dist = dist
                                    overlapping_obstacle = d["object_name"]

                        if overlapping_obstacle:
                            logger.warning(f"QWEN FAILSAFE OVERRIDE: Overlapping object '{overlapping_obstacle}' detected in XY. Forcing relocate.")
                            plan = {
                                "next_action": "relocate",
                                "obstacle_name": overlapping_obstacle,
                                "reasoning": f"XY overlap failsafe: Target '{target}' is physically overlapping with '{overlapping_obstacle}' (distance {min_overlap_dist*1000:.1f}mm), requiring relocation.",
                                "raw_output": plan.get("raw_output", "") + f"\n\n(OVERRIDDEN BY XY OVERLAP FAILSAFE: Relocating {overlapping_obstacle})"
                            }


            next_action = plan.get("next_action", "abort")
            obstacle_name = (plan.get("obstacle_name") or "").strip().lower()
            reasoning = plan.get("reasoning", "")
            raw_output=plan.get("raw_output","")

            if next_action == "abort":
                return [TextContent(type="text", text=json.dumps({
                    "status": "ABORTED",
                    "reasoning": reasoning,
                    "history": action_history,
                    "qwen_raw_output": raw_output,
                }))]

            if next_action == "relocate":
                # ── Repeat-relocate guard ────────────────────────────────
                # If this obstacle was already relocated this cycle, Qwen is
                # likely hallucinating or ignoring history. Force pick instead
                # of relocating the same object forever until iteration cap.
                already_relocated = any(
                    f"Relocated '{obstacle_name}'" in entry
                    for entry in action_history
                )
                if already_relocated:
                    logger.warning(
                        f"Qwen asked to relocate '{obstacle_name}' again but it was "
                        f"already relocated this cycle. Forcing pick instead."
                    )
                    next_action = "pick"

            if next_action == "relocate":
                camera.current_target_class = obstacle_name
                obstacle_dets = [
                    d for d in detections
                    if d.get("object_name") == obstacle_name
                ]

                if obstacle_name and obstacle_dets:
                    obs = obstacle_dets[0]
                elif obstacle_name and target_detections:
                    # Qwen visually saw a blocker that YOLO missed.
                    # DANGEROUS fallback: using the target's own coordinates means
                    # the robot may physically pick up the TARGET, not the blocker.
                    # Only allow this ONCE per cycle, and only when the sensor
                    # analysis also flagged elevation on the target (i.e. there is
                    # physical evidence something is stacked there).
                    fallback_used = any("coordinate-fallback" in e for e in action_history)
                    target_known_h = OBJECT_CATALOGUE.get(target, {}).get("height_m", None)
                    target_elevated = False
                    if target_known_h is not None:
                        t = target_detections[0]
                        target_elevated = (t["z"] - target_known_h / 2) > 0.020

                    if fallback_used or not target_elevated:
                        logger.warning(
                            f"Qwen requested relocate '{obstacle_name}' (not in YOLO detections) "
                            f"but no elevation evidence on target — refusing coordinate fallback, "
                            f"attempting direct pick instead."
                        )
                        next_action = "pick"
                        obs = None
                    else:
                        logger.warning(
                            f"Qwen requested relocate '{obstacle_name}' not in YOLO detections. "
                            f"Target IS elevated — using target coordinates as blocker position (one-time)."
                        )
                        obs = target_detections[0].copy()
                        obs["object_name"] = obstacle_name
                        detections.append(obs)
                        action_history.append(
                            f"(coordinate-fallback used for '{obstacle_name}')"
                        )
                else:
                    # Qwen said relocate but obstacle not found and we can't fall back — abort
                    logger.warning(f"Qwen requested relocate '{obstacle_name}' but it was not found in YOLO detections.")
                    return [TextContent(type="text", text=json.dumps({
                        "status": "ABORTED",
                        "reasoning": f"Cannot relocate '{obstacle_name}' — not found in current detections.",
                        "history": action_history,
                        "qwen_raw_output": raw_output,
                    }))]

            if next_action == "relocate" and obs is not None:
                reloc_result = await call_robot_tool("relocate_object", {
                    "obstacle_name": obs["object_name"],
                    "obstacle_x": obs["x"],
                    "obstacle_y": obs["y"],
                    "obstacle_z": obs["z"],
                    "obstacle_angle_deg": obs.get("angle_deg"),
                    "detections": detections,
                    "target_name": target,
                })

                if reloc_result.get("error"):
                    return [TextContent(type="text", text=json.dumps({
                        "status": "ERROR",
                        "message": reloc_result["error"],
                        "history": action_history,
                        "output": raw_output,
                    }))]

                action_history.append(f"Relocated '{obstacle_name}' — {reasoning}")
                await asyncio.sleep(1.0)
                continue

            if next_action == "pick":
                target_det = target_detections[0]

                if target_det["object_name"] == "pipe" and target_det.get("grasp_label"):
                    grasp_label = target_det["grasp_label"]
                else:
                    grasp_label = target_det.get("grasp_label")

                action_history.append(f"Vision located '{target}' — {reasoning}")

                print("RETURNING COORDINATES:", target_det)

                return [TextContent(
                    type="text",
                    text=json.dumps({
                        "status": "SUCCESS",
                        "target": target,
                        "coordinates": {
                            "x": target_det["x"],
                            "y": target_det["y"],
                            "z": target_det["z"],
                            "angle_deg": target_det.get("angle_deg"),
                            "grasp_label": grasp_label,
                        },
                        "detections": detections,
                        "history": action_history,
                        "qwen_raw_output": raw_output,
                        "iterations": iteration,
                        "qwen_reasoning": reasoning,
                    })
                )]

        return [TextContent(type="text", text=json.dumps({
            "status": "FAILED",
            "message": "Maximum planning iterations reached.",
            "history": action_history,
        }))]

    raise ValueError(f"Unknown tool: {name}")


# ==========================================
# SSE NETWORKING
# ==========================================
sse = SseServerTransport("/messages")

async def sse_app(scope, receive, send):
    async with sse.connect_sse(scope, receive, send) as streams:
        await server.run(
            streams[0], streams[1], server.create_initialization_options()
        )

async def handle_direct_rpc(scope, receive, send):
    """Handle direct JSON-RPC tool calls without MCP SSE session (peer-to-peer)."""
    body = b""
    while True:
        msg = await receive()
        body += msg.get("body", b"")
        if not msg.get("more_body", False):
            break
    try:
        request = json.loads(body.decode("utf-8"))
        tool_name = request.get("params", {}).get("name", "")
        arguments = request.get("params", {}).get("arguments", {})
        result = await handle_call_tool(tool_name, arguments)
        response = json.dumps({
            "jsonrpc": "2.0",
            "result": {"content": [{"type": r.type, "text": r.text} for r in result]},
            "id": request.get("id", 1)
        }).encode("utf-8")
        status = 200
    except Exception as e:
        response = json.dumps({
            "jsonrpc": "2.0",
            "error": {"code": -32603, "message": str(e)},
            "id": 1
        }).encode("utf-8")
        status = 500
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [(b"content-type", b"application/json")]
    })
    await send({
        "type": "http.response.body",
        "body": response
    })

# Raw ASGI Routing App
async def app(scope, receive, send):
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                break
        return

    if scope["type"] == "http":
        path = scope.get("path", "")
        method = scope.get("method", "")
        if path == "/sse" and method == "GET":
            await sse_app(scope, receive, send)
            return
        elif path == "/messages" and method == "POST":
            query = scope.get("query_string", b"").decode()
            if "session_id" in query:
                await sse.handle_post_message(scope, receive, send)
            else:
                await handle_direct_rpc(scope, receive, send)
            return

    # Fallback for other paths / methods
    await send({
        "type": "http.response.start",
        "status": 404,
        "headers": [(b"content-type", b"text/plain")]
    })
    await send({
        "type": "http.response.body",
        "body": b"Not Found"
    })

if __name__ == "__main__":
    # Start camera.py's vision loop in a background thread of this process
    logger.info("📷 Starting camera vision loop background thread...")
    camera_thread = threading.Thread(target=camera.vision_loop, daemon=True)
    camera_thread.start()
    
    # Warm up camera
    time.sleep(2)

    logger.info("📷 Vision MCP Server listening on port 8001...")
    logger.info("Tools: locate_and_pick_object | capture_and_detect | analyse_surroundings | get_camera_snapshot")
    uvicorn.run(app, host="0.0.0.0", port=8001)
