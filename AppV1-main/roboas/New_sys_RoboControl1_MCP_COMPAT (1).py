
# ROBOT MCP SERVER — compatible with Week_11_day_2_mcp_relocate_pick_yaw (1).py
import asyncio
import logging
import os
import json
import importlib.util
import urllib.request
from typing import Any

from mcp.server import Server
from mcp.types import Tool, TextContent
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.requests import Request
import uvicorn

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("robot-mcp")

# ------------------------------------------------------------
# Load the main robot controller without renaming its functions.
# Keep Week_10_day_5Humanedited_MCP_COMPAT.py in the same folder
# as this RoboControl file when running on the robot PC.
# ------------------------------------------------------------
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
ROBOT_CONTROL_PATH = os.path.join(THIS_DIR, "Week_11_day_2_mcp_relocate_pick_yaw (1).py")

# Vision MCP REST endpoint for verification photo after relocation.
# Both servers run on Laptop B, so localhost is correct.
VISION_MCP_CAPTURE_URL = os.environ.get(
    "VISION_MCP_CAPTURE_URL", "http://localhost:8001/api/capture-and-detect"
)


def call_vision_capture_and_detect():
    """
    Call the Vision MCP server's REST endpoint to capture a fresh photo
    and run YOLOv11 OBB detection. Returns the full detection list.

    This is used ONLY after relocate_object to give Qwen an updated
    workspace view. pick_and_place_object does NOT call this because
    the object leaves the workspace entirely.
    """
    try:
        req = urllib.request.Request(VISION_MCP_CAPTURE_URL)
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
            logger.info(
                f"Vision MCP returned {len(data.get('detections', []))} detection(s) "
                f"after verification photo."
            )
            return data
    except Exception as e:
        logger.error(f"Failed to call Vision MCP capture_and_detect: {e}")
        return {"status": "ERROR", "detections": [], "error": str(e)}

spec = importlib.util.spec_from_file_location("week10_robot_control", ROBOT_CONTROL_PATH)
robot_control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(robot_control)

# 1. Initialize the MCP Server
server = Server("robot-arm-mcp-server")

# 2. Define tools that the AI/server.js can call
@server.list_tools()
async def handle_list_tools() -> list[Tool]:
    return [
        Tool(
            name="pick_and_place_object",
            description=(
                "Pick and place one detected object using the main robot controller. "
                "Input comes from the vision MCP/AI pipeline: object_name, x, y, z in metres, "
                "and angle_deg (yaw in degrees, robot base frame, from YOLOv11 OBB decomposed RPY)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "object_name": {
                        "type": "string",
                        "description": "Object to pick, e.g. cube, medicine, nut, pipe, sponge, black marker."
                    },
                    "x": {"type": "number", "description": "Robot-frame X in metres."},
                    "y": {"type": "number", "description": "Robot-frame Y in metres."},
                    "z": {"type": "number", "description": "Robot-frame Z in metres. If too low, main code uses calibrated fallback."},
                    "angle_deg": {
                        "type": "number",
                        "description": (
                            "Object yaw angle in degrees in robot base frame, "
                            "extracted from YOLOv11 OBB transformation matrix RPY decomposition. "
                            "If omitted, the catalogue default grasp angle is used."
                        )
                    }
                },
                "required": ["object_name", "x", "y", "z"]
            }
        ),

        # Backwards-compatible simple move tool. This no longer asks for user input.
        Tool(
            name="move_to_coordinates",
            description="Compatibility wrapper. Moves as a cube pick by default unless object_name is supplied.",
            inputSchema={
                "type": "object",
                "properties": {
                    "x": {"type": "number"},
                    "y": {"type": "number"},
                    "z": {"type": "number"},
                    "object_name": {"type": "string", "default": "cube"},
                    "angle_deg": {
                        "type": "number",
                        "description": (
                            "Object yaw angle in degrees in robot base frame, "
                            "from YOLOv11 OBB RPY decomposition. Optional."
                        )
                    }
                },
                "required": ["x", "y", "z"]
            }
        ),

        # Relocate an obstacle within the pick workspace so the target becomes reachable.
        # After the robot moves the obstacle, this tool automatically triggers a fresh
        # YOLO photo and returns the updated detection list to Qwen.
        Tool(
            name="relocate_object",
            description=(
                "Pick an obstacle object and move it to a safe empty position within "
                "the pick workspace (NOT the placement box), then take a fresh photo "
                "so Qwen receives the updated scene before planning the next action. "
                "Use this when an object is blocking the target and needs to be moved "
                "out of the way first."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "obstacle_name": {
                        "type": "string",
                        "description": "Name of the object to relocate, e.g. cube, medicine."
                    },
                    "obstacle_x": {"type": "number", "description": "Robot-frame X of obstacle in metres."},
                    "obstacle_y": {"type": "number", "description": "Robot-frame Y of obstacle in metres."},
                    "obstacle_z": {"type": "number", "description": "Robot-frame Z of obstacle in metres."},
                    "obstacle_angle_deg": {
                        "type": "number",
                        "description": "Obstacle yaw in degrees from OBB RPY decomposition. Optional."
                    },
                    "detections": {
                        "type": "array",
                        "description": (
                            "Full YOLO detection list for the current scene. Used for dynamic "
                            "obstacle avoidance during the relocation move and to find a clear "
                            "drop spot. Each item must have object_name, x, y, z fields."
                        ),
                        "items": {"type": "object"}
                    }
                },
                "required": ["obstacle_name", "obstacle_x", "obstacle_y", "obstacle_z"]
            }
        )
    ]

# 3. Execute tool calls
@server.call_tool()
async def handle_call_tool(name: str, arguments: dict | None) -> list[TextContent]:
    args = arguments or {}

    if name == "pick_and_place_object":
        object_name = args.get("object_name")
        x = float(args.get("x"))
        y = float(args.get("y"))
        z = float(args.get("z"))
        angle_deg = float(args["angle_deg"]) if "angle_deg" in args else None
        detections = args.get("detections", None)

        logger.info(f"MCP pick_and_place_object: {object_name} at ({x}, {y}, {z}) angle={angle_deg}")
        result = robot_control.run_mcp_pick_and_place(
            object_name, x, y, z, angle=angle_deg, detections=detections
        )
        return [TextContent(type="text", text=f"Completed: {result}")]

    if name == "move_to_coordinates":
        object_name = args.get("object_name", "cube")
        x = float(args.get("x"))
        y = float(args.get("y"))
        z = float(args.get("z"))
        angle_deg = float(args["angle_deg"]) if "angle_deg" in args else None

        logger.info(f"Compatibility move_to_coordinates -> pick_and_place_object: {object_name} at ({x}, {y}, {z}) angle={angle_deg}")
        result = robot_control.run_mcp_pick_and_place(object_name, x, y, z, angle=angle_deg)
        return [TextContent(type="text", text=f"Completed: {result}")]

    if name == "relocate_object":
        obstacle_name  = args.get("obstacle_name")
        obstacle_x     = float(args.get("obstacle_x"))
        obstacle_y     = float(args.get("obstacle_y"))
        obstacle_z     = float(args.get("obstacle_z", 0.0))
        obstacle_angle = float(args["obstacle_angle_deg"]) if "obstacle_angle_deg" in args else None
        detections     = args.get("detections", None)

        logger.info(
            f"MCP relocate_object: {obstacle_name} at "
            f"({obstacle_x}, {obstacle_y}, {obstacle_z}) angle={obstacle_angle}"
        )

        result = robot_control.run_mcp_relocate_object(
            obstacle_name  = obstacle_name,
            obstacle_x     = obstacle_x,
            obstacle_y     = obstacle_y,
            obstacle_z     = obstacle_z,
            obstacle_angle = obstacle_angle,
            detections     = detections,
        )

        # Robot signals it needs a fresh detection before Qwen plans next action.
        # Automatically trigger a verification photo via the Vision MCP's REST
        # endpoint.  The full detection list is returned so Qwen receives the
        # updated workspace state without managing the camera itself.
        if result.get("requires_redetection"):
            logger.info("Relocation complete — triggering fresh YOLO verification photo...")
            vision_result = call_vision_capture_and_detect()
            fresh_detections = vision_result.get("detections", [])
            result["fresh_detections"] = fresh_detections
            logger.info(
                f"Verification photo returned {len(fresh_detections)} detection(s): "
                f"{[d.get('object_name') for d in fresh_detections]}"
            )

        return [TextContent(type="text", text=f"Completed: {result}")]

    raise ValueError(f"Unknown tool: {name}")

# 4. SSE Networking Setup
sse = SseServerTransport("/messages")

async def handle_sse(request: Request):
    """Handles the initial Ethernet/SSE connection from server.js or MCP client."""
    async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())

async def handle_messages(request: Request):
    """Handles incoming tool call requests."""
    await sse.handle_post_message(request.scope, request.receive, request._send)

# 5. Bind routes
app = Starlette(routes=[
    Route("/sse", endpoint=handle_sse),
    Route("/messages", endpoint=handle_messages, methods=["POST"])
])

if __name__ == "__main__":
    logger.info("🦾 Robot Arm MCP Server listening on Ethernet port 8002...")
    logger.info("Tool: pick_and_place_object(object_name, x, y, z, angle_deg[optional])")
    uvicorn.run(app, host="0.0.0.0", port=8002)
