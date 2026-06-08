import asyncio
import logging
import base64
import json
import urllib.request
import os
import math
import threading
import uvicorn
from typing import Any

from mcp.server import Server
from mcp.types import Tool, TextContent
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.requests import Request

# Import the camera module which handles the RealSense feed and YOLO
import camera

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vision-mcp")

# ==========================================
# CONFIGURATION
# ==========================================
LAPTOP_A_IP  = os.environ.get("LAPTOP_A_IP",  "127.0.0.1")
QWEN_MODEL   = os.environ.get("QWEN_MODEL",   "qwen3-vl:2b")

# Robot MCP server address
ROBOT_MCP_URL = os.environ.get("ROBOT_MCP_URL", "http://localhost:8002/messages")

server = Server("vision-mcp-server")

#OBJECT_CATALOGUE = {
    "black marker": {"size": "134 x 20.53 x 20.53 mm"},
    "blue marker":  {"size": "134 x 20.53 x 20.53 mm"},
    #"cube":         {"size": "40 x 40 x 40 mm"},
    "green marker": {"size": "134 x 20.53 x 20.53 mm"},
    "nut":          {"size": "34.6 x 30 x 17 mm"},
    #"sponge":       {"size": "112.58 x 80 x 15.4 mm", "notes": "Includes angled grasp configuration"},
}

# ==========================================
# QWEN VLM UTILITIES
# ==========================================
async def query_qwen(prompt: str, image_b64: str) -> str:
    """Send image + prompt to Qwen3-VL on Laptop A via Ollama API."""
    raw_b64 = image_b64
    # Strip data URL prefix if present
    if raw_b64.startswith("data:"):
        parts = raw_b64.split(",")
        if len(parts) > 1:
            raw_b64 = parts[1]

    payload = {
        "model": QWEN_MODEL,
        "prompt": prompt,
        "stream": False,
        "images": [raw_b64],
    }
    url = f"http://{LAPTOP_A_IP}:11434/api/generate"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    
    loop = asyncio.get_running_loop()
    def fetch():
        with urllib.request.urlopen(req, timeout=45) as response:
            return json.loads(response.read().decode("utf-8"))
            
    try:
        result = await loop.run_in_executor(None, fetch)
        print("Response from Qwen:")
        if "response" in result:
            return result["response"]
        
        else:
            return "no response"
    except Exception as e:
        logger.error(f"Qwen network error: {e}")
        return f"Qwen Network Error: {e}"

# ==========================================
# MCP TOOL REGISTRATION
# ==========================================
@server.list_tools()
async def handle_list_tools() -> list[Tool]:
    return [
        Tool(
            name="analyse_surroundings",
            description="Queries the vision MCP to analyze the workspace surroundings using Qwen-VL and describes the layout and objects present.",
            inputSchema={
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Custom analysis instruction prompt for the model. Defaults to describing objects and layout."
                    }
                }
            }
        ),
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
            name="get_camera_snapshot",
            description="Captures a snapshot from the D435i camera to inspect the environment/workspace. Optionally provide a question for the vision model (Qwen-VL) to analyze the image.",
            inputSchema={
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "Optional question to ask the vision language model about the captured snapshot (e.g., 'what objects are visible?')."
                    }
                }
            }
        )
    ]

@server.call_tool()
async def handle_call_tool(name: str, arguments: dict | None) -> list[TextContent]:
    args = arguments or {}
    
    if name == "analyse_surroundings":
        prompt = args.get("prompt")
        if not prompt:
            prompt = "Describe the objects in the workspace, their layout, and whether any objects are stacked or blocking each other."
        
        image_b64 = camera.get_camera_snapshot()
            
        result = await query_qwen(prompt, image_b64)
        for item in OBJECT_CATALOGUE.keys():
            if item.lower() in result.lower():
                camera.current_target_class=item
                break

        return [TextContent(type="text", text=result)]
        
    elif name == "get_camera_snapshot":
        question = args.get("question")
        image_b64 = camera.get_camera_snapshot()
        if image_b64.startswith("Error:"):
            return [TextContent(type="text", text=image_b64)]
            
        if question:
            result = await query_qwen(question, image_b64)
            return [TextContent(type="text", text=result)]
        else:
            return [TextContent(type="text", text=image_b64)]
            
    elif name == "locate_object":
        target_name = args.get("target_name")
        if not target_name:
            raise ValueError("target_name is required")
            
        # Step 1: VLM Feasibility / Safety check
        image_b64 = camera.get_camera_snapshot()
        if image_b64.startswith("Error:"):
            return [TextContent(type="text", text=json.dumps({"status": f"ERROR: {image_b64}"}))]
            
        gate_prompt = f"Verify if it is safe and possible to pick up the '{target_name}' from the current workspace scene. Check for obstacles, safety hazards, or other blocking items. Respond starting with either 'SAFE' or 'BLOCKED: <reason>'."
        gate_res = await query_qwen(gate_prompt, image_b64)
        
        if "BLOCKED" in gate_res.upper():
            # Safety gate says blocked!
            return [TextContent(type="text", text=json.dumps({
                "status": f"BLOCKED: {gate_res}",
                "coordinates": None
            }))]
            
        # Step 2: YOLO localization
        camera.current_target_class = target_name
        # Wait a bit for the background YOLO thread to detect the object and update coords
        await asyncio.sleep(0.8)
        
        coords = camera.latest_3d_coords
        
        return [TextContent(type="text", text=json.dumps({
            "status": f"SUCCESS: Localized {target_name}",
            "coordinates": coords
        }))]
        
    else:
        raise ValueError(f"Unknown tool: {name}")

# ==========================================
# SSE Networking Setup
# ==========================================
sse = SseServerTransport("/messages")

async def handle_sse(request: Request):
    """Handles the initial SSE connection from the PC"""
    async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())

async def handle_messages(request: Request):
    """Handles incoming tool requests from server.js"""
    await sse.handle_post_message(request.scope, request.receive, request._send)

# Bind the routes to Starlette
app = Starlette(routes=[
    Route("/sse", endpoint=handle_sse),
    Route("/messages", endpoint=handle_messages, methods=["POST"])
])

if __name__ == "__main__":
    # Start the RealSense & YOLO vision loop thread from camera.py
    print("Initializing D435i Camera & YOLO Vision Loop...")
    vision_thread = threading.Thread(target=camera.vision_loop, daemon=True)
    vision_thread.start()
    
    
    print("👁️ Vision MCP Server listening on port 8001...")
    uvicorn.run(app, host="0.0.0.0", port=8001)
