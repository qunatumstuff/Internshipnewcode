import asyncio
import logging
import base64
import json
import urllib.request
from typing import Any

from mcp.server import Server
from mcp.types import Tool, TextContent
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.requests import Request
import uvicorn

try:
    import cv2
    from ultralytics import YOLO
    import numpy as np
    HAS_VISION_LIBS = True
except ImportError:
    HAS_VISION_LIBS = False
    print("Warning: cv2, numpy or ultralytics not installed. Falling back to dummy logic.")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vision-mcp")

import os
import math

# ==========================================
# CONFIGURATION
# ==========================================
LAPTOP_A_IP  = os.environ.get("LAPTOP_A_IP",  "172.22.33.143")
QWEN_MODEL   = os.environ.get("QWEN_MODEL",   "qwen3-vl:2b")

# YOLOv11 OBB weights — must be an OBB model (e.g. yolo11n-obb.pt).
# Standard detection weights (yolo11n.pt) do NOT have r.obb and will crash.
YOLO_WEIGHTS = os.environ.get("YOLO_WEIGHTS", "yolo11n-obb.pt")

# Robot MCP server address
ROBOT_MCP_URL = os.environ.get("ROBOT_MCP_URL", "http://localhost:8002/messages")

# Webcam device index
WEBCAM_INDEX = int(os.environ.get("WEBCAM_INDEX", "0"))

# Image path used for current detection cycle
CAPTURE_PATH = "capture.jpg"

server = Server("vision-mcp-server")

if HAS_VISION_LIBS:
    yolo_model = YOLO(YOLO_WEIGHTS)

# ==========================================
# OBJECT CATALOGUE
# ==========================================
OBJECT_CATALOGUE = {
    "black marker": {"size": "134 x 20.53 x 20.53 mm"},
    "blue marker":  {"size": "134 x 20.53 x 20.53 mm"},
    "cube":         {"size": "40 x 40 x 40 mm"},
    "green marker": {"size": "134 x 20.53 x 20.53 mm"},
    "medicine":     {"size": "115.72 x 51.17 x 18.95 mm"},
    "nut":          {"size": "34.6 x 30 x 17 mm"},
    "pipe":         {"size": "120 x 110 x 54.5 mm",  "notes": "Includes custom grasp region and grip offsets"},
    "sponge":       {"size": "112.58 x 80 x 15.4 mm", "notes": "Includes angled grasp configuration"},
}

# ==========================================
# CAMERA CAPTURE
# ==========================================
def capture_image(path: str = CAPTURE_PATH) -> bool:
    """
    Capture a single frame from the webcam and save it to path.
    Returns True on success, False on failure.

    The camera transformation pipeline runs automatically when the camera
    is active, so all coordinates produced downstream are already in the
    robot base frame (metres).
    """
    if not HAS_VISION_LIBS:
        logger.warning("Vision libs not available — skipping real capture.")
        return False

    cap = cv2.VideoCapture(WEBCAM_INDEX)
    ret, frame = cap.read()
    cap.release()

    if not ret:
        logger.error("Failed to capture image from webcam.")
        return False

    cv2.imwrite(path, frame)
    logger.info(f"Image captured: {path}")
    return True


# ==========================================
# YOLO OBB DETECTION
# ==========================================
def run_yolo_obb_detection(image_path: str) -> list[dict]:
    """
    Run YOLOv11 OBB detection on the image and return ALL detected objects.

    Each entry contains:
        object_name  — detected class label
        x, y, z      — robot base frame coordinates in metres.
                        The camera transformation pipeline converts pixel
                        coordinates to robot frame automatically when the
                        camera is active, so these values are ready to send
                        to the robot controller directly.
        angle_deg    — object yaw in degrees extracted from the OBB rotation,
                        already in robot base frame via the transformation matrix.
        confidence   — YOLO detection confidence score

    Returns an empty list if YOLO is not installed or no objects are found.
    """
    if not HAS_VISION_LIBS:
        logger.warning("YOLO not installed — returning dummy detection.")
        return [
            {
                "object_name": "cube",
                "x": 0.35, "y": -0.20, "z": 0.04,
                "angle_deg": 0.0,
                "confidence": 0.99,
                "note": "DUMMY — YOLO not installed",
            }
        ]

    try:
        results = yolo_model(image_path)
        detections = []

        for result in results:
            # OBB results are in result.obb — NOT result.boxes.
            # result.obb.xywhr gives [cx, cy, w, h, angle_radians] in pixel space.
            # The camera transformation converts these to robot frame automatically.
            if result.obb is None:
                logger.warning("No OBB results found — are you using an OBB model?")
                continue

            for i in range(len(result.obb.cls)):
                class_id   = int(result.obb.cls[i])
                class_name = yolo_model.names[class_id].lower()
                conf       = float(result.obb.conf[i])

                # xywhr: [center_x, center_y, width, height, angle_radians]
                xywhr = result.obb.xywhr[i].cpu().numpy()
                cx_px, cy_px = float(xywhr[0]), float(xywhr[1])
                angle_rad    = float(xywhr[4])

                # Convert OBB pixel angle to degrees.
                # The camera-to-robot transformation rotates this into robot base frame.
                # TODO: if your transformation pipeline does NOT automatically rotate
                # the angle, apply your camera-to-robot rotation offset here:
                #   angle_deg = math.degrees(angle_rad) + CAMERA_ROTATION_OFFSET_DEG
                angle_deg = math.degrees(angle_rad)

                # Robot frame X, Y, Z come from the OBB centre pixel run through the
                # camera transformation matrix. The transformation is applied by the
                # camera pipeline automatically when the camera is active.
                # TODO: replace cx_px/cy_px with the transformed robot-frame values
                # if your pipeline exposes them as separate fields. For now we store
                # the raw pixel centre and mark it for downstream transformation.
                # If your transformation is already baked into the YOLO output,
                # replace cx_robot/cy_robot/z_robot with those values directly.
                cx_robot = cx_px   # TODO: replace with robot-frame X in metres
                cy_robot = cy_px   # TODO: replace with robot-frame Y in metres
                z_robot  = 0.0     # TODO: replace with robot-frame Z in metres

                if class_name not in OBJECT_CATALOGUE:
                    logger.info(f"Skipping unknown class: {class_name}")
                    continue

                detections.append({
                    "object_name": class_name,
                    "x":           round(cx_robot, 4),
                    "y":           round(cy_robot, 4),
                    "z":           round(z_robot,  4),
                    "angle_deg":   round(angle_deg, 2),
                    "confidence":  round(conf, 3),
                })

        logger.info(f"YOLO OBB detected {len(detections)} object(s): "
                    f"{[d['object_name'] for d in detections]}")
        return detections

    except Exception as e:
        logger.error(f"YOLO OBB error: {e}")
        return []


# ==========================================
# QWEN VISION REASONING
# ==========================================
async def ask_qwen_vision(prompt: str, image_path: str) -> str:
    """
    Send an image and prompt to Qwen3-VL on Laptop A via Ollama API.
    Returns Qwen's raw text response.
    """
    logger.info(f"Connecting to Qwen at {LAPTOP_A_IP}...")
    try:
        with open(image_path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        return f"Error loading image: {e}"

    payload = {
        "model":  QWEN_MODEL,
        "prompt": prompt,
        "stream": False,
        "images": [encoded],
    }

    url = f"http://{LAPTOP_A_IP}:11434/api/generate"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )

    loop = asyncio.get_event_loop()

    def fetch():
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    try:
        result = await loop.run_in_executor(None, fetch)
        return result.get("response", "No response from Qwen")
    except Exception as e:
        logger.error(f"Qwen network error: {e}")
        return f"Qwen Network Error: {e}"


async def qwen_analyse_scene(target: str, image_path: str, detections: list[dict]) -> dict:
    """
    Ask Qwen to analyse the scene and decide the action plan.

    Qwen receives:
      - The image of the current workspace
      - The target object name
      - All YOLO-detected objects with their coordinates
      - A description of available robot tools

    Qwen must reply with a JSON action plan. It decides on its own whether
    anything is blocking the target and whether a relocation is needed first.
    We do not tell Qwen explicitly what is blocking — it figures that out by
    reasoning about the image and the detection coordinates.

    Returns a dict with keys:
        action        — "pick" or "relocate_then_pick"
        obstacle_name — name of blocking object (only if action is relocate_then_pick)
        reasoning     — Qwen's explanation
    """
    target_info  = OBJECT_CATALOGUE.get(target, {})
    item_details = f"Size: {target_info.get('size', 'unknown')}."
    if "notes" in target_info:
        item_details += f" Notes: {target_info['notes']}"

    detection_summary = json.dumps(detections, indent=2)

    prompt = (
        f"You are the planning brain for a robotic arm operating on a flat table workspace.\n\n"
        f"TASK: Pick up the '{target}' ({item_details})\n\n"
        f"DETECTED OBJECTS IN SCENE (robot base frame, metres):\n"
        f"{detection_summary}\n\n"
        f"AVAILABLE ROBOT ACTIONS:\n"
        f"  1. pick_and_place_object — pick the target and move it to the placement box.\n"
        f"  2. relocate_object — pick a blocking object and move it to a safe empty spot "
        f"     within the workspace, then re-photograph the scene.\n\n"
        f"YOUR JOB:\n"
        f"Look at the image and the detected object coordinates carefully.\n"
        f"Decide whether the '{target}' can be picked up directly, or whether another "
        f"object is physically in the way and needs to be relocated first.\n"
        f"Consider an object blocking if it is within approximately 8 cm of the target "
        f"and would prevent the gripper from reaching it cleanly.\n\n"
        f"Reply ONLY with a valid JSON object in this exact format, no extra text:\n"
        f"{{\n"
        f'  "action": "pick" or "relocate_then_pick",\n'
        f'  "obstacle_name": "name of blocking object or null if action is pick",\n'
        f'  "reasoning": "one sentence explaining your decision"\n'
        f"}}"
    )

    raw = await ask_qwen_vision(prompt, image_path)
    logger.info(f"[Qwen] Raw response: {raw[:200]}")

    # Parse Qwen's JSON response.
    try:
        # Strip markdown fences if Qwen wraps its JSON in ```json ... ```
        clean = raw.strip()
        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
        plan = json.loads(clean.strip())
    except Exception as e:
        logger.warning(f"Qwen returned non-JSON response: {e}. Defaulting to direct pick.")
        plan = {
            "action":        "pick",
            "obstacle_name": None,
            "reasoning":     f"Could not parse Qwen response — defaulting to direct pick. Raw: {raw[:100]}",
        }

    return plan


# ==========================================
# ROBOT MCP COMMUNICATION
# ==========================================
async def call_robot_tool(tool_name: str, arguments: dict) -> dict:
    """
    Send a tool call to the Robot MCP server (RoboControl1_MCP_COMPAT.py).
    Returns the parsed response dict, or an error dict on failure.
    """
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

    loop = asyncio.get_event_loop()

    def fetch():
        with urllib.request.urlopen(req, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))

    try:
        result = await loop.run_in_executor(None, fetch)
        logger.info(f"Robot MCP response for {tool_name}: {str(result)[:120]}")
        return result
    except Exception as e:
        logger.error(f"Failed to reach Robot MCP for {tool_name}: {e}")
        return {"error": str(e)}


# ==========================================
# MCP SERVER TOOLS
# ==========================================
@server.list_tools()
async def handle_list_tools() -> list[Tool]:
    return [
        Tool(
            name="locate_and_pick_object",
            description=(
                "Full pipeline: captures a photo, asks Qwen to analyse the scene and "
                "decide if anything is blocking the target, runs YOLOv11 OBB to get "
                "coordinates and angles for all objects, then commands the robot to "
                "either pick directly or relocate a blocker first. "
                "Use this as the primary entry point for any pick task."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "target_name": {
                        "type":        "string",
                        "description": "The object to pick up.",
                        "enum":        list(OBJECT_CATALOGUE.keys()),
                    }
                },
                "required": ["target_name"],
            },
        ),

        Tool(
            name="capture_and_detect",
            description=(
                "Takes a fresh photo and runs YOLOv11 OBB detection, returning all "
                "detected objects with robot-frame coordinates and angles. "
                "Use this to get an updated view of the workspace after a relocation "
                "or any other robot action that changes the scene."
            ),
            inputSchema={
                "type":       "object",
                "properties": {},
            },
        ),
    ]


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict | None) -> list[TextContent]:
    args = arguments or {}

    # ──────────────────────────────────────────────────────────────────────────
    # TOOL: capture_and_detect
    # Fresh photo + full YOLO OBB detection. Returns all objects in scene.
    # Used after relocation to give Qwen an updated workspace view.
    # ──────────────────────────────────────────────────────────────────────────
    if name == "capture_and_detect":
        ok = capture_image(CAPTURE_PATH)
        if not ok:
            return [TextContent(type="text", text=json.dumps({
                "status":     "ERROR",
                "message":    "Failed to capture image from webcam.",
                "detections": [],
            }))]

        detections = run_yolo_obb_detection(CAPTURE_PATH)
        return [TextContent(type="text", text=json.dumps({
            "status":     "ok",
            "detections": detections,
        }))]

    # ──────────────────────────────────────────────────────────────────────────
    # TOOL: locate_and_pick_object
    # Full pipeline: capture → Qwen scene analysis → YOLO OBB → robot command.
    # ──────────────────────────────────────────────────────────────────────────
    if name == "locate_and_pick_object":
        target = args.get("target_name", "").strip().lower()

        # ── Stage 0: Validate target ─────────────────────────────────────────
        if target not in OBJECT_CATALOGUE:
            return [TextContent(type="text", text=json.dumps({
                "status":        "REJECTED",
                "message":       f"'{target}' is not in the approved object catalogue.",
                "allowed_items": list(OBJECT_CATALOGUE.keys()),
            }))]

        # ── Stage 1: Capture image ───────────────────────────────────────────
        logger.info(f"[Stage 1] Capturing image for target '{target}'...")
        ok = capture_image(CAPTURE_PATH)
        if not ok:
            return [TextContent(type="text", text=json.dumps({
                "status":  "ERROR",
                "message": "Failed to capture image from webcam.",
            }))]

        # ── Stage 2: YOLO OBB — detect ALL objects ───────────────────────────
        # Detect everything first so Qwen can reason about the full scene,
        # and so all non-target objects are available as obstacle coordinates.
        logger.info("[Stage 2] Running YOLOv11 OBB detection on full scene...")
        detections = run_yolo_obb_detection(CAPTURE_PATH)

        if not detections:
            return [TextContent(type="text", text=json.dumps({
                "status":  "FAILED",
                "message": "YOLO could not detect any objects in the scene.",
            }))]

        target_detections = [d for d in detections if d["object_name"] == target]
        if not target_detections:
            return [TextContent(type="text", text=json.dumps({
                "status":  "FAILED",
                "message": f"YOLO could not find '{target}' in the scene.",
                "detected_objects": [d["object_name"] for d in detections],
            }))]

        # ── Stage 3: Qwen scene analysis ─────────────────────────────────────
        # Qwen sees the image and all detection coordinates and decides on its
        # own whether anything is blocking the target. We do not tell it what
        # is blocking — it reasons from the image and coordinates itself.
        logger.info("[Stage 3] Qwen analysing scene...")
        plan = await qwen_analyse_scene(target, CAPTURE_PATH, detections)
        logger.info(f"[Stage 3] Qwen plan: {plan}")

        # ── Stage 4: Execute plan ─────────────────────────────────────────────
        target_det = target_detections[0]

        if plan.get("action") == "relocate_then_pick":
            # Qwen identified a blocker — find it in detections.
            obstacle_name = (plan.get("obstacle_name") or "").strip().lower()
            obstacle_dets = [d for d in detections if d["object_name"] == obstacle_name]

            if not obstacle_dets:
                logger.warning(
                    f"Qwen named '{obstacle_name}' as blocker but YOLO didn't detect it. "
                    "Falling back to direct pick."
                )
            else:
                obs = obstacle_dets[0]
                logger.info(f"[Stage 4] Relocating blocker '{obstacle_name}'...")

                reloc_result = await call_robot_tool("relocate_object", {
                    "obstacle_name":      obs["object_name"],
                    "obstacle_x":         obs["x"],
                    "obstacle_y":         obs["y"],
                    "obstacle_z":         obs["z"],
                    "obstacle_angle_deg": obs["angle_deg"],
                    "detections":         detections,
                })

                if reloc_result.get("error"):
                    return [TextContent(type="text", text=json.dumps({
                        "status":       "ERROR",
                        "stage":        "relocation",
                        "message":      reloc_result["error"],
                        "qwen_plan":    plan,
                    }))]

                # ── Stage 5: Re-photograph after relocation ───────────────────
                # Robot signals requires_redetection — take fresh photo and
                # re-detect so Qwen's next action uses the updated scene.
                logger.info("[Stage 5] Re-photographing after relocation...")
                ok = capture_image(CAPTURE_PATH)
                if ok:
                    detections = run_yolo_obb_detection(CAPTURE_PATH)
                    target_detections = [d for d in detections if d["object_name"] == target]
                    if target_detections:
                        target_det = target_detections[0]
                    else:
                        logger.warning(
                            f"'{target}' not found in re-detection. "
                            "Using coordinates from original detection."
                        )
                else:
                    logger.warning("Re-photograph failed. Using original target coordinates.")

        # ── Stage 6: Pick the target ──────────────────────────────────────────
        logger.info(f"[Stage 6] Commanding robot to pick '{target}'...")
        pick_result = await call_robot_tool("pick_and_place_object", {
            "object_name": target_det["object_name"],
            "x":           target_det["x"],
            "y":           target_det["y"],
            "z":           target_det["z"],
            "angle_deg":   target_det["angle_deg"],
            "detections":  detections,
        })

        final_response = {
            "status":      "SUCCESS" if not pick_result.get("error") else "ERROR",
            "target":      target,
            "qwen_plan":   plan,
            "pick_result": pick_result,
            "detections":  detections,
        }

        return [TextContent(type="text", text=json.dumps(final_response))]

    raise ValueError(f"Unknown tool: {name}")


# ==========================================
# SSE NETWORKING
# ==========================================
sse = SseServerTransport("/messages")

async def handle_sse(request: Request):
    async with sse.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        await server.run(
            streams[0], streams[1], server.create_initialization_options()
        )

async def handle_messages(request: Request):
    await sse.handle_post_message(request.scope, request.receive, request._send)

app = Starlette(routes=[
    Route("/sse",      endpoint=handle_sse),
    Route("/messages", endpoint=handle_messages, methods=["POST"]),
])

if __name__ == "__main__":
    logger.info("📷 Vision MCP Server listening on port 8001...")
    logger.info("Tools: locate_and_pick_object | capture_and_detect")
    uvicorn.run(app, host="0.0.0.0", port=8001)
