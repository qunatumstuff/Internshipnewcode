# Master Baseline Reference Checkpoint (Pre-LoRA Integration)

This all-in-one document serves as the absolute baseline reference checkpoint, containing the complete architectural design, documentation, guides, configuration files, and full source code of all active subsystems of the Roboas project.

---

## 1. System Architecture

# System Architecture & Ngrok Premium Justification Report

This document outlines the distributed architecture of the AI-driven Robotic Chatbot system (Roboas) and provides a technical justification for upgrading to **Ngrok Premium** for deployment and testing.

---

## 1. Visual System Architecture Diagram

Below is the generated system architecture diagram illustrating the network flow, component locations, and how Ngrok acts as the secure ingress gateway for client operations:

![System Architecture Diagram](file:///C:/Users/Dominic/.gemini/antigravity/brain/6c67177b-db98-4266-8e78-a0c226812ed1/system_architecture_1780647202182.png)

---

## 2. Interactive Flowchart (Mermaid)

This vector flowchart shows how the systems interface across WAN and local Ethernet subnets:

```mermaid
graph TD
    subgraph WAN [Public WAN (Internet)]
        Client["Operator UI (Flutter Web App)<br>- Voice Upload (WAV)<br>- Audio Playback (TTS)<br>- Emergency Stop Trigger"]
        Ngrok["Secure Ngrok TLS Tunnel<br>(HTTPS / WSS / TLS Edge)"]
    end

    subgraph LAN [Local Network (Ethernet/Wi-Fi)]
        LaptopA["Laptop A: Central Orchestrator (Port 3000)<br>- server.js (Node.js)<br>- LangChain Memory Vector Store<br>- Coordinate Transformation Matrix"]
        LaptopB["Laptop B: Vision Server (Port 8001)<br>- vision_mcp.py (FastMCP)<br>- YOLO Segmenter (best13.pt)<br>- Ollama Qwen3-VL Model"]
        RobotPC["Robot PC: Controller (Port 8002)<br>- robot_mcp.py (MCP SSE)<br>- Neura Robotics SDK"]
        
        Camera["Intel RealSense Depth Camera"]
        Arm["Neura LARA 5 Robot Arm (IP: 192.168.2.13)"]
    end

    %% Flow links
    Client ===>|Secure HTTPS / WSS| Ngrok
    Ngrok ===>|Tunnel Forwarding| LaptopA
    
    LaptopA ===>|SSE Client Transport (Port 8001)| LaptopB
    LaptopA ===>|SSE Client Transport (Port 8002)| RobotPC
    
    LaptopB --->|Hardware Pipeline| Camera
    RobotPC --->|Direct SDK Connection| Arm
    
    %% Styling
    style Client fill:#3a0ca3,stroke:#7209b7,stroke-width:2px,color:#fff
    style Ngrok fill:#f72585,stroke:#b5179e,stroke-width:2px,color:#fff
    style LaptopA fill:#240046,stroke:#3c096c,stroke-width:2px,color:#fff
    style LaptopB fill:#03045e,stroke:#0077b6,stroke-width:2px,color:#fff
    style RobotPC fill:#004b23,stroke:#38b000,stroke-width:2px,color:#fff
    style Camera fill:#0096c7,stroke:#03045e,stroke-width:1px,color:#fff
    style Arm fill:#70e000,stroke:#007200,stroke-width:1px,color:#fff
```

---

## 3. Security & Encryption: Does Ngrok Encrypt Traffic?

**Yes. Ngrok fully encrypts all communication.**

When you run `ngrok http 3000`, Ngrok exposes a secure public endpoint (e.g., `https://your-session.ngrok-free.app`). 

* **End-to-End TLS Encryption:** All connections initiated by the operator's browser to the Ngrok edge are secured with high-grade **SSL/TLS (HTTPS and WSS)**. No credentials, voice payloads, or robotic command packets are exposed in transit over public networks.
* **Firewall Penetration:** Ngrok opens an outbound TCP tunnel from Laptop A to the Ngrok Edge. This means you do not need to open inbound ports or modify router settings on your local network (which is often blocked by strict corporate firewalls).

---

## 4. Why We Must Buy Ngrok Premium

While the free tier of Ngrok works for basic hello-world APIs, it is **unusable and highly unsafe** for testing and deploying a real-time physical robotic system. Below is the technical justification to convince your supervisor:

### A. Critical Bandwidth Throttling (The Vision & Audio Bottleneck)
* **The Problem:** The system streams high-bandwidth payloads. Speech prompts are recorded as raw WAV audio files and uploaded to the server, while log streams transmit coordinates and diagnostic data. If we add video feedback or crop image transmission from Laptop B's camera, the data requirements spike.
* **Free Tier Limit:** Ngrok's free tier has strict limits on throughput and monthly data transfer (typically **1GB per month**). A single afternoon of active testing and sending voice/image packets will exceed this limit, causing Ngrok to instantly suspend the tunnel.
* **Premium Benefit:** Unlimited/high-throughput bandwidth guarantees that testing sessions never freeze due to data caps.

### B. Connection Rate Limits (120 Connections/Min Cap)
* **The Problem:** This system is highly asynchronous. The web application maintains a persistent WebSocket connection for real-time status updates and makes constant REST API requests (status polls, tool executions, emergency stop triggers, log retrievals). 
* **Free Tier Limit:** The free plan imposes a strict rate limit of **120 requests/minute**. If the frontend makes parallel status queries and sends continuous audio or log chunks, the browser will exceed this limit in minutes. Once throttled, the frontend loses connection, causing the robot controls to drop or fail mid-task.
* **Premium Benefit:** Removes rate-limiting constraints, ensuring seamless, low-latency, real-time message delivery.

### C. The Safety & Security Hazard (Critical):
* **The Problem:** A physical robot arm (like the LARA 5) is heavy machinery capable of causing physical injury or property damage if operated incorrectly. The free tier of Ngrok creates a **completely public URL** accessible by anyone on the internet who guesses or intercepts it.
* **Free Tier Limit:** Free Ngrok links do not allow advanced authentication at the edge. Anyone loading the URL can press the control buttons, upload malicious PDFs, or trigger robot motions.
* **Premium Benefit:** Enforces **Edge-level OAuth (e.g., Google or Microsoft SSO)** and **IP Whitelisting**. Only authorized developers and operators can access the chatbot control interface. Requests from unauthorized IPs are rejected at the Ngrok servers before they ever reach your local laptop or robot.

### D. Ephemeral URLs vs. Reserved Static Domains
* **The Problem:** Every time the free Ngrok tunnel restarts, it generates a random URL (e.g., `https://a1b2-34-56.ngrok-free.app`). 
* **Free Tier Limit:** Because the URL changes daily (or on connection drop), the developer must rebuild the Flutter Web app (`flutter build web --release`), copy the build to the Node `Public/` folder, and reconfigure the local MCP configs with the new URL. This wastes hours of engineering time and makes it impossible to bookmark the testing page or share a stable link with stakeholders.
* **Premium Benefit:** Provides a **Reserved Static Domain** (e.g., `https://our-robot-project.ngrok.app`). The URL never changes, meaning the Flutter app and server scripts can be hardcoded once, providing a clean, professional "always-on" demo portal.

---

## 5. Technical Summary for Supervisor Review

| Feature | Free Tier | Premium Tier | Impact on Robotics Project |
| :--- | :--- | :--- | :--- |
| **Bandwidth** | Strict monthly limit (1GB) | Unlimited / High-Throughput | Free tier halts operations mid-run when audio/vision logs exceed the cap. |
| **Rate Limiting** | 120 connections / min | Unrestricted | Free tier drops active WebSockets/SSE connections, severing control of the arm. |
| **Endpoint URL** | Randomly changes on restart | Permanent, Reserved Static Domain | Free tier requires daily re-compilation of Flutter Web assets; Premium is set-and-forget. |
| **Access Security** | None (Publicly exposed) | OAuth (Google/Github), IP Whitelisting | **Safety Critical:** Premium blocks unauthorized users from triggering physical arm movements. |
| **Support for TCP/SSE** | Basic | Enhanced | Ensures SSE message streams remain open indefinitely without timeout. |


---

## 2. Project Files, Documentation & Core Subsystem Source Code

Click on any section header below to expand and view the full file contents:

<details>
<summary>📂 <b>README.md</b> (Click to expand)</summary>

```markdown
# Roboas

A Flutter-based AI Chatbot integrated with a Computer Vision system and a Robotic Arm via the Model Context Protocol (MCP).

## Multi-Server Robot & Vision Architecture (MCP)

This outlines the system architecture where the **Camera (Vision)** and the **Robotic Arm (Kinematics)** are operating as two completely separate MCP servers. 

In this setup, the LLM acts as the central intelligence orchestrator, chaining the two services together.

### High-Level Architecture Diagram

```mermaid
graph TD
    %% User and Frontend
    User((User))
    FlutterApp[Chatbot UI\nFlutter App]

    %% Main Server (Orchestrator)
    subgraph "PC / Main Server"
        ServerJS[Orchestration Server\nserver.js]
        LLM[LLM Engine\nGPT-4]
        
        %% Multiple MCP Clients
        MCP_Client_Vision[MCP Client 1\nVision]
        MCP_Client_Arm[MCP Client 2\nRobot Arm]
    end

    %% Edge Device 1: Vision
    subgraph "Vision Subsystem"
        VisionMCP[Camera MCP Server\nvision_mcp.py]
        Camera[Camera / YOLO Object Detection]
    end

    %% Edge Device 2: Robot Arm
    subgraph "Kinematics Subsystem"
        ArmMCP[Robot Arm MCP Server\nrobot_mcp.py]
        ArmHardware[Robotic Arm Hardware SDK]
    end

    %% Connections
    User -->|Voice/Text| FlutterApp
    FlutterApp -->|HTTP/REST| ServerJS
    ServerJS <-->|API Calls| LLM
    
    %% Internal Client Bindings
    ServerJS --- MCP_Client_Vision
    ServerJS --- MCP_Client_Arm
    
    %% External MCP Connections (Ethernet/SSE)
    MCP_Client_Vision <-->|SSE Transport| VisionMCP
    MCP_Client_Arm <-->|SSE Transport| ArmMCP
    
    %% Hardware bindings
    VisionMCP <--> Camera
    ArmMCP <--> ArmHardware
```

---

### Network & Port Assignments (Ethernet SSE)

Because the devices communicate over a physical Ethernet network, they must use the **SSE (Server-Sent Events) transport** instead of standard `stdio`. 

The following ports and IP addresses should be configured for the connection:

*   **PC / Orchestration Server (`server.js`)**
    *   **IP:** `192.168.1.100` (Static IP recommended)
    *   **Port:** `3000` (Listens for Flutter App API calls)
*   **Camera Vision Subsystem (`vision_mcp.py`)**
    *   **IP:** `192.168.1.101` (Static IP recommended)
    *   **Port:** `8001` (Listens for SSE connections on `0.0.0.0`)
*   **Robot Arm Kinematics Subsystem (`robot_mcp.py`)**
    *   **IP:** `192.168.1.102` (Static IP recommended)
    *   **Port:** `8002` (Listens for SSE connections on `0.0.0.0`)

---

### Component Roles

#### 1. Main Server (`server.js`)
`server.js` instantiates **two** separate MCP clients (one for the camera, one for the arm). It aggregates the tools from both servers and presents them to the LLM.

#### 2. Camera MCP Server (`vision_mcp.py`)
A standalone MCP server dedicated entirely to computer vision. 
- **Exposes Tools:** `locate_object(target_name)`, `scan_obstacles()`
- **Output:** Returns spatial data (e.g., X,Y,Z coordinates and bounding boxes).

#### 3. Robot Arm MCP Server (`robot_mcp.py`)
A standalone MCP server dedicated to moving motors.
- **Exposes Tools:** `move_to_coordinates(x, y, z)`, `grab()`, `avoid_obstacles(obstacle_list)`
- **Output:** Returns hardware execution statuses (success/fail).

---

### The "Chain of Tools" Flow (Step-by-Step)

Because the Camera and Arm are separate, the **LLM acts as the orchestrator** linking them together. This requires a multi-step "chain of thought" from the AI.

1. **User:** *"Pick up the screwdriver."*
2. **LLM (Step 1):** Realizes it needs to know where the screwdriver is first.
   - **Action:** Calls the Camera Tool: `locate_object({ target: "screwdriver" })`.
3. **Camera MCP:** Captures the frame, runs detection, and returns the data: 
   - `{"found": true, "coordinates": [120, 45, 10], "obstacles": [{"type": "cup", "coords": [100, 30, 0]}]}`
4. **LLM (Step 2):** Receives the coordinates. Now it knows exactly where to move the arm.
   - **Action:** Calls the Arm Tool: `move_to_coordinates({ x: 120, y: 45, z: 10, obstacles: [...] })`.
5. **Robot Arm MCP:** Calculates the path avoiding the cup, moves the arm to `[120, 45, 10]`, and executes the grab. It returns:
   - `{"status": "success"}`
6. **LLM (Final):** Looks at the success message and responds to the user:
   - *"I've used the camera to locate the screwdriver and successfully commanded the arm to pick it up!"*

---

## Instructions for Future AI Implementation

If you (an AI assistant) are tasked with implementing this architecture in `server.js` in the future, follow these exact steps:

1.  **Add SSE Dependencies:** Ensure `SSEClientTransport` from `@modelcontextprotocol/sdk/client/sse.js` is imported in `server.js`.
2.  **Initialize SSE Clients:** Create two separate `SSEClientTransport` connections pointing to the static IPs and Ports of the Vision and Robot Arm MCP servers (e.g., `http://192.168.1.101:8001/sse` and `http://192.168.1.102:8002/sse`).
3.  **Define the Tools:** Inject the JSON schemas for `locate_object`, `scan_obstacles`, `move_to_coordinates`, `grab`, and `avoid_obstacles_and_move` into the `tools` array of the `openai.createChatCompletion` call in the `/ask-gpt` endpoint.
4.  **Handle Tool Calls:** In the `if (responseMessage.tool_calls)` block, add routing logic to check `toolCall.function.name` and forward the specific tool call to the correct MCP client (`mcpVisionClient.callTool` or `mcpArmClient.callTool`).
5.  **Chain the Responses:** Ensure the result from the Vision MCP (like the coordinates) is successfully appended to the GPT `messages` array as a "tool" role message, and trigger a second `createChatCompletion` so the LLM can immediately read the coordinates and call the Arm MCP.

---

## Flutter App Getting Started

This project is a starting point for a Flutter application.

A few resources to get you started if this is your first Flutter project:

- [Lab: Write your first Flutter app](https://docs.flutter.dev/get-started/codelab)
- [Cookbook: Useful Flutter samples](https://docs.flutter.dev/cookbook)

For help getting started with Flutter development, view the
[online documentation](https://docs.flutter.dev/), which offers tutorials,
samples, guidance on mobile development, and a full API reference.

```

</details>

---

<details>
<summary>📂 <b>ROBOT_ARM_MCP_ETHERNET_GUIDE.md</b> (Click to expand)</summary>

```markdown
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

```

</details>

---

<details>
<summary>📂 <b>ROBOT_SSE_GUIDE.md</b> (Click to expand)</summary>

```markdown
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

```

</details>

---

<details>
<summary>📂 <b>pubspec.yaml</b> (Click to expand)</summary>

```yaml
name: appver
description: "A new Flutter project."
# The following line prevents the package from being accidentally published to
# pub.dev using `flutter pub publish`. This is preferred for private packages.
publish_to: 'none' # Remove this line if you wish to publish to pub.dev

# The following defines the version and build number for your application.
# A version number is three numbers separated by dots, like 1.2.43
# followed by an optional build number separated by a +.
# Both the version and the builder number may be overridden in flutter
# build by specifying --build-name and --build-number, respectively.
# In Android, build-name is used as versionName while build-number used as versionCode.
# Read more about Android versioning at https://developer.android.com/studio/publish/versioning
# In iOS, build-name is used as CFBundleShortVersionString while build-number is used as CFBundleVersion.
# Read more about iOS versioning at
# https://developer.apple.com/library/archive/documentation/General/Reference/InfoPlistKeyReference/Articles/CoreFoundationKeys.html
# In Windows, build-name is used as the major, minor, and patch parts
# of the product and file versions while build-number is used as the build suffix.
version: 1.0.0+1

environment:
  sdk: ^3.10.8

# Dependencies specify other packages that your package needs in order to work.
# To automatically upgrade your package dependencies to the latest versions
# consider running `flutter pub upgrade --major-versions`. Alternatively,
# dependencies can be manually updated by changing the version numbers below to
# the latest version available on pub.dev. To see which dependencies have newer
# versions available, run `flutter pub outdated`.
dependencies:
  flutter:
    sdk: flutter

  # The following adds the Cupertino Icons font to your application.
  # Use with the CupertinoIcons class for iOS style icons.
  cupertino_icons: ^1.0.8
  http: ^1.6.0
  video_player: ^2.11.1
  record: ^5.1.0
  file_picker: ^10.3.10
  path_provider: ^2.1.5
  http_parser: ^4.1.2
  path: ^1.9.0
  google_fonts: ^8.1.0
  flutter_markdown: ^0.7.7+1
  web_socket_channel: ^3.0.1

dev_dependencies:
  flutter_test:
    sdk: flutter

  # The "flutter_lints" package below contains a set of recommended lints to
  # encourage good coding practices. The lint set provided by the package is
  # activated in the `analysis_options.yaml` file located at the root of your
  # package. See that file for information about deactivating specific lint
  # rules and activating additional ones.
  flutter_lints: ^6.0.0

# For information on the generic Dart part of this file, see the
# following page: https://dart.dev/tools/pub/pubspec

# The following section is specific to Flutter packages.
flutter:

  # The following line ensures that the Material Icons font is
  # included with your application, so that you can use the icons in
  # the material Icons class.
  uses-material-design: true

  assets:
    - assets/john_talking.mp4
    - assets/johnidle.mp4
    - assets/johnthinking.mp4
    - assets/linda_talking.mp4
    - assets/lindaidle.mp4
    - assets/lindathinking.mp4
    - assets/singaporepoly.png
    - assets/inbgsplogo.png
  #   - images/a_dot_ham.jpeg

  # An image asset can refer to one or more resolution-specific "variants", see
  # https://flutter.dev/to/resolution-aware-images

  # For details regarding adding assets from package dependencies, see
  # https://flutter.dev/to/asset-from-package

  # To add custom fonts to your application, add a fonts section here,
  # in this "flutter" section. Each entry in this list should have a
  # "family" key with the font family name, and a "fonts" key with a
  # list giving the asset and other descriptors for the font. For
  # example:
  # fonts:
  #   - family: Schyler
  #     fonts:
  #       - asset: fonts/Schyler-Regular.ttf
  #       - asset: fonts/Schyler-Italic.ttf
  #         style: italic
  #   - family: Trajan Pro
  #     fonts:
  #       - asset: fonts/TrajanPro.ttf
  #       - asset: fonts/TrajanPro_Bold.ttf
  #         weight: 700
  #
  # For details regarding fonts from package dependencies,
  # see https://flutter.dev/to/font-from-package



```

</details>

---

<details>
<summary>📂 <b>analysis_options.yaml</b> (Click to expand)</summary>

```yaml
# This file configures the analyzer, which statically analyzes Dart code to
# check for errors, warnings, and lints.
#
# The issues identified by the analyzer are surfaced in the UI of Dart-enabled
# IDEs (https://dart.dev/tools#ides-and-editors). The analyzer can also be
# invoked from the command line by running `flutter analyze`.

# The following line activates a set of recommended lints for Flutter apps,
# packages, and plugins designed to encourage good coding practices.
include: package:flutter_lints/flutter.yaml

linter:
  # The lint rules applied to this project can be customized in the
  # section below to disable rules from the `package:flutter_lints/flutter.yaml`
  # included above or to enable additional rules. A list of all available lints
  # and their documentation is published at https://dart.dev/lints.
  #
  # Instead of disabling a lint rule for the entire project in the
  # section below, it can also be suppressed for a single line of code
  # or a specific dart file by using the `// ignore: name_of_lint` and
  # `// ignore_for_file: name_of_lint` syntax on the line or in the file
  # producing the lint.
  rules:
    # avoid_print: false  # Uncomment to disable the `avoid_print` rule
    # prefer_single_quotes: true  # Uncomment to enable the `prefer_single_quotes` rule

# Additional information about this file can be found at
# https://dart.dev/guides/language/analysis-options

```

</details>

---

<details>
<summary>📂 <b>roboas/package.json</b> (Click to expand)</summary>

```json
{
  "name": "pdf-qa-server",
  "version": "1.0.0",
  "description": "PDF Question Answering Server with OpenAI",
  "main": "server.js",
  "scripts": {
    "start": "node server.js",
    "dev": "nodemon server.js"
  },
  "dependencies": {
    "@anthropic-ai/sdk": "^0.80.0",
    "@langchain/openai": "^0.0.13",
    "@modelcontextprotocol/sdk": "^1.28.0",
    "cors": "^2.8.5",
    "dotenv": "^17.4.2",
    "duck-duck-scrape": "^2.2.7",
    "express": "^4.21.2",
    "langchain": "^0.0.206",
    "multer": "^1.4.5-lts.1",
    "openai": "^3.3.0",
    "pdf-parse": "^1.1.1",
    "serialport": "^13.0.0",
    "ws": "^8.21.0"
  },
  "engines": {
    "node": ">=18.0.0"
  }
}

```

</details>

---

<details>
<summary>📂 <b>roboas/claude_desktop_config.json</b> (Click to expand)</summary>

```json
{
  "mcpServers": {
    "roboas-debugger": {
      "command": "python",
      "args": [
        "c:/Users/Dominic/Downloads/AppV1-main/AiChatbot/AppV1-main/roboas/mcp_debugger_server.py"
      ]
    },
    "roboas-emoji-server": {
      "command": "python",
      "args": [
        "c:/Users/Dominic/Downloads/AppV1-main/AiChatbot/AppV1-main/roboas/mcp_emoji_server.py"
      ]
    }
  }
}

```

</details>

---

<details>
<summary>📂 <b>lib/screens/chatbot_screen.dart</b> (Click to expand)</summary>

```dart
import 'dart:async';
import 'dart:convert';
import 'dart:html' as html;
import 'dart:js' as js;
import 'dart:math' as math;
import 'dart:typed_data';
import 'dart:web_audio' as wa;

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:http_parser/http_parser.dart';
import 'package:record/record.dart';
import 'package:video_player/video_player.dart';

// ─── Data ────────────────────────────────────────────────────────────────────

enum AvatarState { idle, thinking, talking }

class ChatMessage {
  final String text;
  final bool isUser;
  ChatMessage({required this.text, required this.isUser});
}

// ─── Widget ───────────────────────────────────────────────────────────────────

enum HandsOffState {
  handsOffOff,
  wakewordListening,
  userRecording,
  transcribing,
  johnSpeaking,
  restarting
}

class ChatbotScreen extends StatefulWidget {
  const ChatbotScreen({super.key});

  @override
  State<ChatbotScreen> createState() => _ChatbotScreenState();
}

class _ChatbotScreenState extends State<ChatbotScreen>
    with TickerProviderStateMixin {
  // ── Audio ──
  final _audioRecorder = AudioRecorder();
  html.AudioElement? _audioElement;
  bool _isTtsInProgress = false;
  bool _isCcEnabled = true;
  String? _currentSubtitleText;

  // ── Video ──
  VideoPlayerController? _videoController;
  final Map<String, VideoPlayerController> _cachedControllers = {};
  bool _isVideoInitialized = false;
  AvatarState _avatarState = AvatarState.talking; // start on talking
  String _loadedVideoPersona = 'john'; // tracks which persona the current video belongs to
  bool _isSwitchingVideo = false;
  AvatarState? _pendingAvatarState;
  bool _pendingForce = false;

  // ── UI ──
  final TextEditingController _textController = TextEditingController();
  final ScrollController _scrollController = ScrollController();
  final ScrollController _subtitleScrollController = ScrollController();
  bool _isListening = false;
  bool _isMenuVisible = false;

  // ── Hands-free / wake-word ──
  HandsOffState _currentState = HandsOffState.handsOffOff;
  html.WebSocket? _wakeWordSocket;
  html.MediaStream? _manualWebStream;
  String _wakeWsStatus = 'disconnected'; // connected | connecting | disconnected | error
  bool _wakeWsReconnecting = false;      // prevents overlapping reconnect attempts
  bool _isManualRestarting = false;

  // Real-time diagnostics & custom thresholds
  double _lastRms = 0.0;
  double _lastJohnScore = 0.0;
  double _lastJohnV2Score = 0.0;
  double _lastLindaScore = 0.0;
  double _lastLindaV2Score = 0.0;
  double _lastJohnPeak = 0.0;
  double _lastJohnV2Peak = 0.0;
  double _lastLindaPeak = 0.0;
  double _lastLindaV2Peak = 0.0;
  double _johnThresh = 0.001;
  double _lindaThresh = 0.0009;
  double _lastCps = 0.0;
  String _lastCtx = 'none';
  String _lastTrack = 'none';
  bool _lastTrackEnabled = false;
  final TextEditingController _johnThreshController = TextEditingController(text: '0.001');
  final TextEditingController _lindaThreshController = TextEditingController(text: '0.0009');
  bool _showDebugPanel = false;


  // ── Python Audio Server mode ──
  // Set to true to use Python mic. Set to false to fallback to browser mic.
  static const bool USE_PYTHON_AUDIO = false;
  bool _audioServerConnected = false;

  // ── VAD Silence Detection ──
  html.MediaStream? _vadStream;
  wa.AudioContext? _vadAudioContext;
  wa.AnalyserNode? _vadAnalyser;
  Timer? _vadTimer;

  // ── Persona ──
  String _currentPersona = 'john';
  String get _pName => _currentPersona == 'linda' ? 'Linda' : 'John';
  html.SpeechSynthesisUtterance? _currentUtterance;
  bool get _isInteractionBlocked {
    bool isMoving = false;
    try {
      isMoving = js.context['isRobotMoving'] == true;
    } catch (_) {}
    return _isTtsInProgress ||
        _avatarState == AvatarState.thinking ||
        (_messages.isNotEmpty && _messages.last.text == '__THINKING__') ||
        isMoving;
  }

  String get _visibleStateText {
    switch (_currentState) {
      case HandsOffState.handsOffOff:
        return 'Paused';
      case HandsOffState.wakewordListening:
        return 'Listening (RMS: ${_lastRms.toStringAsFixed(4)}, J: ${_lastJohnScore.toStringAsFixed(2)} [P: ${_lastJohnPeak.toStringAsFixed(2)}], L: ${_lastLindaScore.toStringAsFixed(2)} [P: ${_lastLindaPeak.toStringAsFixed(2)}])';
      case HandsOffState.userRecording:
      case HandsOffState.transcribing:
        return 'Recording';
      case HandsOffState.johnSpeaking:
        return 'Speaking';
      case HandsOffState.restarting:
        return 'Restarting';
    }
  }

  // ── Emojis ──
  String _answeringEmoji = '🤖';
  String _idleEmoji = '🤗';

  // ── Messages ──
  final List<ChatMessage> _messages = [];

  // ── Mic Recording Stream ──
  html.MediaRecorder? _webMediaRecorder;
  List<html.Blob> _webAudioChunks = [];

  // ── Mic Debug Logs ──
  final List<String> _micDebugLogs = [];

  void _addUiLog(String log) {
    debugPrint(log);
    if (mounted) {
      setState(() {
        _micDebugLogs.add(log);
        if (_micDebugLogs.length > 15) _micDebugLogs.removeAt(0); // keep last 15
      });
    }
  }

  void _showStatusSnackBar(String message, {bool isError = false}) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).hideCurrentSnackBar();
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(
          message,
          style: const TextStyle(color: Colors.white, fontWeight: FontWeight.bold),
        ),
        backgroundColor: isError ? Colors.red[800] : Colors.green[800],
        duration: const Duration(seconds: 3),
      ),
    );
  }


  // ── Visualizer animation ──
  late AnimationController _vizController;

  // ── URL ──
  static const bool kIsWeb = identical(0, 0.0);
  String get baseUrl => kIsWeb ? '' : 'http://localhost:3000';

  // ─────────────────────────────────────────────────────────────────────────
  //  Lifecycle
  // ─────────────────────────────────────────────────────────────────────────

  @override
  void initState() {
    super.initState();
    _vizController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 500),
    )..repeat(reverse: true);
    _initializeAll();
  }

  @override
  void dispose() {
    _vizController.dispose();
    _audioRecorder.dispose();
    _videoController?.dispose();
    for (final controller in _cachedControllers.values) {
      controller.dispose();
    }
    _cachedControllers.clear();
    _textController.dispose();
    _scrollController.dispose();
    _subtitleScrollController.dispose();
    _johnThreshController.dispose();
    _lindaThreshController.dispose();
    _audioElement?.pause();
    _audioElement = null;
    debugPrint('⚠️ [DEBUG] Calling _wakeWordSocket?.close() from dispose()');
    _wakeWordSocket?.close();
    super.dispose();
  }

  // ─────────────────────────────────────────────────────────────────────────
  //  Init
  // ─────────────────────────────────────────────────────────────────────────

  Future<void> _initializeAll() async {
    await _fetchEmojis();
    await _initRecorder();
    // Load talking video first so it's ready immediately
    await _loadVideo(AvatarState.talking);
    const welcome =
        "Welcome! I'm John, your robotic assistant. How can I help you today?";
    setState(() {
      _messages.add(ChatMessage(
          text: 'John($_idleEmoji): $welcome', isUser: false));
    });
    _speak(welcome);
  }

  Future<void> _fetchEmojis() async {
    try {
      final res = await http.get(Uri.parse('$baseUrl/status-emojis'));
      if (res.statusCode == 200) {
        final data = json.decode(res.body);
        if (mounted) {
          setState(() {
            _answeringEmoji = data['answering'] ?? '🤖';
            _idleEmoji = data['idle'] ?? '🤗';
          });
        }
      }
    } catch (e) {
      debugPrint('Emoji fetch failed: $e');
    }
  }

  Future<void> _initRecorder() async {
    try {
      await _audioRecorder.hasPermission();
    } catch (e) {
      debugPrint('Recorder init error: $e');
    }
  }

  // ─────────────────────────────────────────────────────────────────────────
  //  Video state machine
  // ─────────────────────────────────────────────────────────────────────────

  String _assetFor(String persona, AvatarState state) {
    if (persona == 'linda') {
      switch (state) {
        case AvatarState.idle:
          return 'assets/lindaidle.mp4';
        case AvatarState.thinking:
          return 'assets/lindathinking.mp4';
        case AvatarState.talking:
          return 'assets/linda_talking.mp4';
      }
    } else {
      switch (state) {
        case AvatarState.idle:
          return 'assets/johnidle.mp4';
        case AvatarState.thinking:
          return 'assets/johnthinking.mp4';
        case AvatarState.talking:
          return 'assets/john_talking.mp4';
      }
    }
  }

  Future<void> _preInitPersonaVideos() async {
    final states = [AvatarState.idle, AvatarState.thinking, AvatarState.talking];
    for (final state in states) {
      final asset = _assetFor(_currentPersona, state);
      if (!_cachedControllers.containsKey(asset)) {
        debugPrint('🎬 [VIDEO] Pre-initializing $state for $_currentPersona -> $asset');
        final controller = VideoPlayerController.asset(asset);
        await controller.initialize();
        await controller.setVolume(0);
        await controller.setLooping(true);
        
        // Bulletproof fix for browser auto-pausing the video AND broken web looping
        controller.addListener(() {
          if (!mounted) return;
          final bool isAtEnd = controller.value.isInitialized && 
                               controller.value.duration > Duration.zero &&
                               controller.value.position >= controller.value.duration;
                               
          if (!controller.value.isPlaying && asset.contains(_currentPersona)) {
            if (isAtEnd) {
              controller.seekTo(Duration.zero);
            }
            controller.play().catchError((_) {});
          }
        });
        
        _cachedControllers[asset] = controller;
      }
    }
  }

  Future<void> _loadVideo(AvatarState state, {bool force = false}) async {
    if (force) {
      _pendingForce = true;
    }
    if (_isSwitchingVideo) {
      _pendingAvatarState = state;
      debugPrint('🎬 [VIDEO] Queued pending state: $state (switching in progress)');
      return;
    }
    final useForce = force || _pendingForce;
    _pendingForce = false;

    // Skip reload if same state, same persona, already initialized, and not forced
    if (_avatarState == state && _loadedVideoPersona == _currentPersona && _isVideoInitialized && !useForce) {
      debugPrint('🎬 [VIDEO] Skipping reload - already on $state for $_currentPersona');
      return;
    }
    _isSwitchingVideo = true;
    _pendingAvatarState = null;

    try {
      // Ensure all 3 videos for the active persona are fully initialized and cached!
      await _preInitPersonaVideos();

      final targetAsset = _assetFor(_currentPersona, state);
      final controller = _cachedControllers[targetAsset]!;

      // Keep all 3 videos for the active persona playing to prevent CanvasKit freeze
      final states = [AvatarState.idle, AvatarState.thinking, AvatarState.talking];
      for (final s in states) {
        final asset = _assetFor(_currentPersona, s);
        final c = _cachedControllers[asset];
        if (c != null && !c.value.isPlaying) {
          c.play();
        }
      }

      if (mounted) {
        setState(() {
          _videoController = controller;
          _avatarState = state;
          _loadedVideoPersona = _currentPersona;
          _isVideoInitialized = true;
        });
      }
      debugPrint('🎬 [VIDEO] ✅ Now playing: $state for $_currentPersona');

      // Pause controllers for the OTHER persona to save resources
      _cachedControllers.forEach((key, c) {
        if (!key.contains(_currentPersona)) {
          c.pause();
        }
      });
    } catch (e) {
      debugPrint('🎬 [VIDEO] ❌ Error loading ($state): $e');
    } finally {
      _isSwitchingVideo = false;
      // If a state switch request arrived while we were loading, handle it now
      if (_pendingAvatarState != null) {
        final nextState = _pendingAvatarState!;
        _pendingAvatarState = null;
        debugPrint('🎬 [VIDEO] Processing pending state: $nextState');
        await _loadVideo(nextState, force: _pendingForce);
      }
    }
  }

  Future<void> _setAvatarState(AvatarState state) async {
    // Always reload if persona changed
    final personaChanged = _loadedVideoPersona != _currentPersona;
    if (_avatarState == state && !personaChanged && _isVideoInitialized) return;
    await _loadVideo(state, force: personaChanged);
  }

  // ─────────────────────────────────────────────────────────────────────────
  //  TTS / Stop
  // ─────────────────────────────────────────────────────────────────────────

  /// Send a mute/unmute command to the wake word server so it doesn't
  /// trigger on the robot's own voice.
  void _setWakeWordMute(bool muted) {
    _addUiLog('[OWW] setWakeWordMuted: $muted');
    try {
      js.context.callMethod('setWakeWordMuted', [muted]);
    } catch (e) {
      _addUiLog('[OWW] failed to setWakeWordMuted: $e');
    }
    if (!muted) {
      if (_currentState != HandsOffState.handsOffOff) {
        _changeState(HandsOffState.wakewordListening);
      }
    }
  }

  void _stopManualWebStream() {
    if (_manualWebStream != null) {
      try {
        _manualWebStream!.getTracks().forEach((track) => track.stop());
        _addUiLog('[MIC] Manual stream tracks stopped.');
      } catch (e) {
        _addUiLog('[MIC] Error stopping manual stream tracks: $e');
      }
      _manualWebStream = null;
    }
  }

  Future<void> _speak(String text) async {
    if (text.isEmpty) return;
    
    if (_currentState != HandsOffState.handsOffOff) {
      _changeState(HandsOffState.johnSpeaking);
    }
    
    _setWakeWordMute(true); // Mute wake word while speaking
    if (mounted) {
      setState(() {
        _isTtsInProgress = true;
        _currentSubtitleText = text;
      });
    }
    await _setAvatarState(AvatarState.talking);

    try {
      // Kill any previous audio/speech synthesis sessions
      _audioElement?.pause();
      _audioElement?.src = '';
      try {
        html.window.speechSynthesis?.cancel();
      } catch (_) {}

      final response = await http.post(
        Uri.parse('$baseUrl/tts'),
        headers: {'Content-Type': 'application/json'},
        body: json.encode({'text': text, 'persona': _currentPersona}),
      ).timeout(const Duration(seconds: 15));

      if (response.statusCode == 200) {
        final contentType =
            response.headers['content-type'] ?? 'audio/mpeg';
        final blob = html.Blob([response.bodyBytes], contentType);
        final blobUrl = html.Url.createObjectUrlFromBlob(blob);

        final audio = html.AudioElement(blobUrl);
        _audioElement = audio;

        audio.onEnded.listen((_) async {
          if (_audioElement != audio) return; // Ignore events from old audio sessions
          html.Url.revokeObjectUrl(blobUrl);
          _stopManualWebStream();
          
          _addUiLog('[TTS] Audio playback ended. Starting 2-second cooldown...');
          // Wait 2 seconds before unmuting the wake word engine to let speaker echo and ONNX model scores settle
          await Future.delayed(const Duration(seconds: 2));
          if (_audioElement != audio) return;

          _addUiLog('[OWW] Cooldown finished. Unmuting wake word engine.');
          _setWakeWordMute(false); // Re-enable wake word / restart engine
          if (mounted) {
            setState(() {
              _isTtsInProgress = false;
              _currentSubtitleText = null;
            });
          }
          await _setAvatarState(AvatarState.idle);

          if (_currentState != HandsOffState.handsOffOff) {
            js.context.callMethod('startWakeWordListening');
          }
        });

        audio.onError.listen((_) {
          if (_audioElement != audio) return; // Ignore events from old audio sessions
          html.Url.revokeObjectUrl(blobUrl);
          debugPrint('⚠️ Audio playback encountered an error event.');
        });

        audio.play().then((_) {
          // The browser may auto-pause our muted videos when this new audio starts playing.
          // Force all active persona videos to play immediately!
          if (mounted) {
            final states = [AvatarState.idle, AvatarState.thinking, AvatarState.talking];
            for (final s in states) {
              final asset = _assetFor(_currentPersona, s);
              final c = _cachedControllers[asset];
              if (c != null && !c.value.isPlaying) {
                c.play();
              }
            }
          }
        }).catchError((playError) {
          if (_audioElement != audio) return;
          debugPrint('🔇 Autoplay warning caught or audio play failed: $playError. Falling back to Native TTS...');
          _speakNative(text);
        });
      } else {
        debugPrint('⚠️ Server TTS returned status ${response.statusCode}, falling back to Native TTS...');
        _speakNative(text);
      }
    } catch (e) {
      debugPrint('⚠️ TTS HTTP/Network error: $e, falling back to Native TTS...');
      _speakNative(text);
    }
  }

  /// Free Web Speech API local fallback (bypasses all backend audio key failures and crashes!)
  void _speakNative(String text) {
    try {
      final synth = html.window.speechSynthesis;
      if (synth == null) return;
      _setWakeWordMute(true); // Mute wake word while speaking

      // Cancel any ongoing speech
      synth.cancel();

      final utterance = html.SpeechSynthesisUtterance(text);
      _currentUtterance = utterance;
      
      // Set voice based on current persona
      final voices = synth.getVoices();
      html.SpeechSynthesisVoice? selectedVoice;
      for (var voice in voices) {
        final name = voice.name?.toLowerCase() ?? '';
        final lang = voice.lang?.toLowerCase() ?? '';
        if (_currentPersona == 'linda') {
          if (lang.contains('en') && (name.contains('female') || name.contains('google us english') || name.contains('zira') || name.contains('hazel') || name.contains('samantha'))) {
            selectedVoice = voice;
            break;
          }
        } else {
          if (lang.contains('en') && (name.contains('male') || name.contains('david') || name.contains('google uk english male') || name.contains('mark') || name.contains('microsoft david'))) {
            selectedVoice = voice;
            break;
          }
        }
      }
      if (selectedVoice != null) {
        utterance.voice = selectedVoice;
      }

      utterance.onStart.listen((_) {
        debugPrint('📢 [NATIVE TTS] Started speaking...');
        if (mounted) {
          setState(() {
            _isTtsInProgress = true;
            _currentSubtitleText = text;
          });
          // The browser may auto-pause our muted videos when this new audio starts playing.
          // Force all active persona videos to play immediately!
          final states = [AvatarState.idle, AvatarState.thinking, AvatarState.talking];
          for (final s in states) {
            final asset = _assetFor(_currentPersona, s);
            final c = _cachedControllers[asset];
            if (c != null && !c.value.isPlaying) {
              c.play();
            }
          }
        }
      });

      utterance.onEnd.listen((_) async {
        if (_currentUtterance != utterance) return;
        debugPrint('📢 [NATIVE TTS] Completed successfully.');
        _stopManualWebStream();
        
        _addUiLog('[NATIVE TTS] Speech ended. Starting 2-second cooldown...');
        // Wait 2 seconds before unmuting the wake word engine to let speaker echo and ONNX model scores settle
        await Future.delayed(const Duration(seconds: 2));
        if (_currentUtterance != utterance) return;

        _addUiLog('[OWW] Cooldown finished. Unmuting wake word engine.');
        _setWakeWordMute(false);
        if (mounted) {
          setState(() {
            _isTtsInProgress = false;
            _currentSubtitleText = null;
          });
        }
        await _setAvatarState(AvatarState.idle);
        if (_currentState != HandsOffState.handsOffOff) {
          js.context.callMethod('startWakeWordListening');
        }
      });

      utterance.onError.listen((e) async {
        if (_currentUtterance != utterance) return;
        debugPrint('❌ [NATIVE TTS] Error: $e');
        _stopManualWebStream();
        
        _addUiLog('[NATIVE TTS] Error occurred. Starting 2-second cooldown...');
        // Wait 2 seconds before unmuting the wake word engine to let speaker echo and ONNX model scores settle
        await Future.delayed(const Duration(seconds: 2));
        if (_currentUtterance != utterance) return;

        _addUiLog('[OWW] Cooldown finished. Unmuting wake word engine.');
        _setWakeWordMute(false);
        if (mounted) {
          setState(() {
            _isTtsInProgress = false;
            _currentSubtitleText = null;
          });
        }
        await _setAvatarState(AvatarState.idle);
        if (_currentState != HandsOffState.handsOffOff) {
          js.context.callMethod('startWakeWordListening');
        }
      });

      synth.speak(utterance);
    } catch (e) {
      debugPrint('❌ [NATIVE TTS] Exception: $e');
    }
  }

  Future<void> _stopSpeaking() async {
    // Completely kill audio
    _audioElement?.pause();
    _audioElement?.src = '';
    _audioElement = null;
    try {
      html.window.speechSynthesis?.cancel();
    } catch (_) {}
    if (mounted) {
      setState(() {
        _isTtsInProgress = false;
        _currentSubtitleText = null;
      });
    }
    await _setAvatarState(AvatarState.idle);

    _setWakeWordMute(false); // Re-enable wake word / restart engine

    if (_currentState != HandsOffState.handsOffOff) {
      js.context.callMethod('startWakeWordListening');
    }
  }

  Future<void> _emergencyStop() async {
    // Notify the backend immediately to halt robot hardware
    try {
      http.post(
        Uri.parse('$baseUrl/emergency-stop'),
        headers: {'Content-Type': 'application/json'},
        body: json.encode({}),
      ).catchError((e) {
        debugPrint('Failed to send emergency stop request: $e');
        return http.Response('Failed', 500);
      });
    } catch (e) {
      debugPrint('Failed to initialize emergency stop HTTP: $e');
    }

    // Kill audio
    _audioElement?.pause();
    _audioElement?.src = '';
    _audioElement = null;
    try {
      html.window.speechSynthesis?.cancel();
    } catch (_) {}

    // Stop recording
    if (_isListening) {
      try {
        await _audioRecorder.stop();
      } catch (_) {}
    }
    _stopSilenceDetection();

    // Reset wake word socket if connected
    if (_currentState != HandsOffState.handsOffOff) {
      if (_wakeWordSocket != null && _wakeWordSocket!.readyState == html.WebSocket.OPEN) {
        _wakeWordSocket!.send(json.encode({'action': 'stop_wakeword'}));
      }
    }// Set avatar to idle
    await _setAvatarState(AvatarState.idle);

    if (mounted) {
      setState(() {
        _isListening = false;
        _isTtsInProgress = false;
        _currentSubtitleText = null;
        _textController.clear();
        _messages.add(ChatMessage(
            text: "⚠️ System: EMERGENCY STOP triggered! All operations halted.",
            isUser: false));
      });
    }
    _scrollToBottom();
  }

  Future<void> _returnHome() async {
    try {
      http.post(
        Uri.parse('$baseUrl/return-home'),
        headers: {'Content-Type': 'application/json'},
        body: json.encode({}),
      ).catchError((e) {
        debugPrint('Failed to send return home request: $e');
        return http.Response('Failed', 500);
      });
    } catch (e) {
      debugPrint('Failed to initialize return home HTTP: $e');
    }

    if (mounted) {
      setState(() {
        _messages.add(ChatMessage(
            text: "🏠 System: Returning Robot Arm to Home Position...",
            isUser: false));
      });
    }
    _scrollToBottom();
  }

  void _clearChat() {
    setState(() {
      _messages.clear();
      final welcome =
          "Welcome! I'm John, your robotic assistant. How can I help you today?";
      _messages.add(ChatMessage(
          text: '$_pName($_idleEmoji): $welcome', isUser: false));
    });
    _scrollToBottom();
  }

  Widget _buildPresetQuestion(String label, String fullQuestion) {
    final bool blocked = _isInteractionBlocked;
    return Padding(
      padding: const EdgeInsets.only(right: 6, bottom: 6),
      child: GestureDetector(
        onTap: blocked ? () {} : () => _sendMessage(fullQuestion),
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
          decoration: BoxDecoration(
            color: blocked ? Colors.grey[300] : const Color(0xFFE0F7FA), // Light blue/teal tint matching screenshot
            borderRadius: BorderRadius.circular(20),
          ),
          child: Text(
            label,
            style: TextStyle(
              color: blocked ? Colors.black38 : Colors.black87,
              fontWeight: FontWeight.bold,
              fontSize: 12,
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildPillBtn(String label, IconData icon, Color color, VoidCallback onTap, {bool allowAlways = false}) {
    final bool blocked = _isInteractionBlocked && !allowAlways;
    return Padding(
      padding: const EdgeInsets.only(right: 6, bottom: 6),
      child: GestureDetector(
        onTap: blocked ? () {} : onTap,
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
          decoration: BoxDecoration(
            color: blocked ? Colors.grey[400] : color,
            borderRadius: BorderRadius.circular(20),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(icon, color: blocked ? Colors.black38 : Colors.white, size: 14),
              const SizedBox(width: 4),
              Text(
                label,
                style: TextStyle(
                  color: blocked ? Colors.black38 : Colors.white,
                  fontWeight: FontWeight.bold,
                  fontSize: 12,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  // ─────────────────────────────────────────────────────────────────────────
  //  Microphone / transcription
  // ─────────────────────────────────────────────────────────────────────────

  Future<void> _listen() async {
    _addUiLog('[MIC] Button pressed');
    // Do not allow listening while chatbot is speaking
    if (_isTtsInProgress) {
      _addUiLog('[MIC] Blocked: TTS is in progress');
      return;
    }
    _unlockAudio();

    // ── Python Audio Server mode ──
    if (USE_PYTHON_AUDIO) {
      return _listenViaPython();
    }
    // ── Browser fallback mode (below) ──

    if (!_isListening) {
      _setWakeWordMute(true);
      if (_currentState == HandsOffState.wakewordListening) {
        _changeState(HandsOffState.userRecording);
      }

      try {
        js.context.callMethod('stopWakeWordListening');
        _addUiLog('[MIC] Stopped wakeword listening for manual recording');
      } catch (e) {
        _addUiLog('[MIC] Warning: could not call stopWakeWordListening: $e');
      }

      _addUiLog('[MIC] Requesting browser microphone permission');
      html.MediaStream? stream;
      try {
        stream = await html.window.navigator.mediaDevices?.getUserMedia({'audio': true});
      } catch (e) {
        _addUiLog('[MIC] Permission denied or error: $e');
        _showStatusSnackBar('Mic issue. Try again.', isError: true);
        _abortAndRestartWakeWord();
        return;
      }
      if (stream == null) {
        _addUiLog('[MIC] Permission denied (stream null)');
        _showStatusSnackBar('Mic issue. Try again.', isError: true);
        _abortAndRestartWakeWord();
        return;
      }
      _addUiLog('[MIC] Permission granted');
      _manualWebStream = stream;

      setState(() {
        _isListening = true;
        _textController.text = 'Listening...';
      });

      String mimeType = '';
      if (html.MediaRecorder.isTypeSupported('audio/webm;codecs=opus')) {
        mimeType = 'audio/webm;codecs=opus';
      } else if (html.MediaRecorder.isTypeSupported('audio/webm')) {
        mimeType = 'audio/webm';
      } else if (html.MediaRecorder.isTypeSupported('audio/ogg;codecs=opus')) {
        mimeType = 'audio/ogg;codecs=opus';
      } else {
        _addUiLog('[MIC] Warning: no standard webm/opus type supported. Using default.');
      }
      _addUiLog('[MIC] Selected MIME type: ${mimeType.isEmpty ? "default" : mimeType}');

      _webMediaRecorder = mimeType.isNotEmpty 
          ? html.MediaRecorder(stream, {'mimeType': mimeType})
          : html.MediaRecorder(stream);

      _webAudioChunks = [];

      _webMediaRecorder!.addEventListener('start', (e) {
        _addUiLog('[MIC] MediaRecorder state: ${_webMediaRecorder?.state}');
      });

      _webMediaRecorder!.addEventListener('pause', (e) {
        _addUiLog('[MIC] MediaRecorder state: ${_webMediaRecorder?.state}');
      });

      _webMediaRecorder!.addEventListener('dataavailable', (html.Event e) {
        final blobEvent = e as html.BlobEvent;
        final data = blobEvent.data;
        if (data != null) {
          _addUiLog('[MIC] ondataavailable: size=${data.size}, type=${data.type}');
          if (data.size > 0) {
            _webAudioChunks.add(data);
          }
        } else {
          _addUiLog('[MIC] ondataavailable: null data');
        }
      });

      _webMediaRecorder!.addEventListener('stop', (e) async {
        _addUiLog('[FLUTTER] recording stopped');
        _addUiLog('[MIC] chunk count: ${_webAudioChunks.length}');
        
        final sizes = _webAudioChunks.map((b) => b.size).join(',');
        _addUiLog('[MIC] chunk sizes: $sizes');
        
        if (_currentState == HandsOffState.userRecording) {
          _changeState(HandsOffState.transcribing);
        }

        if (_webAudioChunks.isEmpty) {
          _addUiLog('[MIC] audio bytes are empty. Aborting.');
          if (mounted) setState(() => _textController.clear());
          await _setAvatarState(AvatarState.idle);
          _abortAndRestartWakeWord();
          return;
        }

        final blob = html.Blob(_webAudioChunks, mimeType.isEmpty ? 'audio/webm' : mimeType);
        _addUiLog('[MIC] final blob size: ${blob.size}');

        final reader = html.FileReader();
        reader.readAsArrayBuffer(blob);
        await reader.onLoadEnd.first; // wait for read to complete
        final Uint8List audioBytes = reader.result as Uint8List;

        _addUiLog('[MIC] Audio blob/file size: ${audioBytes.length} bytes');

        if (audioBytes.length < 5000) {
          _addUiLog('[MIC] Audio bytes too small: ${audioBytes.length}');
          setState(() {
            _messages.add(ChatMessage(text: 'Recording was too short. Try again.', isUser: false));
          });
          await _setAvatarState(AvatarState.idle);
          _abortAndRestartWakeWord();
          return;
        }

        final uploadUrl = '$baseUrl/transcribe';
        _addUiLog('[MIC] Uploading to: $uploadUrl');
        final req = http.MultipartRequest('POST', Uri.parse(uploadUrl));
        req.files.add(http.MultipartFile.fromBytes(
          'audio',
          audioBytes,
          filename: 'audio.webm',
          contentType: MediaType('audio', 'webm'),
        ));

        _addUiLog('[MIC] POST /transcribe started');
        try {
          final res = await req.send();
          _addUiLog('[MIC] Upload response status: ${res.statusCode}');
          if (res.statusCode == 200) {
            final body = await res.stream.bytesToString();
            _addUiLog('[MIC] Upload response body: $body');
            final data = json.decode(body);
            if (data['success'] == true && data['text'] != null && (data['text'] as String).trim().isNotEmpty) {
              if (mounted) _textController.clear();
              _sendMessage(data['text'] as String);
            } else {
              if (mounted) _textController.clear();
              await _setAvatarState(AvatarState.idle);
              debugPrint('🎙️ [ASR] Empty/unsuccessful transcription returned.');
              _abortAndRestartWakeWord();
            }
          } else {
            _addUiLog('[MIC] Upload response error: HTTP ${res.statusCode}');
            throw Exception('Transcription HTTP ${res.statusCode}');
          }
        } catch (e) {
          _addUiLog('[MIC] Upload response error: $e');
          if (mounted) {
            setState(() {
              _messages.add(ChatMessage(text: 'Something went wrong. Please try again.', isUser: false));
              _textController.clear();
            });
          }
          await _setAvatarState(AvatarState.idle);
          _abortAndRestartWakeWord();
        }
      });

      _webMediaRecorder!.start(200); // Request data every 200ms
      _addUiLog('[FLUTTER] recording started');
      _addUiLog('[MIC] Recording started');
      if (_currentState != HandsOffState.handsOffOff) {
        _startSilenceDetection();
      }
    } else {
      setState(() {
        _isListening = false;
        _textController.text = 'Transcribing...';
      });
      await _setAvatarState(AvatarState.thinking);
      _stopSilenceDetection();

      // Small pad so trailing syllables are captured
      await Future.delayed(const Duration(milliseconds: 400));
      _webMediaRecorder?.stop();
      _addUiLog('[MIC] Recording stopped');
    }
  }

  // ─────────────────────────────────────────────────────────────────────────
  //  Python Audio Server recording (USE_PYTHON_AUDIO = true)
  // ─────────────────────────────────────────────────────────────────────────

  Future<void> _listenViaPython() async {
    if (!_isListening) {
      // ── START recording via Python ──
      _addUiLog('[FLUTTER] record_now requested');
      
      if (_currentState == HandsOffState.wakewordListening) {
        _changeState(HandsOffState.userRecording);
      }

      setState(() {
        _isListening = true;
        _textController.text = 'Listening...';
      });

      _sendWakeWordCommand({'action': 'record_now'});
    } else {
      // ── STOP recording via Python ──
      _addUiLog('[FLUTTER] stop_recording requested');

      setState(() {
        _isListening = false;
        _textController.text = 'Transcribing...';
      });
      await _setAvatarState(AvatarState.thinking);

      _sendWakeWordCommand({'action': 'stop_recording'});
    }
  }

  void _startSilenceDetection() async {
    try {
      final mediaDevices = html.window.navigator.mediaDevices;
      if (mediaDevices == null) {
        debugPrint('⚠️ VAD: mediaDevices is null');
        return;
      }
      final stream = await mediaDevices.getUserMedia({'audio': true});
      _vadStream = stream;

      final audioCtx = wa.AudioContext();
      _vadAudioContext = audioCtx;
      
      final analyser = audioCtx.createAnalyser();
      _vadAnalyser = analyser;
      analyser.fftSize = 256;

      final source = audioCtx.createMediaStreamSource(stream);
      source.connectNode(analyser);

      final bufferLength = analyser.frequencyBinCount ?? 0;
      final dataArray = Float32List(bufferLength);

      bool hasSpoken = false;
      int silenceTicks = 0;
      int maxTicks = 100; // 10 seconds max timeout (100 * 100ms)
      int elapsedTicks = 0;

      const silenceThreshold = 0.008; 
      const speechThreshold = 0.02;

      _vadTimer = Timer.periodic(const Duration(milliseconds: 100), (timer) async {
        if (!mounted || !_isListening) {
          _stopSilenceDetection();
          return;
        }

        elapsedTicks++;
        if (elapsedTicks >= maxTicks) {
          debugPrint('⏱️ VAD: Max recording duration reached (10s). Auto-stopping...');
          _stopSilenceDetection();
          if (_isListening) {
            await _listen();
          }
          return;
        }

        analyser.getFloatTimeDomainData(dataArray);

        double sum = 0;
        for (int i = 0; i < bufferLength; i++) {
          sum += dataArray[i] * dataArray[i];
        }
        double rms = math.sqrt(sum / bufferLength);

        if (rms > speechThreshold) {
          if (!hasSpoken) {
            hasSpoken = true;
            debugPrint('🗣️ VAD: Speech detected (RMS: ${rms.toStringAsFixed(4)})');
          }
          silenceTicks = 0;
        } else if (rms < silenceThreshold) {
          if (hasSpoken) {
            silenceTicks++;
            if (silenceTicks >= 15) { // 1.5 seconds of silence
              debugPrint('🤫 VAD: Silence detected after speech (1.5s). Auto-stopping...');
              _stopSilenceDetection();
              if (_isListening) {
                await _listen();
              }
            }
          } else {
            if (elapsedTicks >= 40) { // 4 seconds of initial silence
              debugPrint('⏱️ VAD: No speech detected for 4s. Auto-stopping...');
              _stopSilenceDetection();
              if (_isListening) {
                await _listen();
              }
            }
          }
        } else {
          silenceTicks = 0;
        }
      });

    } catch (e) {
      debugPrint('⚠️ VAD Initialization failed: $e');
      _vadTimer = Timer(const Duration(seconds: 5), () async {
        if (mounted && _isListening) {
          debugPrint('⏱️ VAD Fallback: Auto-stopping after 5s...');
          await _listen();
        }
      });
    }
  }

  void _stopSilenceDetection() {
    _vadTimer?.cancel();
    _vadTimer = null;
    
    try {
      final tracks = _vadStream?.getTracks();
      if (tracks != null) {
        for (var track in tracks) {
          track.stop();
        }
      }
    } catch (_) {}
    _vadStream = null;

    try {
      _vadAudioContext?.close();
    } catch (_) {}
    _vadAudioContext = null;
    _vadAnalyser = null;
  }

  // ─────────────────────────────────────────────────────────────────────────
  //  Hands-free / wake-word
  // ─────────────────────────────────────────────────────────────────────────

  void _changeState(HandsOffState newState) {
    if (mounted) {
      setState(() {
        _currentState = newState;
      });
      _addUiLog('[STATE] changed to ${newState.name}');
    }
  }

  void _abortAndRestartWakeWord() {
    _addUiLog('[OWW] auto-restart on abort');
    _stopManualWebStream();
    
    bool robotMoving = false;
    try {
      robotMoving = js.context['isRobotMoving'] == true;
    } catch (_) {}
    
    _setWakeWordMute(robotMoving);
    
    if (_currentState != HandsOffState.handsOffOff) {
      js.context.callMethod('startWakeWordListening');
    }
  }

  void _manualRestartWakeWord() {
    if (!_audioServerConnected) return;
    _isManualRestarting = true;
    _changeState(HandsOffState.restarting);
    _addUiLog('[OWW] manual restart initiated');
    js.context.callMethod('restartWakeWordEngine');
  }

  void _startWakeWord() {
    if (!_audioServerConnected) {
      _showStatusSnackBar('Wakeword Engine is not initialized! Enable Hands-Free first.', isError: true);
      return;
    }
    _changeState(HandsOffState.wakewordListening);
    js.context.callMethod('startWakeWordListening');
    _addUiLog('[FLUTTER] Start Wakeword clicked');
  }

  void _stopWakeWord() {
    if (!_audioServerConnected) return;
    _changeState(HandsOffState.handsOffOff);
    js.context.callMethod('stopWakeWordListening');
    _addUiLog('[FLUTTER] Stop Wakeword clicked');
  }

  void _testJohnCallback() {
    _addUiLog('[OWW] John detected (score: 0.9900)');
    _handleWakeWordDetected('john');
  }

  void _testLindaCallback() {
    _addUiLog('[OWW] Linda detected (score: 0.9900)');
    _handleWakeWordDetected('linda');
  }

  void _toggleHandsFree() {
    if (!_audioServerConnected) {
      if (_wakeWsStatus == 'disconnected') {
        _connectWakeWord();
      }
      _showStatusSnackBar('Initializing Wakeword Engine... Please wait.');
      return;
    }
    if (_currentState == HandsOffState.handsOffOff) {
      _changeState(HandsOffState.restarting);
      _addUiLog('[OWW] Hands Off ON: starting engine listening');
      js.context.callMethod('startWakeWordListening');
    } else {
      _changeState(HandsOffState.handsOffOff);
      _addUiLog('[OWW] Hands Off OFF: stopping engine listening');
      js.context.callMethod('stopWakeWordListening');
    }
  }

  String get _wakeWordUrl {
    final host = html.window.location.hostname ?? '';
    final socketHost = host.isEmpty ? 'localhost' : host;
    final port = html.window.location.port;
    final wsPort = (port != null && port.isNotEmpty) ? ':$port' : '';
    // Use the same protocol scheme - wss for https, ws for http
    final protocol = html.window.location.protocol == 'https:' ? 'wss' : 'ws';
    return '$protocol://$socketHost$wsPort/wakeword';
  }

  Future<void> _handleWakeWordEvent() async {
    _addUiLog('[FLUTTER] calling manual mic function');
    
    if (_isTtsInProgress) {
      _addUiLog('[WAKE] blocked because: TTS is in progress');
      _showStatusSnackBar('Wakeword heard, but recording did not start.', isError: true);
    } else if (_isListening) {
      _addUiLog('[WAKE] blocked because: already listening');
      _showStatusSnackBar('Wakeword heard, but recording did not start.', isError: true);
    } else if (_isInteractionBlocked) {
      _addUiLog('[WAKE] blocked because: interaction is blocked');
      _showStatusSnackBar('Wakeword heard, but recording did not start.', isError: true);
    } else {
      _addUiLog('[MIC] recording started');
      if (_currentState != HandsOffState.handsOffOff) {
        _changeState(HandsOffState.userRecording);
      }
      await _listen(); // start recording
    }
  }

  // ─── Wake Word WebSocket helpers ─────────────────────────────────────────

  /// Send any action to Python via the WS proxy.
  /// If the socket is closed/closing, reconnect first then send after open.
  void _sendWakeWordCommand(Map<String, dynamic> payload) {
    final msg = json.encode(payload);
    if (_wakeWordSocket != null &&
        _wakeWordSocket!.readyState == html.WebSocket.OPEN) {
      _wakeWordSocket!.send(msg);
    } else {
      _addUiLog('[WAKE WS] not open – reconnecting then sending: ${payload["action"]}');
      _connectWakeWord(onConnected: () {
        if (_wakeWordSocket != null &&
            _wakeWordSocket!.readyState == html.WebSocket.OPEN) {
          _wakeWordSocket!.send(msg);
          _addUiLog('[WAKE WS] sent after reconnect: ${payload["action"]}');
        }
      });
    }
  }

  void _connectWakeWord({VoidCallback? onConnected}) {
    _initBrowserWakeWord();
  }

  void _updateThresholds() {
    final johnVal = double.tryParse(_johnThreshController.text) ?? 0.001;
    final lindaVal = double.tryParse(_lindaThreshController.text) ?? 0.0009;
    setState(() {
      _johnThresh = johnVal;
      _lindaThresh = lindaVal;
    });
    try {
      js.context.callMethod('setWakeWordThresholds', [johnVal, lindaVal]);
      _addUiLog('[OWW] updated thresholds: J=$johnVal, L=$lindaVal');
    } catch (e) {
      _addUiLog('[OWW] failed to set thresholds: $e');
    }
  }

  void _initBrowserWakeWord() {
    if (mounted) setState(() => _wakeWsStatus = 'connecting');
    _addUiLog('[OWW] Initializing client-side openWakeWord engine...');
    debugPrint('🔌 [OWW] Initializing client-side openWakeWord engine...');
    
    try {
      js.context.callMethod('initWakeWordEngine', [
        js.allowInterop((score, [modelName]) {
          _addUiLog('[OWW] John detected (score: $score)');
          _handleWakeWordDetected('john');
        }),
        js.allowInterop((score, [modelName]) {
          _addUiLog('[OWW] Linda detected (score: $score)');
          _handleWakeWordDetected('linda');
        }),
        js.allowInterop(() {
          _addUiLog('[OWW] client-side openWakeWord engine is ready.');
          if (mounted) {
            setState(() {
              _audioServerConnected = true;
              _wakeWsStatus = 'connected';
            });
            _updateThresholds(); // Call update thresholds on ready to synchronize settings
            _showStatusSnackBar('Client-side openWakeWord Ready');
            _toggleHandsFree(); // Automatically enable hands-free listening
          }
        }),
        js.allowInterop((eventName) {
          _handleOwwEvent(eventName);
        })
      ]);
    } catch (e) {
      _addUiLog('[OWW] Failed to call initWakeWordEngine: $e');
      if (mounted) setState(() => _wakeWsStatus = 'error');
    }
  }

  void _handleOwwEvent(String eventName) {
    if (eventName.startsWith('status_update:')) {
      final parts = eventName.substring('status_update:'.length).split(',');
      String rms = '0.0000';
      String john = '0.00';
      String johnPeak = '0.00';
      String johnV2 = '0.00';
      String johnV2Peak = '0.00';
      String linda = '0.00';
      String lindaPeak = '0.00';
      String lindaV2 = '0.00';
      String lindaV2Peak = '0.00';
      String models = 'none';
      String threshJohn = '0.001';
      String threshLinda = '0.0009';
      String callback = 'false';
      String cps = '0.0';
      String ctx = 'none';
      String track = 'none';
      String trackEnabled = 'false';
      for (var part in parts) {
        final kv = part.split('=');
        if (kv.length == 2) {
          if (kv[0] == 'rms') rms = kv[1];
          if (kv[0] == 'john') john = kv[1];
          if (kv[0] == 'john_peak') johnPeak = kv[1];
          if (kv[0] == 'john_v2') johnV2 = kv[1];
          if (kv[0] == 'john_v2_peak') johnV2Peak = kv[1];
          if (kv[0] == 'linda') linda = kv[1];
          if (kv[0] == 'linda_peak') lindaPeak = kv[1];
          if (kv[0] == 'linda_v2') lindaV2 = kv[1];
          if (kv[0] == 'linda_v2_peak') lindaV2Peak = kv[1];
          if (kv[0] == 'models') models = kv[1];
          if (kv[0] == 'thresh_john') threshJohn = kv[1];
          if (kv[0] == 'thresh_linda') threshLinda = kv[1];
          if (kv[0] == 'callback') callback = kv[1];
          if (kv[0] == 'cps') cps = kv[1];
          if (kv[0] == 'ctx') ctx = kv[1];
          if (kv[0] == 'track') track = kv[1];
          if (kv[0] == 'track_enabled') trackEnabled = kv[1];
        }
      }
      if (mounted) {
        setState(() {
          _lastRms = double.tryParse(rms) ?? 0.0;
          _lastJohnScore = double.tryParse(john) ?? 0.0;
          _lastJohnPeak = double.tryParse(johnPeak) ?? 0.0;
          _lastJohnV2Score = double.tryParse(johnV2) ?? 0.0;
          _lastJohnV2Peak = double.tryParse(johnV2Peak) ?? 0.0;
          _lastLindaScore = double.tryParse(linda) ?? 0.0;
          _lastLindaPeak = double.tryParse(lindaPeak) ?? 0.0;
          _lastLindaV2Score = double.tryParse(lindaV2) ?? 0.0;
          _lastLindaV2Peak = double.tryParse(lindaV2Peak) ?? 0.0;
          _lastCps = double.tryParse(cps) ?? 0.0;
          _lastCtx = ctx;
          _lastTrack = track;
          _lastTrackEnabled = trackEnabled == 'true';
        });
      }
      final double jPercent = (double.tryParse(john) ?? 0.0) * 100;
      final double jV2Percent = (double.tryParse(johnV2) ?? 0.0) * 100;
      final double lPercent = (double.tryParse(linda) ?? 0.0) * 100;
      final double lV2Percent = (double.tryParse(lindaV2) ?? 0.0) * 100;
      
      // Print live score debug info directly to VS Code Console
      if (jPercent > 0.1 || jV2Percent > 0.1 || lPercent > 0.1 || lV2Percent > 0.1) {
        debugPrint('[OWW Scores] John: ${jPercent.toStringAsFixed(1)}% (V2: ${jV2Percent.toStringAsFixed(1)}%) | Linda: ${lPercent.toStringAsFixed(1)}% (V2: ${lV2Percent.toStringAsFixed(1)}%)');
      }
      
      _addUiLog('[OWW] Active | RMS: $rms | J: ${jPercent.toStringAsFixed(1)}% (V2: ${jV2Percent.toStringAsFixed(1)}%) | L: ${lPercent.toStringAsFixed(1)}% (V2: ${lV2Percent.toStringAsFixed(1)}%)');
      return;
    }

    if (eventName.startsWith('robot_moving_status:')) {
      final isMoving = eventName.substring('robot_moving_status:'.length) == 'true';
      _addUiLog('[OWW] Robot moving status: $isMoving');
      
      if (isMoving) {
        _setWakeWordMute(true);
        // Abort any active manual recording immediately
        if (_isListening) {
          _addUiLog('[MIC] Robot moving — aborting active recording.');
          setState(() {
            _isListening = false;
            _textController.clear();
          });
          _stopSilenceDetection();
          try {
            _webMediaRecorder?.stop();
          } catch (_) {}
          _abortAndRestartWakeWord();
        }
      } else {
        // Only unmute and restart listening if TTS is not in progress!
        if (!_isTtsInProgress) {
          _addUiLog('[OWW] Robot stopped moving. Starting 2-second cooldown...');
          // Add a 2.0 second cooldown delay to let motor stopping noise/vibrations settle before listening
          Future.delayed(const Duration(milliseconds: 2000), () {
            if (mounted && !_isTtsInProgress) {
              bool stillStopped = true;
              try {
                stillStopped = js.context['isRobotMoving'] != true;
              } catch (_) {}
              if (stillStopped) {
                _addUiLog('[OWW] Cooldown finished. Unmuting wake word engine.');
                _setWakeWordMute(false);
                if (_currentState != HandsOffState.handsOffOff) {
                  js.context.callMethod('startWakeWordListening');
                }
              }
            }
          });
        } else {
          _addUiLog('[OWW] Robot stopped, but TTS is in progress. Keeping wake word muted.');
        }
      }
      if (mounted) {
        setState(() {}); // rebuild UI to grey out mic button
      }
      return;
    }

    if (eventName.startsWith('near_miss:')) {
      final parts = eventName.substring('near_miss:'.length).split('=');
      if (parts.length == 2) {
        final keyword = parts[0];
        final score = parts[1];
        _addUiLog('[OWW] $keyword near miss: score $score');
      }
    }

    if (eventName.startsWith('tts:')) {
      final msg = eventName.substring('tts:'.length);
      _addUiLog('[FLUTTER] Received TTS event: $msg');
      if (mounted) {
        setState(() {
          _messages.add(ChatMessage(text: '$_pName($_answeringEmoji): $msg', isUser: false));
        });
        _scrollToBottom();
      }
      _speak(msg);
      return;
    }

    switch (eventName) {
      case 'started':
        _addUiLog('[OWW] started');
        break;
      case 'stopped':
        _addUiLog('[OWW] stopped');
        break;
      case 'restarting':
        _addUiLog('[OWW] restarting');
        _changeState(HandsOffState.restarting);
        break;
      case 'restarted':
        _addUiLog('[OWW] restarted');
        break;
      case 'active_listening_confirmed':
        if (_currentState == HandsOffState.restarting) {
          _changeState(HandsOffState.wakewordListening);
          if (_isManualRestarting) {
            _addUiLog('[OWW] manual restart complete');
            _isManualRestarting = false;
          }
        }
        break;
      case 'audio_active':
        _addUiLog('[OWW] audio active');
        break;
      case 'no_audio_detected':
        _addUiLog('[OWW] no audio detected, restarting');
        _showStatusSnackBar('Wakeword restarted.');
        break;
      case 'mic_issue':
        _addUiLog('[OWW] mic issue');
        _showStatusSnackBar('Mic issue. Try again.', isError: true);
        break;
    }
  }

  Future<void> _handleWakeWordDetected(String keyword) async {
    _addUiLog('[FLUTTER] wake word detected client-side: $keyword');
    if (_currentPersona != keyword) {
      _addUiLog('[WAKE] Ignored $keyword because current persona is $_currentPersona');
      return;
    }
    if (_isTtsInProgress || _isListening || _isInteractionBlocked) {
      _addUiLog('[WAKE] blocked: tts=$_isTtsInProgress, listen=$_isListening, blocked=$_isInteractionBlocked');
      _showStatusSnackBar('Wakeword heard, but recording did not start.', isError: true);
      return;
    }

    // 1. Mute openWakeWord detection callbacks
    _setWakeWordMute(true);

    // Add a 800ms delay to allow the browser to release the microphone device completely
    await Future.delayed(const Duration(milliseconds: 800));

    // 3. Trigger manual mic recording flow
    await _handleWakeWordEvent();
  }

  void _disconnectWakeWord() {
    _addUiLog('[OWW] shutting down client-side openWakeWord listening');
    js.context.callMethod('stopWakeWordListening');
  }

  // ─────────────────────────────────────────────────────────────────────────
  //  Send message
  // ─────────────────────────────────────────────────────────────────────────

  Future<void> _sendMessage(String text) async {
    if (text.trim().isEmpty) return;
    _unlockAudio();

    setState(() => _messages.add(ChatMessage(text: text, isUser: true)));
    _textController.clear();
    _scrollToBottom();
    
    // Add a thinking placeholder in chat
    final thinkingIndex = _messages.length;
    setState(() => _messages.add(ChatMessage(text: '__THINKING__', isUser: false)));
    _scrollToBottom();
    
    await _setAvatarState(AvatarState.thinking);

    try {
      final response = await http
          .post(
            Uri.parse('$baseUrl/ask-gpt'),
            headers: {'Content-Type': 'application/json'},
            body: jsonEncode({'question': text}),
          )
          .timeout(const Duration(seconds: 120));

      if (response.statusCode == 200) {
        final data = jsonDecode(response.body);
        if (data['success'] == true) {
          final answer = data['answer'] as String;
          final newPersona = data['persona'] as String?;
          if (newPersona != null && newPersona != _currentPersona) {
            setState(() => _currentPersona = newPersona);
            _loadVideo(_avatarState, force: true);
            if (_wakeWordSocket != null && _wakeWordSocket!.readyState == html.WebSocket.OPEN) {
              _wakeWordSocket!.send(json.encode({
                'action': 'set_persona',
                'persona': newPersona
              }));
            }
          }
          // Replace thinking placeholder with actual response
          setState(() {
            if (thinkingIndex < _messages.length && _messages[thinkingIndex].text == '__THINKING__') {
              _messages[thinkingIndex] = ChatMessage(text: '$_pName($_answeringEmoji): $answer', isUser: false);
            } else {
              _messages.add(ChatMessage(text: '$_pName($_answeringEmoji): $answer', isUser: false));
            }
          });
          _scrollToBottom();
          _speak(answer);
        } else {
          throw Exception(data['message'] ?? 'Unknown error');
        }
      } else {
        throw Exception('Server error ${response.statusCode}');
      }
    } catch (e) {
      debugPrint('Send error: $e');
      const errMsg =
          "Oops! I couldn't process that right now. Please try again!";
      // Replace thinking placeholder with error
      setState(() {
        if (thinkingIndex < _messages.length && _messages[thinkingIndex].text == '__THINKING__') {
          _messages[thinkingIndex] = ChatMessage(text: '$_pName(❌): $errMsg', isUser: false);
        } else {
          _messages.add(ChatMessage(text: '$_pName(❌): $errMsg', isUser: false));
        }
      });
      _speak(errMsg);
    } finally {
      _scrollToBottom();
    }
  }

  // ─────────────────────────────────────────────────────────────────────────
  //  PDF upload
  // ─────────────────────────────────────────────────────────────────────────

  Future<void> _uploadPdf() async {
    final result = await FilePicker.platform.pickFiles(
      type: FileType.custom,
      allowedExtensions: ['pdf'],
      withData: true,
    );
    if (result == null || result.files.single.bytes == null) return;

    final bytes = result.files.single.bytes!;
    final filename = result.files.single.name;
    setState(() => _messages.add(
        ChatMessage(text: 'System: Uploading PDF...', isUser: false)));

    try {
      final req =
          http.MultipartRequest('POST', Uri.parse('$baseUrl/upload-pdf'));
      req.files.add(http.MultipartFile.fromBytes(
        'pdf',
        bytes,
        filename: filename,
        contentType: MediaType('application', 'pdf'),
      ));
      final res = await req.send();
      setState(() => _messages.add(ChatMessage(
          text: res.statusCode == 200
              ? 'System: PDF uploaded successfully!'
              : 'System Error: Upload failed (${res.statusCode}).',
          isUser: false)));
    } catch (e) {
      setState(() => _messages.add(
          ChatMessage(text: 'System Error: PDF upload failed.', isUser: false)));
    }
  }

  // ─────────────────────────────────────────────────────────────────────────
  //  Persona switch
  // ─────────────────────────────────────────────────────────────────────────

  Future<void> _switchPersona(String newPersona) async {
    if (_isTtsInProgress) return;
    _unlockAudio();
    setState(() => _currentPersona = newPersona);

    await _loadVideo(AvatarState.talking, force: true); // Force talking animation on switch

    try {
      await http.post(
        Uri.parse('$baseUrl/switch-persona'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({'persona': newPersona}),
      );
      if (_wakeWordSocket != null && _wakeWordSocket!.readyState == html.WebSocket.OPEN) {
        _wakeWordSocket!.send(json.encode({
          'action': 'set_persona',
          'persona': newPersona
        }));
      }
    } catch (_) {}

    final greeting = newPersona == 'linda'
        ? "Hi! I'm Linda, your robotic assistant. How can I help you today?"
        : "Hey! I'm John, your robotic assistant. What can I do for you?";
    setState(() => _messages.add(
        ChatMessage(text: '$_pName($_idleEmoji): $greeting', isUser: false)));
    _scrollToBottom();
    _speak(greeting);
  }

  void _scrollToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scrollController.hasClients) {
        _scrollController.animateTo(
          _scrollController.position.maxScrollExtent,
          duration: const Duration(milliseconds: 300),
          curve: Curves.easeOut,
        );
      }
    });
  }

  void _unlockAudio() {
    try {
      final dummy = html.AudioElement()
        ..src = 'data:audio/wav;base64,UklGRigAAABXQVZFZm10IBIAAAABAAEARKwAAIhYAQACABAAAABkYXRhAgAAAAEA';
      dummy.play().catchError((_) {});
    } catch (_) {}
  }

  // ─────────────────────────────────────────────────────────────────────────
  //  Build helpers
  // ─────────────────────────────────────────────────────────────────────────

  Widget _buildAvatar() {
    // Fetch the cached controllers for the active persona's three states
    final idleAsset = _assetFor(_currentPersona, AvatarState.idle);
    final thinkingAsset = _assetFor(_currentPersona, AvatarState.thinking);
    final talkingAsset = _assetFor(_currentPersona, AvatarState.talking);

    final idleController = _cachedControllers[idleAsset];
    final thinkingController = _cachedControllers[thinkingAsset];
    final talkingController = _cachedControllers[talkingAsset];

    // Show indicator if the three videos are not fully loaded yet
    if (idleController == null || !idleController.value.isInitialized ||
        thinkingController == null || !thinkingController.value.isInitialized ||
        talkingController == null || !talkingController.value.isInitialized) {
      return Container(
        color: Colors.white,
        child: const Center(child: CircularProgressIndicator(color: Colors.green)),
      );
    }

    // Helper to compile individual video player viewport
    Widget buildPlayer(VideoPlayerController controller, bool isActive) {
      final double factor = _currentPersona == 'linda' ? 1.0 : 0.8;
      final bool isTalking = controller.dataSource.contains('talking');

      Widget player = FittedBox(
        fit: isTalking ? BoxFit.cover : BoxFit.contain,
        child: SizedBox(
          width: controller.value.size.width,
          height: controller.value.size.height,
          child: VideoPlayer(controller, key: ValueKey(controller)),
        ),
      );

      if (!isTalking) {
        player = FractionallySizedBox(
          widthFactor: factor,
          heightFactor: factor,
          child: player,
        );
      }

      return Container(
        key: ValueKey(controller.dataSource),
        color: Colors.white,
        width: double.infinity,
        height: double.infinity,
        child: IgnorePointer(
          ignoring: !isActive,
          child: player,
        ),
      );
    }

    Widget idlePlayer = buildPlayer(idleController, _avatarState == AvatarState.idle);
    Widget thinkingPlayer = buildPlayer(thinkingController, _avatarState == AvatarState.thinking);
    Widget talkingPlayer = buildPlayer(talkingController, _avatarState == AvatarState.talking);

    List<Widget> stackChildren = [];
    
    // 1. Add inactive players to the bottom of the stack
    if (_avatarState != AvatarState.idle) stackChildren.add(idlePlayer);
    if (_avatarState != AvatarState.thinking) stackChildren.add(thinkingPlayer);
    if (_avatarState != AvatarState.talking) stackChildren.add(talkingPlayer);
    
    // 2. Add active player to the top of the stack (rendered last = on top)
    if (_avatarState == AvatarState.idle) stackChildren.add(idlePlayer);
    if (_avatarState == AvatarState.thinking) stackChildren.add(thinkingPlayer);
    if (_avatarState == AvatarState.talking) stackChildren.add(talkingPlayer);

    return Stack(
      children: stackChildren,
    );
  }

  Widget _buildVisualizer() {
    final bool shouldAnimate = _isTtsInProgress || _avatarState == AvatarState.talking || _isListening;
    
    Widget buildBars(double animationValue) {
      return Row(
        mainAxisAlignment: MainAxisAlignment.center,
        mainAxisSize: MainAxisSize.min,
        children: List.generate(6, (i) {
          final h = 6.0 +
              18.0 *
                  math
                      .sin((animationValue * math.pi * 2) + i * 0.8)
                      .abs();
          return Container(
            margin: const EdgeInsets.symmetric(horizontal: 2),
            width: 4,
            height: h,
            decoration: BoxDecoration(
              color: Colors.greenAccent,
              borderRadius: BorderRadius.circular(4),
            ),
          );
        }),
      );
    }

    if (!shouldAnimate) {
      // If we should not animate, stop the controller ticker and return a static list of bars
      if (_vizController.isAnimating) {
        _vizController.stop();
      }
      return buildBars(0.0); // Stationary visualizer
    }

    // Start the ticker if it was stopped
    if (!_vizController.isAnimating) {
      _vizController.repeat(reverse: true);
    }

    return AnimatedBuilder(
      animation: _vizController,
      builder: (_, __) => buildBars(_vizController.value),
    );
  }

  Widget _buildCollapsedBarContent() {
    final bool isTalking = _isTtsInProgress || _avatarState == AvatarState.talking;
    if (isTalking) {
      return Center(
        child: Row(
          mainAxisAlignment: MainAxisAlignment.center,
          mainAxisSize: MainAxisSize.min,
          children: [
            Flexible(
              child: Text(
                '$_pName($_answeringEmoji): $_pName is talking ',
                style: const TextStyle(
                  color: Colors.white,
                  fontSize: 15,
                  fontWeight: FontWeight.bold,
                ),
                overflow: TextOverflow.ellipsis,
              ),
            ),
            _buildBouncingDots(color: Colors.greenAccent),
          ],
        ),
      );
    }
    
    final bool isThinking = _avatarState == AvatarState.thinking ||
        (_messages.isNotEmpty && _messages.last.text == '__THINKING__');
    if (isThinking) {
      return Center(
        child: Row(
          mainAxisAlignment: MainAxisAlignment.center,
          mainAxisSize: MainAxisSize.min,
          children: [
            Flexible(
              child: Text(
                '$_pName($_idleEmoji): $_pName is thinking ',
                style: const TextStyle(
                  color: Colors.white,
                  fontSize: 15,
                  fontWeight: FontWeight.bold,
                ),
                overflow: TextOverflow.ellipsis,
              ),
            ),
            _buildBouncingDots(color: Colors.amber),
          ],
        ),
      );
    }
    
    return Row(
      children: [
        _circleBtn(
          _isListening ? Icons.mic : Icons.mic_none,
          _isInteractionBlocked
              ? Colors.grey
              : (_isListening ? Colors.red : Colors.blue),
          _isInteractionBlocked ? () {} : _listen,
        ),
        const SizedBox(width: 10),
        if (_isListening) ...[
          _buildVisualizer(),
          const SizedBox(width: 10),
        ],
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Text(
                'Ask $_pName anything...',
                style: const TextStyle(color: Colors.white, fontSize: 14),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
              ),
              if (true) ...[
                const SizedBox(height: 2),
                Text(
                  _audioServerConnected ? '🟢 Wakeword Engine Ready' : '🔴 Wakeword Engine Loading...',
                  style: TextStyle(
                    color: _audioServerConnected ? Colors.greenAccent : Colors.redAccent,
                    fontSize: 10,
                  ),
                ),
              ],
            ],
          ),
        ),
      ],
    );
  }

  /// Three bouncing dots animation for "Speaking..." and "Thinking..."
  Widget _buildBouncingDots({Color color = Colors.white}) {
    return AnimatedBuilder(
      animation: _vizController,
      builder: (_, __) {
        return Row(
          mainAxisSize: MainAxisSize.min,
          children: List.generate(3, (i) {
            final offset = 4.0 *
                math.sin((_vizController.value * math.pi * 2) + i * 1.2).abs();
            return Container(
              margin: const EdgeInsets.symmetric(horizontal: 2),
              child: Transform.translate(
                offset: Offset(0, -offset),
                child: Container(
                  width: 8,
                  height: 8,
                  decoration: BoxDecoration(
                    color: color,
                    shape: BoxShape.circle,
                  ),
                ),
              ),
            );
          }),
        );
      },
    );
  }

  Widget _buildChatBubble(ChatMessage msg) {
    // Thinking placeholder → show bouncing dots
    if (!msg.isUser && msg.text == '__THINKING__') {
      return Align(
        alignment: Alignment.centerLeft,
        child: Container(
          margin: const EdgeInsets.symmetric(vertical: 3, horizontal: 4),
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
          constraints: const BoxConstraints(maxWidth: 340),
          decoration: BoxDecoration(
            color: Colors.grey[800],
            borderRadius: const BorderRadius.only(
              topLeft: Radius.circular(16),
              topRight: Radius.circular(16),
              bottomLeft: Radius.circular(4),
              bottomRight: Radius.circular(16),
            ),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text('$_pName is thinking ',
                  style: const TextStyle(color: Colors.white70, fontSize: 13, fontStyle: FontStyle.italic)),
              _buildBouncingDots(color: Colors.amber),
            ],
          ),
        ),
      );
    }
    return Align(
      alignment: msg.isUser ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: 3, horizontal: 4),
        padding:
            const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
        constraints: const BoxConstraints(maxWidth: 340),
        decoration: BoxDecoration(
          color: msg.isUser ? Colors.green[700] : Colors.grey[800],
          borderRadius: BorderRadius.only(
            topLeft: const Radius.circular(16),
            topRight: const Radius.circular(16),
            bottomLeft: Radius.circular(msg.isUser ? 16 : 4),
            bottomRight: Radius.circular(msg.isUser ? 4 : 16),
          ),
        ),
        child: Text(
          msg.text,
          style: const TextStyle(color: Colors.white, fontSize: 13),
        ),
      ),
    );
  }

  Widget _circleBtn(IconData icon, Color color, VoidCallback onTap,
      {double radius = 22}) {
    return GestureDetector(
      onTap: onTap,
      child: CircleAvatar(
        radius: radius,
        backgroundColor: color,
        child: Icon(icon, color: Colors.white, size: radius * 0.9),
      ),
    );
  }

  Widget _buildExpandedChat() {
    return Container(
      height: 380, // Fixed height!
      padding: const EdgeInsets.fromLTRB(14, 14, 14, 12),
      decoration: BoxDecoration(
        color: Colors.grey[900]!.withOpacity(0.93),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: Colors.white12),
      ),
      child: Column(
        children: [
          // ── Chat history ──────────────────────────────────
          Expanded(
            child: _messages.isEmpty
                ? const Center(
                    child: Text('No messages yet',
                        style: TextStyle(color: Colors.white38)))
                : ListView.builder(
                    controller: _scrollController,
                    itemCount: _messages.length,
                    itemBuilder: (_, i) => _buildChatBubble(_messages[i]),
                  ),
          ),
          const SizedBox(height: 8),

          // ── Visualizer when listening ─────────────────────
          if (_isListening) ...[
            _buildVisualizer(),
            const SizedBox(height: 8),
          ],

          // ── Preset sample questions + Stop + Clear Row ───
          Align(
            alignment: Alignment.centerLeft,
            child: Wrap(
              alignment: WrapAlignment.start,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                _buildPresetQuestion("Pick up?", "How do I instruct the robotic arm to pick up a screwdriver?"),
                _buildPresetQuestion("Payload?", "What are the payload limits of this robotic arm?"),
                _buildPresetQuestion("Calibrate?", "Can you explain the calibration process for the arm?"),
                _buildPillBtn("Stop", Icons.stop, Colors.amber[700]!, _stopSpeaking, allowAlways: true),
                _buildPillBtn("Clear", Icons.delete, Colors.red[600]!, _clearChat, allowAlways: true),
              ],
            ),
          ),
          const SizedBox(height: 8),
          if (true) ...[
            Row(
              mainAxisAlignment: MainAxisAlignment.end,
              children: [
                Text(
                  _audioServerConnected ? '🟢 Wakeword: $_visibleStateText' : '🔴 Wakeword Engine Loading...',
                  style: TextStyle(
                    color: _audioServerConnected ? Colors.greenAccent : Colors.redAccent,
                    fontSize: 10,
                    fontWeight: FontWeight.bold,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 4),
          ],

          // ── Input row ─────────────────────────────────────
          Row(
            children: [
              Expanded(
                child: TextField(
                  controller: _textController,
                  style: const TextStyle(color: Colors.white),
                  decoration: InputDecoration(
                     hintText: 'Type a question...',
                    hintStyle: const TextStyle(color: Colors.green),
                    filled: true,
                    fillColor: Colors.grey[800],
                    contentPadding: const EdgeInsets.symmetric(
                        horizontal: 16, vertical: 10),
                    border: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(25),
                      borderSide: BorderSide.none,
                    ),
                  ),
                  onSubmitted: _sendMessage,
                ),
              ),
              const SizedBox(width: 8),
              // Headphone (hands-free toggle) - green when active (LEFT of mic)
              _circleBtn(
                Icons.headphones,
                _currentState != HandsOffState.handsOffOff ? Colors.green : Colors.grey[600]!,
                _toggleHandsFree,
                radius: 18,
              ),
              const SizedBox(width: 6),
              // Mic
              _circleBtn(
                _isListening ? Icons.mic : Icons.mic_none,
                _isInteractionBlocked
                    ? Colors.grey
                    : (_isListening ? Colors.red : Colors.blue),
                _isInteractionBlocked ? () {} : _listen,
                radius: 22,
              ),
              const SizedBox(width: 6),
              // Send
              _circleBtn(
                Icons.send,
                _isInteractionBlocked ? Colors.grey : Colors.green,
                _isInteractionBlocked ? () {} : () => _sendMessage(_textController.text),
                radius: 22,
              ),
            ],
          ),
        ],
      ),
    );
  }

  // ─────────────────────────────────────────────────────────────────────────
  //  Build
  // ─────────────────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.white,
      body: Stack(
        children: [
          // ── Avatar (full screen) ──────────────────────────
          Positioned.fill(child: _buildAvatar()),

          // ── Debug Overlay ─────────────────────────────────
          if (_showDebugPanel)
            Positioned(
              left: 20,
              top: 80,
              width: 320,
              bottom: 160,
              child: Container(
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: Colors.black.withOpacity(0.85),
                  borderRadius: BorderRadius.circular(16),
                  border: Border.all(color: Colors.white24, width: 1.5),
                  boxShadow: const [
                    BoxShadow(
                      color: Colors.black54,
                      blurRadius: 10,
                      offset: Offset(0, 4),
                    )
                  ],
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        const Text(
                          '🐞 DEBUGGING HUD',
                          style: TextStyle(
                            color: Colors.greenAccent,
                            fontWeight: FontWeight.bold,
                            fontSize: 14,
                            fontFamily: 'monospace',
                          ),
                        ),
                        IconButton(
                          icon: const Icon(Icons.clear, color: Colors.white70, size: 16),
                          constraints: const BoxConstraints(),
                          padding: EdgeInsets.zero,
                          onPressed: () {
                            setState(() {
                              _micDebugLogs.clear();
                            });
                          },
                          tooltip: 'Clear Logs',
                        ),
                      ],
                    ),
                    const Divider(color: Colors.white30, height: 8),
                    Expanded(
                      child: ListView.builder(
                        itemCount: _micDebugLogs.length,
                        reverse: true, // Show newest logs at the bottom/start
                        itemBuilder: (context, index) {
                          final logText = _micDebugLogs[_micDebugLogs.length - 1 - index];
                          return Padding(
                            padding: const EdgeInsets.symmetric(vertical: 2.0),
                            child: Text(
                              logText,
                              style: const TextStyle(
                                color: Colors.green,
                                fontSize: 11,
                                fontFamily: 'monospace',
                              ),
                            ),
                          );
                        },
                      ),
                    ),
                  ],
                ),
              ),
            ),


          // ── Logo ──────────────────────────────────────────
          Positioned(
            top: 20,
            right: 20,
            child: SafeArea(
                child: Image.asset('assets/singaporepoly.png', height: 40)),
          ),

          // ── E-STOP Button ─────────────────────────────────
          Positioned(
            top: 20,
            right: 170,
            child: SafeArea(
              child: ElevatedButton.icon(
                onPressed: _emergencyStop,
                icon: const Icon(Icons.warning, size: 18, color: Colors.white),
                label: const Text('E-STOP',
                    style: TextStyle(fontWeight: FontWeight.bold, fontSize: 13)),
                style: ElevatedButton.styleFrom(
                  backgroundColor: Colors.red[800],
                  foregroundColor: Colors.white,
                  elevation: 8,
                  padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(20),
                    side: const BorderSide(color: Colors.white, width: 2),
                  ),
                ),
              ),
            ),
          ),

          // ── HOME Button ─────────────────────────────────
          Positioned(
            top: 65,
            right: 170,
            child: SafeArea(
              child: ElevatedButton.icon(
                onPressed: _returnHome,
                icon: const Icon(Icons.home, size: 18, color: Colors.white),
                label: const Text('HOME',
                    style: TextStyle(fontWeight: FontWeight.bold, fontSize: 13)),
                style: ElevatedButton.styleFrom(
                  backgroundColor: Colors.blue[700],
                  foregroundColor: Colors.white,
                  elevation: 8,
                  padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 8),
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(20),
                    side: const BorderSide(color: Colors.white, width: 2),
                  ),
                ),
              ),
            ),
          ),

          // ── Back button ───────────────────────────────────
          Positioned(
            top: 20,
            left: 20,
            child: SafeArea(
              child: ElevatedButton.icon(
                onPressed: () => Navigator.pop(context),
                icon: const Icon(Icons.arrow_back, size: 16),
                label: const Text('Back'),
                style: ElevatedButton.styleFrom(
                  backgroundColor: Colors.grey[600],
                  foregroundColor: Colors.white,
                  padding: const EdgeInsets.symmetric(
                      horizontal: 16, vertical: 8),
                  shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(20)),
                ),
              ),
            ),
          ),

          // ── Debug HUD Toggle Button ──────────────────────
          Positioned(
            top: 20,
            left: 140,
            child: SafeArea(
              child: ElevatedButton.icon(
                onPressed: () {
                  setState(() {
                    _showDebugPanel = !_showDebugPanel;
                  });
                },
                icon: Icon(_showDebugPanel ? Icons.bug_report : Icons.bug_report_outlined, size: 16),
                label: Text(_showDebugPanel ? 'Hide Debug' : 'Show Debug'),
                style: ElevatedButton.styleFrom(
                  backgroundColor: _showDebugPanel ? Colors.red[700] : Colors.grey[850],
                  foregroundColor: Colors.white,
                  padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
                  shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(20)),
                ),
              ),
            ),
          ),

          // ── Collapsed status bar (menu hidden) ────────────
          if (!_isMenuVisible)
            Positioned(
              bottom: 76,
              left: 80,
              right: 80,
              height: 68, // Fixed height for consistency and shape!
              child: Container(
                padding: const EdgeInsets.symmetric(
                    horizontal: 18, vertical: 12),
                decoration: BoxDecoration(
                  color: Colors.grey[900]!.withOpacity(0.87),
                  borderRadius: BorderRadius.circular(14),
                  border: Border.all(color: Colors.white24),
                ),
                child: _buildCollapsedBarContent(),
              ),
            ),

          // ── Expanded chat panel ───────────────────────────
          if (_isMenuVisible)
            Positioned(
              bottom: 88,
              left: 14,
              right: 14,
              child: _buildExpandedChat(),
            ),

          // ── Persona switch – bottom left ──────────────────
          Positioned(
            bottom: 26,
            left: 16,
            child: GestureDetector(
              onTap: () => _switchPersona(
                  _currentPersona == 'john' ? 'linda' : 'john'),
              child: AnimatedContainer(
                duration: const Duration(milliseconds: 300),
                padding: const EdgeInsets.symmetric(
                    horizontal: 16, vertical: 10),
                decoration: BoxDecoration(
                  color: _currentPersona == 'linda'
                      ? Colors.pink[600]
                      : Colors.blue[700],
                  borderRadius: BorderRadius.circular(30),
                  boxShadow: [
                    BoxShadow(
                      color: (_currentPersona == 'linda'
                              ? Colors.pink
                              : Colors.blue)
                          .withOpacity(0.45),
                      blurRadius: 10,
                      spreadRadius: 2,
                    ),
                  ],
                ),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(
                      _currentPersona == 'linda'
                          ? Icons.female
                          : Icons.male,
                      color: Colors.white,
                      size: 20,
                      key: const ValueKey('persona_icon'),
                    ),
                    const SizedBox(width: 6),
                    Text(
                      _currentPersona == 'linda' ? 'Linda ⇆' : 'John ⇆',
                      style: const TextStyle(
                          color: Colors.white,
                          fontWeight: FontWeight.bold,
                          fontSize: 15),
                    ),
                  ],
                ),
              ),
            ),
          ),

          // ── Upload PDF + CC Toggle + Menu toggle – bottom right ──
          Positioned(
            bottom: 26,
            right: 16,
            child: Row(
              children: [
                if (_isMenuVisible) ...[
                  ElevatedButton.icon(
                    onPressed: _uploadPdf,
                    icon: const Icon(Icons.upload_file, size: 16),
                    label: const Text('Upload PDF'),
                    style: ElevatedButton.styleFrom(
                      backgroundColor: Colors.grey[700],
                      foregroundColor: Colors.white,
                      shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(20)),
                      padding: const EdgeInsets.symmetric(
                          horizontal: 16, vertical: 10),
                    ),
                  ),
                  const SizedBox(width: 8),
                ],
                ElevatedButton.icon(
                  onPressed: () => setState(() => _isCcEnabled = !_isCcEnabled),
                  icon: Icon(
                    _isCcEnabled ? Icons.closed_caption : Icons.closed_caption_disabled,
                    size: 16,
                  ),
                  label: Text(_isCcEnabled ? 'CC On' : 'CC Off'),
                  style: ElevatedButton.styleFrom(
                    backgroundColor: Colors.blue[600],
                    foregroundColor: Colors.white,
                    shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(20)),
                    padding: const EdgeInsets.symmetric(
                        horizontal: 16, vertical: 10),
                  ),
                ),
                const SizedBox(width: 8),
                ElevatedButton.icon(
                  onPressed: () =>
                      setState(() => _isMenuVisible = !_isMenuVisible),
                  icon: const Icon(Icons.menu, size: 16),
                  label: Text(_isMenuVisible ? 'Hide Menu' : 'Menu'),
                  style: ElevatedButton.styleFrom(
                    backgroundColor: Colors.grey[700],
                    foregroundColor: Colors.white,
                    shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(20)),
                    padding: const EdgeInsets.symmetric(
                        horizontal: 16, vertical: 10),
                  ),
                ),
              ],
            ),
          ),

          // ── Subtitle Overlay ──
          // Separate box above the message box showing what the robot is saying
          Positioned(
            bottom: _isMenuVisible ? 518 : 194, // Exactly 50px above the message box!
            left: 30,
            right: 30,
            height: 110, // Strictly bounded height to prevent layout crashes on Flutter Web!
            child: Visibility(
              visible: _isCcEnabled && _isTtsInProgress && (_currentSubtitleText ?? '').isNotEmpty,
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 12),
                decoration: BoxDecoration(
                  color: Colors.black.withOpacity(0.85),
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(color: Colors.white24),
                ),
                child: Scrollbar(
                  controller: _subtitleScrollController,
                  thumbVisibility: true,
                  child: SingleChildScrollView(
                    controller: _subtitleScrollController,
                    physics: const BouncingScrollPhysics(),
                    child: Container(
                      alignment: Alignment.center,
                      width: double.infinity,
                      child: Text(
                        _currentSubtitleText ?? '',
                        textAlign: TextAlign.center,
                        style: const TextStyle(
                          color: Colors.white,
                          fontSize: 15,
                          fontWeight: FontWeight.w500,
                          height: 1.4,
                        ),
                      ),
                    ),
                  ),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

```

</details>

---

<details>
<summary>📂 <b>roboas/server.js</b> (Click to expand)</summary>

```javascript
const express = require('express');
const multer = require('multer');
const pdfParse = require('pdf-parse');
const cors = require('cors');
const fs = require('fs');
const path = require('path');
const { Configuration, OpenAIApi } = require('openai');
const { RecursiveCharacterTextSplitter } = require('langchain/text_splitter');
require('langchain/vectorstores/memory');
const { MemoryVectorStore } = require('langchain/vectorstores/memory');
const { OpenAIEmbeddings } = require('@langchain/openai');
// const { SerialPort } = require('serialport'); // Arduino removed
const { exec } = require('child_process');
const https = require('https');
const { Client } = require("@modelcontextprotocol/sdk/client/index.js");
const { StdioClientTransport } = require("@modelcontextprotocol/sdk/client/stdio.js");
const { SSEClientTransport } = require("@modelcontextprotocol/sdk/client/sse.js");
const { search } = require('duck-duck-scrape');
const WebSocket = require('ws');
const http = require('http');
require('dotenv').config();

const WAKEWORD_WS_URL = process.env.WAKEWORD_WS_URL || 'ws://localhost:8003';

// ==========================================
// CONFIGURATION
// ==========================================
// Laptop B (Robot/Vision Laptop) IP Address
const LAPTOP_B_IP = "192.168.2.99"; // Ethernet IP for Laptop B
// === Tool Call Activity Log ===
let toolCallLog = [];
const TOOL_LOG_FILE = path.join(__dirname, 'gpt_tool_log.json');
try {
  if (fs.existsSync(TOOL_LOG_FILE)) {
    toolCallLog = JSON.parse(fs.readFileSync(TOOL_LOG_FILE, 'utf-8'));
    console.log(`📜 Loaded ${toolCallLog.length} tool logs from disk.`);
  }
} catch (e) {
  console.error('Failed to load tool log from disk:', e.message);
}

function logToolCall(userQuestion, toolName, args, result) {
  const entry = {
    timestamp: new Date().toISOString(),
    userQuestion: userQuestion.substring(0, 50) + (userQuestion.length > 50 ? '...' : ''),
    toolName,
    args,
    result
  };
  toolCallLog.push(entry);
  if (toolCallLog.length > 50) toolCallLog.shift(); // Keep last 50 entries
  
  // High-Visibility Colorized Terminal Output
  const cyan = "\x1b[36m";
  const green = "\x1b[32m";
  const yellow = "\x1b[33m";
  const reset = "\x1b[0m";

  console.log("\n" + "=".repeat(50));
  console.log(`🤖 ${cyan}[MCP TOOL TRIGGERED]: ${toolName.toUpperCase()}${reset}`);
  console.log(`❓ User Asked: "${userQuestion}"`);
  console.log(`📦 Arguments:  ${yellow}${JSON.stringify(args)}${reset}`);
  console.log(`✅ Result:     ${green}${result}${reset}`);
  console.log("=".repeat(50) + "\n");

  // Write to disk so Claude Desktop MCP server can read it
  try {
    fs.writeFileSync(TOOL_LOG_FILE, JSON.stringify(toolCallLog, null, 2));
  } catch (e) {
    console.error('❌ \x1b[31mFailed to write tool log to disk:\x1b[0m', e.message);
  }
}

// === MCP Emoji Server Client ===
let mcpEmojiClient = null;
let isEmojiConnected = false;
async function startMcpClient() {
  if (isEmojiConnected) return;
  try {
    const transport = new StdioClientTransport({
      command: "python",
      args: [path.join(__dirname, "mcp_emoji_server.py")]
    });
    mcpEmojiClient = new Client({ name: "roboas-main", version: "1.0.0" }, { capabilities: {} });
    await mcpEmojiClient.connect(transport);
    isEmojiConnected = true;
    console.log("✅ \x1b[32mMCP Emoji Server (Python) connected via Stdio\x1b[0m");
  } catch (err) {
    console.error("❌ \x1b[31mFailed to bind MCP Client:\x1b[0m", err.message);
    isEmojiConnected = false;
    mcpEmojiClient = null;
  }
}
startMcpClient();

// === Wake Word Server (Python) ===
function startWakeWordServer() {
  try {
    const { spawn } = require('child_process');
    const wakeWordProcess = spawn('python', ['-u', path.join(__dirname, 'wakeword_server.py')]);
    
    wakeWordProcess.stdout.on('data', (data) => {
      console.log(`[WAKEWORD]: ${data.toString().trim()}`);
    });
    
    wakeWordProcess.stderr.on('data', (data) => {
      console.error(`[WAKEWORD ERROR]: ${data.toString().trim()}`);
    });
    
    console.log("✅ Python Wake Word Server spawned automatically.");
  } catch (err) {
    console.error("❌ Failed to start Wake Word Server:", err.message);
  }
}
// startWakeWordServer(); // TEMPORARILY DISABLED FOR MANUAL MIC TESTING

// === Vision MCP Server Client (Remote on Laptop B) ===
let visionMcpClient = null;
let isVisionConnected = false;
async function startVisionMcpClient() {
  if (isVisionConnected) return;
  try {
    const transport = new SSEClientTransport(new URL(`http://${LAPTOP_B_IP}:8001/sse`));
    visionMcpClient = new Client({ name: "roboas-main", version: "1.0.0" }, { capabilities: {} });
    await visionMcpClient.connect(transport);
    isVisionConnected = true;
    console.log(`✅ \x1b[32mVision MCP Server connected via SSE at ${LAPTOP_B_IP}:8001\x1b[0m`);
  } catch (err) {
    console.error(`❌ \x1b[31mFailed to bind Vision MCP Client at ${LAPTOP_B_IP}:\x1b[0m`, err.message);
    isVisionConnected = false;
    visionMcpClient = null;
  }
}
startVisionMcpClient();

// === Robot MCP Server Client (Local/Ethernet via SSE) ===
let robotMcpClient = null;
let isRobotConnected = false;
async function startRobotMcpClient() {
  if (isRobotConnected) return;
  try {
    const transport = new SSEClientTransport(new URL(`http://${LAPTOP_B_IP}:8002/sse`));
    robotMcpClient = new Client({ name: "roboas-robot-mcp", version: "1.0.0" }, { capabilities: {} });
    await robotMcpClient.connect(transport);
    isRobotConnected = true;
    console.log(`✅ \x1b[32mRobot MCP Server connected via SSE at ${LAPTOP_B_IP}:8002\x1b[0m`);
  } catch (err) {
    console.error("❌ \x1b[31mFailed to bind Robot MCP Client:\x1b[0m", err.message);
    isRobotConnected = false;
    robotMcpClient = null;
  }
}
startRobotMcpClient();

// Periodic Reconnection Check Loop
setInterval(() => {
  if (!isEmojiConnected) {
    console.log("🔌 Attempting to reconnect to Emoji MCP Server...");
    startMcpClient();
  }
  if (!isVisionConnected) {
    console.log("🔌 Attempting to reconnect to Vision MCP Server...");
    startVisionMcpClient();
  }
  if (!isRobotConnected) {
    console.log("🔌 Attempting to reconnect to Robot MCP Server...");
    startRobotMcpClient();
  }
}, 10000);

async function getStatusEmoji(state) {
  if (!mcpEmojiClient) return state === "answering" ? "🤖" : "🤗";
  try {
    const result = await mcpEmojiClient.callTool({
      name: "get_status_emoji",
      arguments: { state }
    });
    const emoji = result.content[0].text;
    logToolCall("System Status", "get_status_emoji", { state }, `updated to ${emoji}`);
    return emoji;
  } catch (e) {
    console.error("❌ \x1b[31mMCP Tool error:\x1b[0m", e.message);
    isEmojiConnected = false;
    mcpEmojiClient = null;
    return state === "answering" ? "🤖" : "🤗";
  }
}

// Claude Tool Definitions
const CLAUDE_TOOLS = [
  {
    name: "switch_avatar",
    description: "Switch the persona to John (male) or Linda (female) when instructed.",
    input_schema: {
      type: "object",
      properties: {
        persona: { type: "string", enum: ["john", "linda"] }
      },
      required: ["persona"]
    }
  },
  {
      name: "get_status_emoji",
      description: "Get the current status emoji for the bot state (answering or idle).",
      input_schema: {
        type: "object",
        properties: {
          state: { type: "string", enum: ["answering", "idle"] }
        },
        required: ["state"]
      }
  }
];

async function switchAvatar(persona) {
  if (!mcpEmojiClient) {
    currentPersona = persona;
    return persona;
  }
  try {
    const result = await mcpEmojiClient.callTool({
      name: "switch_avatar",
      arguments: { persona }
    });
    currentPersona = persona;
    
    // Log MCP switch action
    logToolCall("System Command", "switch_avatar", { persona }, `switched to ${persona}`);
    
    return result.content[0].text;
  } catch (e) {
    console.error("❌ \x1b[31mMCP Tool error:\x1b[0m", e.message);
    isEmojiConnected = false;
    mcpEmojiClient = null;
    currentPersona = persona;
    return persona;
  }
}



const app = express();
const port = 3000;
const uploadsDir = path.join(__dirname, 'uploads');
if (!fs.existsSync(uploadsDir)) fs.mkdirSync(uploadsDir, { recursive: true });
app.use(express.static(path.join(__dirname, 'Public')));
app.use(cors());
app.use(express.json());

// === OpenAI Configuration ===
const configuration = new Configuration({
  apiKey: "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"
});
const openai = new OpenAIApi(configuration);

// === Multer Storage Setup ===
const storage = multer.diskStorage({
  destination: (req, file, cb) => cb(null, uploadsDir),
  filename: (req, file, cb) => {
    const uniqueSuffix = Date.now() + '-' + Math.round(Math.random() * 1E9);
    cb(null, file.fieldname + '-' + uniqueSuffix + path.extname(file.originalname));
  }
});
const upload = multer({
  storage,
  fileFilter: (req, file, cb) => {
    const allowedMimeTypes = [
      'application/pdf',
      'audio/mpeg', // .mp3
      'audio/wav',  // .wav
      'audio/ogg',  // .ogg
      'audio/webm', // .webm
      'audio/mp4'   // .mp4 (for audio)
    ];
    if (allowedMimeTypes.includes(file.mimetype)) cb(null, true);
    else cb(new Error('Only PDF or audio files are allowed'), false);
  },
  limits: { fileSize: 25 * 1024 * 1024 } // Increased limit for audio files
});

let chatHistory = [];

// === MCP Emoji Sync Endpoint ===
app.get('/status-emojis', async (req, res) => {
  // Force reset State to John on frontend boot
  currentPersona = "john";
  chatHistory = []; // Wipe conversation memory for new user session
  
  await switchAvatar("john"); // Ensures MCP server resets as well

  const answering = await getStatusEmoji("answering");
  const idle = await getStatusEmoji("idle");
  res.json({ success: true, answering, idle });
});

// === PDF Processing Logic ===
let vectorStore = null;
let currentPdfName = '';
let currentPdfPath = '';
const PDF_TRACKER_FILE = path.join(__dirname, 'current_pdf.json');

async function processPdf(filePath, filename) {
  try {
    console.log(`📄 Processing PDF: ${filename}`);
    const buffer = fs.readFileSync(filePath);
    const data = await pdfParse(buffer);

    if (!data.text || data.text.trim().length === 0)
      throw new Error('PDF is either empty or image-based');

    const splitter = new RecursiveCharacterTextSplitter({ chunkSize: 1000, chunkOverlap: 200 });
    const chunks = await splitter.splitText(data.text);

    const embeddings = new OpenAIEmbeddings({ openAIApiKey: configuration.apiKey, modelName: "text-embedding-ada-002" });
    vectorStore = await MemoryVectorStore.fromTexts(chunks, {}, embeddings);

    currentPdfName = filename;
    currentPdfPath = filePath;

    fs.writeFileSync(PDF_TRACKER_FILE, JSON.stringify({
      filename, path: filePath, uploadedAt: new Date().toISOString()
    }));

    console.log(`✅ PDF processed: ${chunks.length} chunks`);
    return { success: true, chunks: chunks.length, pages: data.numpages || 'unknown' };
  } catch (error) {
    console.error('❌ PDF Processing Error:', error);
    if (fs.existsSync(filePath)) fs.unlinkSync(filePath);
    throw error;
  }
}

async function loadLatestPdf() {
  try {
    if (fs.existsSync(PDF_TRACKER_FILE)) {
      const tracker = JSON.parse(fs.readFileSync(PDF_TRACKER_FILE));
      if (fs.existsSync(tracker.path)) {
        await processPdf(tracker.path, tracker.filename);
        console.log(`📁 Loaded from tracker: ${tracker.filename}`);
        return;
      }
    }

    const files = fs.readdirSync(uploadsDir);
    const pdfs = files.filter(f => f.endsWith('.pdf')).sort().reverse();
    if (pdfs.length > 0) {
      const pdfPath = path.join(uploadsDir, pdfs[0]);
      await processPdf(pdfPath, pdfs[0]);
      console.log(`📁 Loaded fallback PDF: ${pdfs[0]}`);
    } else {
      console.log('⚠️ No PDFs available to load.');
    }
  } catch (err) {
    console.error('❌ Error loading latest PDF:', err.message);
  }
}

// Clear PDF state on startup so each session starts fresh
function clearPdfOnStartup() {
  try {
    // Delete the tracker file so no PDF is remembered
    if (fs.existsSync(PDF_TRACKER_FILE)) {
      fs.unlinkSync(PDF_TRACKER_FILE);
    }
    // Delete all uploaded PDF files so storage stays clean
    if (fs.existsSync(uploadsDir)) {
      const files = fs.readdirSync(uploadsDir);
      files.filter(f => f.endsWith('.pdf')).forEach(f => {
        try { fs.unlinkSync(path.join(uploadsDir, f)); } catch (_) {}
      });
    }
    currentPdfName = '';
    currentPdfPath = '';
    vectorStore = null;
    console.log('🧹 PDF session cleared on startup.');
  } catch (err) {
    console.error('❌ Error clearing PDF on startup:', err.message);
  }
}

// === Dynamic Reasoning Classifier ===
function getReasoningLevel(question) {
  const q = question.toLowerCase().trim();

  // HIGH: complex multi-step, sequential, or planning-heavy instructions
  const highPatterns = [
    /step.*(by|then|after|sequence)/,
    /(then|after that|followed by|next|finally)/,
    /(multiple|several|both|all of the)/,
    /(plan|strategy|sequence|workflow|procedure)/,
    /(pick up.*and.*place|move.*then|rotate.*while)/,
    /(calibrat|troubleshoot|diagnos|explain why|analyse|analyze)/,
    /(compare|difference between|contrast)/,
    /(how do i|how should i|what is the best way)/
  ];

  // LOW: simple status checks, greetings, single-word commands
  const lowPatterns = [
    /^(hi|hello|hey|ok|okay|yes|no|stop|pause|resume)$/,
    /^(what is your name|who are you|status|ready)$/,
    /(switch (to|back)|change (to|back))/,
    /(led (on|off)|turn (on|off))/,
    /^.{0,20}$/ // very short questions (under 20 chars)
  ];

  if (highPatterns.some(p => p.test(q))) {
    console.log(`🧠 Reasoning: HIGH for "${q.substring(0, 40)}"`);
    return "high";
  }
  if (lowPatterns.some(p => p.test(q))) {
    console.log(`⚡ Reasoning: LOW for "${q.substring(0, 40)}"`);
    return "low";
  }
  console.log(`🔄 Reasoning: MEDIUM for "${q.substring(0, 40)}"`);
  return "medium";
}

// Map to keep track of active operations and their completion promises/resolvers
let activeOperations = new Map();

function waitForRobotEvent(timeoutMs = 300000) {
  return new Promise((resolve, reject) => {
    let resolved = false;
    
    const timer = setTimeout(() => {
      if (!resolved) {
        resolved = true;
        activeOperations.delete('robot_completion');
        reject(new Error("Timeout waiting for robot completion event."));
      }
    }, timeoutMs);

    activeOperations.set('robot_completion', (data) => {
      if (!resolved) {
        resolved = true;
        clearTimeout(timer);
        if (data.event === 'error') {
          reject(new Error(data.error || "Robot execution failed."));
        } else {
          resolve(data);
        }
      }
    });
  });
}

// Robot completion event receiver
app.post('/robot-event', (req, res) => {
  const { event, error } = req.body;
  console.log(`🤖 [Robot Event Received]: "${event}" ${error ? `(Error: ${error})` : ''}`);

  if (event === 'relocate_placed') {
    // Notify the frontend with TTS that the relocation is complete and we are proceeding to pick.
    sendProgress(null, true, "I will pick up the requested object now after relocating.");
  }

  if (activeOperations.has('robot_completion')) {
    const resolve = activeOperations.get('robot_completion');
    activeOperations.delete('robot_completion');
    resolve({ event, error });
  }

  res.json({ success: true });
});

// === Endpoints ===

// Progress Status SSE Channel
let progressClients = [];
app.get('/progress', (req, res) => {
  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection', 'keep-alive');
  res.flushHeaders();

  progressClients.push(res);
  req.on('close', () => {
    progressClients = progressClients.filter(client => client !== res);
  });
});

function sendProgress(status, isRobotMoving = false, ttsMessage = null) {
  console.log(`📡 [SSE Progress Broadcast]: "${status}" (isRobotMoving: ${isRobotMoving}, ttsMessage: ${ttsMessage !== null})`);
  logToolCall("System Event", "progress_sse", { status, isRobotMoving, hasTts: ttsMessage !== null }, ttsMessage || "No TTS message.");
  progressClients.forEach(client => {
    try {
      client.write(`data: ${JSON.stringify({ status, isRobotMoving, tts_message: ttsMessage })}\n\n`);
    } catch (e) {
      console.error('❌ SSE write error:', e.message);
    }
  });
}

// Arduino LED endpoint removed.

// Upload PDF
app.post('/upload-pdf', upload.single('pdf'), async (req, res) => {
  if (!req.file) return res.status(400).json({ success: false, message: 'No PDF uploaded.' });
  try {
    const result = await processPdf(req.file.path, req.file.originalname);
    res.json({ success: true, message: 'PDF uploaded.', ...result, filename: req.file.originalname });
  } catch (error) {
    res.status(400).json({ success: false, message: error.message });
  }
});

// Clear / Remove PDF
app.post('/clear-pdf', async (req, res) => {
  try {
    // Delete the physical file
    if (currentPdfPath && fs.existsSync(currentPdfPath)) {
      fs.unlinkSync(currentPdfPath);
    }
    // Delete tracker
    if (fs.existsSync(PDF_TRACKER_FILE)) {
      fs.unlinkSync(PDF_TRACKER_FILE);
    }
    // Reset in-memory state
    currentPdfName = '';
    currentPdfPath = '';
    vectorStore = null;
    console.log('🗑️ PDF cleared by user.');
    res.json({ success: true, message: 'PDF removed.' });
  } catch (err) {
    console.error('❌ Error clearing PDF:', err.message);
    res.status(500).json({ success: false, message: err.message });
  }
});
// Get current PDF status
app.get('/pdf-status', (req, res) => {
  res.json({ loaded: !!currentPdfName, filename: currentPdfName || null });
});

// Transcribe Audio (Whisper STT - Optimized for Option B: Prompt + Regex + LLM)
app.post('/transcribe', upload.single('audio'), async (req, res) => {
  console.log(`\n\n=== 🎙️ [POST /transcribe] Endpoint Hit! ===`);
  if (!req.file) {
    console.log(`❌ [POST /transcribe] Error: No audio file uploaded.`);
    logToolCall("System Event", "transcribe_error", {}, "No audio file uploaded.");
    return res.status(400).json({ success: false, message: 'No audio file uploaded.' });
  }
  
  console.log(`✅ [POST /transcribe] Uploaded file size: ${req.file.size} bytes`);
  console.log(`✅ [POST /transcribe] Uploaded MIME type: ${req.file.mimetype}`);
  logToolCall("System Event", "transcribe_request", { size: req.file.size, mimetype: req.file.mimetype }, "Transcription request received.");

  try {
    // TIER 1: Optimized Context Prompt (Natural flow)
    const promptString = "Hello John and Linda from Roboas! Welcome to Singapore Polytechnic (SP). Could you please calibrate the robotic arm to pick up the screwdriver and check the payload? Switch avatar, or switch back.";

    const transcription = await openai.createTranscription(
      fs.createReadStream(req.file.path),
      "whisper-1",
      promptString,
      undefined,
      0.0, // Zero temperature for maximum accuracy
      "en"
    );

    let recognizedText = transcription.data.text.trim();
    console.log(`[Whisper - Raw STT]: "${recognizedText}"`);

    // TIER 2: Expanded Phonetic Regex Map (Zero latency fast-fix)
    const cleanupMap = {
      "polytechnic": "Polytechnic",
      "sp": "SP",
      "robo us": "Roboas",
      "robots": "Roboas",
      "robot's": "Roboas",
      "robas": "Roboas",
      "robass": "Roboas",
      "robust": "Roboas",
      "row boss": "Roboas",
      "rubber ass": "Roboas",
      "singapore poly": "Singapore Polytechnic",
      "singa poor poly": "Singapore Polytechnic",
      "sp poly": "Singapore Polytechnic",
      "johns": "John's",
      "lindas": "Linda's",
      "lint ah": "Linda",
      "lint up": "Linda"
    };

    for (const [misheard, correct] of Object.entries(cleanupMap)) {
      const regex = new RegExp(`\\b${misheard}\\b`, 'gi');
      recognizedText = recognizedText.replace(regex, correct);
    }
    console.log(`[Whisper - Regex Fixed]: "${recognizedText}"`);

    // TIER 3: LLM Correction Pass (Highest Accuracy, Small Latency)
    // Only bother fixing if the text is longer than a basic yes/no/hi
    if (recognizedText.length > 3) {
      try {
        const correctionCompletion = await openai.createChatCompletion({
          model: "gpt-5.4-mini", // Very fast, cheap proofreader
          temperature: 0.1, // Near deterministic
          messages: [
            {
              role: "system",
              content: `You are a strict STT proofreader for a robotics chatbot called 'Roboas' at 'Singapore Polytechnic'. 
Your ONLY job is to fix phonetically misheard domain words. 
Do NOT answer the question. Do NOT change the meaning. Do NOT add extra punctuation if not necessary.
Return ONLY the corrected text.
Terms to protect:
- 'Roboas' (often misheard as 'robots', 'robust')
- 'John', 'Linda'
- 'calibrate', 'payload', 'screwdriver'`
            },
            { role: "user", content: recognizedText }
          ]
        });

        const correctedOutput = correctionCompletion.data.choices[0].message.content.trim();
        if (correctedOutput && correctedOutput.length > 0) {
          recognizedText = correctedOutput;
          console.log(`[Whisper - LLM Proofread]: "${recognizedText}"`);
        }
      } catch (llmError) {
        console.error('⚠️ LLM Correction Pass Failed (falling back to regex output):', llmError.message);
      }
    }

    // Clean up the temporary audio file
    if (fs.existsSync(req.file.path)) fs.unlinkSync(req.file.path);

    logToolCall("System Event", "transcribe_success", {}, `Transcription result: "${recognizedText}"`);
    res.json({ success: true, text: recognizedText });
  } catch (error) {
    const errorDetails = error.response ? JSON.stringify(error.response.data) : error.message;
    console.error('❌ Transcription Error:', errorDetails);
    if (fs.existsSync(req.file.path)) fs.unlinkSync(req.file.path);
    logToolCall("System Event", "transcribe_error", {}, `Transcription failed: ${errorDetails}`);
    res.status(500).json({ success: false, message: 'Transcription failed.', error: errorDetails });
  }
});

// Ask Question from PDF
app.post('/ask-question', async (req, res) => {
  const question = req.body.question;
  if (!question) return res.status(400).json({ success: false, message: 'No question provided.' });

  if (!vectorStore) {
    console.log('🧠 Vector store missing. Reloading...');
    await loadLatestPdf();
    if (!vectorStore) {
      return res.status(400).json({ success: false, message: 'No PDF content available.' });
    }
  }

  // Mute wake word during thinking/processing
  await sendWakewordCommand('mute');

  try {
    const relevantDocs = await vectorStore.similaritySearch(question, 3);
    if (!relevantDocs.length) {
      await sendWakewordCommand('unmute');
      return res.json({ success: true, answer: "I couldn't find relevant information in the document." });
    }

    const context = relevantDocs.map(d => d.pageContent).join('\n\n---\n\n');
    const reasoningLevel = getReasoningLevel(question);
    const completion = await openai.createChatCompletion({
      model: "gpt-5.4-mini",
      reasoning_effort: reasoningLevel,
      messages: [
        {
          role: "system",
          content: `You are a super-friendly and excited AI assistant named John. Answer only from the document "${currentPdfName}" in an upbeat and helpful tone. ` +
            `Make it clear in your response that the information comes from the document. ` +
            `For example: "Wow! According to the document..." or "I'm happy to tell you that the PDF states..."\n` +
            `CRITICAL IDENTITY RULE: NEVER start your response with any introduction (e.g., do NOT say "I am John, your robotic assistant" or "I am John, the LARA 5 assistant"). NEVER repeat your name or role unless the user explicitly asks for it. Start answering the user's question directly and immediately.\n` +
            `CRITICAL OUTPUT CLEANLINESS: DO NOT output any raw coordinate data (e.g. coordinates like x, y, z), tool arguments, or structured JSON/dictionary info. Always output only what the user asked for in a conversational tone. No technical data info.\n` +
            `IMPORTANT: Do not use hyphens (-) in your response.`
        },
        { role: "user", content: `Document:\n${context}\n\nQuestion:\n${question}` }
      ],

      max_tokens: 500
    });

    const answer = cleanChatbotResponse(completion.data.choices[0].message.content);
    const emoji = await getStatusEmoji("answering");
    await sendWakewordCommand('unmute');
    res.json({ success: true, answer, emoji, persona: currentPersona });
  } catch (err) {
    await sendWakewordCommand('unmute');
    const errorDetails = err.response ? JSON.stringify(err.response.data) : err.message;
    console.error('❌ Error in /ask-question:', errorDetails);
    res.status(500).json({ success: false, message: 'AI failed to respond.', error: errorDetails });
  }
});

function parseTextToolCall(text) {
  if (!text) return null;
  const toolNames = ["search_web", "switch_avatar", "locate_object", "get_camera_snapshot", "analyse_surroundings", "pick_and_place_object", "relocate_object"];
  let matchedTool = null;
  
  for (const name of toolNames) {
    if (text.includes(name)) {
      matchedTool = name;
      break;
    }
  }
  
  if (!matchedTool) return null;
  
  const jsonStart = text.indexOf('{');
  const jsonEnd = text.lastIndexOf('}');
  
  if (jsonStart !== -1 && jsonEnd !== -1 && jsonEnd > jsonStart) {
    const jsonStr = text.substring(jsonStart, jsonEnd + 1);
    try {
      const args = JSON.parse(jsonStr);
      return { toolName: matchedTool, args };
    } catch (e) {
      if (matchedTool === "search_web") {
        const queryMatch = text.match(/"query"\s*:\s*"([^"]+)"/);
        if (queryMatch) return { toolName: matchedTool, args: { query: queryMatch[1] } };
      } else if (matchedTool === "locate_object") {
        const targetMatch = text.match(/"target_name"\s*:\s*"([^"]+)"/);
        if (targetMatch) return { toolName: matchedTool, args: { target_name: targetMatch[1] } };
      } else if (matchedTool === "switch_avatar") {
        const personaMatch = text.match(/"persona"\s*:\s*"([^"]+)"/);
        if (personaMatch) return { toolName: matchedTool, args: { persona: personaMatch[1] } };
      } else if (matchedTool === "get_camera_snapshot") {
        const questionMatch = text.match(/"question"\s*:\s*"([^"]+)"/);
        if (questionMatch) return { toolName: matchedTool, args: { question: questionMatch[1] } };
      } else if (matchedTool === "analyse_surroundings") {
        const promptMatch = text.match(/"prompt"\s*:\s*"([^"]+)"/);
        if (promptMatch) return { toolName: matchedTool, args: { prompt: promptMatch[1] } };
      } else if (matchedTool === "pick_and_place_object") {
        const objectMatch = text.match(/"object_name"\s*:\s*"([^"]+)"/);
        const xMatch = text.match(/"x"\s*:\s*([0-9.-]+)/);
        const yMatch = text.match(/"y"\s*:\s*([0-9.-]+)/);
        const zMatch = text.match(/"z"\s*:\s*([0-9.-]+)/);
        const angleMatch = text.match(/"angle_deg"\s*:\s*([0-9.-]+)/);
        if (objectMatch && xMatch && yMatch && zMatch) {
          return {
            toolName: matchedTool,
            args: {
              object_name: objectMatch[1],
              x: parseFloat(xMatch[1]),
              y: parseFloat(yMatch[1]),
              z: parseFloat(zMatch[1]),
              angle_deg: angleMatch ? parseFloat(angleMatch[1]) : undefined
            }
          };
        }
      } else if (matchedTool === "relocate_object") {
        const obstacleMatch = text.match(/"obstacle_name"\s*:\s*"([^"]+)"/);
        const xMatch = text.match(/"obstacle_x"\s*:\s*([0-9.-]+)/);
        const yMatch = text.match(/"obstacle_y"\s*:\s*([0-9.-]+)/);
        const zMatch = text.match(/"obstacle_z"\s*:\s*([0-9.-]+)/);
        const angleMatch = text.match(/"obstacle_angle_deg"\s*:\s*([0-9.-]+)/);
        if (obstacleMatch && xMatch && yMatch && zMatch) {
          return {
            toolName: matchedTool,
            args: {
              obstacle_name: obstacleMatch[1],
              obstacle_x: parseFloat(xMatch[1]),
              obstacle_y: parseFloat(yMatch[1]),
              obstacle_z: parseFloat(zMatch[1]),
              obstacle_angle_deg: angleMatch ? parseFloat(angleMatch[1]) : undefined
            }
          };
        }
      }
    }
  }
  
  if (matchedTool === "search_web") {
    const queryMatch = text.match(/"query"\s*:\s*"([^"]+)"/) || text.match(/query\s*=\s*([^&\n\r]+)/);
    if (queryMatch) {
      return { toolName: matchedTool, args: { query: queryMatch[1] } };
    }
  } else if (matchedTool === "get_camera_snapshot") {
    const questionMatch = text.match(/"question"\s*:\s*"([^"]+)"/) || text.match(/question\s*=\s*([^&\n\r]+)/);
    return { toolName: matchedTool, args: { question: questionMatch ? questionMatch[1] : undefined } };
  } else if (matchedTool === "analyse_surroundings") {
    const promptMatch = text.match(/"prompt"\s*:\s*"([^"]+)"/) || text.match(/prompt\s*=\s*([^&\n\r]+)/);
    return { toolName: matchedTool, args: { prompt: promptMatch ? promptMatch[1] : undefined } };
  }
  
  return null;
}

function cleanChatbotResponse(text) {
  if (!text) return text;
  
  let cleaned = text;
  
  // Strip out tool call markers, target destinations, and raw JSON strings
  cleaned = cleaned.replace(/to=functions\.[a-zA-Z_]+\s*([^\n]+)?/gi, '');
  cleaned = cleaned.replace(/to=[a-zA-Z_]+\s*([^\n]+)?/gi, '');
  cleaned = cleaned.replace(/[a-zA-Z_]+:\s*wuregjson/gi, '');
  cleaned = cleaned.replace(/[a-zA-Z_]+:\s*json/gi, '');
  
  // Strip explicit JSON properties/values
  cleaned = cleaned.replace(/\{[^{}]*"query"[^{}]*\}/gi, '');
  cleaned = cleaned.replace(/\{[^{}]*"target_name"[^{}]*\}/gi, '');
  cleaned = cleaned.replace(/\{[^{}]*"persona"[^{}]*\}/gi, '');
  cleaned = cleaned.replace(/\{[^{}]*"question"[^{}]*\}/gi, '');
  cleaned = cleaned.replace(/\{[^{}]*:[^{}]*\}/gi, '');
  cleaned = cleaned.replace(/```(json)?\s*[\s\S]*?```/gi, '');

  // Strip token-level Chinese/gibberish hallucinations caused by broken stop tokens
  cleaned = cleaned.replace(/天天中彩票有人\s*(json)?/gi, '');
  cleaned = cleaned.replace(/wuregjson/gi, '');

  // Trim extra spaces and duplicates
  cleaned = cleaned.replace(/\n\s*\n+/g, '\n');
  cleaned = cleaned.trim();
  
  return cleaned;
}

function sendWakewordCommand(action) {
  // Client-side openWakeWord-JS handles engine control. No-op on backend.
  return Promise.resolve(true);
}

// === GPT-Powered Chat (Voice + Tools) ===
app.post('/ask-gpt', async (req, res) => {
  const question = req.body.question;
  if (!question) return res.status(400).json({ success: false, message: 'No question provided.' });

  logToolCall(question, "ask-gpt_prompt", {}, "GPT request received.");

  // Mute wake word during thinking/processing
  await sendWakewordCommand('mute');
  let hasRobotMovement = false;

  try {
    let visualContext = "";
    const lowerQuestion = question.toLowerCase();
    const isVisualQuery = lowerQuestion.includes("what") || lowerQuestion.includes("how");

    if (visionMcpClient && isVisualQuery) {
      try {
        console.log(`[Vision] Prompt contains question keywords. Capturing camera snapshot & asking Qwen: "${question}"`);
        const snapshotRes = await visionMcpClient.callTool({
          name: "get_camera_snapshot",
          arguments: { question: question }
        });
        if (snapshotRes && snapshotRes.content && snapshotRes.content[0]) {
          const answer = snapshotRes.content[0].text;
          visualContext = `\n\nVISUAL CONTEXT (from D435i camera snapshot analysed by Qwen-VL):\n${answer}`;
          console.log(`[Vision] Snapshot visual context retrieved: ${answer}`);
        }
      } catch (e) {
        console.error("Failed to fetch camera snapshot visual context:", e.message);
        isVisionConnected = false;
        visionMcpClient = null;
      }
    }

    let contextStr = "";
    if (vectorStore) {
      const relevantDocs = await vectorStore.similaritySearch(question, 2);
      if (relevantDocs.length > 0) {
        contextStr = `\n\nBACKGROUND KNOWLEDGE (from ${currentPdfName}):\n` +
          relevantDocs.map(d => d.pageContent).join('\n---\n');
      }
    }

    const messages = [
      {
        role: "system",
        content: `You are a helpful, super-excited AI named ${currentPersona === 'linda' ? 'Linda' : 'John'}. You represent LARA 5, a collaborative robot (cobot) by NEURA Robotics at Singapore Polytechnic.

CRITICAL IDENTITY RULES:
- NEVER introduce yourself or state your name, role, or that you are a robotic assistant at the beginning of your responses. Do NOT say "I am John, the LARA 5 robotic assistant" or "I am Linda, the LARA 5 assistant" or anything similar.
- NEVER start your answers with a repetitive introductory formula. Start answering the user's question directly, naturally, and immediately.
- Only state your name or identity if the user explicitly asks "Who are you?", "What is your name?", or similar identity-focused questions.

CRITICAL DUCKDUCKGO / INTERNET ACCESS RULES:
- You DO have direct, real-time access to the internet/web search via the 'search_web' tool (powered by DuckDuckGo and Wikipedia).
- When asked about search engines, DuckDuckGo, or internet access, you MUST clearly, confidently, and enthusiastically declare that you CAN query DuckDuckGo directly in real-time. Never deny having internet search access or claim you are limited to static knowledge.

CRITICAL OBJECT INFERENCE RULE:
- If the user makes an implicit or vague request to pick or locate an item (e.g. expressing a need like being sick, wanting to write, or needing to clean), use your common-sense reasoning to select the most appropriate object from the available tool parameters/enum values and call the tool directly instead of asking for clarification.
- However, if the user explicitly asks for a specific object that is NOT in the available tool parameters/enum values (e.g. a "screwdriver"), you MUST NOT call any locate or pick tool. Instead, politely inform the user that this object is not available in the workspace and list the actual objects you can interact with. Never guess or fall back to an unrelated object like "cube".

CRITICAL OUTPUT CLEANLINESS:
- Do NOT output raw coordinates (e.g. x, y, z values), technical tool arguments, or structured JSON/dictionary info. Keep your responses purely conversational, natural, and concise. Speak about actions in plain English, not data info.

ROBOTIC ARM — PICK AND PLACE RULES:
- When the user asks you to pick up an object, you MUST simply call the 'locate_object' tool.
- Once you call 'locate_object', the system will automatically find the coordinates and trigger the robot arm for you.
- You do NOT need to call 'pick_and_place_object' yourself.
- Approved objects the robot can pick: black marker, blue marker, cube, green marker, medicine, nut, pipe, sponge.

IMPORTANT: Do not use hyphens (-) in your response.\n` + contextStr + visualContext
      },
      ...chatHistory,
      { role: "user", content: question }
    ];

    const reasoningLevel = getReasoningLevel(question);

    // Call GPT with tool support
    const completion = await openai.createChatCompletion({
      model: "gpt-5.4-mini",
      messages: messages,
      tools: [
        {
          type: "function",
          function: {
            name: "switch_avatar",
            description: "Switch the persona to John (male) or Linda (female) when instructed.",
            parameters: {
              type: "object",
              properties: { persona: { type: "string", enum: ["john", "linda"] } },
              required: ["persona"]
            }
          }
        },
        {
          type: "function",
          function: {
            name: "locate_object",
            description: "Uses the robotic vision camera to identify an object and get its coordinates.",
            parameters: {
              type: "object",
              properties: { 
                target_name: { 
                  type: "string", 
                  description: "Name of the object to locate.", 
                  enum: ["black marker", "blue marker", "cube", "green marker", "medicine", "nut", "pipe", "sponge"] 
                }
              },
              required: ["target_name"]
            }
          }
        },
        {
          type: "function",
          function: {
            name: "search_web",
            description: "Search the internet for real-time information and facts.",
            parameters: {
              type: "object",
              properties: { 
                query: { type: "string", description: "The search query to look up on the web." } 
              },
              required: ["query"]
            }
          }
        },
        {
          type: "function",
          function: {
            name: "get_camera_snapshot",
            description: "Captures a snapshot from the D435i camera to inspect the environment/workspace. Optionally provide a question for the vision model (Qwen-VL) to analyze the image.",
            parameters: {
              type: "object",
              properties: { 
                question: { 
                  type: "string", 
                  description: "Optional question to ask the vision language model about the captured snapshot (e.g., 'what objects are visible?')." 
                } 
              }
            }
          }
        },
        {
          type: "function",
          function: {
            name: "analyse_surroundings",
            description: "Queries the vision MCP to analyze the workspace surroundings using Qwen-VL and describes the layout and objects present.",
            parameters: {
              type: "object",
              properties: { 
                prompt: { 
                  type: "string", 
                  description: "Custom analysis instruction prompt for the model. Defaults to describing objects and layout." 
                } 
              }
            }
          }
        },
        {
          type: "function",
          function: {
            name: "pick_and_place_object",
            description: "Pick and place one detected object using the main robot controller. Input comes from the vision MCP/AI pipeline: object_name, x, y, and z in metres, and optionally angle_deg (yaw in degrees, robot base frame).",
            parameters: {
              type: "object",
              properties: {
                object_name: {
                  type: "string",
                  description: "Object to pick.",
                  enum: ["black marker", "blue marker", "cube", "green marker", "medicine", "nut", "pipe", "sponge"]
                },
                x: { type: "number", description: "Robot-frame X in metres." },
                y: { type: "number", description: "Robot-frame Y in metres." },
                z: { type: "number", description: "Robot-frame Z in metres." },
                angle_deg: { type: "number", description: "Object yaw angle in degrees in robot base frame (optional)." }
              },
              required: ["object_name", "x", "y", "z"]
            }
          }
        },
        {
          type: "function",
          function: {
            name: "relocate_object",
            description: "Pick an obstacle object and move it to a safe empty position within the pick workspace (NOT the placement box), then take a fresh photo. Use this when an object is blocking the target and needs to be moved out of the way first.",
            parameters: {
              type: "object",
              properties: {
                obstacle_name: {
                  type: "string",
                  description: "Name of the object to relocate.",
                  enum: ["black marker", "blue marker", "cube", "green marker", "medicine", "nut", "pipe", "sponge"]
                },
                obstacle_x: { type: "number", description: "Robot-frame X of obstacle in metres." },
                obstacle_y: { type: "number", description: "Robot-frame Y of obstacle in metres." },
                obstacle_z: { type: "number", description: "Robot-frame Z of obstacle in metres." },
                obstacle_angle_deg: { type: "number", description: "Obstacle yaw in degrees (optional)." },
                detections: {
                  type: "array",
                  description: "Full YOLO detection list for the current scene (optional). Used for dynamic obstacle avoidance.",
                  items: { type: "object" }
                }
              },
              required: ["obstacle_name", "obstacle_x", "obstacle_y", "obstacle_z"]
            }
          }
        }
      ],
      tool_choice: "auto",
    });

    const responseMessage = completion.data.choices[0].message;
    let answerText = "";

    // 1. Detect if the model output a tool call as plain text instead of native tool_calls
    let textToolCall = null;
    if (!responseMessage.tool_calls || responseMessage.tool_calls.length === 0) {
      if (responseMessage.content) {
        textToolCall = parseTextToolCall(responseMessage.content);
      }
    }

    // 2. Normalize tool calls into a unified array to process
    let toolCallsToProcess = [];
    let isTextBasedCall = false;

    if (responseMessage.tool_calls && responseMessage.tool_calls.length > 0) {
      toolCallsToProcess = responseMessage.tool_calls.map(tc => ({
        id: tc.id,
        name: tc.function.name,
        arguments: JSON.parse(tc.function.arguments)
      }));
    } else if (textToolCall) {
      isTextBasedCall = true;
      toolCallsToProcess = [{
        id: "call_txt_" + Date.now(),
        name: textToolCall.toolName,
        arguments: textToolCall.args
      }];
      
      // Push the assistant's intermediate message to history
      messages.push({
        role: "assistant",
        content: responseMessage.content
      });
    }

    // 3. Process the tool calls
    let skipSecondCompletion = false;
    
    if (toolCallsToProcess.length > 0) {
      if (!isTextBasedCall) {
        messages.push(responseMessage); // Push the native assistant message
      }

      for (const toolCall of toolCallsToProcess) {
        const args = toolCall.arguments;
        let toolResultText = "";

        if (toolCall.name === "switch_avatar") {
          await switchAvatar(args.persona);
          logToolCall(question, toolCall.name, args, `switched to ${args.persona}`);
          toolResultText = `Switched to ${currentPersona}. Now greeting the user warmly as ${currentPersona === 'linda' ? 'Linda' : 'John'}.`;
        } 
        else if (toolCall.name === "locate_object") {
          hasRobotMovement = true;
          logToolCall(question, "locate_object", args, "Orchestrating autonomous scan & pick in background...");
          sendProgress(`Initiating workspace scan for "${args.target_name}"...`, true);
          
          if (visionMcpClient) {
            // Fire-and-forget background pipeline
            visionMcpClient.callTool({ 
              name: "locate_object", 
              arguments: { target_name: args.target_name, user_context: question } 
            }, undefined, { timeout: 900000 })
            .then(async res => {
              const parsed = JSON.parse(res.content[0].text);
              logToolCall(question, "locate_object", args, parsed.status === "SUCCESS" ? "Target located" : "Scan failed");
              
              if (parsed.status === "SUCCESS") {
                sendProgress(`Target "${args.target_name}" clear! Instructing robot to pick it up...`, true);
                if (robotMcpClient) {
                  try {
                    const robotArgs = {
                      object_name: parsed.target,
                      x: parsed.coordinates.x,
                      y: parsed.coordinates.y,
                      z: parsed.coordinates.z,
                      angle_deg: parsed.coordinates.angle_deg,
                      detections: parsed.detections
                    };
                    if (parsed.coordinates.grasp_label) {
                      robotArgs.grasp_label = parsed.coordinates.grasp_label;
                    }
                    
                    const completionPromise = waitForRobotEvent(900000);
                    robotMcpClient.callTool({
                      name: "pick_and_place_object",
                      arguments: robotArgs
                    }, undefined, { timeout: 900000 }).catch(e => console.error("Background robot pick failed:", e));
                    
                    await completionPromise;
                    sendProgress(`Successfully picked up the ${args.target_name}!`, true);
                    setTimeout(async () => {
                      sendProgress(null, false, `I have finished picking and placing the requested object, ${args.target_name}.`);
                      await sendWakewordCommand('unmute');
                    }, 3000);
                  } catch (e) {
                    sendProgress(`Robot pick error: ${e.message}`, false);
                    setTimeout(async () => {
                      sendProgress(null, false, `Sorry, I could not complete the action because: ${e.message}`);
                      await sendWakewordCommand('unmute');
                    }, 5000);
                  }
                } else {
                  sendProgress("Error: Robot MCP is not connected for pickup.", false);
                  setTimeout(async () => {
                    sendProgress(null, false);
                    await sendWakewordCommand('unmute');
                  }, 5000);
                }
              } else {
                sendProgress(`Scan stopped: ${parsed.message || parsed.reasoning || "Obstacle blockage"}`, false);
                setTimeout(async () => {
                  sendProgress(null, false, `Scan stopped. ${parsed.message || parsed.reasoning || "Obstacle blockage"}`);
                  await sendWakewordCommand('unmute');
                }, 5000);
              }
            }).catch(e => {
              sendProgress(`Vision MCP Error: ${e.message}`, false);
              setTimeout(async () => {
                sendProgress(null, false);
                await sendWakewordCommand('unmute');
              }, 5000);
            });
          } else {
            sendProgress("Error: Vision MCP is not connected.", false);
            setTimeout(async () => {
              sendProgress(null, false);
              await sendWakewordCommand('unmute');
            }, 5000);
          }

          answerText = `I am checking the workspace for the ${args.target_name}. Once the path is clear, I will pick it up for you.`;
          skipSecondCompletion = true;
        }
        else if (toolCall.name === "search_web") {
          logToolCall(question, "search_web", args, "Searching web...");
          sendProgress(`Searching the web for "${args.query}"...`);
          try {
            const searchResults = await search(args.query);
            const topResults = searchResults.results.slice(0, 4).map(r => `Title: ${r.title}\nSnippet: ${r.description}\nURL: ${r.url}`).join('\n\n');
            toolResultText = `Search Results for '${args.query}':\n\n${topResults || "No results found."}`;
            logToolCall(question, "search_web", args, toolResultText);
          } catch (err) {
            console.log(`DDG failed, falling back to Wikipedia: ${err.message}`);
            try {
              // Fallback to free Wikipedia API
              const wikiRes = await fetch(`https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch=${encodeURIComponent(args.query)}&utf8=&format=json`);
              const wikiData = await wikiRes.json();
              if (wikiData.query && wikiData.query.search && wikiData.query.search.length > 0) {
                const topResults = wikiData.query.search.slice(0, 4).map(r => `Title: ${r.title}\nSnippet: ${r.snippet.replace(/<[^>]*>?/gm, '')}`).join('\n\n');
                toolResultText = `Search Results (Wikipedia) for '${args.query}':\n\n${topResults}`;
                logToolCall(question, "search_web", args, toolResultText);
              } else {
                throw new Error("No Wikipedia results found.");
              }
            } catch (wikiErr) {
              toolResultText = `Search failed: ${err.message}. Wikipedia fallback also failed: ${wikiErr.message}`;
              logToolCall(question, "search_web", args, `Failed: DDG and Wiki both failed.`);
            }
          }
        }
        else if (toolCall.name === "get_camera_snapshot") {
          logToolCall(question, "get_camera_snapshot", args, "Calling Remote Vision MCP...");
          sendProgress("Capturing camera snapshot...");
          if (visionMcpClient) {
            try {
              const res = await visionMcpClient.callTool({ name: "get_camera_snapshot", arguments: args });
              toolResultText = res.content[0].text;
              logToolCall(question, "get_camera_snapshot", args, toolResultText);
              if (args && args.question) {
                console.log("\n🧠 \x1b[35m[QWEN RAW OUTPUT]:\x1b[0m");
                console.log(toolResultText);
                console.log("=".repeat(50) + "\n");
              }
              sendProgress("Snapshot retrieved successfully.");
              await new Promise(resolve => setTimeout(resolve, 1500));
            } catch (e) {
              toolResultText = `Error calling Vision MCP: ${e.message}`;
              sendProgress(`Error: ${e.message}`);
              logToolCall(question, "get_camera_snapshot", args, `Failed: ${e.message}`);
              isVisionConnected = false;
              visionMcpClient = null;
              await new Promise(resolve => setTimeout(resolve, 2000));
            }
          } else {
            toolResultText = "Error: Vision MCP is not connected.";
            sendProgress("Error: Remote Vision MCP is not connected.");
            logToolCall(question, "get_camera_snapshot", args, "Failed: Not Connected");
            await new Promise(resolve => setTimeout(resolve, 2000));
          }
        }
        else if (toolCall.name === "analyse_surroundings") {
          logToolCall(question, "analyse_surroundings", args, "Calling Remote Vision MCP...");
          sendProgress("Analyzing surroundings...");
          if (visionMcpClient) {
            try {
              const res = await visionMcpClient.callTool({ name: "analyse_surroundings", arguments: args });
              toolResultText = res.content[0].text;
              logToolCall(question, "analyse_surroundings", args, toolResultText);
              console.log("\n🧠 \x1b[35m[QWEN RAW OUTPUT]:\x1b[0m");
              console.log(toolResultText);
              console.log("=".repeat(50) + "\n");
              sendProgress("Analysis complete.");
              await new Promise(resolve => setTimeout(resolve, 1500));
            } catch (e) {
              toolResultText = `Error calling Vision MCP: ${e.message}`;
              sendProgress(`Error: ${e.message}`);
              logToolCall(question, "analyse_surroundings", args, `Failed: ${e.message}`);
              isVisionConnected = false;
              visionMcpClient = null;
              await new Promise(resolve => setTimeout(resolve, 2000));
            }
          } else {
            toolResultText = "Error: Vision MCP is not connected.";
            sendProgress("Error: Remote Vision MCP is not connected.");
            logToolCall(question, "analyse_surroundings", args, "Failed: Not Connected");
            await new Promise(resolve => setTimeout(resolve, 2000));
          }
        }
        else if (toolCall.name === "pick_and_place_object") {
          hasRobotMovement = true;
          logToolCall(question, "pick_and_place_object", args, "Calling Robot MCP in background...");
          sendProgress(`Executing pick-and-place for "${args.object_name}"...`, true);
          
          if (robotMcpClient) {
            const completionPromise = waitForRobotEvent(900000);
            robotMcpClient.callTool({ name: "pick_and_place_object", arguments: args }, undefined, { timeout: 900000 })
              .catch(e => console.error("Background robot pick failed:", e));
            
            completionPromise.then(() => {
                sendProgress(`Pick-and-place completed for "${args.object_name}".`, true);
                setTimeout(async () => {
                  sendProgress(null, false, `I have finished picking and placing the requested object, ${args.object_name}.`);
                  await sendWakewordCommand('unmute');
                }, 3000);
              })
              .catch(e => {
                sendProgress(`Error: ${e.message}`, false);
                setTimeout(async () => {
                  sendProgress(null, false, `Sorry, the action failed because of an error: ${e.message}`);
                  await sendWakewordCommand('unmute');
                }, 5000);
              });
          } else {
            sendProgress("Error: Robot MCP is not connected.", false);
            logToolCall(question, "pick_and_place_object", args, "Failed: Not Connected");
            setTimeout(async () => {
              sendProgress(null, false);
              await sendWakewordCommand('unmute');
            }, 5000);
          }
          
          answerText = `I am picking up the ${args.object_name} right now.`;
          skipSecondCompletion = true;
        }
        else if (toolCall.name === "relocate_object") {
          hasRobotMovement = true;
          logToolCall(question, "relocate_object", args, "Calling Robot MCP in background...");
          sendProgress(`Relocating obstacle "${args.obstacle_name}"...`, true);
          if (robotMcpClient) {
            const completionPromise = waitForRobotEvent(900000);
            robotMcpClient.callTool({ name: "relocate_object", arguments: args }, undefined, { timeout: 900000 })
              .catch(e => console.error("Background robot relocation failed:", e));
            
            completionPromise.then(() => {
                sendProgress(`Relocated "${args.obstacle_name}" to a safe spot.`, true);
                setTimeout(async () => {
                  sendProgress(null, false, `I have relocated the object ${args.obstacle_name}.`);
                  await sendWakewordCommand('unmute');
                }, 3000);
              })
              .catch(e => {
                sendProgress(`Error: ${e.message}`, false);
                setTimeout(async () => {
                  sendProgress(null, false, `Sorry, the action failed because of an error: ${e.message}`);
                  await sendWakewordCommand('unmute');
                }, 5000);
              });
          } else {
            sendProgress("Error: Robot MCP is not connected.", false);
            logToolCall(question, "relocate_object", args, "Failed: Not Connected");
            setTimeout(async () => {
              sendProgress(null, false);
              await sendWakewordCommand('unmute');
            }, 5000);
          }
          
          answerText = `I am moving the ${args.obstacle_name} out of the way for you.`;
          skipSecondCompletion = true;
        }

        if (!skipSecondCompletion) {
          messages.push({
            role: "tool",
            tool_call_id: toolCall.id,
            content: toolResultText
          });
        }
      }

      if (!skipSecondCompletion) {
        const secondCompletion = await openai.createChatCompletion({
          model: "gpt-5.4-mini",
          messages: messages,
          reasoning_effort: reasoningLevel
        });
        answerText = secondCompletion.data.choices[0].message.content;
      }
    } else {
      answerText = responseMessage.content;
    }

    // 4. Clean up any leftover tool calling traces or token spam from the final answer text
    answerText = cleanChatbotResponse(answerText);

    const emoji = await getStatusEmoji("answering");

    // Sync Chat History
    chatHistory.push({ role: "user", content: question });
    chatHistory.push({ role: "assistant", content: answerText });
    if (chatHistory.length > 10) chatHistory = chatHistory.slice(-10);

    if (!hasRobotMovement) {
      sendProgress(null); // Clear progress overlay
      await sendWakewordCommand('unmute');
    }
    logToolCall(question, "ask-gpt_response", { persona: currentPersona, emoji }, answerText);
    res.json({ success: true, answer: answerText, emoji, persona: currentPersona });

  } catch (err) {
    if (!hasRobotMovement) {
      sendProgress(null); // Clear progress overlay on error
      await sendWakewordCommand('unmute');
    }
    const errorDetails = err.response ? JSON.stringify(err.response.data) : err.message;
    console.error('❌ AI Chat Error:', errorDetails);
    logToolCall(question, "ask-gpt_error", {}, errorDetails);
    res.status(500).json({ success: false, message: 'AI failed to respond.', error: errorDetails });
  }
});

// === Hybrid TTS Endpoint (OpenAI Primary, Espeak Fallback) ===
app.post('/tts', async (req, res) => {
  const { text } = req.body;
  if (!text) return res.status(400).json({ success: false, message: 'No text provided.' });

  console.log(`🎙️ Generating TTS for: "${text.substring(0, 30)}..."`);
  logToolCall("System Event", "tts_request", { text }, "Generating TTS...");

  // 1. Try OpenAI TTS (Premium)
  // Accept persona override from request body for guaranteed voice matching
  const voicePersona = req.body.persona || currentPersona;
  const postData = JSON.stringify({
    model: "tts-1",
    voice: voicePersona === "linda" ? "nova" : "onyx",
    input: text
  });

  const options = {
    hostname: 'api.openai.com',
    path: '/v1/audio/speech',
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${configuration.apiKey}`,
      'Content-Type': 'application/json',
    }
  };

  let fallbackTriggered = false;
  const safeFallback = () => {
    if (fallbackTriggered) return;
    fallbackTriggered = true;
    logToolCall("System Event", "tts_fallback", { text }, "OpenAI TTS failed. Fallback to Espeak triggered.");
    runEspeakFallback(text, res);
  };

  const attemptOpenAITTS = (attempt = 1) => {
    if (fallbackTriggered) return;
    
    console.log(`[TTS] OpenAI attempt ${attempt}...`);
    let attemptFinished = false;

    const openaiReq = https.request(options, (openaiRes) => {
      if (attemptFinished) return;
      if (openaiRes.statusCode === 200) {
        attemptFinished = true;
        console.log('✅ OpenAI TTS Success');
        logToolCall("System Event", "tts_success", { text, attempt }, "OpenAI TTS generated successfully.");
        res.setHeader('Content-Type', 'audio/mpeg');
        openaiRes.pipe(res);
      } else {
        let errData = '';
        openaiRes.on('data', (d) => { errData += d.toString(); });
        openaiRes.on('end', () => {
          if (attemptFinished) return;
          attemptFinished = true;
          console.error(`⚠️ OpenAI TTS Status: ${openaiRes.statusCode} - ${errData}`);
          if (attempt < 3) {
            setTimeout(() => attemptOpenAITTS(attempt + 1), attempt * 1000);
          } else {
            console.error('❌ OpenAI TTS failed after 3 attempts. Falling back to espeak...');
            safeFallback();
          }
        });
      }
    });

    openaiReq.on('error', (e) => {
      if (attemptFinished) return;
      attemptFinished = true;
      console.error(`❌ OpenAI Request Error (Attempt ${attempt}): ${e.message}`);
      if (attempt < 3) {
        setTimeout(() => attemptOpenAITTS(attempt + 1), attempt * 1000);
      } else {
        safeFallback();
      }
    });

    openaiReq.setTimeout(10000, () => {
      if (attemptFinished) return;
      attemptFinished = true;
      console.error(`❌ OpenAI TTS Request Timeout (Attempt ${attempt}).`);
      openaiReq.destroy();
      if (attempt < 3) {
        setTimeout(() => attemptOpenAITTS(attempt + 1), attempt * 1000);
      } else {
        safeFallback();
      }
    });

    openaiReq.write(postData);
    openaiReq.end();
  };

  attemptOpenAITTS(1);
});

function runEspeakFallback(text, res) {
  console.log('🗣️ Running local espeak fallback...');
  const tempFile = path.join(__dirname, `temp_voice_${Date.now()}.wav`);
  // -w saves to wav, -s 150 for speed, -p 40 for lower pitch
  const command = `espeak-ng -w "${tempFile}" -s 150 -p 40 "${text.replace(/"/g, '')}"`;

  exec(command, (error, stdout, stderr) => {
    if (error) {
      console.error('❌ Espeak failed execution:', error.message);
      console.error('❌ Espeak stderr:', stderr);
      return res.status(500).json({ success: false, message: 'TTS Espeak Failed', error: error.message });
    }

    if (fs.existsSync(tempFile)) {
      res.setHeader('Content-Type', 'audio/wav');
      const stream = fs.createReadStream(tempFile);
      stream.pipe(res);
      stream.on('end', () => {
        try { fs.unlinkSync(tempFile); } catch (e) { } // Cleanup
      });
    } else {
      res.status(500).send('Fallback failed');
    }
  });
}

// === Tool Log Endpoint (for Claude Desktop MCP) ===
app.get('/tool-log', (req, res) => {
  res.json({
    success: true,
    currentPersona,
    totalCalls: toolCallLog.length,
    log: toolCallLog.slice(-20), // Last 20 tool calls
    mcpStatus: {
      emoji: isEmojiConnected,
      vision: isVisionConnected,
      robot: isRobotConnected
    }
  });
});

// === Debug Trigger Mock Tool Endpoint ===
app.post('/debug/trigger-mock-tool', (req, res) => {
  const { toolName, args } = req.body;
  if (!toolName) {
    return res.status(400).json({ success: false, message: 'Missing toolName' });
  }
  
  console.log(`[DEBUG HUD] Mocking tool call: ${toolName}`);
  
  let mockResult = "Success (Mock)";
  
  // If we mock locate_object, simulate coordinate translation!
  if (toolName === "locate_object") {
    let rawX = args.x !== undefined ? Number(args.x) : -0.1009;
    let rawY = args.y !== undefined ? Number(args.y) : -0.1316;
    let rawZ = args.z !== undefined ? Number(args.z) : 0.9670;
    
    // Transform coordinates
    const xr = 0.7337634310 * rawX + 0.6126652048 * rawY - 0.2936538341 * rawZ + 0.7173839756;
    const yr = 0.6785283256 * rawX - 0.6388791698 * rawY + 0.3625365054 * rawZ - 0.4903506740;
    const zr = 0.0345041846 * rawX - 0.4652684744 * rawY - 0.8844968672 * rawZ + 0.7880605490;
    
    console.log(`📍 \x1b[35m[MOCK COORDINATES TRANSFORMED]:\x1b[0m`);
    console.log(`   Raw Camera:  X: ${rawX.toFixed(4)}, Y: ${rawY.toFixed(4)}, Z: ${rawZ.toFixed(4)}`);
    console.log(`   Robot Base:  \x1b[32mX: ${xr.toFixed(4)}, Y: ${yr.toFixed(4)}, Z: ${zr.toFixed(4)}\x1b[0m`);
    
    mockResult = JSON.stringify({
      status: `SUCCESS: Localized ${args.target_name || "object"}`,
      coordinates: { x: xr, y: yr, z: zr },
      raw_coordinates: { x: rawX, y: rawY, z: rawZ }
    });
  } else if (toolName === "pick_and_place_object" || toolName === "relocate_object") {
    mockResult = `Completed: mock action for ${toolName} finished successfully.`;
  }
  
  logToolCall("Mock Debugger Trigger", toolName, args, mockResult);
  
  res.json({
    success: true,
    toolName,
    args,
    result: mockResult
  });
});

// === Debug Clear Logs Endpoint ===
app.post('/debug/clear-logs', (req, res) => {
  console.log(`[DEBUG HUD] Clearing tool log history...`);
  toolCallLog.length = 0; // Empty the in-memory array
  try {
    fs.writeFileSync(TOOL_LOG_FILE, JSON.stringify(toolCallLog, null, 2));
    res.json({ success: true, message: 'Logs cleared successfully' });
  } catch (err) {
    res.status(500).json({ success: false, message: `Failed to clear logs: ${err.message}` });
  }
});

// Debug vector state
app.get('/debug-vectorstore', (req, res) => {
  if (!vectorStore) return res.json({ status: 'No vector store initialized' });
  res.json({ status: 'Vector store exists', pdfName: currentPdfName });
});

// Force reload PDF
app.get('/force-reload', async (req, res) => {
  await loadLatestPdf();
  res.json({ success: true, message: 'Force reloaded latest PDF.' });
});

// Get current PDF info
app.get('/current-pdf', (req, res) => {
  if (!currentPdfPath || !fs.existsSync(currentPdfPath)) {
    return res.json({ hasPdf: false });
  }
  res.json({
    hasPdf: true,
    filename: currentPdfName,
    storedFilename: path.basename(currentPdfPath),
    uploadedAt: fs.statSync(currentPdfPath).mtime.toISOString()
  });
});

// Get raw PDF file
app.get('/get-pdf', (req, res) => {
  if (!currentPdfPath || !fs.existsSync(currentPdfPath)) {
    return res.status(404).json({ success: false, message: 'No PDF available.' });
  }
  res.setHeader('Content-Type', 'application/pdf');
  res.setHeader('Content-Disposition', `inline; filename="${currentPdfName}"`);
  res.sendFile(currentPdfPath);
});

// Cleanup old files (over 3 days old)
function cleanupOldFiles() {
  const now = Date.now();
  const maxAge = 3 * 24 * 60 * 60 * 1000;
  fs.readdirSync(uploadsDir).forEach(file => {
    const filePath = path.join(uploadsDir, file);
    const stats = fs.statSync(filePath);
    if (now - stats.mtimeMs > maxAge) {
      fs.unlinkSync(filePath);
      console.log(`🧹 Deleted old file: ${file}`);
    }
  });
}

// Ask Claude to inspect tool usage
app.post('/ask-claude', async (req, res) => {
  const { question } = req.body;
  if (!question) return res.status(400).json({ success: false, message: 'No question provided.' });

  // Mute wake word during thinking/processing
  await sendWakewordCommand('mute');

  try {
    const recentTools = toolCallLog.slice(-10); // Last 10 tool calls
    const toolSummary = recentTools.length === 0
      ? 'No tools have been called yet in this session.'
      : recentTools.map((t, i) =>
        `[${i + 1}] At ${t.timestamp}:\n  User asked: "${t.userQuestion}"\n  GPT called tool: ${t.toolName}\n  Args: ${JSON.stringify(t.args)}\n  Result: ${t.result}`
      ).join('\n\n');

    const message = await anthropic.messages.create({
      model: 'claude-opus-4-5',
      max_tokens: 1024,
      messages: [
        {
          role: 'user',
          content: `You are a helpful AI assistant monitoring the Roboas chatbot system. Here is the recent tool usage log from GPT:\n\n${toolSummary}\n\nUser question: ${question}`
        }
      ]
    });

    const answer = message.content[0].text;
    console.log(`🧠 Claude response: ${answer.substring(0, 80)}...`);
    await sendWakewordCommand('unmute');
    res.json({ success: true, answer, toolLog: recentTools });
  } catch (err) {
    await sendWakewordCommand('unmute');
    console.error('❌ Claude error:', err.message);
    res.status(500).json({ success: false, message: err.message });
  }
});

// Switch Persona from Flutter FAB (Hard Sync with Brain)
app.post('/switch-persona', (req, res) => {
  const { persona, silent } = req.body;
  if (!persona || !['john', 'linda'].includes(persona)) {
    return res.status(400).json({ success: false, message: 'Invalid persona.' });
  }
  currentPersona = persona;

  // 1. Log manual switch for transparency (Suppress if it's an internal AI sync)
  if (!silent) {
    logToolCall("System Sync", "switch_avatar", { persona }, `switched to ${persona} via Remote Control`);
  }

  console.log(`🔄 Persona switched to: ${persona} (Brain Synced ${silent ? ' - SILENT' : ''})`);
  res.json({ success: true, persona });
});

// Emergency Stop Endpoint (from Chatbot UI)
app.post('/emergency-stop', async (req, res) => {
  console.log('\n🚨 \x1b[41m\x1b[37m[EMERGENCY STOP]: Received UI signal! Halting Robot Arm...\x1b[0m\n');
  
  await sendWakewordCommand('mute');
  sendProgress("Emergency stop activated. Halting the robot.", true, "Emergency stop activated. Halting the robot.");

  let robotSuccess = false;
  let robotError = null;
  let responseText = "";

  if (robotMcpClient) {
    try {
      const result = await robotMcpClient.callTool({
        name: "emergency_stop",
        arguments: {}
      });
      robotSuccess = true;
      responseText = result.content[0].text;
      console.log('⚠️ Robot MCP Emergency Stop Success:', responseText);
    } catch (err) {
      robotError = err.message;
      console.error('❌ Failed to call Robot MCP emergency_stop:', err.message);
    }
  } else {
    robotError = "Robot MCP is not connected.";
    console.error('❌ Robot MCP is not connected.');
  }

  // Log the emergency stop event
  logToolCall("System Emergency Button", "emergency_stop", {}, robotSuccess ? "Robot halted" : `Failed: ${robotError}`);

  setTimeout(async () => {
    sendProgress(null, false);
    await sendWakewordCommand('unmute');
  }, 3500);

  res.json({
    success: robotSuccess,
    message: robotSuccess ? "Emergency stop sent successfully." : `Failed to stop robot: ${robotError}`,
    detail: responseText || robotError
  });
});

// Return Home Endpoint (from Chatbot UI)
app.all('/return-home', async (req, res) => {
  console.log('\n🏠 \x1b[46m\x1b[30m[HOME BUTTON]: Received UI signal! Returning Robot Arm to Home...\x1b[0m\n');
  
  await sendWakewordCommand('mute');
  sendProgress("Returning robot arm to home position.", true, "Returning robot arm to home position.");

  let robotSuccess = false;
  let robotError = null;
  let responseText = "";

  if (robotMcpClient) {
    try {
      const result = await robotMcpClient.callTool({
        name: "return_home",
        arguments: {}
      });
      robotSuccess = true;
      responseText = result.content[0].text;
      console.log('🏠 Robot MCP Return Home Success:', responseText);
    } catch (err) {
      robotError = err.message;
      console.error('❌ Failed to call Robot MCP return_home:', err.message);
    }
  } else {
    robotError = "Robot MCP is not connected.";
    console.error('❌ Robot MCP is not connected.');
  }

  // Log the return home event
  logToolCall("System Home Button", "return_home", {}, robotSuccess ? "Robot returning home" : `Failed: ${robotError}`);

  if (robotSuccess) {
    sendProgress("Arm has been returned to home. Waiting for your next input.", true, "Arm has been returned to home. Waiting for your next input.");
    setTimeout(async () => {
      sendProgress(null, false);
      await sendWakewordCommand('unmute');
    }, 4500);
  } else {
    setTimeout(async () => {
      sendProgress(null, false);
      await sendWakewordCommand('unmute');
    }, 4000);
  }

  res.json({
    success: robotSuccess,
    message: robotSuccess ? "Return home sent successfully." : `Failed to return home: ${robotError}`,
    detail: responseText || robotError
  });
});



// Serve debug dashboard
app.get('/debug', (req, res) => {
  res.setHeader('Cache-Control', 'no-store'); // Ensure fresh HUD on reload
  res.sendFile(path.join(__dirname, 'resources', 'debug.html'));
});

// Serve index.html
app.get('/', (req, res) => {
  res.sendFile(path.join(__dirname, 'Public', 'index.html'));
});

// Start server with WebSocket proxy for wake word
const server = http.createServer(app);
server.timeout = 300000; // 5 minutes
server.headersTimeout = 300000; // 5 minutes
server.keepAliveTimeout = 300000; // 5 minutes

server.listen(port, async () => {
  console.log(`🚀 Server running at http://localhost:${port}`);
  console.log(`🔊 Client-side openWakeWord engine active (Vosk WS proxy disabled)`);
  clearPdfOnStartup(); // Always start fresh — no PDF memory between sessions
  
  // Auto-load the LARA datasheet from resources so the robot has product knowledge
  const laraPath = path.join(__dirname, 'resources', 'LARA_NEURA_Robotics_Datasheet_Web.pdf');
  if (fs.existsSync(laraPath)) {
    try {
      await processPdf(laraPath, 'LARA_NEURA_Robotics_Datasheet_Web.pdf');
      console.log('📄 Auto-loaded LARA datasheet from resources/');
    } catch (e) {
      console.error('⚠️ Failed to auto-load LARA datasheet:', e.message);
    }
  }
  
  cleanupOldFiles();
  setInterval(cleanupOldFiles, 24 * 60 * 60 * 1000);
});

```

</details>

---

<details>
<summary>📂 <b>web/index.html</b> (Click to expand)</summary>

```html
<!DOCTYPE html>
<html>
<head>
  <!--
    If you are serving your web app in a path other than the root, change the
    href value below to reflect the base path you are serving from.

    The path provided below has to start and end with a slash "/" in order for
    it to work correctly.

    For more details:
    * https://developer.mozilla.org/en-US/docs/Web/HTML/Element/base

    This is a placeholder for base href that will be replaced by the value of
    the `--base-href` argument provided to `flutter build`.
  -->
  <base href="$FLUTTER_BASE_HREF">

  <meta charset="UTF-8">
  <meta content="IE=Edge" http-equiv="X-UA-Compatible">
  <meta name="description" content="A new Flutter project.">

  <!-- iOS meta tags & icons -->
  <meta name="mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black">
  <meta name="apple-mobile-web-app-title" content="appver">
  <link rel="apple-touch-icon" href="icons/Icon-192.png">

  <!-- Favicon -->
  <link rel="icon" type="image/png" href="favicon.png"/>

  <title>appver</title>
  <link rel="manifest" href="manifest.json">
  <script>
    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.getRegistrations().then(function(registrations) {
        for(let registration of registrations) {
          registration.unregister().then(function(success) {
            if (success) {
              console.log('🧹 Old Service Worker unregistered successfully. Reloading...');
              window.location.reload(true);
            }
          });
        }
      });
    }
  </script>
</head>
<body>
  <!-- Load ONNX Runtime Web directly in the browser -->
  <script src="https://cdn.jsdelivr.net/npm/onnxruntime-web@1.18.0/dist/ort.min.js"></script>

  <!-- JS wrapper modules for Flutter Dart calls -->
  <script type="module">
    import { WakeWordEngine } from './WakeWordEngine.js';

    window.johnThreshold = 0.001;
    window.lindaThreshold = 0.0009;
    window.johnScoresWindow = [];
    window.lindaScoresWindow = [];

    window.setWakeWordThresholds = function(johnVal, lindaVal) {
      window.johnThreshold = Number(johnVal);
      window.lindaThreshold = Number(lindaVal);
      console.log(`[JS] Thresholds updated: John=${window.johnThreshold}, Linda=${window.lindaThreshold}`);
    };

    window.wakewordDetectionMuted = false;

    window.isRobotMoving = false;
    window.ttsMuted = false;

    window.setWakeWordMuted = function(muted) {
      window.ttsMuted = !!muted;
      window.wakewordDetectionMuted = window.ttsMuted || window.isRobotMoving;
      console.log(`[JS] WakeWord muted: ${window.wakewordDetectionMuted} (tts: ${window.ttsMuted}, robot: ${window.isRobotMoving})`);
    };

    const progressSource = new EventSource('/progress');
    progressSource.onmessage = function(event) {
      try {
        const data = JSON.parse(event.data);
        if (data.tts_message) {
          if (onEventCallback) {
            onEventCallback('tts:' + data.tts_message);
          }
        }
        if (data.isRobotMoving !== undefined) {
          window.isRobotMoving = data.isRobotMoving;
          window.wakewordDetectionMuted = window.ttsMuted || window.isRobotMoving;
          console.log(`[JS] Progress SSE: robot=${window.isRobotMoving}, wakeword muted=${window.wakewordDetectionMuted}`);
          if (onEventCallback) {
            onEventCallback('robot_moving_status:' + data.isRobotMoving);
          }
        }
      } catch (e) {
        console.error('[JS] Error parsing SSE:', e);
      }
    };

    let engine = null;
    let isListening = false;
    let lastActiveTime = Date.now();
    let watchdogTimer = null;
    let lastAudioActiveLogged = 0;

    let onDetectJohnCallback = null;
    let onDetectLindaCallback = null;
    let onReadyCallback = null;
    let onEventCallback = null;
    let detectCallbackAttached = false;

    window.initWakeWordEngine = async function(onDetectJohn, onDetectLinda, onReady, onEvent) {
      console.log("[JS] Registering openWakeWord engine interops...");
      onDetectJohnCallback = onDetectJohn;
      onDetectLindaCallback = onDetectLinda;
      onReadyCallback = onReady;
      onEventCallback = onEvent;

      return await setupEngine();
    };

    async function setupEngine() {
      if (onEventCallback) onEventCallback('restarting');
      console.log("[JS] Initializing openWakeWord engine for John and Linda (including optional V2)...");
      detectCallbackAttached = false;
      window.johnScoresWindow = [];
      window.johnV2ScoresWindow = [];
      window.lindaScoresWindow = [];
      window.lindaV2ScoresWindow = [];
      try {
        ort.env.wasm.wasmPaths = '/ort/';

        engine = new WakeWordEngine({
          baseAssetUrl: '/models',
          keywords: ['john', 'john_v2', 'linda', 'linda_v2'],
          detectionThreshold: 99.0, // Disable built-in threshold callback to prevent cooldown locks
          cooldownMs: 2500,
          ortWasmPath: '/ort/',
          debug: true
        });

        await engine.load();
        console.log("[JS] WakeWordEngine loaded successfully.");
        if (onReadyCallback) onReadyCallback();
        if (onEventCallback) onEventCallback('restarted');

        // Note: Built-in 'detect' is bypassed by detectionThreshold: 99.0
        engine.on('detect', ({ keyword, score }) => {
          console.log(`[JS] Built-in detect (unused): ${keyword} (${score})`);
        });
        detectCallbackAttached = true;

        let framesCount = 0;
        let lastScoreRmsLogged = 0;
        let chunkCount = 0;
        let lastChunkCountTime = Date.now();
        let chunksPerSecond = 0;
        window.lastNonZeroRmsTime = Date.now();

        // Intercept chunk processing to track activity/watchdog
        const originalProcessChunk = engine._processChunk;
        engine._processChunk = async function(chunk, options) {
          lastActiveTime = Date.now();
          let isRmsZero = true;
          for (let i = 0; i < chunk.length; i++) {
            if (chunk[i] !== 0) {
              isRmsZero = false;
              break;
            }
          }
          if (!isRmsZero) {
            window.lastNonZeroRmsTime = Date.now();
          }
          framesCount++;
          if (framesCount === 5) {
            if (onEventCallback) onEventCallback('active_listening_confirmed');
          }

          // Call the original chunk processor to execute VAD and compute new scores
          const result = await originalProcessChunk.call(engine, chunk, options);

          // Calculate RMS and fetch active keyword scores
          const now = Date.now();
          let johnScore = 0;
          let johnV2Score = 0;
          let lindaScore = 0;
          let lindaV2Score = 0;
          if (engine && engine._keywordModels) {
            if (engine._keywordModels.john && engine._keywordModels.john.scores) {
              const s = engine._keywordModels.john.scores;
              johnScore = s[s.length - 1] || 0;
            }
            if (engine._keywordModels.john_v2 && engine._keywordModels.john_v2.scores) {
              const s = engine._keywordModels.john_v2.scores;
              johnV2Score = s[s.length - 1] || 0;
            }
            if (engine._keywordModels.linda && engine._keywordModels.linda.scores) {
              const s = engine._keywordModels.linda.scores;
              lindaScore = s[s.length - 1] || 0;
            }
            if (engine._keywordModels.linda_v2 && engine._keywordModels.linda_v2.scores) {
              const s = engine._keywordModels.linda_v2.scores;
              lindaV2Score = s[s.length - 1] || 0;
            }
          }

          // Maintain rolling score peaks for the last 5 seconds (5000ms)
          window.johnScoresWindow.push({ score: johnScore, timestamp: now });
          window.johnV2ScoresWindow.push({ score: johnV2Score, timestamp: now });
          window.lindaScoresWindow.push({ score: lindaScore, timestamp: now });
          window.lindaV2ScoresWindow.push({ score: lindaV2Score, timestamp: now });

          window.johnScoresWindow = window.johnScoresWindow.filter(item => now - item.timestamp <= 5000);
          window.johnV2ScoresWindow = window.johnV2ScoresWindow.filter(item => now - item.timestamp <= 5000);
          window.lindaScoresWindow = window.lindaScoresWindow.filter(item => now - item.timestamp <= 5000);
          window.lindaV2ScoresWindow = window.lindaV2ScoresWindow.filter(item => now - item.timestamp <= 5000);

          const johnPeak = Math.max(...window.johnScoresWindow.map(item => item.score), 0);
          const johnV2Peak = Math.max(...window.johnV2ScoresWindow.map(item => item.score), 0);
          const lindaPeak = Math.max(...window.lindaScoresWindow.map(item => item.score), 0);
          const lindaV2Peak = Math.max(...window.lindaV2ScoresWindow.map(item => item.score), 0);

          const speechActive = engine ? engine._isSpeechActive : false;
          const johnThresh = window.johnThreshold || 0.001;
          const lindaThresh = window.lindaThreshold || 0.0009;

          // Check John Manual Threshold
          if (johnScore >= johnThresh) {
            if (speechActive && !window.johnCooldownActive && !window.wakewordDetectionMuted) {
              window.johnCooldownActive = true;
              console.log(`[OWW] John detected (score: ${johnScore.toFixed(4)})`);
              if (onDetectJohnCallback) onDetectJohnCallback(johnScore, 'john');
              setTimeout(() => { window.johnCooldownActive = false; }, 2500);
            }
          } else if (johnScore >= (johnThresh - 0.10) && johnScore > 0.001) {
            if (speechActive && !window.johnNearMissCooldown && !window.wakewordDetectionMuted) {
              window.johnNearMissCooldown = true;
              console.log(`[OWW] John near miss: score ${johnScore.toFixed(2)}`);
              if (onEventCallback) {
                onEventCallback(`near_miss:john=${johnScore.toFixed(2)}`);
              }
              setTimeout(() => { window.johnNearMissCooldown = false; }, 1500);
            }
          }

          // Check John V2 Manual Threshold
          if (johnV2Score >= johnThresh) {
            if (speechActive && !window.johnCooldownActive && !window.wakewordDetectionMuted) {
              window.johnCooldownActive = true;
              console.log(`[OWW] John_v2 detected (score: ${johnV2Score.toFixed(4)})`);
              if (onDetectJohnCallback) onDetectJohnCallback(johnV2Score, 'john_v2');
              setTimeout(() => { window.johnCooldownActive = false; }, 2500);
            }
          } else if (johnV2Score >= (johnThresh - 0.10) && johnV2Score > 0.001) {
            if (speechActive && !window.johnV2NearMissCooldown && !window.wakewordDetectionMuted) {
              window.johnV2NearMissCooldown = true;
              console.log(`[OWW] John_v2 near miss: score ${johnV2Score.toFixed(2)}`);
              if (onEventCallback) {
                onEventCallback(`near_miss:john_v2=${johnV2Score.toFixed(2)}`);
              }
              setTimeout(() => { window.johnV2NearMissCooldown = false; }, 1500);
            }
          }

          // Check Linda Manual Threshold
          if (lindaScore >= lindaThresh) {
            if (speechActive && !window.lindaCooldownActive && !window.wakewordDetectionMuted) {
              window.lindaCooldownActive = true;
              console.log(`[OWW] Linda detected (score: ${lindaScore.toFixed(4)})`);
              if (onDetectLindaCallback) onDetectLindaCallback(lindaScore, 'linda');
              setTimeout(() => { window.lindaCooldownActive = false; }, 2500);
            }
          } else if (lindaScore >= (lindaThresh - 0.10) && lindaScore > 0.001) {
            if (speechActive && !window.lindaNearMissCooldown && !window.wakewordDetectionMuted) {
              window.lindaNearMissCooldown = true;
              console.log(`[OWW] Linda near miss: score ${lindaScore.toFixed(2)}`);
              if (onEventCallback) {
                onEventCallback(`near_miss:linda=${lindaScore.toFixed(2)}`);
              }
              setTimeout(() => { window.lindaNearMissCooldown = false; }, 1500);
            }
          }

          // Check Linda V2 Manual Threshold
          if (lindaV2Score >= lindaThresh) {
            if (speechActive && !window.lindaCooldownActive && !window.wakewordDetectionMuted) {
              window.lindaCooldownActive = true;
              console.log(`[OWW] Linda_v2 detected (score: ${lindaV2Score.toFixed(4)})`);
              if (onDetectLindaCallback) onDetectLindaCallback(lindaV2Score, 'linda_v2');
              setTimeout(() => { window.lindaCooldownActive = false; }, 2500);
            }
          } else if (lindaV2Score >= (lindaThresh - 0.10) && lindaV2Score > 0.001) {
            if (speechActive && !window.lindaV2NearMissCooldown && !window.wakewordDetectionMuted) {
              window.lindaV2NearMissCooldown = true;
              console.log(`[OWW] Linda_v2 near miss: score ${lindaV2Score.toFixed(2)}`);
              if (onEventCallback) {
                onEventCallback(`near_miss:linda_v2=${lindaV2Score.toFixed(2)}`);
              }
              setTimeout(() => { window.lindaV2NearMissCooldown = false; }, 1500);
            }
          }

          chunkCount++;
          const elapsed = now - lastChunkCountTime;
          if (elapsed >= 1000) {
            chunksPerSecond = (chunkCount * 1000) / elapsed;
            chunkCount = 0;
            lastChunkCountTime = now;
          }

          // Emit diagnostic status update every 150ms for live VU meter reactivity
          if (now - lastScoreRmsLogged > 150) {
            lastScoreRmsLogged = now;
            let sumSquares = 0;
            for (let i = 0; i < chunk.length; i++) {
              sumSquares += chunk[i] * chunk[i];
            }
            let rms = Math.sqrt(sumSquares / chunk.length);

            // Log live confidence percentages to browser console for tuning
            if (johnScore > 0.001 || johnV2Score > 0.001 || lindaScore > 0.001 || lindaV2Score > 0.001) {
              console.log(`[OWW Scores] John: ${(johnScore * 100).toFixed(1)}% (Peak: ${(johnPeak * 100).toFixed(1)}%), John V2: ${(johnV2Score * 100).toFixed(1)}% | Linda: ${(lindaScore * 100).toFixed(1)}% (Peak: ${(lindaPeak * 100).toFixed(1)}%), Linda V2: ${(lindaV2Score * 100).toFixed(1)}%`);
            }

            let loadedModels = [];
            if (engine && engine._keywordModels) {
              loadedModels = Object.keys(engine._keywordModels);
            }
            const ctxState = (engine && engine._audioContext) ? engine._audioContext.state : 'none';
            let trackState = 'none';
            let trackEnabled = 'none';
            if (engine && engine._mediaStream) {
              const tracks = engine._mediaStream.getTracks();
              if (tracks.length > 0) {
                trackState = tracks[0].readyState;
                trackEnabled = tracks[0].enabled ? 'true' : 'false';
              }
            }
            if (onEventCallback) {
              onEventCallback(`status_update:rms=${rms.toFixed(4)},john=${johnScore.toFixed(2)},john_peak=${johnPeak.toFixed(2)},john_v2=${johnV2Score.toFixed(2)},john_v2_peak=${johnV2Peak.toFixed(2)},linda=${lindaScore.toFixed(2)},linda_peak=${lindaPeak.toFixed(2)},linda_v2=${lindaV2Score.toFixed(2)},linda_v2_peak=${lindaV2Peak.toFixed(2)},models=${loadedModels.join('+')},thresh_john=${johnThresh.toFixed(2)},thresh_linda=${lindaThresh.toFixed(2)},callback=${detectCallbackAttached},cps=${chunksPerSecond.toFixed(1)},ctx=${ctxState},track=${trackState},track_enabled=${trackEnabled}`);
            }
          }

          triggerAudioActive();
          return result;
        };

        return true;
      } catch (err) {
        console.error("[JS] Failed to initialize WakeWordEngine:", err);
        return false;
      }
    }

    function triggerAudioActive() {
      const now = Date.now();
      if (now - lastAudioActiveLogged > 5000) {
        lastAudioActiveLogged = now;
        if (onEventCallback) onEventCallback('audio_active');
      }
    }

    function startWatchdog() {
      stopWatchdog();
      lastActiveTime = Date.now();
      window.lastNonZeroRmsTime = Date.now();
      watchdogTimer = setInterval(async () => {
        if (!isListening) return;
        const idleTime = Date.now() - lastActiveTime;
        const zeroRmsTime = Date.now() - (window.lastNonZeroRmsTime || Date.now());
        if (idleTime > 4000 || zeroRmsTime > 5000) {
          console.warn(`[JS] Watchdog trigger: idleTime=${idleTime}ms, zeroRmsTime=${zeroRmsTime}ms. Restarting engine...`);
          if (onEventCallback) onEventCallback('no_audio_detected');
          await window.restartWakeWordEngine();
        }
      }, 1000);
    }

    function stopWatchdog() {
      if (watchdogTimer) {
        clearInterval(watchdogTimer);
        watchdogTimer = null;
      }
    }

    window.startWakeWordListening = async function() {
      if (!engine) {
        console.error("[JS] Engine not initialized.");
        return false;
      }
      if (isListening) return true;
      try {
        console.log("[JS] Starting WakeWordEngine mic capture...");
        await engine.start();
        if (engine._audioContext && engine._audioContext.state === 'suspended') {
          console.log("[JS] start: AudioContext suspended. Resuming...");
          await engine._audioContext.resume();
        }
        isListening = true;
        lastActiveTime = Date.now();
        startWatchdog();
        if (onEventCallback) onEventCallback('started');
        return true;
      } catch (err) {
        console.error("[JS] Failed to start engine:", err);
        if (onEventCallback) onEventCallback('mic_issue');
        return false;
      }
    };

    window.stopWakeWordListening = async function() {
      if (!engine || !isListening) return true;
      try {
        console.log("[JS] Stopping WakeWordEngine mic capture...");
        stopWatchdog();
        if (engine && engine._mediaStream) {
          engine._mediaStream.getTracks().forEach(t => t.stop());
        }
        await engine.stop();
        isListening = false;
        detectCallbackAttached = false;
        if (onEventCallback) onEventCallback('stopped');
        return true;
      } catch (err) {
        console.error("[JS] Failed to stop engine:", err);
        return false;
      }
    };

    window.restartWakeWordEngine = async function() {
      console.log("[JS] Fully restarting openWakeWord engine...");
      try {
        if (engine) {
          if (engine._mediaStream) {
            engine._mediaStream.getTracks().forEach(t => t.stop());
          }
          await engine.stop();
        }
      } catch (e) {
        console.error("[JS] Error stopping engine during restart:", e);
      }
      isListening = false;
      detectCallbackAttached = false;
      stopWatchdog();

      const success = await setupEngine();
      if (success) {
        try {
          console.log("[JS] Starting WakeWordEngine mic capture after restart...");
          await engine.start();
          if (engine._audioContext && engine._audioContext.state === 'suspended') {
            console.log("[JS] restart: AudioContext suspended. Resuming...");
            await engine._audioContext.resume();
          }
          isListening = true;
          startWatchdog();
          if (onEventCallback) onEventCallback('started');
          return true;
        } catch (err) {
          console.error("[JS] Failed to start engine after restart:", err);
          if (onEventCallback) onEventCallback('mic_issue');
          return false;
        }
      }
      return false;
    };
  </script>

  <script src="flutter_bootstrap.js" async></script>
</body>
</html>

```

</details>

---

<details>
<summary>📂 <b>web/WakeWordEngine.js</b> (Click to expand)</summary>

```javascript
const ort = window.ort || globalThis.ort;

export const MODEL_FILE_MAP = {
    john: 'john.onnx',
    john_v2: 'john_v3.onnx',
    linda: 'linda.onnx',
    linda_v2: 'linda_v3.onnx',
    alexa: 'alexa.onnx',
    hey_mycroft: 'hey_mycroft_v0.1.onnx',
    hey_jarvis: 'hey_jarvis.onnx',
    hey_rhasspy: 'hey_rhasspy_v0.1.onnx',
    timer: 'timer_v0.1.onnx',
    weather: 'weather_v0.1.onnx',
};

const AUDIO_PROCESSOR = `
class AudioProcessor extends AudioWorkletProcessor {
    bufferSize = 1280;
    _buffer = new Float32Array(this.bufferSize);
    _pos = 0;
    process(inputs) {
        const input = inputs[0][0];
        if (input) {
            for (let i = 0; i < input.length; i++) {
                this._buffer[this._pos++] = input[i];
                if (this._pos === this.bufferSize) {
                    this.port.postMessage(this._buffer);
                    this._pos = 0;
                }
            }
        }
        return true;
    }
}
registerProcessor('audio-processor', AudioProcessor);
`;

const createEmitter = () => {
    const listeners = new Map();
    return {
        on(event, handler) {
            if (!listeners.has(event)) listeners.set(event, new Set());
            listeners.get(event).add(handler);
            return () => this.off(event, handler);
        },
        off(event, handler) {
            const set = listeners.get(event);
            if (set) set.delete(handler);
        },
        emit(event, payload) {
            const set = listeners.get(event);
            if (!set) return;
            for (const handler of Array.from(set)) handler(payload);
        }
    };
};

export class WakeWordEngine {
    constructor({
        keywords = ['hey_jarvis'],
        modelFiles = MODEL_FILE_MAP,
        baseAssetUrl = '/models',
        ortWasmPath,
        frameSize = 1280,
        sampleRate = 16000,
        vadHangoverFrames = 12,
        detectionThreshold = 0.5,
        cooldownMs = 2000,
        executionProviders = ['wasm'],
        embeddingWindowSize = 16,
        debug = false
    } = {}) {
        this.config = {
            keywords,
            modelFiles,
            baseAssetUrl,
            frameSize,
            sampleRate,
            vadHangoverFrames,
            detectionThreshold,
            cooldownMs,
            executionProviders,
            embeddingWindowSize,
            debug
        };
        this._setOrtPath(ortWasmPath);
        this._emitter = createEmitter();
        this._melBuffer = [];
        this._embeddingWindowSize = embeddingWindowSize;
        this._activeKeywords = new Set(keywords);
        this._vadState = { h: null, c: null };
        this._isSpeechActive = false;
        this._vadHangover = 0;
        this._mediaStream = null;
        this._audioContext = null;
        this._workletNode = null;
        this._gainNode = null;
        this._processingQueue = Promise.resolve();
        this._isDetectionCoolingDown = false;
        this._loaded = false;
    }

    on(event, handler) {
        return this._emitter.on(event, handler);
    }

    off(event, handler) {
        this._emitter.off(event, handler);
    }

    async load() {
        if (this._loaded) return;
        const sessionOptions = { executionProviders: this.config.executionProviders };
        const resolver = (file) => `${this.config.baseAssetUrl.replace(/\/+$/, '')}/${file}`;
        this._debug('Loading core models with options', sessionOptions);

        this._melspecModel = await ort.InferenceSession.create(resolver('melspectrogram.onnx'), sessionOptions);
        this._embeddingModel = await ort.InferenceSession.create(resolver('embedding_model.onnx'), sessionOptions);
        this._vadModel = await ort.InferenceSession.create(resolver('silero_vad.onnx'), sessionOptions);

        this._keywordModels = {};
        let maxWindowSize = this.config.embeddingWindowSize;
        for (const keyword of this.config.keywords) {
            const file = this.config.modelFiles[keyword];
            if (!file) {
                this._debug(`No model file configured for keyword "${keyword}"`);
                continue;
            }
            try {
                const session = await ort.InferenceSession.create(resolver(file), sessionOptions);
                const windowSize = this._inferKeywordWindowSize(session) ?? this.config.embeddingWindowSize;
                maxWindowSize = Math.max(maxWindowSize, windowSize);
                const history = [];
                for (let i = 0; i < windowSize; i++) {
                    history.push(new Float32Array(96).fill(0));
                }
                this._keywordModels[keyword] = {
                    session,
                    scores: new Array(50).fill(0),
                    windowSize,
                    history
                };
                this._debug('Loaded keyword model', { keyword, file, windowSize });
            } catch (err) {
                console.warn(`[WakeWordEngine] Optional model "${keyword}" (${file}) failed to load. Skipping.`, err);
            }
        }
        this._embeddingWindowSize = maxWindowSize;
        this._debug('Embedding window size resolved', this._embeddingWindowSize);
        this._resetState();
        this._loaded = true;
        this._emitter.emit('ready');
    }

    async start({ deviceId, gain = 1.0 } = {}) {
        if (!this._loaded) throw new Error('Call load() before start()');
        if (this._workletNode) return;

        this._resetState();
        this._mediaStream = await navigator.mediaDevices.getUserMedia({
            audio: deviceId ? { deviceId: { exact: deviceId } } : true
        });

        this._audioContext = new AudioContext({ sampleRate: this.config.sampleRate });
        const source = this._audioContext.createMediaStreamSource(this._mediaStream);
        this._gainNode = this._audioContext.createGain();
        this._gainNode.gain.value = gain;

        const blob = new Blob([AUDIO_PROCESSOR], { type: 'application/javascript' });
        const workletURL = URL.createObjectURL(blob);
        await this._audioContext.audioWorklet.addModule(workletURL);
        this._workletNode = new AudioWorkletNode(this._audioContext, 'audio-processor');

        this._workletNode.port.onmessage = (event) => {
            const chunk = event.data;
            if (!chunk) return;
            this._processingQueue = this._processingQueue.then(() => this._processChunk(chunk)).catch((err) => {
                this._emitter.emit('error', err);
            });
        };

        source.connect(this._gainNode);
        this._gainNode.connect(this._workletNode);
        this._workletNode.connect(this._audioContext.destination);
        this._debug('Microphone stream started', { deviceId: deviceId ?? 'default', gain });
    }

    async stop() {
        if (this._workletNode) {
            this._workletNode.port.onmessage = null;
            this._workletNode.disconnect();
            this._workletNode = null;
        }
        if (this._gainNode) {
            this._gainNode.disconnect();
            this._gainNode = null;
        }
        if (this._audioContext && this._audioContext.state !== 'closed') {
            await this._audioContext.close();
        }
        this._audioContext = null;
        if (this._mediaStream) {
            this._mediaStream.getTracks().forEach((track) => track.stop());
            this._mediaStream = null;
        }
        this._isDetectionCoolingDown = false;
        this._debug('Engine stopped and media stream closed');
    }

    setGain(value) {
        if (this._gainNode) {
            this._gainNode.gain.value = value;
        }
    }

    async runWav(buffer) {
        if (!this._loaded) throw new Error('Call load() before runWav()');
        this._resetState();

        const audioContext = new (window.AudioContext || window.webkitAudioContext)();
        const decoded = await audioContext.decodeAudioData(buffer.slice(0));
        const offline = new OfflineAudioContext(1, Math.ceil(decoded.length * this.config.sampleRate / decoded.sampleRate), this.config.sampleRate);
        const src = offline.createBufferSource();
        src.buffer = decoded;
        src.connect(offline.destination);
        src.start();
        const rendered = await offline.startRendering();
        const audioData = rendered.getChannelData(0);
        this._debug('Running offline WAV', { samples: audioData.length });

        const minRequiredSamples = this._embeddingWindowSize * this.config.frameSize;
        let padded = audioData;
        if (padded.length < minRequiredSamples) {
            const padding = new Float32Array(minRequiredSamples - padded.length);
            const newAudioData = new Float32Array(minRequiredSamples);
            newAudioData.set(padded, 0);
            newAudioData.set(padding, padded.length);
            padded = newAudioData;
        }

        let highest = 0;
        for (let i = 0; i < Math.floor(padded.length / this.config.frameSize); i++) {
            const chunk = padded.subarray(i * this.config.frameSize, (i + 1) * this.config.frameSize);
            await this._processChunk(chunk, { emitEvents: false });
            for (const key of Object.keys(this._keywordModels)) {
                const tail = this._keywordModels[key].scores.slice(-1)[0];
                if (tail > highest) highest = tail;
            }
        }
        return highest;
    }

    _resetState() {
        this._melBuffer = [];
        const vadShape = [2, 1, 64];
        if (!this._vadState.h) {
            this._vadState.h = new ort.Tensor('float32', new Float32Array(128).fill(0), vadShape);
            this._vadState.c = new ort.Tensor('float32', new Float32Array(128).fill(0), vadShape);
        } else {
            this._vadState.h.data.fill(0);
            this._vadState.c.data.fill(0);
        }
        this._isSpeechActive = false;
        this._vadHangover = 0;
        this._isDetectionCoolingDown = false;
        if (this._keywordModels) {
            for (const key of Object.keys(this._keywordModels)) {
                this._keywordModels[key].scores.fill(0);
                const history = this._keywordModels[key].history;
                if (history) {
                    for (let i = 0; i < history.length; i++) {
                        history[i].fill(0);
                    }
                }
            }
        }
        this._debug('Internal buffers reset');
    }

    async _processChunk(chunk, { emitEvents = true } = {}) {
        if (this.config.debug) {
            let peak = 0;
            let sumSquares = 0;
            for (let i = 0; i < chunk.length; i++) {
                const sample = chunk[i];
                sumSquares += sample * sample;
                const abs = Math.abs(sample);
                if (abs > peak) peak = abs;
            }
            const rms = Math.sqrt(sumSquares / chunk.length);
            this._debug('Chunk received', { rms: Number(rms.toFixed(4)), peak: Number(peak.toFixed(4)) });
        }
        const vadTriggered = await this._runVad(chunk);
        if (vadTriggered) {
            if (!this._isSpeechActive && emitEvents) this._emitter.emit('speech-start');
            this._isSpeechActive = true;
            this._vadHangover = this.config.vadHangoverFrames;
        } else if (this._isSpeechActive) {
            this._vadHangover -= 1;
            if (this._vadHangover <= 0) {
                this._isSpeechActive = false;
                if (emitEvents) this._emitter.emit('speech-end');
            }
        }

        await this._runInference(chunk, this._isSpeechActive, emitEvents);
    }

    async _runVad(chunk) {
        try {
            const tensor = new ort.Tensor('float32', chunk, [1, chunk.length]);
            const sr = new ort.Tensor('int64', [BigInt(this.config.sampleRate)], []);
            const res = await this._vadModel.run({ input: tensor, sr, h: this._vadState.h, c: this._vadState.c });
            this._vadState.h = res.hn;
            this._vadState.c = res.cn;
            const confidence = res.output.data[0];
            this._debug('VAD result', { confidence: Number(confidence.toFixed(3)) });
            return confidence > 0.5;
        } catch (err) {
            this._emitter.emit('error', err);
            return false;
        }
    }

    async _runInference(chunk, isSpeechActive, emitEvents) {
        const melspecTensor = new ort.Tensor('float32', chunk, [1, this.config.frameSize]);
        const melspecResults = await this._melspecModel.run({ [this._melspecModel.inputNames[0]]: melspecTensor });
        const newMelData = melspecResults[this._melspecModel.outputNames[0]].data;

        for (let j = 0; j < newMelData.length; j++) {
            newMelData[j] = newMelData[j] / 10.0 + 2.0;
        }
        for (let j = 0; j < 5; j++) {
            this._melBuffer.push(new Float32Array(newMelData.subarray(j * 32, (j + 1) * 32)));
        }

        while (this._melBuffer.length >= 76) {
            const windowFrames = this._melBuffer.slice(0, 76);
            const flattenedMel = new Float32Array(76 * 32);
            for (let j = 0; j < windowFrames.length; j++) {
                flattenedMel.set(windowFrames[j], j * 32);
            }

            const embeddingFeeds = { [this._embeddingModel.inputNames[0]]: new ort.Tensor('float32', flattenedMel, [1, 76, 32, 1]) };
            const embeddingOut = await this._embeddingModel.run(embeddingFeeds);
            const newEmbedding = embeddingOut[this._embeddingModel.outputNames[0]].data;

            const embeddingVector = new Float32Array(newEmbedding);

            for (const name of Object.keys(this._keywordModels)) {
                const keywordModel = this._keywordModels[name];
                keywordModel.history.shift();
                keywordModel.history.push(embeddingVector);

                const flattenedEmbeddings = new Float32Array(keywordModel.windowSize * 96);
                for (let j = 0; j < keywordModel.history.length; j++) {
                    flattenedEmbeddings.set(keywordModel.history[j], j * 96);
                }
                const finalInput = new ort.Tensor('float32', flattenedEmbeddings, [1, keywordModel.windowSize, 96]);
                const results = await keywordModel.session.run({ [keywordModel.session.inputNames[0]]: finalInput });
                const score = results[keywordModel.session.outputNames[0]].data[0];
                keywordModel.scores.shift();
                keywordModel.scores.push(score);
                this._debug('Keyword score', { keyword: name, score: Number(score.toFixed(3)), windowSize: keywordModel.windowSize });

                const keywordActive = this._activeKeywords.has(name);
                if (emitEvents && keywordActive && score > this.config.detectionThreshold && isSpeechActive && !this._isDetectionCoolingDown) {
                    this._isDetectionCoolingDown = true;
                    this._debug('Detection emitted', { keyword: name, score });
                    this._emitter.emit('detect', { keyword: name, score, at: performance.now() });
                    setTimeout(() => { this._isDetectionCoolingDown = false; }, this.config.cooldownMs);
                } else if (emitEvents && !keywordActive) {
                    this._debug('Detection suppressed (inactive keyword)', { keyword: name, score });
                }
            }
            this._melBuffer.splice(0, 8);
        }
    }

    _setOrtPath(path) {
        if (path) {
            ort.env.wasm.wasmPaths = path;
        }
    }

    _inferKeywordWindowSize(session) {
        if (!session) return undefined;
        const metadata = session.inputMetadata;
        const inputName = session.inputNames?.[0];
        if (!metadata || !inputName) return undefined;
        let meta;
        if (Array.isArray(metadata)) {
            meta = metadata.find((m) => m?.name === inputName) || metadata[0];
        } else {
            meta = metadata[inputName];
        }
        if (!meta || !meta.isTensor || !Array.isArray(meta.shape)) return undefined;
        const dim = meta.shape[1];
        return typeof dim === 'number' && Number.isFinite(dim) ? dim : undefined;
    }

    _debug(...args) {
        if (this.config.debug) {
            console.debug('[WakeWordEngine]', ...args);
        }
    }

    setActiveKeywords(keywords) {
        const next = Array.isArray(keywords) && keywords.length ? keywords : this.config.keywords;
        this._activeKeywords = new Set(next);
        this._debug('Active keywords updated', Array.from(this._activeKeywords));
    }
}

```

</details>

---

<details>
<summary>📂 <b>roboas/robot_mcp.py</b> (Click to expand)</summary>

```python

# ROBOT MCP SERVER — compatible with Week_10_day_5Humanedited_MCP_COMPAT.py
import asyncio
import logging
import os
import importlib.util
import json
from typing import Any

from mcp.server import Server
from mcp.types import Tool, TextContent
from mcp.server.sse import SseServerTransport
# Starlette removed - using pure ASGI routing for maximum robustness
import uvicorn

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("robot-mcp")

# ------------------------------------------------------------
# Load the main robot controller without renaming its functions.
# Keep Week_10_day_5Humanedited_MCP_COMPAT.py in the same folder
# as this RoboControl file when running on the robot PC.
# ------------------------------------------------------------
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
ROBOT_CONTROL_PATH = os.path.join(THIS_DIR, "nogripperref.py")

spec = importlib.util.spec_from_file_location("robot_control", ROBOT_CONTROL_PATH)
robot_control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(robot_control)

# ------------------------------------------------------------
# Event completion signaling back to server.js
# ------------------------------------------------------------
CLIENT_IP = "localhost"

def send_robot_event(event_type, error_msg=None):
    global CLIENT_IP
    import urllib.request
    import json
    
    server_host = os.environ.get("SERVER_HOST", CLIENT_IP)
    url = f"http://{server_host}:3000/robot-event"
    payload = {"event": event_type}
    if error_msg:
        payload["error"] = str(error_msg)
        
    logger.info(f"Sending event '{event_type}' to server at {url}...")
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=3) as response:
            logger.info(f"Event sent successfully, response status: {response.status}")
    except Exception as e:
        logger.error(f"Failed to send event to server: {e}")

# Register the callback in the robot control module
robot_control.ROBOT_EVENT_CALLBACK = send_robot_event

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
                "and angle_deg (yaw in degrees, robot base frame, from YOLOv11 OBB decomposed RPY). "
                "For the pipe, grasp_label identifies which end the segmentation model selected."
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
                    },
                    "grasp_label": {
                        "type": "string",
                        "description": (
                            "For pipe only: which end vision segmentation selected "
                            "(grasp_A or grasp_B). When provided, x/y/z already point to "
                            "that end and catalogue offsets are zeroed automatically."
                        )
                    },
                    "detections": {
                        "type": "array",
                        "description": "Full YOLO detection list for the current scene. Used for placement boundary occupancy.",
                        "items": {"type": "object"}
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
                    },
                    "target_name": {
                        "type": "string",
                        "description": "Name of the target object being cleared. Used to ensure extra clearance."
                    }
                },
                "required": ["obstacle_name", "obstacle_x", "obstacle_y", "obstacle_z"]
            }
        ),
        Tool(
            name="emergency_stop",
            description="Emergency stop. Halts all physical robot movements immediately.",
            inputSchema={"type": "object", "properties": {}}
        ),
        Tool(
            name="return_home",
            description="Return home. Clears errors, ensures robot is ready, and moves to the home position safely.",
            inputSchema={"type": "object", "properties": {}}
        )
    ]

# 3. Execute tool calls
@server.call_tool()
async def handle_call_tool(name: str, arguments: dict | None) -> list[TextContent]:
    args = arguments or {}

    if name == "pick_and_place_object":
        object_name = args.get("object_name")
        x           = float(args.get("x"))
        y           = float(args.get("y"))
        z           = float(args.get("z"))
        angle_deg   = float(args["angle_deg"]) if "angle_deg" in args else None
        grasp_label = args.get("grasp_label", None)
        detections  = args.get("detections", None)

        logger.info(
            f"MCP pick_and_place_object: {object_name} at ({x}, {y}, {z}) "
            f"angle={angle_deg} grasp={grasp_label} detections_count={len(detections) if detections else 0}"
        )
        result = await asyncio.to_thread(
            robot_control.run_mcp_pick_and_place,
            object_name,
            x, y, z,
            angle=angle_deg,
            detections=detections,
            grasp_label=grasp_label,
        )
        return [TextContent(type="text", text=f"Completed: {result}")]

    if name == "move_to_coordinates":
        object_name = args.get("object_name", "cube")
        x = float(args.get("x"))
        y = float(args.get("y"))
        z = float(args.get("z"))
        angle_deg = float(args["angle_deg"]) if "angle_deg" in args else None
        detections = args.get("detections", None)

        logger.info(f"Compatibility move_to_coordinates -> pick_and_place_object: {object_name} at ({x}, {y}, {z}) angle={angle_deg}")
        result = await asyncio.to_thread(
            robot_control.run_mcp_pick_and_place,
            object_name, x, y, z, angle=angle_deg, detections=detections
        )
        return [TextContent(type="text", text=f"Completed: {result}")]

    if name == "relocate_object":
        obstacle_name  = args.get("obstacle_name")
        obstacle_x     = float(args.get("obstacle_x"))
        obstacle_y     = float(args.get("obstacle_y"))
        obstacle_z     = float(args.get("obstacle_z", 0.0))
        obstacle_angle = float(args["obstacle_angle_deg"]) if "obstacle_angle_deg" in args else None
        detections     = args.get("detections", None)
        target_name    = args.get("target_name", None)

        logger.info(
            f"MCP relocate_object: {obstacle_name} at "
            f"({obstacle_x}, {obstacle_y}, {obstacle_z}) angle={obstacle_angle} target={target_name}"
        )

        result = await asyncio.to_thread(
            robot_control.run_mcp_relocate_object,
            obstacle_name=obstacle_name,
            obstacle_x=obstacle_x,
            obstacle_y=obstacle_y,
            obstacle_z=obstacle_z,
            obstacle_angle=obstacle_angle,
            detections=detections,
            target_name=target_name,
        )

        # Robot signals it needs a fresh detection before Qwen plans next action.
        # The MCP server triggers the YOLO re-photograph here so Qwen automatically
        # receives the updated scene in the response without managing the camera itself.
        if result.get("requires_redetection"):
            logger.info("Relocation complete — triggering fresh YOLO detection...")
            try:
                import urllib.request
                import json
                payload = {
                    "jsonrpc": "2.0",
                    "method": "tools/call",
                    "params": {"name": "capture_and_detect", "arguments": {}},
                    "id": 1
                }
                req = urllib.request.Request(
                    "http://localhost:8001/messages",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"}
                )
                with urllib.request.urlopen(req, timeout=10) as r:
                    res_data = json.loads(r.read().decode("utf-8"))
                    if "result" in res_data:
                        content_text = res_data["result"]["content"][0]["text"]
                        parsed_content = json.loads(content_text)
                        result["fresh_detections"] = parsed_content.get("detections", [])
                        result["redetection_note"] = "Fresh YOLO photo triggered and detections parsed successfully."
                    else:
                        result["redetection_note"] = f"Failed to call Vision MCP: {res_data.get('error')}"
            except Exception as e:
                logger.error(f"Failed to fetch fresh detections from Vision MCP: {e}")
                result["redetection_note"] = f"Failed to trigger fresh YOLO photo: {str(e)}"

        return [TextContent(type="text", text=f"Completed: {result}")]

    if name == "emergency_stop":
        logger.warning("⚠️ EMERGENCY STOP TRIGGERED!")
        try:
            if hasattr(robot_control, 'power_off_robot'):
                await asyncio.to_thread(robot_control.power_off_robot)
            elif hasattr(robot_control, 'r') and robot_control.r is not None:
                await asyncio.to_thread(robot_control.r.stop)
            return [TextContent(type="text", text="Emergency Stop Successful: Robot powered off.")]
        except Exception as e:
            logger.error(f"Error during emergency stop: {e}")
            return [TextContent(type="text", text=f"Error executing emergency stop: {str(e)}")]

    if name == "return_home":
        print("\n🏠 [VS CODE CONSOLE] RETURN HOME SIGNAL RECEIVED! Interrupting motion and returning home.\n")
        logger.info("🏠 RETURN HOME TRIGGERED!")
        try:
            if hasattr(robot_control, 'mcp_return_home'):
                await asyncio.to_thread(robot_control.mcp_return_home)
            return [TextContent(type="text", text="Return Home Successful: Robot interrupted and moved to home position.")]
        except Exception as e:
            logger.error(f"Error during return home: {e}")
            return [TextContent(type="text", text=f"Error executing return home: {str(e)}")]



    raise ValueError(f"Unknown tool: {name}")

# 4. SSE Networking Setup
sse = SseServerTransport("/messages")

async def sse_app(scope, receive, send):
    """Handles the initial Ethernet/SSE connection from server.js or MCP client."""
    async with sse.connect_sse(scope, receive, send) as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())

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

# 5. Raw ASGI Routing App
async def app(scope, receive, send):
    global CLIENT_IP

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
            client = scope.get("client")
            if client:
                CLIENT_IP = client[0]
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
    logger.info("🦾 Robot Arm MCP Server listening on Ethernet port 8002...")
    logger.info("Tool: pick_and_place_object(object_name, x, y, z, angle_deg[optional])")
    uvicorn.run(app, host="0.0.0.0", port=8002)

```

</details>

---

<details>
<summary>📂 <b>roboas/vision_mcp.py</b> (Click to expand)</summary>

```python
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
    "blue marker":  {"size": "134 x 20.53 x 20.53 mm", "height_m": 0.02053,
                     "length_m": 0.134,   "breadth_m": 0.02053},
    "cube":         {"size": "40 x 40 x 40 mm",         "height_m": 0.040,
                     "length_m": 0.040,   "breadth_m": 0.040},
    "green marker": {"size": "134 x 20.53 x 20.53 mm",  "height_m": 0.02053,
                     "length_m": 0.134,   "breadth_m": 0.02053},
    "medicine":     {"size": "115.72 x 51.17 x 18.95 mm","height_m": 0.01895,
                     "length_m": 0.11572, "breadth_m": 0.05117},
    "nut":          {"size": "34.6 x 30 x 17 mm",        "height_m": 0.017,
                     "length_m": 0.0346,  "breadth_m": 0.030},
    "pipe":         {"size": "120 x 110 x 54.5 mm",      "height_m": 0.0545,
                     "length_m": 0.120,   "breadth_m": 0.110,
                     "notes": "Smart grasp via segmentation mask"},
    "sponge":       {"size": "112.58 x 80 x 15.4 mm",    "height_m": 0.01540,
                     "length_m": 0.11258, "breadth_m": 0.080,
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
    """Send image + prompt to Qwen3-VL via Ollama API."""
    logger.info(f"Connecting to Qwen at {OLLAMA_IP} with model {QWEN_MODEL}...")

    raw_b64 = base64_image
    if raw_b64.startswith("data:"):
        parts = raw_b64.split(",")
        if len(parts) > 1:
            raw_b64 = parts[1]

    # Removed /no_think because it caused qwen3-vl:2b to return empty responses.
    prompt_with_directive = prompt

    payload = {
        "model": QWEN_MODEL,
        "prompt": prompt_with_directive,
        "stream": False,
        "think": False,   # Stops Qwen3 burning all tokens on 'thinking' before the answer
        "images": [raw_b64],
        "options": {
            "temperature": 0.1,
            "num_predict": 1024
        }
    }

    print("IMAGE SIZE:", len(raw_b64))

    url = f"http://{OLLAMA_IP}:11434/api/generate"
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

        response_text = result.get("response", "")
        
        # Qwen3 thinking mode fallback: Ollama splits output into
        # "thinking" + "response". If the model spent all tokens
        # thinking, "response" is empty but the answer may be
        # inside the "thinking" field.
        if not response_text.strip():
            thinking_text = result.get("thinking", "")
            if thinking_text.strip():
                logger.warning("Qwen 'response' empty but 'thinking' has content — using it.")
                print("QWEN THINKING FIELD:", thinking_text[:500])
                response_text = thinking_text
            else:
                logger.error("Ollama returned empty in both 'response' and 'thinking'.")
                print("FULL OLLAMA RESULT KEYS:", list(result.keys()))
                print("FULL OLLAMA RESULT:", json.dumps(result, indent=2)[:2000])
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
    target_det = next((d for d in detections if d["object_name"] == target and not is_inside_placement_box(d)), None)

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
        expected_z = known_h / 2
        excess = d["z"] - expected_z
        if excess <= 0.020:
            continue

        elevation_found = True
        implied_height = excess * 2
        MATCH_TOLERANCE = 0.015
        plausible = []
        for obj_name, obj_info in OBJECT_CATALOGUE.items():
            if obj_name == d["object_name"]:
                continue
            h = obj_info["height_m"]
            if abs(h - implied_height) <= MATCH_TOLERANCE:
                plausible.append(obj_name)

        if d["object_name"] == target:
            # TARGET has an anomalously high surface Z.
            lines.append(
                f"  - CAUTION: Target '{target}' has an anomalously high surface (Z={d['z']*1000:.0f}mm). "
                f"Look at the image carefully. If you see a smaller object (like a 'cube') resting on top of OR inside/blocking the {target}, you MUST output 'relocate' for that object."
            )
        else:
            # NON-TARGET is elevated — check if it might be sitting ON the target
            # Only flag as block if it is physically close/overlapping in XY to the target
            dist = 9999.0
            if target_det:
                dist = math.hypot(d["x"] - target_det["x"], d["y"] - target_det["y"])

            if dist < 0.080 and target in plausible:
                lines.append(
                    f"  - WARNING: {d['object_name']} is elevated (Z={d['z']*1000:.0f}mm) "
                    f"and is likely sitting ON TOP OF target '{target}'. "
                    f"MUST relocate {d['object_name']} first."
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
            if d["object_name"] == target or is_inside_placement_box(d):
                continue
            if check_overlap_obb(d, target_det, clearance=0.015):
                overlap_found = True
                lines.append(f"  - WARNING: {d['object_name']} overlaps with target {target}.")

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
    scene_analysis = compute_scene_analysis(target, detections)
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
            f"  Use this as a hint when the scene is ambiguous.\n\n"
        )

    prompt = (
        f"You are the visual safety gate for a robotic arm.\n\n"
        f"GOAL: Pick up the '{target}'\n\n"
        f"{user_context_section}"
        f"KNOWN OBJECT CATALOGUE: {catalogue_list}\n\n"
        f"PYTHON SENSOR ANALYSIS (Depth Elevation & XY Overlap):\n"
        f"{scene_analysis}\n\n"
        f"HISTORY:\n{history_summary}\n\n"
        f"INSTRUCTIONS:\n"
        f"  1. Look at the image AND read the Python sensor analysis.\n"
        f"  2. If the analysis shows a WARNING that another object is ON TOP of or overlapping with the target, output 'relocate' for that blocking object.\n"
        f"  3. If the analysis shows CAUTION that the target has an anomalously high surface, look for an object sitting ON TOP OF or INSIDE/BLOCKING it. If you see one (like a 'cube'), output 'relocate' for that object. If it is clear, output 'pick'.\n"
        f"  4. If the analysis says the target is clear, verify visually. If it looks clear and nothing is inside/blocking it, output 'pick'.\n"
        f"  5. If you see an unknown object (not in catalogue) blocking the target, output 'abort'.\n"
        f"  6. Do not re-relocate already moved objects (check history).\n\n"
        f"AVAILABLE ACTIONS:\n"
        f"  - relocate: move one blocking object to a safe spot.\n"
        f"  - pick: pick the target.\n"
        f"  - abort: cannot safely reach the target.\n\n"
        f"CRITICAL INSTRUCTION: You may think first, but your final output MUST be a valid JSON block enclosed in '```json' and '```' markers.\n"
        f"NO EXPLANATIONS AFTER THE JSON. ONLY OUTPUT JSON AS THE FINAL RESULT.\n\n"
        f"Pick format:\n"
        f'{{"next_action":"pick","obstacle_name":null,"reasoning":"target is visible and safe"}}\n\n'
        f"Relocate format:\n"
        f'{{"next_action":"relocate","obstacle_name":"[name_of_blocking_object]","reasoning":"[name] is blocking the target"}}\n\n'
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
        
        # Default to PICK not abort — the Python sensor analysis (overlap +
        # elevation) has already gated this point. If sensors said the target
        # was blocked, the relocate would have been forced earlier. Aborting
        # because the LLM rambled is the wrong failure mode.
        plan = {
            "next_action": "pick",
            "obstacle_name": None,
            "reasoning": f"Qwen unparseable ({clean_reason}) — sensor analysis clear, proceeding with direct pick."
        }
        
    print("PARSED QWEN ACTION:", plan)
    plan["raw_output"]=raw
    
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
                if d.get("object_name") == target and not is_inside_placement_box(d)
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
                if expected_h is not None and target_detections:
                    expected_z = expected_h / 2
                    excess = target_detections[0]["z"] - expected_z
                    if excess > 0.020:
                        # Find overlapping or closest detected obstacle to target
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
                            if dist < 0.080 and dist < min_dist:
                                min_dist = dist
                                best_obstacle = d.get("object_name")
                        sensor_obstacle = best_obstacle if best_obstacle else "cube"
                        sensor_reasoning = f"Depth sensor override: Target '{target}' surface is {excess*1000:.0f}mm higher than expected, indicating overlapping object '{sensor_obstacle}' is on top."

                # Check 2: XY overlap
                if not sensor_obstacle and target_detections:
                    target_det = target_detections[0]
                    target_radius = math.hypot(
                        target_info.get("length_m", 0.04),
                        target_info.get("breadth_m", 0.04),
                    ) / 2

                    overlapping_obstacle = None
                    min_overlap_dist = 9999.0

                    for d in detections:
                        if d["object_name"] == target or is_inside_placement_box(d):
                            continue
                        if check_overlap_obb(d, target_det, clearance=0.015):
                            dist = math.hypot(d["x"] - target_det["x"], d["y"] - target_det["y"])
                            if dist < min_overlap_dist:
                                min_overlap_dist = dist
                                overlapping_obstacle = d["object_name"]

                    if overlapping_obstacle:
                        sensor_obstacle = overlapping_obstacle
                        sensor_reasoning = f"XY overlap override: Target '{target}' is physically overlapping with '{overlapping_obstacle}' (distance {min_overlap_dist*1000:.1f}mm), requiring relocation."

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
                    plan = await qwen_plan_next_action(
                        target,
                        frame_b64,
                        detections,
                        action_history,
                        user_context,
                    ) if frame_b64 else {
                        "next_action": "abort",
                        "obstacle_name": None,
                        "reasoning": "No camera frame — aborting for safety.",
                        "raw_output": "No camera frame available, did not run qwen",
                    }
                print("AFTER QWEN/SENSOR PLAN:", plan)

                if plan.get("next_action") == "pick":
                    # Failsafe: If Qwen hallucinated that the path is clear, but depth sensor knows there is an anomaly.
                    target_info = OBJECT_CATALOGUE.get(target, {})
                    expected_h = target_info.get("height_m")
                    if expected_h is not None and target_detections:
                        expected_z = expected_h / 2
                        excess = target_detections[0]["z"] - expected_z
                        if excess > 0.020:
                            # Find overlapping or closest detected obstacle to target
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
                                if dist < 0.080 and dist < min_dist:
                                    min_dist = dist
                                    best_obstacle = d.get("object_name")
                            obstacle_name = best_obstacle if best_obstacle else "cube"

                            logger.warning(f"QWEN FAILSAFE OVERRIDE: Target '{target}' Z is anomalously high (+{excess*1000:.0f}mm). Forcing relocate of '{obstacle_name}'.")
                            plan = {
                                "next_action": "relocate",
                                "obstacle_name": obstacle_name,
                                "reasoning": f"Depth sensor failsafe: Target '{target}' surface is {excess*1000:.0f}mm higher than expected, indicating an undetected or overlapping object '{obstacle_name}' is inside or on top of it.",
                                "raw_output": plan.get("raw_output", "") + f"\n\n(OVERRIDDEN BY DEPTH SENSOR FAILSAFE: Relocating {obstacle_name})"
                            }

                    # XY Overlap Failsafe: If Qwen hallucinated that it is clear, but YOLO detects an overlap
                    if plan.get("next_action") == "pick" and target_detections:
                        target_det = target_detections[0]
                        target_info = OBJECT_CATALOGUE.get(target, {})
                        target_radius = math.hypot(
                            target_info.get("length_m", 0.04),
                            target_info.get("breadth_m", 0.04),
                        ) / 2

                        overlapping_obstacle = None
                        min_overlap_dist = 9999.0

                        for d in detections:
                            if d["object_name"] == target or is_inside_placement_box(d):
                                continue
                            if check_overlap_obb(d, target_det, clearance=0.015):
                                dist = math.hypot(d["x"] - target_det["x"], d["y"] - target_det["y"])
                                # Overlaps! Find the closest overlapping object
                                if dist < min_overlap_dist:
                                    min_overlap_dist = dist
                                    overlapping_obstacle = d["object_name"]

                        if overlapping_obstacle:
                            logger.warning(f"QWEN FAILSAFE OVERRIDE: Overlapping object '{overlapping_obstacle}' detected in XY. Forcing relocate.")
                            plan = {
                                "next_action": "relocate",
                                "obstacle_name": overlapping_obstacle,
                                "reasoning": f"XY overlap failsafe: Target '{target}' is physically overlapping with '{overlapping_obstacle}' (distance {min_overlap_dist*1000:.1f}mm), requiring relocation.",
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
                elif obstacle_name and target_detections:
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
                        t = target_detections[0]
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
                        obs = target_detections[0].copy()
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

```

</details>

---

<details>
<summary>📂 <b>roboas/camera.py</b> (Click to expand)</summary>

```python
import math
import os
import cv2
import numpy as np
import pyrealsense2 as rs
from ultralytics import YOLO
from mcp.server.fastmcp import FastMCP
import threading
import base64
import time
# Unused leftover imports (inference, vision) removed

# -----------------------------------------------------------------------------
# 1. Global State Variables (Shared between Vision Thread and MCP Server)
# -----------------------------------------------------------------------------
current_rgb_frame = None
current_target_class = None  # e.g., "cup", "bottle", "apple"
latest_3d_coords = {"x": 0.0, "y": 0.0, "z": 0.0}
current_depth_frame = None
camera_intrinsics = None
inference_lock = threading.Lock()

# Tweak this value to add a global Z offset (in meters) to all detected objects.
# E.g., setting it to 0.02 will raise the target pick point by 2 cm.
Z_OFFSET = 0.025
DISPLAY_ONLY_TARGET = True

def smooth_coord(old_val, new_val, alpha=0.2, snap_thresh=0.05):
    """
    Applies Exponential Moving Average to stabilize flickering coordinates.
    If the new value jumps significantly (> snap_thresh in meters), it snaps instantly.
    """
    if old_val == 0.0 or abs(old_val - new_val) > snap_thresh:
        return new_val
    return alpha * new_val + (1 - alpha) * old_val

# Initialize F                                                                                                                                                          1`astMCP Server
mcp = FastMCP("TIEFA_Module_B_Vision")

model=YOLO("best16.pt")
segment=YOLO("best13.pt")

# -----------------------------------------------------------------------------
# 2. MCP Tools Definition (Exposed to System 2 / ZBook)
# -----------------------------------------------------------------------------

tracked=False

@mcp.tool()
def get_camera_snapshot() -> str:
    """
    Capture a current RGB frame from the D435i camera.
    Returns the image as a Base64 encoded string for the VLM (System 2) to analyze.
    If a question is provided, asks the question to Qwen and returns the text response.
    """
    global current_rgb_frame
    if current_rgb_frame is None:
        return "Error: Camera frame not ready."
    
    # Encode frame to JPEG, then to Base64
    _, buffer = cv2.imencode('.jpg', current_rgb_frame)
    base64_str = base64.b64encode(buffer).decode('utf-8')
    print("Took snapshot")
    
    return f"data:image/jpeg;base64,{base64_str}"


@mcp.tool()
def set_tracking_target(target_name: str) -> str:
    """
    Set the object class for System 1 (YOLO) to track.
    System 2 calls this after reasoning. Example target_name: "bottle"
    """
    global current_target_class
    current_target_class = target_name
    print(current_target_class)

    return current_target_class

# -----------------------------------------------------------------------------
# 3. Vision Loop (Runs in a separate background thread)
# -----------------------------------------------------------------------------

def rotationMatrixToEulerAngles(R):
    sy = math.sqrt(R[0, 0] ** 2 + R[1, 0] ** 2)
    singular = sy < 1e-6

    if not singular:
        x = math.atan2(R[2, 1], R[2, 2])
        y = math.atan2(-R[2, 0], sy)
        z = math.atan2(R[1, 0], R[0, 0])
    else:
        x = math.atan2(-R[1, 2], R[1, 1])
        y = math.atan2(-R[2, 0], sy)
        z = 0

    return np.array([x, y, z])



def _vision_loop_inner():
    global current_rgb_frame, current_target_class, latest_3d_coords, last_click, spatial_coords, current_depth_frame, camera_intrinsics
    # Configure Intel RealSense pipeline
    pipeline = rs.pipeline()
    config = rs.config()
    config.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
    config.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 30)

    # Start streaming
    profile = pipeline.start(config)

    # Align depth stream to color stream (Crucial for 3D mapping)
    align_to = rs.stream.color
    align = rs.align(align_to)

    # Get camera intrinsics (Needed for pixel to 3D conversion)
    depth_sensor = profile.get_device().first_depth_sensor()
    camera=profile.get_device()
    advanced_mode = rs.rs400_advanced_mode(camera)
    depth_table=advanced_mode.get_depth_table()
    depth_table.disparityShift=20
    depth_scale = depth_sensor.get_depth_scale()
    intrinsics = profile.get_stream(rs.stream.color).as_video_stream_profile().get_intrinsics()
    camera_intrinsics = intrinsics

    print("[Vision Thread] D435i Camera Started. YOLO Inference Running...")

    clicked=""

    frame_counter = 0
    results = []
    sponge_detection = []

    try:
        while True:
            frames = pipeline.wait_for_frames()
            aligned_frames = align.process(frames)
            
            depth_frame = aligned_frames.get_depth_frame()
            color_frame = aligned_frames.get_color_frame()
            
            if not depth_frame or not color_frame:
                continue

            # Convert images to numpy arrays
            color_image = np.asanyarray(color_frame.get_data())
            current_rgb_frame = color_image.copy() # Update global state for MCP snapshot
            current_depth_frame = depth_frame

            # Run inference every 4th frame to conserve GPU/VRAM resources for Qwen/Ollama
            if frame_counter % 4 == 0:
                with inference_lock:
                    results = model(color_image, verbose=False, agnostic_nms=False, iou=0.35, conf=0.35)
                    sponge_detection = segment(color_image, verbose=False, agnostic_nms=False, iou=0.35, conf=0.35)

            current_boxes = []

            # Draw segmentation masks for sponge/pipe
            for sponge in sponge_detection:
                if sponge.masks is None:
                    continue

                masks = sponge.masks.data.cpu().numpy()
                class_ids = sponge.boxes.cls.cpu().numpy().astype(int)
                confidences = sponge.boxes.conf.cpu().numpy()

                for mask, class_id, conf in zip(masks, class_ids, confidences):
                    cls_name = segment.names[class_id].lower()

                    is_target = (
                        current_target_class is not None and
                        cls_name == current_target_class.lower()
                    )

                    if DISPLAY_ONLY_TARGET and current_target_class is not None and not is_target:
                        continue

                    mask_binary = cv2.resize(mask, (color_image.shape[1], color_image.shape[0]))
                    mask_binary = ((mask_binary > 0.5) * 255).astype(np.uint8)

                    colored_mask = color_image.copy()
                    colored_mask[mask_binary > 0] = [215, 215, 218]
                    color_image = cv2.addWeighted(color_image, 0.7, colored_mask, 0.3, 0)

                    contours, _ = cv2.findContours(mask_binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                    cv2.drawContours(color_image, contours, -1, (0, 255, 0), 2)

                    if len(contours) > 0:
                        largest_contour = max(contours, key=cv2.contourArea)
                        x, y, w, h = cv2.boundingRect(largest_contour)
                        cv2.putText(
                            color_image,
                            f"{cls_name} SEG {conf:.2f}",
                            (x, y - 10),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.5,
                            (0, 255, 0),
                            2
                        )

            # Draw OBB boxes for all detected objects
            for result in results:
                if result.obb is None:
                    continue

                for obb in result.obb:
                    cls_id = int(obb.cls[0])
                    cls_name = model.names[cls_id].lower()
                    confidence = float(obb.conf[0])

                    (x1, y1), (x2, y2), (x3, y3), (x4, y4) = obb.xyxyxyxy[0].cpu().numpy().astype(int)

                    current_boxes.append((x1, y1, x2, y2, x3, y3, x4, y4, cls_name, confidence))

                    is_target = (
                        current_target_class is not None and
                        cls_name == current_target_class.lower()
                    )

                    if DISPLAY_ONLY_TARGET and current_target_class is not None and not is_target:
                        continue

                    cv2.polylines(
                        color_image,
                        [np.array([(x1, y1), (x2, y2), (x3, y3), (x4, y4)], dtype=np.int32)],
                        True,
                        (0, 255, 255),
                        2
                    )

                    cv2.putText(
                        color_image,
                        f"{cls_name} OBB {confidence:.2f}",
                        (x1, y1 - 10),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.5,
                        (0, 255, 255),
                        2
                    )

                    # Only update latest_3d_coords for the selected target
                    if current_target_class and cls_name == current_target_class.lower():
                        CAM_TO_ROBOT_T = np.array([
                            [0.7337634310,  0.6126652048, -0.2936538341,  0.7173839756],
                            [0.6785283256, -0.6388791698,  0.3625365054, -0.4903506740],
                            [0.0345041846, -0.4652684744, -0.8844968672,  0.7880605490],
                            [0.0,           0.0,           0.0,            1.0]
                        ], dtype=np.float64)

                        rotation_from_matrix = CAM_TO_ROBOT_T[:3, :3]

                        center_x = int(obb.xywhr[0][0])
                        center_y = int(obb.xywhr[0][1])
                        angle = float(obb.xywhr[0][4])

                        rotation_from_camera = np.array([
                            [math.cos(angle), -math.sin(angle), 0],
                            [math.sin(angle),  math.cos(angle), 0],
                            [0,                0,               1]
                        ])

                        distance = depth_frame.get_distance(center_x, center_y)
                        spatial_coords = rs.rs2_deproject_pixel_to_point(intrinsics, [center_x, center_y], distance)

                        latest_3d_coords["x"] = smooth_coord(latest_3d_coords["x"], spatial_coords[0])
                        latest_3d_coords["y"] = smooth_coord(latest_3d_coords["y"], spatial_coords[1])
                        latest_3d_coords["z"] = smooth_coord(latest_3d_coords["z"], spatial_coords[2] + Z_OFFSET)

                        roll, pitch, yaw = rotationMatrixToEulerAngles(rotation_from_matrix @ rotation_from_camera)
                        robot = CAM_TO_ROBOT_T @ np.array([spatial_coords[0], spatial_coords[1], spatial_coords[2], 1.0])

                        cv2.putText(
                            color_image,
                            f"TARGET {cls_name}: X:{robot[0]*1000:.1f} Y:{robot[1]*1000:.1f} Z:{robot[2]*1000:.1f}mm",
                            (20, 70),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.7,
                            (0, 0, 255),
                            2
                        )
                        

            # Show the live feed (for debugging on i5 laptop)
            cv2.imshow("Module B: System 1 Vision Reflex", color_image)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

            frame_counter += 1
            time.sleep(0.01)

    finally:
        try:
            pipeline.stop()
            cv2.destroyAllWindows()
        except:
            pass

def vision_loop():
    try:
        _vision_loop_inner()
    except Exception as e:
        import traceback
        print(f"[FATAL] Camera Vision Loop crashed: {e}")
        traceback.print_exc()

# -----------------------------------------------------------------------------
# 4. Main Execution
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    # Start the vision processing in a background thread
    
    vision_thread = threading.Thread(target=vision_loop, daemon=True)
    vision_thread.start()

    # Give the camera 2 seconds to warm up
    time.sleep(2)
    print("[MCP Server] Starting FastMCP Server on Main Thread...")
    
    # Run the MCP server (This blocks the main thread, handling API requests)
    mcp.run()
```

</details>

---

<details>
<summary>📂 <b>roboas/mcp_emoji_server.py</b> (Click to expand)</summary>

```python
import asyncio
import sys
import json
import urllib.request
from mcp.server.stdio import stdio_server
from mcp.server import Server
from mcp.types import (
    TextContent,
    Tool,
)

# Initialize the MCP Server
server = Server("roboas-emoji-server")

@server.list_tools()
async def handle_list_tools() -> list[Tool]:
    """List available tools for the robot's state management."""
    return [
        Tool(
            name="switch_avatar",
            description="Switch the persona to John (male) or Linda (female) when explicitly requested.",
            inputSchema={
                "type": "object",
                "properties": {
                    "persona": {
                        "type": "string",
                        "enum": ["john", "linda"],
                        "description": "The targeted persona to switch to."
                    }
                },
                "required": ["persona"]
            }
        ),
        Tool(
            name="get_status_emoji",
            description="Get the specific emoji character to append beside John's name based on his current state.",
            inputSchema={
                "type": "object",
                "properties": {
                    "state": {
                        "type": "string",
                        "enum": ["answering", "idle"],
                        "description": "The current state of the robot. 'answering' when speaking to the user, 'idle' when waiting for a question."
                    }
                },
                "required": ["state"]
            }
        )
    ]

# Persistence for the active persona within the server session
active_persona = "john"

@server.call_tool()
async def handle_call_tool(name: str, arguments: dict | None) -> list[TextContent]:
    """Handle tool execution requests from the MCP client."""
    global active_persona
    
    if name == "switch_avatar":
        persona = (arguments or {}).get("persona")
        if persona in ["john", "linda"]:
            active_persona = str(persona)
            
            # --- REMOTE CONTROL SIGNAL ---
            # We tell the Web App (server.js) to switch the UI instantly!
            try:
                data = json.dumps({"persona": active_persona, "silent": True}).encode('utf-8')
                req = urllib.request.Request("http://localhost:3000/switch-persona", data=data)
                req.add_header('Content-Type', 'application/json')
                with urllib.request.urlopen(req, timeout=1) as response:
                    pass # Signal received by website!
            except Exception as e:
                # Silently ignore if website is down
                pass

        return [TextContent(type="text", text=active_persona)]
    
    elif name == "get_status_emoji":
        state = (arguments or {}).get("state")
        # Strict mappings as requested
        if state == "answering":
            return [TextContent(type="text", text="🤖")]
        else:
            return [TextContent(type="text", text="🤗")]
            
    else:
        raise ValueError(f"Unknown tool: {name}")

async def main():
    """Run the server using the standard input/output transport."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options()
        )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

```

</details>

---

<details>
<summary>📂 <b>roboas/mcp_debugger_server.py</b> (Click to expand)</summary>

```python
import json
import os
import sys
import urllib.request
from mcp.server.fastmcp import FastMCP

# Initialize MCP Server for "Claude Desktop Monitoring"
mcp = FastMCP("Roboas Monitor")

LOG_FILE = os.path.join(os.path.dirname(__file__), "gpt_tool_log.json")

@mcp.tool()
def get_gpt_tool_logs(limit: int = 10, tool_filter: str = None):
    """
    Get the most recent tool calls made by the Shadow Brain (OpenAI/Claude Dual-Brain).
    Use this to see exactly what is happening in the Roboas Web App.
    
    Parameters:
    - limit: The maximum number of log entries to return (default is 10).
    - tool_filter: Optional filter to only return logs for a specific tool (e.g., 'locate_object').
    """
    api_error = None
    try:
        with urllib.request.urlopen("http://localhost:3000/tool-log", timeout=3) as r:
            data = json.loads(r.read().decode("utf-8"))
            if data.get("success"):
                logs = data.get("log", [])
                
                # Apply tool filter if present
                if tool_filter:
                    logs = [log for log in logs if tool_filter.lower() in log.get("toolName", "").lower()]
                
                recent = logs[-limit:]
                recent.reverse()
                
                mcp_status = data.get("mcpStatus", {})
                emoji_status = "Online" if mcp_status.get("emoji") else "Offline"
                vision_status = "Online" if mcp_status.get("vision") else "Offline"
                robot_status = "Online" if mcp_status.get("robot") else "Offline"
                
                result_obj = {
                    "source": "Active Node.js Server (HTTP API)",
                    "active_persona": data.get("currentPersona", "unknown"),
                    "mcp_servers_health": {
                        "emoji_server": emoji_status,
                        "vision_mcp": vision_status,
                        "robot_mcp": robot_status
                    },
                    "total_calls_logged": data.get("totalCalls", len(logs)),
                    "returned_calls_count": len(recent),
                    "logs": recent
                }
                return json.dumps(result_obj, indent=2)
    except Exception as e:
        api_error = str(e)

    if not os.path.exists(LOG_FILE):
        return json.dumps({
            "source": "None",
            "error": f"Failed to connect to Node.js server ({api_error}) and log file does not exist at {LOG_FILE}",
            "logs": []
        }, indent=2)

    try:
        with open(LOG_FILE, "r") as f:
            logs = json.load(f)
            
            # Apply tool filter if present
            if tool_filter:
                logs = [log for log in logs if tool_filter.lower() in log.get("toolName", "").lower()]
            
            recent = logs[-limit:]
            recent.reverse()
            
            return json.dumps({
                "source": "Disk Fallback (Local File)",
                "note": f"Could not reach Node.js server (Error: {api_error}). Showing logs from disk.",
                "total_calls_logged": len(logs),
                "returned_calls_count": len(recent),
                "logs": recent
            }, indent=2)
    except Exception as disk_err:
        return json.dumps({
            "source": "None",
            "error": f"Failed to connect to Node.js server ({api_error}) and failed to read log file ({str(disk_err)})",
            "logs": []
        }, indent=2)

@mcp.tool()
def get_roboas_status():
    """
    Return the current persona, state (idle/answering), and emoji of the Roboas system.
    Outputs status details based on recent logs.
    """
    if not os.path.exists(LOG_FILE):
        return "Status: 🌟 Ready | Waiting for first contact..."

    try:
        with open(LOG_FILE, "r") as f:
            logs = json.load(f)
            if not logs:
                return "Status: 🌟 Ready | No tools called yet."
            
            # Find the most recent switches/states
            current_persona = "unknown"
            current_state = "unknown"
            current_emoji = "unknown"

            # Scan from newest to oldest
            for log in reversed(logs):
                # Track persona
                if log.get("toolName") == "switch_avatar" and current_persona == "unknown":
                    current_persona = log.get("args", {}).get("persona", "unknown")
                
                # Track state and emoji
                if log.get("toolName") == "get_status_emoji" and current_state == "unknown":
                    current_state = log.get("args", {}).get("state", "unknown")
                    # Extract emoji from result like "updated to 😊"
                    result = log.get("result", "")
                    if "updated to " in result:
                        current_emoji = result.split("updated to ")[1]

            return f"Status: 🌟 Active | Persona: {current_persona} | State: {current_state} | Emoji: {current_emoji}"
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
def check_mcp_servers_health():
    """
    Query the Roboas backend to check the connection status of the Emoji, Vision, and Robot MCP servers.
    Use this to check if all local and remote servers are connected.
    """
    try:
        with urllib.request.urlopen("http://localhost:3000/tool-log", timeout=3) as r:
            data = json.loads(r.read().decode("utf-8"))
            if not data.get("success"):
                return "Backend reported unsuccessful query."
            
            mcp_status = data.get("mcpStatus", {})
            emoji = "✅ Online" if mcp_status.get("emoji") else "❌ Offline"
            vision = "✅ Online" if mcp_status.get("vision") else "❌ Offline"
            robot = "✅ Online" if mcp_status.get("robot") else "❌ Offline"
            
            return (
                f"=== Roboas Backend MCP Health Check ===\n"
                f"Emoji Server: {emoji}\n"
                f"Vision MCP:   {vision} (Port 8001)\n"
                f"Robot MCP:    {robot} (Port 8002)\n"
                f"Active Persona: {data.get('currentPersona', 'unknown')}\n"
                f"Total Logged Tool Calls: {data.get('totalCalls', 0)}"
            )
    except Exception as e:
        return f"❌ Failed to reach Roboas Backend (http://localhost:3000). Is the server running? Error: {str(e)}"

@mcp.tool()
def trigger_mock_tool(tool_name: str, args_json: str):
    """
    Simulate a tool execution on the Roboas backend. This tests coordinate mapping,
    logging, and server pipelines.
    args_json must be a JSON string, e.g. '{"target_name": "cube"}' or '{"object_name": "medicine", "x": -0.1009, "y": -0.1316, "z": 0.967}'
    """
    try:
        try:
            parsed_args = json.loads(args_json)
        except Exception:
            return "Error: args_json must be a valid JSON object string. Example: '{\"target_name\": \"cube\"}'"

        payload = json.dumps({"toolName": tool_name, "args": parsed_args}).encode("utf-8")
        req = urllib.request.Request(
            "http://localhost:3000/debug/trigger-mock-tool",
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            res_data = json.loads(r.read().decode("utf-8"))
            return json.dumps(res_data, indent=2)
    except Exception as e:
        return f"Error triggering mock tool: {str(e)}"

@mcp.tool()
def clear_gpt_tool_logs():
    """
    Clears the Roboas tool log file on disk and resets the in-memory array.
    """
    try:
        req = urllib.request.Request(
            "http://localhost:3000/debug/clear-logs",
            data=b"{}",
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=3) as r:
            res_data = json.loads(r.read().decode("utf-8"))
            return f"Success: {res_data.get('message', 'Logs cleared')}"
    except Exception as e:
        return f"Error clearing logs: {str(e)}"

if __name__ == "__main__":
    mcp.run()

```

</details>

---

<details>
<summary>📂 <b>roboas/wakeword_server.py</b> (Click to expand)</summary>

```python
import asyncio
import websockets
import json
import vosk
import queue
import threading
import sounddevice as sd
import sys
import os

# ─────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────
SAMPLE_RATE     = 16000
BLOCK_SIZE      = 4000      # ~250ms per block
CHANNELS        = 1
DTYPE           = 'int16'
MIC_DEVICE      = "hw:2,0"  # Default ALSA device on Raspberry Pi (adjustable in OS if needed)

print("[PY] Loading Vosk Offline Model...")
try:
    model = vosk.Model(lang="en-us")
    print("[PY] Vosk Model loaded successfully!")
except Exception as e:
    print(f"[PY] Error loading Vosk model: {e}")
    sys.exit(1)

# ─────────────────────────────────────────────────────────
# Shared state
# ─────────────────────────────────────────────────────────
active_persona    = "john"
is_muted          = False
connected_clients = set()
audio_queue       = queue.Queue()

# Threading events for mic control
mic_stop_event    = threading.Event()
mic_start_event   = threading.Event()

# The asyncio loop reference (set in main)
main_loop = None


# ─────────────────────────────────────────────────────────
# Audio callback (feeds raw PCM into the queue)
# ─────────────────────────────────────────────────────────
def audio_callback(indata, frames, time_info, status):
    if status:
        print(f"[PY] Sounddevice status: {status}", file=sys.stderr)
    if not mic_stop_event.is_set():
        audio_queue.put(bytes(indata))


# ─────────────────────────────────────────────────────────
# Broadcast events to all Flutter clients
# ─────────────────────────────────────────────────────────
async def broadcast_event(event_name, payload=None):
    if not connected_clients:
        return

    msg_dict = {"event": event_name}
    if payload:
        msg_dict.update(payload)

    event_msg = json.dumps(msg_dict)
    results = await asyncio.gather(
        *[client.send(event_msg) for client in connected_clients],
        return_exceptions=True
    )
    for r in results:
        if isinstance(r, Exception):
            print(f"[PY] Send error: {r}")
    print(f"[PY] Sent {event_name}")


def broadcast(event_name, payload=None):
    """Thread-safe wrapper to broadcast from non-async code."""
    if main_loop:
        asyncio.run_coroutine_threadsafe(
            broadcast_event(event_name, payload), main_loop
        )


# ─────────────────────────────────────────────────────────
# Vosk worker – runs in a background thread
# Opens and releases the mic stream dynamically
# ─────────────────────────────────────────────────────────
def vosk_worker(loop):
    global active_persona, main_loop
    main_loop = loop

    grammar = '["hey john", "john", "hey linda", "linda", "lind", "hey lind", "[unk]"]'

    while True:
        # Wait until we are told to open the mic (start_wakeword)
        mic_start_event.wait()
        mic_start_event.clear()
        mic_stop_event.clear()

        # Flush any stale audio from the queue
        while not audio_queue.empty():
            try:
                audio_queue.get_nowait()
            except queue.Empty:
                break

        print("[PY] Opening mic stream for wake-word listening...")
        rec = vosk.KaldiRecognizer(model, SAMPLE_RATE, grammar)
        broadcast("WAKEWORD_STARTED")

        try:
            # Open mic stream (released automatically when block is exited)
            with sd.RawInputStream(
                samplerate=SAMPLE_RATE,
                blocksize=BLOCK_SIZE,
                dtype=DTYPE,
                channels=CHANNELS,
                callback=audio_callback,
                device=MIC_DEVICE
            ):
                print("[PY] Microphone open, listening for wake word...")
                while not mic_stop_event.is_set():
                    try:
                        data = audio_queue.get(timeout=0.5)
                    except queue.Empty:
                        continue

                    if rec.AcceptWaveform(data):
                        res = json.loads(rec.Result())
                        text = res.get('text', '')
                    else:
                        res = json.loads(rec.PartialResult())
                        text = res.get('partial', '')

                    if text and text != "[unk]":
                        print(f"[PY] Heard speech: '{text}'")
                        current_persona_local = active_persona.lower()
                        is_match = (
                            (current_persona_local == "john" and "john" in text) or
                            (current_persona_local == "linda" and ("linda" in text or "lind" in text))
                        )

                        if is_match:
                            if is_muted:
                                print(f"[PY] Wake word detected but MUTED: {text}")
                                rec.Reset()
                            else:
                                print(f"[PY] WAKE WORD DETECTED: {text}")
                                # Broadcast event to Flutter
                                broadcast("WAKE_WORD_DETECTED", {
                                    "model": text,
                                    "persona": current_persona_local
                                })
                                # Stop mic immediately so Flutter browser mic can take over
                                mic_stop_event.set()
                                break
        except Exception as e:
            print(f"[PY] Microphone stream error: {e}", file=sys.stderr)

        print("[PY] Microphone released")
        broadcast("WAKEWORD_STOPPED")


# ─────────────────────────────────────────────────────────
# WebSocket client handler
# ─────────────────────────────────────────────────────────
async def handle_client(websocket):
    global active_persona, is_muted
    connected_clients.add(websocket)
    print(f"[PY] Client connected. Total: {len(connected_clients)}")

    # Send server ready + persona sync
    try:
        await websocket.send(json.dumps({"event": "AUDIO_SERVER_READY"}))
        await websocket.send(json.dumps({
            "event": "PERSONA_SYNC",
            "persona": active_persona
        }))
    except Exception as e:
        print(f"[PY] Failed to send initial sync: {e}")

    try:
        async for message in websocket:
            if isinstance(message, str):
                try:
                    data = json.loads(message)
                    action = data.get("action", "")

                    if action == "set_persona":
                        active_persona = data.get("persona", "john").lower()
                        print(f"[PY] Persona set to: {active_persona}")

                    elif action == "mute":
                        is_muted = True
                        print("[PY] Wake-word muted (TTS speaking)")

                    elif action == "unmute":
                        is_muted = False
                        print("[PY] Wake-word unmuted (TTS finished)")

                    elif action in ("start_wakeword", "resume_wakeword", "restart_wakeword"):
                        print(f"[PY] Received '{action}' – opening mic")
                        mic_stop_event.clear()
                        mic_start_event.set()

                    elif action in ("stop_wakeword", "pause_wakeword"):
                        print(f"[PY] Received '{action}' – releasing mic")
                        mic_stop_event.set()

                except Exception as e:
                    print(f"[PY] Error parsing message: {e}")

    except websockets.exceptions.ConnectionClosed:
        print("[PY] Client disconnected.")
    finally:
        connected_clients.discard(websocket)
        print(f"[PY] Client removed. Total: {len(connected_clients)}")


# ─────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────
async def main():
    loop = asyncio.get_running_loop()
    # Start the background Vosk listening thread
    threading.Thread(target=vosk_worker, args=(loop,), daemon=True).start()
    print("[PY] Wakeword Server starting on ws://0.0.0.0:8003")
    async with websockets.serve(handle_client, "0.0.0.0", 8003):
        print("[PY] Wakeword Server ready")
        await asyncio.Future()  # run forever

if __name__ == "__main__":
    asyncio.run(main())

```

</details>

---


## 3. Summary of Core Functionality
- **Dual Personas (John/Linda)**: Implements independent voice configurations (Node.js TTS engine with browser SpeechSynthesis fallback) and state-synchronized loopable video loops (Idle, Thinking, Talking) driven by a Flutter canvas-rendering state machine.
- **Hands-Free Wake Word Engine**: Local in-browser openWakeWord WebAssembly pipeline, streaming voice input threshold checks dynamically. Supports Python Vosk-based offline wake-word listener (`wakeword_server.py`) as an alternative/hybrid.
- **Microphone Protection & Cooldowns**: Fully protected against speech loopback triggers and robot motion motor noise feedback using temporary cooldown gates and mute signals.
- **Robot Arm MCP Ethernet Gateway**: Control layer for the Neura LARA 5 robotic arm (IP: 192.168.2.13) via Neura SDK, exposing status, joint motions, emergency stops, homing, and coordinate transformations.
- **VLM & YOLO Vision Control**: Laptop B runs Yolov8 and segmentation (`camera.py`) to feed RealSense D435i spatial depth coordinate mapping. Vision planning and obstacle detection reasoning are coordinated via Ollama running Qwen3-VL/Qwen2.5-VL (`vision_mcp.py`).
- **HUD & Diagnostics logging**: Debugging server logging and on-screen HUD overlays display status emojis, logs, and RMS audio values in real-time.
