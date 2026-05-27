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

# We wrap the heavy CV/YOLO imports in try-except in case they aren't installed yet
try:
    import cv2
    from ultralytics import YOLO
    HAS_VISION_LIBS = True
except ImportError:
    HAS_VISION_LIBS = False
    print("Warning: cv2 or ultralytics not installed. Falling back to dummy YOLO logic.")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vision-mcp")

import os
import math

# ==========================================
# CONFIGURATION
# ==========================================
# Laptop A (Qwen/GPT Laptop) IP Address
# You can set this via environment variable before running, e.g.:
# export LAPTOP_A_IP="192.168.1.100"
LAPTOP_A_IP = os.environ.get("LAPTOP_A_IP", "172.22.33.143") # Wi-Fi IP for Laptop A
QWEN_MODEL = os.environ.get("QWEN_MODEL", "qwen3-vl:2b")
YOLO_WEIGHTS = os.environ.get("YOLO_WEIGHTS", "yolo11n.pt")

# Pixel coordinates defining where the physical robot arm base is located in the camera view.
# (Defaulting to bottom center for a standard 640x480 webcam feed)
ROBOT_BASE_X = int(os.environ.get("ROBOT_BASE_X", "320"))
ROBOT_BASE_Y = int(os.environ.get("ROBOT_BASE_Y", "480"))

server = Server("vision-mcp-server")

if HAS_VISION_LIBS:
    yolo_model = YOLO(YOLO_WEIGHTS) 

# ==========================================
# OBJECT CATALOGUE
# ==========================================
# The robot is strictly limited to picking up these defined items.
OBJECT_CATALOGUE = {
    "black marker": {"size": "134 x 20.53 x 20.53 mm"},
    "blue marker": {"size": "134 x 20.53 x 20.53 mm"},
    "cube": {"size": "40 x 40 x 40 mm"},
    "green marker": {"size": "134 x 20.53 x 20.53 mm"},
    "medicine": {"size": "115.72 x 51.17 x 18.95 mm"},
    "nut": {"size": "34.6 x 30 x 17 mm"},
    "pipe": {"size": "120 x 110 x 54.5 mm", "notes": "Includes custom grasp region and grip offsets"},
    "sponge": {"size": "112.58 x 80 x 15.4 mm", "notes": "Includes angled grasp configuration"}
}

# ==========================================
# AI FUNCTIONS
# ==========================================
async def ask_qwen_vision(prompt: str, image_path: str) -> str:
    """Stage 1: Send image over Ethernet to Laptop A's Qwen3-VL"""
    logger.info(f"Connecting to Qwen at {LAPTOP_A_IP}...")
    try:
        with open(image_path, "rb") as image_file:
            encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
    except Exception as e:
        return f"Error loading image: {e}"

    url = f"http://{LAPTOP_A_IP}:11434/api/generate"
    payload = {
        "model": QWEN_MODEL, 
        "prompt": prompt,
        "stream": False,
        "images": [encoded_string]
    }

    loop = asyncio.get_event_loop()
    req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers={'Content-Type': 'application/json'})
    
    def fetch():
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode('utf-8'))
            
    try:
        result = await loop.run_in_executor(None, fetch)
        return result.get("response", "No response from Qwen")
    except Exception as e:
        logger.error(f"Network error contacting Qwen on Laptop A: {e}")
        return f"Qwen Network Error: {e}"

def run_yolo_detection(image_path: str, target_class: str) -> dict:
    """Stage 2: Precise Localization via YOLOv8 on Laptop B - Finds Closest Match"""
    if not HAS_VISION_LIBS:
        return {"found": True, "x": 320, "y": 240, "z": 0, "note": "DUMMY COORDS - YOLO NOT INSTALLED"}

    try:
        results = yolo_model(image_path)
        candidates = []
        
        for r in results:
            boxes = r.boxes
            for box in boxes:
                class_id = int(box.cls[0])
                class_name = yolo_model.names[class_id]
                
                if class_name.lower() == target_class.lower():
                    x1, y1, x2, y2 = box.xyxy[0]
                    center_x = int((x1 + x2) / 2)
                    center_y = int((y1 + y2) / 2)
                    
                    # Calculate distance to robot base
                    distance = math.sqrt((center_x - ROBOT_BASE_X)**2 + (center_y - ROBOT_BASE_Y)**2)
                    candidates.append((distance, center_x, center_y, class_name))
        
        if candidates:
            # Sort by distance (ascending) and pick the first one (shortest distance)
            candidates.sort(key=lambda item: item[0])
            best_dist, best_x, best_y, best_class = candidates[0]
            logger.info(f"Selected closest '{best_class}' at ({best_x}, {best_y}) with distance {best_dist:.1f}px")
            return {"found": True, "x": best_x, "y": best_y, "class": best_class, "distance": best_dist}
            
    except Exception as e:
        logger.error(f"YOLO Error: {e}")
        return {"found": False, "error": str(e)}
        
    return {"found": False}

async def send_coordinates_to_arm(x: float, y: float, z: float):
    """Bypasses GPT: Sends coordinates directly to the Robot MCP on localhost:8002"""
    logger.info(f"Directing Robot Arm to ({x}, {y}, {z})...")
    
    # We construct a manual JSON-RPC call to the Robot MCP Server
    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "name": "move_to_coordinates",
            "arguments": {
                "x": x,
                "y": y,
                "z": z
            }
        },
        "id": 1
    }
    
    url = "http://localhost:8002/messages"
    loop = asyncio.get_event_loop()
    req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers={'Content-Type': 'application/json'})
    
    def fetch():
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.read().decode('utf-8')
            
    try:
        await loop.run_in_executor(None, fetch)
        return True
    except Exception as e:
        logger.error(f"Failed to communicate directly with Robot Arm: {e}")
        return False

# ==========================================
# MCP SERVER
# ==========================================
@server.list_tools()
async def handle_list_tools() -> list[Tool]:
    return [
        Tool(
            name="locate_object",
            description="Uses the camera to find the coordinates of a specific object.",
            inputSchema={
                "type": "object",
                "properties": {
                    "target_name": {
                        "type": "string", 
                        "description": "The name of the object to locate.",
                        "enum": list(OBJECT_CATALOGUE.keys())
                    }
                },
                "required": ["target_name"]
            }
        )
    ]

@server.call_tool()
async def handle_call_tool(name: str, arguments: dict | None) -> list[TextContent]:
    if name == "locate_object":
        target = (arguments or {}).get("target_name", "unknown").lower()
        
        # ── STAGE 0: Validate against OBJECT_CATALOGUE ──────────────────────
        if target not in OBJECT_CATALOGUE:
            logger.warning(f"Rejected attempt to pick up unauthorized item: {target}")
            final_response = {
                "status": f"REJECTED - '{target}' is not in the approved OBJECT_CATALOGUE.",
                "qwen_analysis": None,
                "coordinates_used": None,
                "allowed_items": list(OBJECT_CATALOGUE.keys())
            }
            return [TextContent(type="text", text=json.dumps(final_response))]
            
        target_info = OBJECT_CATALOGUE[target]
        
        # 0. Capture image from webcam
        test_image_path = "capture.jpg"
        if HAS_VISION_LIBS:
            cap = cv2.VideoCapture(0)
            ret, frame = cap.read()
            cap.release()
            
            if ret:
                cv2.imwrite(test_image_path, frame)
            else:
                logger.error("Failed to capture image from webcam.")
                final_response = {
                    "status": "ERROR - Could not read from webcam. Is it connected?",
                    "qwen_analysis": None,
                    "coordinates_used": None
                }
                return [TextContent(type="text", text=json.dumps(final_response))]
        
        # ── STAGE 1: Qwen Environment Scan & Feasibility Check ──────────────
        # Qwen scans the scene and decides if it is SAFE + POSSIBLE for the
        # robotic arm to pick up the target. It must reply with a structured
        # verdict so we can gate Stage 2.
        logger.info(f"[Stage 1] Qwen scanning environment for '{target}'...")
        
        item_details = f"Approx size: {target_info.get('size', 'unknown')}."
        if 'notes' in target_info:
            item_details += f" Notes: {target_info['notes']}"
            
        qwen_prompt = (
            f"You are an assistant for a robotic arm. Look at this image carefully.\n"
            f"Task: Determine if it is physically possible and safe for a robotic arm "
            f"to pick up the '{target}'.\n"
            f"Item details: {item_details}\n"
            f"Rules:\n"
            f"- Check if the '{target}' is clearly visible and not obstructed.\n"
            f"- Check if the surrounding area is clear enough for the arm to reach it.\n"
            f"- Start your reply with exactly 'FEASIBLE' or 'NOT_FEASIBLE', then explain why in one sentence."
        )
        qwen_response = await ask_qwen_vision(qwen_prompt, test_image_path)
        logger.info(f"[Stage 1] Qwen verdict: {qwen_response[:80]}...")

        # Parse Qwen's verdict — only proceed if it says FEASIBLE
        qwen_upper = qwen_response.strip().upper()
        is_feasible = qwen_upper.startswith("FEASIBLE")

        if not is_feasible:
            # Qwen blocked the pickup — skip YOLO entirely
            logger.warning(f"[Stage 1] Qwen blocked pickup: {qwen_response}")
            final_response = {
                "status": "BLOCKED - Qwen determined pickup is not feasible",
                "qwen_analysis": qwen_response,
                "coordinates_used": None
            }
            return [TextContent(type="text", text=json.dumps(final_response))]

        # ── STAGE 2: YOLOv8 Precise Localization ────────────────────────────
        # Qwen confirmed the object exists and pickup is safe.
        # Now use YOLO to get exact pixel coordinates.
        logger.info(f"[Stage 2] Qwen approved. YOLO locating '{target}'...")
        yolo_coords = run_yolo_detection(test_image_path, target)

        # ── STAGE 3: Send Coordinates to Robot Arm ───────────────────────────
        if yolo_coords.get("found"):
            success = await send_coordinates_to_arm(
                yolo_coords.get("x", 0), yolo_coords.get("y", 0), 0
            )
            final_response = {
                "status": "SUCCESS - Arm moving to object" if success else "WARNING - Coordinates found but arm failed to respond",
                "qwen_analysis": qwen_response,
                "coordinates_used": yolo_coords
            }
        else:
            # Qwen thought it was feasible but YOLO couldn't pin it — report both
            final_response = {
                "status": "FAILED - Qwen approved but YOLO could not locate the object",
                "qwen_analysis": qwen_response,
                "coordinates_used": None
            }
        
        return [TextContent(type="text", text=json.dumps(final_response))]
    
    raise ValueError(f"Unknown tool: {name}")

# ==========================================
# SSE NETWORKING
# ==========================================
sse = SseServerTransport("/messages")

async def handle_sse(request: Request):
    async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())

async def handle_messages(request: Request):
    await sse.handle_post_message(request.scope, request.receive, request._send)

app = Starlette(routes=[
    Route("/sse", endpoint=handle_sse),
    Route("/messages", endpoint=handle_messages, methods=["POST"])
])

if __name__ == "__main__":
    logger.info("📷 Vision MCP Server listening on Ethernet port 8001...")
    uvicorn.run(app, host="0.0.0.0", port=8001)
