import asyncio
import logging
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

# 1. Initialize the MCP Server
server = Server("robot-arm-mcp-server")

# 2. Define tools that the AI can call
@server.list_tools()
async def handle_list_tools() -> list[Tool]:
    return [
        Tool(
            name="move_to_coordinates",
            description="Moves the robotic arm to specific X, Y, Z coordinates.",
            inputSchema={
                "type": "object",
                "properties": {
                    "x": {"type": "number"},
                    "y": {"type": "number"},
                    "z": {"type": "number"}
                },
                "required": ["x", "y", "z"]
            }
        ),
        Tool(
            name="grab",
            description="Opens or closes the robotic gripper.",
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["open", "close"]}
                },
                "required": ["action"]
            }
        ),
        Tool(
            name="avoid_obstacles_and_move",
            description="Calculates a safe path avoiding obstacles and moves to the target.",
            inputSchema={
                "type": "object",
                "properties": {
                    "target_coords": {
                        "type": "object",
                        "properties": {"x": {"type": "number"}, "y": {"type": "number"}, "z": {"type": "number"}}
                    },
                    "obstacles": {
                        "type": "array",
                        "items": {"type": "object"} # Accepts arbitrary obstacle objects
                    }
                },
                "required": ["target_coords"]
            }
        )
    ]

# 3. Execute the tool when called by the AI
@server.call_tool()
async def handle_call_tool(name: str, arguments: dict | None) -> list[TextContent]:
    if name == "move_to_coordinates":
        x = (arguments or {}).get("x")
        y = (arguments or {}).get("y")
        z = (arguments or {}).get("z")
        logger.info(f"Moving arm to coordinates: ({x}, {y}, {z})")
        
        # TODO: Insert real Robot SDK movement logic here
        return [TextContent(type="text", text=f"Successfully moved to ({x}, {y}, {z}).")]

    elif name == "grab":
        action = (arguments or {}).get("action", "close")
        logger.info(f"Gripper action: {action}")
        
        # TODO: Insert real Robot SDK gripper logic here
        return [TextContent(type="text", text=f"Gripper successfully {action}ed.")]

    elif name == "avoid_obstacles_and_move":
        target = (arguments or {}).get("target_coords")
        obstacles = (arguments or {}).get("obstacles", [])
        logger.info(f"Avoiding {len(obstacles)} obstacles to reach {target}")
        
        # TODO: Insert real path planning logic here
        return [TextContent(type="text", text="Safely avoided obstacles and reached target.")]

    else:
        raise ValueError(f"Unknown tool: {name}")

# 4. SSE Networking Setup
sse = SseServerTransport("/messages")

async def handle_sse(request: Request):
    """Handles the initial Ethernet connection from the PC"""
    async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())

async def handle_messages(request: Request):
    """Handles incoming tool call requests from server.js"""
    await sse.handle_post_message(request.scope, request.receive, request._send)

# 5. Bind routes
app = Starlette(routes=[
    Route("/sse", endpoint=handle_sse),
    Route("/messages", endpoint=handle_messages, methods=["POST"])
])

if __name__ == "__main__":
    logger.info("🦾 Robot Arm MCP Server listening on Ethernet port 8002...")
    uvicorn.run(app, host="0.0.0.0", port=8002)
