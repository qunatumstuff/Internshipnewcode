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
    "black marker": {"size": "134 x 20.53 x 20.53 mm", "height_m": 0.02053},
    "blue marker":  {"size": "134 x 20.53 x 20.53 mm", "height_m": 0.02053},
    "cube":         {"size": "40 x 40 x 40 mm",         "height_m": 0.040},
    "green marker": {"size": "134 x 20.53 x 20.53 mm",  "height_m": 0.02053},
    "medicine":     {"size": "115.72 x 51.17 x 18.95 mm","height_m": 0.01895},
    "nut":          {"size": "34.6 x 30 x 17 mm",        "height_m": 0.017},
    "pipe":         {"size": "120 x 110 x 54.5 mm",      "height_m": 0.0545,
                     "notes": "Smart grasp via segmentation mask"},
    "sponge":       {"size": "112.58 x 80 x 15.4 mm",    "height_m": 0.01540,
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
            color_image, verbose=False, agnostic_nms=True, iou=0.35, conf=0.35
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
            })

    # ── Pass 2: Segmentation — pipe and sponge centre/endpoint positions ────────
    if camera.inference_lock.acquire(timeout=2.0):
        try:
            seg_results = camera.segment(
            color_image, verbose=False, agnostic_nms=True, iou=0.35, conf=0.35
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
    """Send image + prompt to Qwen3-VL via Ollama API.
    
    Qwen3 models have 'thinking mode' enabled by default. The model
    generates <think>...</think> tags internally which can consume all
    tokens, leaving the actual content field empty. We disable thinking
    via /no_think suffix AND extract from thinking content as fallback.
    """
    logger.info(f"Connecting to Qwen at {OLLAMA_IP} with model {QWEN_MODEL}...")

    raw_b64 = base64_image
    if raw_b64.startswith("data:"):
        parts = raw_b64.split(",")
        if len(parts) > 1:
            raw_b64 = parts[1]

    # Append /no_think to disable Qwen3's internal thinking mode
    prompt_no_think = prompt + "\n/no_think"

    payload = {
        "model": QWEN_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "You are a JSON-only robot planner. Respond with a single JSON object. No thinking, no explanation, no markdown."
            },
            {
                "role": "user",
                "content": prompt_no_think,
                "images": [raw_b64]
            }
        ],
        "stream": False,
        "options": {
            "temperature": 0.1,
            "num_predict": 512
        }
    }

    url = f"http://{OLLAMA_IP}:11434/api/chat"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    loop = asyncio.get_running_loop()
    def fetch():
        logger.info("SENDING TO QWEN...")
        with urllib.request.urlopen(req, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))
    try:
        result = await loop.run_in_executor(None, fetch)
        logger.info("QWEN RESPONSE RECEIVED")
        print("RAW OLLAMA RESULT:", json.dumps(result, indent=2)[:2000])

        if "error" in result:
            logger.error(f"Ollama API Error: {result['error']}")
            return f"Ollama API Error: {result['error']}"

        message = result.get("message", {})
        response_text = message.get("content", "")

        # Qwen3 thinking fallback: if content is empty, check if the
        # model put everything inside <think> tags or a 'thinking' field
        if not response_text.strip():
            logger.warning("Content field is empty — checking for thinking content...")
            
            # Check for thinking field (some Ollama versions use this)
            thinking_text = message.get("thinking", "")
            if thinking_text:
                logger.info(f"Found thinking content ({len(thinking_text)} chars), extracting JSON...")
                response_text = thinking_text
            
            # Check raw response string for any JSON buried anywhere
            if not response_text.strip():
                raw_str = json.dumps(result)
                json_start = raw_str.find('{"next_action"')
                if json_start != -1:
                    json_end = raw_str.find('}', json_start) + 1
                    response_text = raw_str[json_start:json_end]
                    logger.info(f"Extracted JSON from raw response: {response_text}")

        if not response_text.strip():
            logger.error("Ollama returned completely empty output even after fallback checks.")
            return "Ollama API Error: Model returned empty string."
            
        logger.info(f"[Qwen] {response_text[:200]}")
        return response_text
    except Exception as e:
        logger.error(f"Qwen network error: {e}")
        return f"Qwen Network Error: {e}"


# ==========================================
# QWEN PLANNING
# ==========================================
def extract_qwen_json(raw: str) -> dict:
    raw = raw.strip()

    # Remove XML-style thinking
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()

    # If Qwen prints "Thinking... ...done thinking.", remove everything before final JSON
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
        "reasoning": f"Could not parse Qwen output, defaulting to pick. Raw: {raw[:100]}"
    }

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
    target_info  = OBJECT_CATALOGUE.get(target, {})
    item_details = f"Size: {target_info.get('size', 'unknown')}."
    if "notes" in target_info:
        item_details += f" Notes: {target_info['notes']}"

    catalogue_heights = "\n".join(
        f"  - {name}: known height = {info['height_m']*1000:.1f}mm"
        for name, info in OBJECT_CATALOGUE.items()
    )

    # Build detection lines with elevation analysis
    detection_lines = []
    for d in detections:
        known_h = OBJECT_CATALOGUE.get(d["object_name"], {}).get("height_m", None)
        elevation_note = ""
        if known_h is not None:
            expected_z     = known_h / 2
            excess         = d["z"] - expected_z
            implied_height = excess * 2

            if excess > 0.020:
                MATCH_TOLERANCE = 0.015
                plausible = []
                for obj_name, obj_info in OBJECT_CATALOGUE.items():
                    if obj_name == d["object_name"]:
                        continue
                    h = obj_info["height_m"]
                    if abs(h - implied_height) <= MATCH_TOLERANCE:
                        plausible.append(
                            f"{obj_name} ({h*1000:.0f}mm, "
                            f"off by {abs(h-implied_height)*1000:.0f}mm)"
                        )
                if plausible:
                    elevation_note = (
                        f"\n    *** ELEVATED: actual Z={d['z']*1000:.0f}mm, "
                        f"expected ~{expected_z*1000:.0f}mm. "
                        f"Plausible hidden objects: {', '.join(plausible)} ***"
                    )
                else:
                    elevation_note = (
                        f"\n    *** ELEVATED: actual Z={d['z']*1000:.0f}mm, "
                        f"expected ~{expected_z*1000:.0f}mm. "
                        f"No catalogue match — likely measurement noise. "
                        f"Do NOT relocate without other evidence. ***"
                    )

        line = (
            f"  - {d['object_name']}: "
            f"X={d['x']:.3f}m Y={d['y']:.3f}m Z={d['z']:.3f}m "
            f"angle={d.get('angle_deg', 0):.1f}deg "
            f"confidence={d.get('confidence', 0):.2f}"
            f"{elevation_note}"
        )
        if d["object_name"] == "pipe" and d.get("grasp_label"):
            line += f" [grasp_end={d['grasp_label']}]"
        detection_lines.append(line)

    detection_summary = "\n".join(detection_lines) if detection_lines else "  (none detected)"
    catalogue_list    = ", ".join(OBJECT_CATALOGUE.keys())
    history_summary   = (
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
        f"You are the planning brain for a robotic arm on a flat table workspace.\n\n"
        f"GOAL: Pick up the '{target}' ({item_details})\n\n"
        f"{user_context_section}"
        f"KNOWN PHYSICAL HEIGHTS FROM CATALOGUE:\n{catalogue_heights}\n\n"
        f"CURRENT SCENE — YOLO DETECTIONS (robot base frame):\n{detection_summary}\n\n"
        f"HOW TO REASON ABOUT ELEVATED OBJECTS:\n"
        f"  Table surface = Z=0.0m. Each detected Z is the object centre height.\n"
        f"  Objects marked ELEVATED may have something underneath.\n"
        f"  BEFORE relocating, verify at least one plausible catalogue object\n"
        f"  matches the implied hidden height AND is relevant to the task.\n"
        f"  Do NOT relocate if the elevation note says no catalogue match.\n\n"
        f"HISTORY:\n{history_summary}\n\n"
        f"RULES:\n"
        f"  1. Only interact with detected objects from the list above.\n"
        f"     Approved catalogue: {catalogue_list}.\n"
        f"  2. Only relocate if height matching or direct blocking justifies it.\n"
        f"  3. Do not re-relocate already moved objects (check history).\n"
        f"  4. If target is clear, say pick.\n"
        f"  5. If unresolvable, say abort with a reason.\n\n"
        f"AVAILABLE NEXT ACTIONS (choose exactly one):\n"
        f"  - relocate: move one blocking object to a safe workspace spot.\n"
        f"  - pick: pick the target — path is clear.\n"
        f"  - abort: cannot safely reach the target.\n\n"
        f"Reply ONLY with one valid JSON object. Do not explain. Do not write thinking.\n"
        f"Use exactly one of these formats:\n\n"
        f"Pick format:\n"
        f'{{"next_action":"pick","obstacle_name":null,"reasoning":"target is visible and safe"}}\n\n'
        f"Relocate format:\n"
        f'{{"next_action":"relocate","obstacle_name":"cube","reasoning":"cube is blocking the target"}}\n\n'
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
        
        plan = {
            "next_action": "pick",
            "obstacle_name": None,
            "reasoning": f"Safety check bypassed: {clean_reason}"
        }
        
    print("PARSED QWEN ACTION:", plan)
    
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
        with urllib.request.urlopen(req, timeout=60) as response:
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
                if d.get("object_name") == target
            ]

            if not target_detections:
                return [TextContent(type="text", text=json.dumps({
                    "status": "FAILED",
                    "message": f"'{target}' not found.",
                    "detected_objects": [d.get("object_name") for d in detections],
                    "history": action_history,
                }))]
            
            print("BEFORE QWEN")

            plan = await qwen_plan_next_action(
                target,
                frame_b64,
                detections,
                action_history,
                user_context,
            ) if frame_b64 else {
                "next_action": "pick",
                "obstacle_name": None,
                "reasoning": "No frame, defaulting to pick.",
            }
            print("AFTER QWEN:", plan)

            next_action = plan.get("next_action", "pick")
            obstacle_name = (plan.get("obstacle_name") or "").strip().lower()
            reasoning = plan.get("reasoning", "")

            if next_action == "abort":
                return [TextContent(type="text", text=json.dumps({
                    "status": "ABORTED",
                    "reasoning": reasoning,
                    "history": action_history,
                }))]

            if next_action == "relocate":
                obstacle_dets = [
                    d for d in detections
                    if d.get("object_name") == obstacle_name
                ]

                if obstacle_name and obstacle_dets:
                    obs = obstacle_dets[0]

                    reloc_result = await call_robot_tool("relocate_object", {
                        "obstacle_name": obs["object_name"],
                        "obstacle_x": obs["x"],
                        "obstacle_y": obs["y"],
                        "obstacle_z": obs["z"],
                        "obstacle_angle_deg": obs.get("angle_deg"),
                        "detections": detections,
                    })

                    if reloc_result.get("error"):
                        return [TextContent(type="text", text=json.dumps({
                            "status": "ERROR",
                            "message": reloc_result["error"],
                            "history": action_history,
                        }))]

                    action_history.append(f"Relocated '{obstacle_name}' — {reasoning}")
                    await asyncio.sleep(1.0)
                    continue

                next_action = "pick"

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
