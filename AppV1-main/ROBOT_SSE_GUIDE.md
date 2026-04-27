# Model Context Protocol (MCP) over SSE for Robotics

This document explains how to set up an MCP Server natively on physical hardware (like a Raspberry Pi or Robotic Arm) so that an AI Client (like ChatGPT or Claude Desktop running on a laptop) can securely connect to it over a local Wi-Fi network.

## The Architecture
Because the AI (Laptop) and the hardware (Robot) are on two different physical computers, you cannot use the standard `stdio` (Standard Input/Output) transport. Instead, you must use **SSE (Server-Sent Events)**.

* **Laptop (AI Client):** Holds the API key mapped to the Robot's IP address.
* **Robot (MCP Server):** Runs the Python MCP script. Contains all the pre-built hardware functions.

## Step 1: Install Requirements on the Robot
Log into your robot and install the official Python MCP SDK with SSE and networking support:
```bash
pip install mcp starlette uvicorn
```

## Step 2: The Robot's MCP Server Code
Save this entirely on the robot (e.g., `robot_mcp.py`). It marries your pre-built robot Python code with the MCP Server.

```python
import asyncio
from mcp.server import Server
from mcp.types import Tool, TextContent
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.requests import Request
import uvicorn

# 1. Initialize the Server
server = Server("robot-arm-server")

# 2. Define your High-Level Tools for the AI
@server.list_tools()
async def handle_list_tools() -> list[Tool]:
    return [
        Tool(
            name="pick_up_item",
            description="Finds and picks up a requested object using the camera and arm.",
            inputSchema={
                "type": "object",
                "properties": {"target_item": {"type": "string"}},
                "required": ["target_item"]
            }
        )
    ]

# 3. Handle Tool Execution
@server.call_tool()
async def handle_call_tool(name: str, arguments: dict | None) -> list[TextContent]:
    if name == "pick_up_item":
        item = arguments.get("target_item")
        
        # --- TRIGGER YOUR PREBUILT PYTHON HARDWARE CODE HERE ---
        # import my_camera_code
        # my_camera_code.find_and_grab(item)
        print(f"Hardware activating to grab: {item}")
        
        return [TextContent(type="text", text=f"Successfully grabbed the {item}!")]

# ==========================================
# 4. Networking Setup (SSE Transport)
# ==========================================
sse = SseServerTransport("/messages")

async def handle_sse(request: Request):
    """Handles the initial Wi-Fi connection from the Laptop"""
    async with sse:
        await server.run(sse.read_stream(), sse.write_stream(), server.create_initialization_options())

async def handle_messages(request: Request):
    """Handles incoming tool requests from the Laptop"""
    await sse.handle_post_message(request.scope, request.receive, request._send)

# Bind the routes to Startlette to handle the web traffic
app = Starlette(routes=[
    Route("/sse", endpoint=handle_sse),
    Route("/messages", endpoint=handle_messages, methods=["POST"])
])

if __name__ == "__main__":
    # Listen on all IPs on your local Wi-Fi port 8000
    print("Robot MCP Server is listening for AI commands...")
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

## Step 3: Configure the AI Client (On the Laptop)
Now, tell the AI that the robot exists! Depending on how you use ChatGPT/OpenAI, you have two options.

### Option A: Using a Custom OpenAI (ChatGPT) Python Script
If you are writing your own custom Python script on the laptop to talk to ChatGPT, you use the MCP Client SDK to connect ChatGPT to the robot.

Install the MCP client tools on your laptop:
```bash
pip install mcp openai
```
Then use this inside your laptop's Python script to link ChatGPT to the robot:
```python
import asyncio
from mcp.client.sse import sse_client
from mcp.client.session import ClientSession
from openai import AsyncOpenAI

async def run_chatgpt_robot():
    # Connect to the Robot's IP address!
    async with sse_client("http://192.168.1.55:8000/sse") as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            
            # Fetch the tools the robot has available
            tools = await session.list_tools()
            
            # (Your ChatGPT processing logic goes here. 
            # You pass these 'tools' into your OpenAI API call, 
            # and if OpenAI says to use 'pick_up_item', you call it via session.call_tool())
            
asyncio.run(run_chatgpt_robot())
```

### Option B: Using an IDE or App (Cursor, Claude Desktop)
If you are using an IDE that supports OpenAI + MCP natively (like the Cursor Editor), or a generic MCP client app, you just plug the IP address into its configuration JSON file:

```json
{
  "mcpServers": {
    "my_robot": {
      "command": "npx",
      "args": [
        "-y", 
        "@modelcontextprotocol/client-sse", 
        "--url", 
        "http://192.168.1.55:8000/sse"
      ]
    }
  }
}
```
Whenever your ChatGPT script or app boots up, it will quietly connect to the robot, read its tools, and be permanently ready to physically manipulate the world on command.
