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

# Import camera.py — hardware layer.
# Provides: current_rgb_frame, get_camera_snapshot(), vision_loop()
import camera

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
    return "127.0.0.1", "qwen2.5-vl:7b"

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
    "blue cube":    {"size": "30 x 30 x 30 mm", "height_m": 0.03,
                     "length_m": 0.03,   "breadth_m": 0.03},
    "red cube":     {"size": "30 x 30 x 30 mm",         "height_m": 0.030,
                     "length_m": 0.030,   "breadth_m": 0.030},
    "green cube":   {"size": "30 x 30 x 30 mm",  "height_m": 0.03,
                     "length_m": 0.03,   "breadth_m": 0.03},
    "medicine":     {"size": "112 x 28 x 23 mm","height_m": 0.023,
                     "length_m": 0.112, "breadth_m": 0.028},
    "nut":          {"size": "34.6 x 30 x 17 mm",        "height_m": 0.017,
                     "length_m": 0.0346,  "breadth_m": 0.030},
    "yellow cube":  {"size": "25 x 25 x 25 mm", "height_m": 0.025,
                     "length_m": 0.025,   "breadth_m": 0.025,},
    "sponge":       {"size": "75 x 18 x 30 mm",    "height_m": 0.018,
                     "length_m": 0.071, "breadth_m": 0.03,
                     "notes": "Angled grasp configuration"},
    "screwdriver":  {"size": "104 x 25 x 25 mm",    "height_m": 0.025,
                     "length_m": 0.104, "breadth_m": 0.025,
                     "notes": "Angled grasp configuration"},
    "cube":         {"size": "30 x 30 x 30 mm", "height_m": 0.03,
                     "length_m": 0.03,   "breadth_m": 0.03},
    "soy milk":     {"size": "30 x 30 x 30 mm", "height_m": 0.03,
                     "length_m": 0.03,   "breadth_m": 0.03},
    "umbrella":     {"size": "30 x 30 x 30 mm", "height_m": 0.03,
                     "length_m": 0.03,   "breadth_m": 0.03},
    "wrench":       {"size": "30 x 30 x 30 mm", "height_m": 0.03,
                     "length_m": 0.03,   "breadth_m": 0.03},
    "hat":          {"size": "30 x 30 x 30 mm", "height_m": 0.03,
                     "length_m": 0.03,   "breadth_m": 0.03},
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
        for obb in result.obb:
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
                "object_name": cls_name,
                "x":           coords["x"],
                "y":           coords["y"],
                "z":           coords["z"],
                "angle_deg":   coords["angle_deg"],
                "confidence":  round(conf, 3),
                "cx_px":       cx_px,
                "cy_px":       cy_px,
                "w_px":        float(obb.xywhr[0][2]),
                "h_px":        float(obb.xywhr[0][3]),
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

def crop_target_snapshot(target_dets: list[dict], zoom_factor: float = 2.5) -> str | None:
    """
    Create a zoomed-in snapshot centred on the target candidates.
    Uses the stored pixel coordinates from YOLO detections to crop
    the current RGB frame around the targets, then encodes as base64 JPEG.
    Does NOT touch the live camera feed - works on a copy.
    Returns base64 JPEG string (no data URL prefix), or None on fail
    """
    import base64
    frame = camera.current_rgb_frame
    if frame is None:
        return None
    frame = frame.copy()
    img_h, img_w = frame.shape[:2]

    # Filter targets that have pixel coordinates
    targets_with_px = [d for d in target_dets if "cx_px" in d and "cy_px" in d]
    if not targets_with_px:
        return None

    # Compute bounding box that encloses all target candidates
    min_x = img_w
    min_y = img_h
    max_x = 0
    max_y = 0
    for d in targets_with_px:
        cx = d["cx_px"]
        cy = d["cy_px"]
        w = d.get("w_px", 60)
        h = d.get("h_px", 60)
        half_diag = math.hypot(w, h) / 2  # accounts for rotation
        min_x = min(min_x, cx - half_diag)
        min_y = min(min_y, cy - half_diag)
        max_x = max(max_x, cx + half_diag)
        max_y = max(max_y, cy + half_diag)

    # Add generous padding around the bounding box for context
    pad_x = (max_x - min_x) * (zoom_factor - 1) / 2
    pad_y = (max_y - min_y) * (zoom_factor - 1) / 2
    # Ensure minimum crop size of 200px in each dimension
    crop_w = max(max_x - min_x + 2 * pad_x, 200)
    crop_h = max(max_y - min_y + 2 * pad_y, 200)
    center_x = (min_x + max_x) / 2
    center_y = (min_y + max_y) / 2

    x1 = int(max(0, center_x - crop_w / 2))
    y1 = int(max(0, center_y - crop_h / 2))
    x2 = int(min(img_w, center_x + crop_w / 2))
    y2 = int(min(img_h, center_y + crop_h / 2))

    if x2 - x1 < 10 or y2 - y1 < 10:
        return None

    cropped = frame[y1:y2, x1:x2]

    # Upscale the crop to a reasonable display size (at least 480px wide)
    scale = max(1.0, 900.0 / cropped.shape[1])
    if scale > 1.0:
        cropped = cv2.resize(cropped, None, fx=scale, fy=scale, interpolation=cv2.INTER_LANCZOS4)

    _, buffer = cv2.imencode('.jpg', cropped, [cv2.IMWRITE_JPEG_QUALITY, 95])
    b64_str = base64.b64encode(buffer).decode('utf-8')
    logger.info(f"Cropped snapshot: {x2-x1}x{y2-y1}px region, {len(b64_str)} b64 chars")
    return b64_str

def crop_single_target(det: dict, out_size: int = 768, pad_ratio: float = 0.6) -> str | None:
    """Crop ONE detection to its own image, upscaled large. For sticker reading."""
    import base64
    frame = camera.current_rgb_frame
    if frame is None or "cx_px" not in det:
        return None
    frame = frame.copy()
    img_h, img_w = frame.shape[:2]

    cx, cy = det["cx_px"], det["cy_px"]
    w = det.get("w_px", 60)
    h = det.get("h_px", 60)
    half = math.hypot(w, h) / 2
    pad = half * pad_ratio

    x1 = int(max(0, cx - half - pad))
    y1 = int(max(0, cy - half - pad))
    x2 = int(min(img_w, cx + half + pad))
    y2 = int(min(img_h, cy + half + pad))
    if x2 - x1 < 10 or y2 - y1 < 10:
        return None

    crop = frame[y1:y2, x1:x2]
    scale = max(1.0, out_size / crop.shape[1])
    crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_LANCZOS4)
    _, buf = cv2.imencode('.jpg', crop, [cv2.IMWRITE_JPEG_QUALITY, 95])
    return base64.b64encode(buf).decode('utf-8')
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

    # Disable Qwen3 thinking mode: use system message + top-level flag + higher token budget.
    # The model was burning all tokens on internal <think> reasoning and never producing JSON output.
    prompt_with_directive = prompt
    
    payload = {
        "model": QWEN_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "/no_think\nYou are a robotic vision assistant. Respond ONLY with a JSON object. Do NOT use <think> tags. Do NOT reason internally. Output the JSON immediately."
            },
            {
                "role": "user",
                "content": prompt,
                "images": [raw_b64]
            }
        ],
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 8192},
        "think": False
    }

    print("IMAGE SIZE:", len(raw_b64))

    url = f"http://{OLLAMA_IP}:11434/api/chat"
    
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

        # /api/chat returns text in message.content (NOT "response")
        message = result.get("message", {})
        response_text = message.get("content", "")

        # Empty content means the model burned its whole token budget thinking
        # (done_reason 'length') and never wrote an answer. Do NOT parse the
        # thinking ramble — it contains no JSON and will fail. Surface honestly.
        if not response_text.strip():
            dr = result.get("done_reason", "")
            logger.error(f"Qwen returned empty content (done_reason={dr}). No answer produced.")
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


def is_target_match(target: str, object_name: str) -> bool:
    """Helper to match logical target names against YOLO object_names."""
    if target in ["soy milk", "soymilk", "umbrella", "wrench", "hat"]:
        return "cube" in object_name
    return target in object_name

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
    target_det = next((d for d in detections if is_target_match(target, d.get("object_name", "")) and not is_inside_placement_box(d)), None)

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
        top_surface_z = d["z"] - Z_OFFSET_M
        excess = top_surface_z - known_h
        if excess <= 0.020:
            continue

        elevation_found = True
        implied_support_height = excess
        MATCH_TOLERANCE = 0.015
        plausible = []
        for obj_name, obj_info in OBJECT_CATALOGUE.items():
            if obj_name == d["object_name"]:
                continue
            h = obj_info["height_m"]
            if abs(h - implied_support_height) <= MATCH_TOLERANCE:
                plausible.append(obj_name)

        if is_target_match(target, d.get("object_name", "")):
            # TARGET is elevated — it's sitting ON something else.
            # Check if any nearby object has HIGHER Z (i.e., something is on top of the target).
            blocker_on_top = None
            if target_det:
                for other in detections:
                    if is_target_match(target, other.get("object_name", "")) or is_inside_placement_box(other):
                        continue
                    odist = math.hypot(other["x"] - target_det["x"], other["y"] - target_det["y"])
                    if odist < 0.080 and other["z"] > d["z"]:
                        blocker_on_top = other["object_name"]
                        break
            if blocker_on_top:
                lines.append(
                    f"  - WARNING: Target '{target}' is elevated (Z={d['z']*1000:.0f}mm) "
                    f"AND '{blocker_on_top}' has even higher Z, likely ON TOP of the target. "
                    f"MUST relocate {blocker_on_top} first."
                )
            else:
                lines.append(
                    f"  - INFO: Target '{target}' is elevated (Z={d['z']*1000:.0f}mm) "
                    f"but has the highest Z among nearby objects. Target is ON TOP and accessible. "
                    f"Safe to pick directly."
                )
        else:
            # NON-TARGET is elevated — check if it might be sitting ON the target
            # Only flag as block if it is physically close/overlapping in XY to the target
            # AND the non-target has HIGHER Z than the target (it's on top)
            dist = 9999.0
            target_z = target_det["z"] if target_det else 0.0
            if target_det:
                dist = math.hypot(d["x"] - target_det["x"], d["y"] - target_det["y"])

            if dist < 0.080 and target in plausible and d["z"] > target_z:
                lines.append(
                    f"  - WARNING: {d['object_name']} is elevated (Z={d['z']*1000:.0f}mm, target Z={target_z*1000:.0f}mm) "
                    f"and is likely sitting ON TOP OF target '{target}'. "
                    f"MUST relocate {d['object_name']} first."
                )
            elif dist < 0.080 and target in plausible and d["z"] <= target_z:
                lines.append(
                    f"  - INFO: {d['object_name']} is elevated (Z={d['z']*1000:.0f}mm) "
                    f"but target '{target}' has higher Z ({target_z*1000:.0f}mm). "
                    f"Target is ON TOP and accessible. Safe to pick directly."
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
            if is_target_match(target, d.get("object_name", "")) or is_inside_placement_box(d):
                continue
            if check_overlap_obb(d, target_det, clearance=0.015):
                overlap_found = True
                if d["z"] > target_det["z"]:
                    lines.append(
                        f"  - WARNING: {d['object_name']} overlaps with target {target} "
                        f"AND has higher Z ({d['z']*1000:.0f}mm vs {target_det['z']*1000:.0f}mm). "
                        f"Likely ON TOP — MUST relocate {d['object_name']} first."
                    )
                else:
                    lines.append(
                        f"  - INFO: {d['object_name']} overlaps with target {target} in XY "
                        f"but target has higher Z ({target_det['z']*1000:.0f}mm vs {d['z']*1000:.0f}mm). "
                        f"Target is ON TOP and accessible. Safe to pick."
                    )

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
    # 1. Resolve sticker -> colour mapping and find the base target name
    STICKER_MAPPINGS = {
        "umbrella": "yellow",
        "wrench": "blue",
        "soy milk": "green",
        "soymilk": "green",
        "hat": "red"
    }

    target_base = target.lower()
    for sticker in STICKER_MAPPINGS.keys():
        if sticker in target_base or sticker in user_context.lower():
            target_base = "cube"
            break
    if "cube" in target_base:
        target_base = "cube"

    target_dets = [
        d for d in detections
        if is_target_match(target_base, d.get("object_name", "").lower())
    ]

    # 2. Map implied colours to disambiguate targets
    COLOUR_WORDS = ["blue", "red", "green", "yellow", "black", "white", "orange", "purple"]
    request_text = f"{target} {user_context}".lower()
    mentioned_colours = [c for c in COLOUR_WORDS if c in request_text]
    
    for sticker, mapped_color in STICKER_MAPPINGS.items():
        if sticker in request_text and mapped_color not in mentioned_colours:
            mentioned_colours.append(mapped_color)

    resolved_target = target_base
    colour_matches = []
    if mentioned_colours:
        colour_matches = [
            d for d in target_dets
            if any(c in d.get("object_name", "").lower() for c in mentioned_colours)
        ]
        if len(colour_matches) == 1:
            resolved_target = colour_matches[0].get("object_name", resolved_target)

    # 3. Compute scene analysis using the RESOLVED target name (so physical overlaps work for stickers)
    scene_analysis = compute_scene_analysis(resolved_target, detections)

    # 4. We found exactly one colour match, but Qwen skip logic has been disabled per user request.
    # Qwen will now always process the scene.
    if len(colour_matches) == 1:
        logger.info(f"Python colour-match identified '{resolved_target}'. Passing to Qwen for full visual verification.")
        
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
            f"  Use this as a hint when the scene is ambiguous or when you need to identify a specific sticker/icon.\n\n"
        )
    
    target_list_str = ""

    if len(target_dets) > 1:
        target_list_str = "MULTIPLE TARGET CANDIDATES DETECTED:\n"

        for i, d in enumerate(target_dets):
          target_list_str += (
            f"  - [ID: {i}] {d['object_name']} "
            f"at X:{d['x']:.3f}, Y:{d['y']:.3f}, "
            f"inside_placement_box:{is_inside_placement_box(d)}\n"
        )

        target_list_str += (
        "Use the image to identify which candidate matches the user's sticker/icon/color request. "
        "Return that candidate ID as 'target_id' and set 'next_action' to 'pick'.\n\n"
    )

    elif len(target_dets) == 1:
        target_list_str = (
        "SINGLE TARGET CANDIDATE DETECTED:\n"
        f"  - [ID: 0] {target_dets[0]['object_name']} "
        f"at X:{target_dets[0]['x']:.3f}, Y:{target_dets[0]['y']:.3f}, "
        f"inside_placement_box:{is_inside_placement_box(target_dets[0])}\n"
        "Use target_id: 0.\n\n"
    )

    else:
        target_list_str = (
        "NO TARGET CANDIDATES DETECTED BY PYTHON.\n"
        "Use the image to check if the target is visible. "
        "If it is not visible, output abort.\n\n"
    )
    prompt = (
        f"You are the visual safety gate for a robotic arm.\n\n"
        f"GOAL: Pick up the '{target}'\n\n"
        f"{user_context_section}"
        f"{target_list_str}"
        f"KNOWN OBJECT CATALOGUE: {catalogue_list}\n\n"
        f"PYTHON SENSOR ANALYSIS (Depth Elevation & XY Overlap):\n"
        f"{scene_analysis}\n\n"
        f"HISTORY:\n{history_summary}\n\n"
        f"INSTRUCTIONS:\n"
        f"  1. Look at the image AND read the Python sensor analysis.\n"
        f"  2. IMPORTANT: Do NOT try to visually look for the stickers on the cubes. The camera cannot see them clearly. You MUST blindly trust the following mapping.\n"
        f"  3. EXPLICIT MAPPING: 'soy milk' = GREEN cube. 'hat' = RED cube. 'wrench' = BLUE cube. 'umbrella' = YELLOW cube. If the user asks for one of these, you MUST output 'pick' and select the corresponding colored cube. Do NOT abort. Do NOT visually verify the sticker.\n"
        f"  4. If the user asks for an object NOT in this list, output 'abort'.\n"
        f"  5. HIDDEN OBJECT DEDUCTION: If your target cube is visually missing, BUT the Python Sensor Analysis says an obstacle is 'Likely resting on' your target cube, you MUST deduce the target is hidden underneath it. Output 'relocate' and set 'obstacle_name' to the blocking object.\n"
        f"  6. RELOCATE OVERRIDE: If the Python Sensor Analysis explicitly says 'MUST relocate [object] first', you MUST immediately output 'relocate' and set 'obstacle_name' to that object. Do NOT second-guess it. Do NOT try to pick the target.\n"
        f"  7. If the analysis shows CAUTION that the target has an anomalously high surface, look for an object sitting ON TOP OF or INSIDE/BLOCKING it. If you see one, output 'relocate' for that object. If clear, output 'pick'.\n"
        f"  8. If you see an unknown object blocking the target, output 'abort'.\n"
        f"  9. Do not re-relocate already moved objects (check history).\n\n"
        f"AVAILABLE ACTIONS:\n"
        f"  - relocate: move one blocking object to a safe spot.\n"
        f"  - pick: pick the target.\n"
        f"  - abort: cannot safely reach the target.\n\n"
        f"CRITICAL INSTRUCTION: You may think first, but your final output MUST be a valid JSON block enclosed in '```json' and '```' markers.\n"
        f"IMPORTANT: Do NOT over-think. Do NOT enter infinite loops. Keep your reasoning concise (under 100 words). If you cannot clearly see the requested sticker/icon, pick the best visual match or output abort.\n\n"
        f"NO EXPLANATIONS AFTER THE JSON. ONLY OUTPUT JSON AS THE FINAL RESULT.\n\n"
        f"Pick format:\n"
        f'{{"next_action":"pick","obstacle_name":null,"target_id":0,"reasoning":"target is visible and safe"}}\n\n'
        f"Relocate format:\n"
        f'{{"next_action":"relocate","obstacle_name":"[name_of_blocking_object]","target_id":0,"reasoning":"[name] is blocking the target"}}\n\n'
        f"Abort format:\n"
        f'{{"next_action":"abort","obstacle_name":null,"target_id":0,"reasoning":"target cannot be safely reached"}}'
    )
    
    raw = await ask_qwen_vision(prompt, base64_image)
    print("RAW QWEN PLAN:", repr(raw))
    try:
        import re
        clean = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL)
        clean = re.sub(r'<think>.*$', '', clean, flags=re.DOTALL)  # unterminated think block
        plan = extract_qwen_json(clean)
    
        try:
            target_id = int(plan.get("target_id", 0))
        except (ValueError, TypeError):
            target_id = 0
            
        plan["target_id"] = target_id

        if target_dets and 0 <= target_id < len(target_dets):
           chosen_target_det = target_dets[target_id]
           plan["chosen_target_name"] = chosen_target_det.get("object_name")
           plan["chosen_target_x"] = chosen_target_det.get("x")
           plan["chosen_target_y"] = chosen_target_det.get("y")

           print("===== QWEN CHOSEN TARGET =====")
           print("target_id:", target_id)
           print("object:", chosen_target_det.get("object_name"))
           print("x:", chosen_target_det.get("x"))
           print("y:", chosen_target_det.get("y"))
        else:
           print("WARNING: Qwen target_id invalid or no target_dets available.")
    except Exception as e:
        logger.error(f"Failed to parse Qwen JSON: {e}")
        
        # Clean fallback message for the UI
        clean_reason = raw[:100] if "Ollama API Error" in raw else "JSON Parse Error"
        
        # Default to PICK not abort — the Python sensor analysis (overlap +
        # elevation) has already gated this point. If sensors said the target
        # was blocked, the relocate would have been forced earlier. Aborting
        # because the LLM rambled is the wrong failure mode.
        
        if target_dets and len(target_dets) > 1:
            # Multiple identical candidates (e.g. 4 cubes). We genuinely don't
            # know which one was requested — do NOT silently default to [0].
            plan = {
                "next_action": "abort",
                "obstacle_name": None,
                "reasoning": f"Could not identify which {target_base} was requested ({clean_reason}). Please specify by colour or position."
            }
        else:
            # Single candidate — [0] is correct, safe to proceed.
            plan = {
                "next_action": "pick",
                "obstacle_name": None,
                "reasoning": f"Qwen unparseable ({clean_reason}) — single candidate, proceeding with direct pick."
            }
        
        
    print("PARSED QWEN ACTION:", plan)
    plan["raw_output"]=raw

    print("===== FINAL PICK TARGET DEBUG =====")
    print("original target:", target)
    print("target_base:", target_base)
    print("qwen target_id:", plan.get("target_id"))
    print("chosen_target_name:", plan.get("chosen_target_name"))
    print("chosen_target_x:", plan.get("chosen_target_x"))
    print("chosen_target_y:", plan.get("chosen_target_y"))
    
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
        
        # Intercept string-based error messages returned gracefully from the robot server
        res_data = result.get("result", {})
        if isinstance(res_data, dict):
            content = res_data.get("content", [])
            if content and isinstance(content, list) and isinstance(content[0], dict):
                text = content[0].get("text", "")
                if isinstance(text, str) and text.startswith("Error:"):
                    return {"error": text}
                    
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
                    }
                },
                "required": ["target_name"]
            }
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

    # ── analyse_surroundings ──────────────────────────────────────────────────
    if name == "analyse_surroundings":
        prompt    = args.get("prompt") or (
            "Describe all objects in the workspace, their positions relative to each "
            "other, and whether any appear stacked or blocking others. Ignore yellow tape if any."

           "Identify all cubes visible in the image by their color (e.g., blue, red, yellow, green)."

           "Look closely at the sticker illustration on top of each cube. Identify the icon depicted (e.g., wrench, hat, umbrella)."

           "If I asked for sticker, then if my target sticker is '[Insert Target Sticker Here, e.g., wrench]', state which cube color matches it and clarify its current position relative to the yellow boundary."
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
            
            logger.info("Moving robot to home position before taking snapshot...")
            await call_robot_tool("return_home", {})
            await asyncio.sleep(1.5)  # Give camera time to settle after arm moves out of view

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
                if is_target_match(target, d.get("object_name", "")) and not is_inside_placement_box(d)
            ]

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
                if expected_h is not None and len(target_detections) == 1:
                    expected_z = expected_h / 2
                    target_z = target_detections[0]["z"]
                    excess = target_z - expected_z
                    if excess > 0.020:
                        # Target is elevated. Find nearby objects with HIGHER Z
                        # (meaning they are ON TOP of the target and must be relocated).
                        # If no nearby object has higher Z, the target itself is on top
                        # and is accessible — do NOT force relocate.
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
                            # Only consider objects that are HIGHER than the target (on top of it)
                            if dist < 0.080 and dist < min_dist and d["z"] > target_z:
                                min_dist = dist
                                best_obstacle = d.get("object_name")
                        if best_obstacle:
                            sensor_obstacle = best_obstacle
                            sensor_reasoning = f"Depth sensor override: Target '{target}' is elevated (+{excess*1000:.0f}mm) and '{best_obstacle}' has higher Z — it is on top of the target."
                        else:
                            # Target is elevated but has the highest Z — it's on top, accessible
                            logger.info(f"Target '{target}' is elevated (+{excess*1000:.0f}mm) but has highest Z among nearby objects. Target is on top and accessible.")

                # Check 2: XY overlap
                if not sensor_obstacle and len(target_detections) == 1:
                    target_det = target_detections[0]
                    target_radius = math.hypot(
                        target_info.get("length_m", 0.04),
                        target_info.get("breadth_m", 0.04),
                    ) / 2

                    overlapping_obstacle = None
                    min_overlap_dist = 9999.0

                    target_z = target_det["z"]
                    for d in detections:
                        if is_target_match(target, d.get("object_name", "")) or is_inside_placement_box(d):
                            continue
                        if check_overlap_obb(d, target_det, clearance=0.015):
                            # Only flag as obstacle if the overlapping object has HIGHER Z
                            # (it's physically on top of the target, blocking access)
                            if d["z"] > target_z:
                                dist = math.hypot(d["x"] - target_det["x"], d["y"] - target_det["y"])
                                if dist < min_overlap_dist:
                                    min_overlap_dist = dist
                                    overlapping_obstacle = d["object_name"]
                            else:
                                logger.info(f"XY overlap with '{d['object_name']}' but target '{target}' has higher Z ({target_z*1000:.0f}mm vs {d['z']*1000:.0f}mm) — target is on top, accessible.")

                    if overlapping_obstacle:
                        sensor_obstacle = overlapping_obstacle
                        sensor_reasoning = f"XY overlap override: '{overlapping_obstacle}' overlaps with target '{target}' AND has higher Z — it is on top, requiring relocation."

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
                    snapshot_b64 = None
                    try:
                        snapshot_b64 = crop_target_snapshot(target_detections)
                    except Exception as e:
                        import traceback
                        logger.error(f"Snapshot error: {traceback.format_exc()}")
                        
                    image_to_send = snapshot_b64 if snapshot_b64 else frame_b64
                    plan = await qwen_plan_next_action(
                        target,
                        image_to_send,
                        detections,
                        action_history,
                        user_context,
                    ) if frame_b64 else {
                        "next_action": "abort",
                        "obstacle_name": None,
                        "reasoning": "No camera frame — aborting for safety.",
                        "raw_output": "No camera frame available, did not run qwen",
                    }
                if "next_action" not in plan and "target_id" in plan:
                    plan["next_action"] = "pick"

                print("AFTER QWEN/SENSOR PLAN:", plan)

                try:
                    target_id = int(plan.get("target_id", 0))
                except (ValueError, TypeError):
                    target_id = 0
                if not (0 <= target_id < len(target_detections)):
                    target_id = 0
                target_det = target_detections[target_id] if target_detections else None

                if plan.get("next_action") == "pick":
                    # Failsafe: If Qwen hallucinated that the path is clear, but depth sensor knows there is an anomaly.
                    target_info = OBJECT_CATALOGUE.get(target, {})
                    expected_h = target_info.get("height_m")
                    if expected_h is not None and target_det:
                        expected_z = expected_h / 2
                        excess = target_det["z"] - expected_z
                        if excess > 0.020:
                            # Find nearby object with HIGHER Z than target (on top of it)
                            target_x = target_det["x"]
                            target_y = target_det["y"]
                            target_z = target_det["z"]
                            best_obstacle = None
                            min_dist = 9999.0
                            for d in detections:
                                if d is target_det or is_inside_placement_box(d):
                                    continue
                                dx = d["x"] - target_x
                                dy = d["y"] - target_y
                                dist = (dx*dx + dy*dy)**0.5
                                if dist < 0.080 and dist < min_dist and d["z"] > target_z:
                                    min_dist = dist
                                    best_obstacle = d.get("object_name")

                            if best_obstacle:
                                logger.warning(f"QWEN FAILSAFE OVERRIDE: Target '{target}' Z is anomalously high (+{excess*1000:.0f}mm) and '{best_obstacle}' has higher Z. Forcing relocate.")
                                plan = {
                                    "next_action": "relocate",
                                    "obstacle_name": best_obstacle,
                                    "reasoning": f"Depth sensor failsafe: '{best_obstacle}' has higher Z than target '{target}', indicating it is on top.",
                                    "raw_output": plan.get("raw_output", "") + f"\n\n(OVERRIDDEN BY DEPTH SENSOR FAILSAFE: Relocating {best_obstacle})"
                                }
                            else:
                                # Target is elevated but has highest Z — it's on top, accessible
                                logger.info(f"Qwen failsafe: Target '{target}' elevated (+{excess*1000:.0f}mm) but has highest Z. Target is on top — proceeding with pick.")

                    # XY Overlap Failsafe: If Qwen hallucinated that it is clear, but YOLO detects an overlap
                    if plan.get("next_action") == "pick" and target_det:
                        target_info = OBJECT_CATALOGUE.get(target, {})
                        target_radius = math.hypot(
                            target_info.get("length_m", 0.04),
                            target_info.get("breadth_m", 0.04),
                        ) / 2

                        overlapping_obstacle = None
                        min_overlap_dist = 9999.0

                        target_z = target_det["z"]
                        for d in detections:
                            if is_target_match(target, d.get("object_name", "")) or is_inside_placement_box(d):
                                continue
                            if check_overlap_obb(d, target_det, clearance=0.015):
                                # Only flag if overlapping object has HIGHER Z (on top of target)
                                if d["z"] > target_z:
                                    dist = math.hypot(d["x"] - target_det["x"], d["y"] - target_det["y"])
                                    if dist < min_overlap_dist:
                                        min_overlap_dist = dist
                                        overlapping_obstacle = d["object_name"]
                                else:
                                    logger.info(f"XY overlap failsafe: '{d['object_name']}' overlaps but target has higher Z — target is on top, accessible.")

                        if overlapping_obstacle:
                            logger.warning(f"QWEN FAILSAFE OVERRIDE: '{overlapping_obstacle}' overlaps with target AND has higher Z. Forcing relocate.")
                            plan = {
                                "next_action": "relocate",
                                "obstacle_name": overlapping_obstacle,
                                "reasoning": f"XY overlap failsafe: '{overlapping_obstacle}' overlaps with target '{target}' and has higher Z — it is on top, requiring relocation.",
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
                elif obstacle_name and target_det:
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
                        t = target_det
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
                        obs = target_det.copy()
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
                target_id = plan.get("target_id", 0)
                if 0 <= target_id < len(target_detections):
                    target_det = target_detections[target_id]
                else:
                    target_det = target_detections[0]

                grasp_label = target_det.get("grasp_label")

                action_history.append(f"Vision located '{target}' — {reasoning}")

                print("RETURNING COORDINATES:", target_det)

                return [TextContent(
                    type="text",
                    text=json.dumps({
                        "status": "SUCCESS",
                        "target": target_det["object_name"],
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
