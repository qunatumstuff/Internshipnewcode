import asyncio
import logging
import base64
import json
import urllib.request
import ast
from typing import Any

from mcp.server import Server
from mcp.types import Tool, TextContent
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.requests import Request
from starlette.responses import JSONResponse
import uvicorn
import camera
import arm
import numpy as np
HAS_VISION_LIBS = True
HAS_VISION_LIBS = False
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vision-mcp")

import os
import math

# ==========================================
# CONFIGURATION
# ==========================================
LAPTOP_A_IP  = os.environ.get("LAPTOP_A_IP",  "172.22.33.143")
QWEN_MODEL   = os.environ.get("QWEN_MODEL",   "qwen3-vl:2b")


# Robot MCP server address
ROBOT_MCP_URL = os.environ.get("ROBOT_MCP_URL", "http://localhost:8002/messages")

# Webcam device index
WEBCAM_INDEX = int(os.environ.get("WEBCAM_INDEX", "0"))

# Image path used for current detection cycle
CAPTURE_PATH = "capture.jpg"

server = Server("vision-mcp-server")



# ==========================================
# QWEN VISION REASONING
# ==========================================
async def ask_qwen_vision(prompt: str, image_path: str) -> str:
    image_path=camera.get_camera_snapshot()

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
    target_info  = arm.OBJECT_CATALOGUE.get(target, {})
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
    Route("/api/capture-and-detect", endpoint=handle_capture_and_detect, methods=["GET", "POST"]),
])

if __name__ == "__main__":
    logger.info("📷 Vision MCP Server listening on port 8001...")
    logger.info("Tools: locate_and_pick_object | capture_and_detect")
    uvicorn.run(app, host="0.0.0.0", port=8001)
