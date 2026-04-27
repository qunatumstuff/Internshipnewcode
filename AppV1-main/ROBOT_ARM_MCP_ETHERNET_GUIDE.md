# Robot Arm MCP over Ethernet — Full Guide

A complete reference for connecting your AI chatbot (`server.js`) to a physical robot arm over Ethernet using the Model Context Protocol (MCP) with SSE transport.

---

## Overview

Because your PC and the robot arm are **two separate physical devices**, you cannot use the standard `stdio` transport. Instead, you use **SSE (Server-Sent Events)** over Ethernet.

| | Your PC | Robot Device |
|---|---|---|
| **File** | `server.js` (existing) | `robot_mcp.py` (new) |
| **Role** | MCP Client | MCP Server |
| **Port** | 3000 | 8000 |
| **Transport** | SSE Client | SSE Server |

---

## Architecture Diagram

```
┌─────────────────────────┐        ┌──────────────────────────┐
│       YOUR PC           │        │      ROBOT DEVICE         │
│                         │        │                           │
│  ┌─────────────┐        │Ethernet│  ┌────────────────────┐  │
│  │  server.js  │────────┼──SSE──►│  │  robot_mcp.py      │  │
│  │             │◄───────┼────────│  │                    │  │
│  │  MCP Client │        │        │  │  list_tools()      │  │
│  │  callTool() │        │        │  │  ├ pick_up_item    │  │
│  └──────┬──────┘        │        │  │  └ reset_arm       │  │
│         │               │        │  │                    │  │
│         ▼               │        │  │  call_tool()       │  │
│  ┌─────────────┐        │        │  │  └ arm.grab() ◄──► │  │
│  │  GPT/Claude │        │        │  │    REAL MOTOR      │  │
│  └─────────────┘        │        │  └────────────────────┘  │
└─────────────────────────┘        └──────────────────────────┘
```

---

## How a Tool Call Flows (Step by Step)

```
1. User:        "Pick up the screwdriver"
       ↓
2. GPT:         decides → call pick_up_item({ target: "screwdriver" })
       ↓
3. server.js:   mcpRobotClient.callTool("pick_up_item", { target: "screwdriver" })
       ↓
4. Ethernet:    request sent to http://<ROBOT_IP>:8000/messages
       ↓
5. robot_mcp.py: handle_call_tool() fires → arm.grab("screwdriver") 🦾
       ↓
6. Result:      "Successfully grabbed screwdriver" returned to GPT
       ↓
7. GPT:         "I've picked up the screwdriver for you!"
```

---

## Comparison: Emoji MCP vs Robot Arm MCP

| | Emoji (existing) | Robot Arm (new) |
|---|---|---|
| Transport | **Stdio** (local pipe) | **SSE** (Ethernet) |
| Location | Same PC | Separate device |
| Server file | `mcp_emoji_server.py` | `robot_mcp.py` |
| Tools defined in | `mcp_emoji_server.py` | `robot_mcp.py` |
| Tools seen by GPT | Via `CLAUDE_TOOLS` in `server.js` | Auto-fetched via MCP |

---

## Step 1: Install Requirements on the Robot Device

SSH into your robot device and install the Python MCP SDK:

```bash
pip install mcp starlette uvicorn
```

---

## Step 2: Create `robot_mcp.py` on the Robot Device

Save this file **on the robot**, e.g. at `/home/pi/robot_mcp.py`.
Replace the placeholder hardware calls with your real robot SDK.

```python
import asyncio
from mcp.server import Server
from mcp.types import Tool, TextContent
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.requests import Request
import uvicorn

# 1. Initialize the MCP Server
server = Server("robot-arm-server")

# 2. Define tools that the AI can call
@server.list_tools()
async def handle_list_tools() -> list[Tool]:
    return [
        Tool(
            name="pick_up_item",
            description="Finds and picks up a requested object using the camera and arm.",
            inputSchema={
                "type": "object",
                "properties": {
                    "target_item": { "type": "string" }
                },
                "required": ["target_item"]
            }
        ),
        Tool(
            name="reset_arm",
            description="Resets the robotic arm to its home/default position.",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        Tool(
            name="grip",
            description="Opens or closes the gripper.",
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["open", "close"]
                    }
                },
                "required": ["action"]
            }
        )
    ]

# 3. Execute the tool when called by the AI
@server.call_tool()
async def handle_call_tool(name: str, arguments: dict | None) -> list[TextContent]:
    if name == "pick_up_item":
        item = (arguments or {}).get("target_item")

        # --- YOUR REAL ROBOT CODE GOES HERE ---
        # import my_robot_sdk
        # my_robot_sdk.find_and_grab(item)
        print(f"Hardware activating to grab: {item}")

        return [TextContent(type="text", text=f"Successfully grabbed the {item}!")]

    elif name == "reset_arm":
        # my_robot_sdk.go_home()
        print("Resetting arm to home position")
        return [TextContent(type="text", text="Arm reset to home position.")]

    elif name == "grip":
        action = (arguments or {}).get("action")
        # my_robot_sdk.grip(action)
        print(f"Gripper: {action}")
        return [TextContent(type="text", text=f"Gripper {action}ed.")]

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
    print("🤖 Robot Arm MCP Server listening for AI commands on port 8000...")
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

Run it on the robot:
```bash
python robot_mcp.py
```

---

## Step 3: Add SSE Client to `server.js` (Your PC)

Add the following to your existing `server.js`, alongside your existing `mcpEmojiClient`:

```js
const { SSEClientTransport } = require("@modelcontextprotocol/sdk/client/sse.js");

// === MCP Robot Arm Client (Ethernet/SSE) ===
let mcpRobotClient = null;

async function startRobotMcpClient() {
  try {
    const transport = new SSEClientTransport(
      new URL("http://192.168.1.XXX:8000/sse") // ← Replace with robot's Ethernet IP
    );
    mcpRobotClient = new Client(
      { name: "roboas-robot-arm", version: "1.0.0" },
      { capabilities: {} }
    );
    await mcpRobotClient.connect(transport);
    console.log("✅ Robot Arm MCP connected via Ethernet/SSE");
  } catch (err) {
    console.error("❌ Robot Arm MCP connection failed:", err.message);
  }
}
startRobotMcpClient();

// Helper to call a robot arm tool
async function callRobotTool(toolName, args, userQuestion = "") {
  if (!mcpRobotClient) return `Robot not connected.`;
  try {
    const result = await mcpRobotClient.callTool({ name: toolName, arguments: args });
    const output = result.content[0].text;
    logToolCall(userQuestion, toolName, args, output);
    return output;
  } catch (e) {
    console.error("❌ Robot Tool Error:", e.message);
    return `Failed to execute ${toolName}.`;
  }
}
```

---

## Step 4: Add Robot Tools to GPT Tool Definitions

In `server.js`, add robot tools to the tools array in `/ask-gpt`:

```js
tools: [
  // ... existing switch_avatar tool ...
  {
    type: "function",
    function: {
      name: "pick_up_item",
      description: "Commands the robot arm to pick up a specified object.",
      parameters: {
        type: "object",
        properties: { target_item: { type: "string" } },
        required: ["target_item"]
      }
    }
  },
  {
    type: "function",
    function: {
      name: "reset_arm",
      description: "Resets the robot arm to its home position.",
      parameters: { type: "object", properties: {} }
    }
  },
  {
    type: "function",
    function: {
      name: "grip",
      description: "Opens or closes the robot gripper.",
      parameters: {
        type: "object",
        properties: { action: { type: "string", enum: ["open", "close"] } },
        required: ["action"]
      }
    }
  }
],
```

Then handle them in the `tool_calls` block inside `/ask-gpt`:

```js
if (toolCall.function.name === "pick_up_item") {
  const result = await callRobotTool("pick_up_item", args, question);
  // push result to messages and do second GPT completion...
}
```

---

## Step 5: Find Your Robot's Ethernet IP

On the robot device, run:
```bash
# Linux / Raspberry Pi
ip addr show eth0
# or
hostname -I
```

Use that IP in Step 3 where it says `192.168.1.XXX`.

---

## Quick Checklist

- [ ] Install `mcp starlette uvicorn` on the robot device
- [ ] Create and run `robot_mcp.py` on the robot device
- [ ] Find the robot's Ethernet IP address
- [ ] Add SSE client to `server.js` with the correct IP
- [ ] Add robot tool definitions to the `/ask-gpt` tools array
- [ ] Handle robot tool calls in the `tool_calls` block
- [ ] Test: ask GPT "pick up the screwdriver" and watch terminal output on both devices

---

> **Note:** Tools are defined **on the robot** in `robot_mcp.py`. Your `server.js` just connects and forwards calls. The actual hardware logic never lives on the PC side.

> **Tip:** Use a **static IP** on the robot's Ethernet interface so the address never changes. Configure this in your router's DHCP reservation settings or directly on the robot OS.
