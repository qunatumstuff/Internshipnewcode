# 🤖 HRI Robot Assistant — Setup & Operation Guide

A voice-controlled Human-Robot Interaction (HRI) system where users speak naturally to an AI persona ("John" or "Linda") to command a **Neura LARA robotic arm** to pick and place physical objects on a table. The system combines speech recognition, GPT-4 reasoning, YOLO computer vision, and direct robot arm control into a single real-time pipeline.

---

## System Architecture

```
Flutter Web App (UI + Wakeword)
        │
        ▼
  Node.js Server (server.js)
        │
   ┌────┴────┐
   ▼         ▼
Vision MCP  Robot MCP
(vision_mcp.py)  (robot_mcp.py)
   │              │
Camera.py    nogripperref.py
(RealSense)  (Neura LARA API)
```

---

## Hardware Requirements

| Component | Details |
|-----------|---------|
| **Robot Arm** | Neura Robotics LARA arm with Modbus gripper |
| **Depth Camera** | Intel RealSense D435i (or compatible) |
| **PC** | Windows 10/11, Ubuntu, or macOS host |
| **Network** | Robot arm and PC must be on the same Ethernet subnet |
| **Microphone** | Any USB or built-in microphone for wake word detection |

---

## Prerequisites

### 1. Python (≥ 3.10)
Download from [python.org](https://www.python.org/downloads/).

### 2. Node.js (≥ 18.0.0)
Download from [nodejs.org](https://nodejs.org/).

### 3. Flutter SDK (≥ 3.10)
Download from [flutter.dev](https://flutter.dev/docs/get-started/install).

### 4. Neura Robot SDK (`neurapy`)
Install from your Neura Robotics distribution package or internal repository:
```bash
pip install neurapy
```
> ⚠️ This is a proprietary SDK. Contact your Neura Robotics representative if you don't have access.

### 5. Intel RealSense SDK
Download and install the **librealsense** SDK from:
[https://github.com/IntelRealSense/librealsense/releases](https://github.com/IntelRealSense/librealsense/releases)

---

## Python Dependencies

Install all Python packages with:

```bash
pip install opencv-python numpy pyrealsense2 ultralytics mcp uvicorn keyboard
```

| Package | Purpose |
|---------|---------|
| `opencv-python` | Computer vision |
| `numpy` | Numerical computation |
| `pyrealsense2` | Intel RealSense camera SDK |
| `ultralytics` | YOLO object detection (YOLOv8/v11) |
| `mcp` | Model Context Protocol SDK |
| `uvicorn` | ASGI server for MCP endpoints |
| `neurapy` | Neura LARA robot SDK (proprietary) |
| `keyboard` | Keyboard shortcut support |

---

## Node.js Dependencies

```bash
cd AppV1-main/roboas
npm install
```

| Package | Purpose |
|---------|---------|
| `openai` | GPT-4 API for John/Linda AI reasoning |
| `@anthropic-ai/sdk` | Claude AI SDK (alternative agent) |
| `@modelcontextprotocol/sdk` | MCP client to communicate with Python servers |
| `express` | HTTP web server |
| `cors` | Cross-origin request support |
| `dotenv` | Load environment variables from `.env` |
| `multer` | File upload handling |
| `ws` | WebSocket support |

---

## Flutter Dependencies

```bash
cd AppV1-main
flutter pub get
```

| Package | Purpose |
|---------|---------|
| `http` | HTTP requests to Node.js backend |
| `record` | Microphone audio recording |
| `video_player` | John/Linda avatar animation videos |
| `google_fonts` | UI typography |
| `flutter_markdown` | Render markdown in chat responses |

---

## Environment Setup

Create `AppV1-main/roboas/.env`:

```env
OPENAI_API_KEY=sk-...
FIREBASE_PROJECT_ID=...
PORT=3000
```

### Python Server Ports
| Server | Default Port |
|--------|-------------|
| `vision_mcp.py` | `8081` |
| `robot_mcp.py` | `8082` |

---

## YOLO Model Files

Place in `AppV1-main/roboas/`:

| File | Purpose |
|------|---------|
| `best29.pt` | Primary OBB object orientation detection |
| `best28.pt` | Segmentation for precise centre-of-mass |
| `best (14).pt` | Depth-edge fallback detection |

---

## Startup Instructions

Open **4 separate terminals** and run each in order:

### Terminal 1 — Vision Server
```bash
cd AppV1-main/roboas
python vision_mcp.py
```
Wait for: `Uvicorn running on http://0.0.0.0:8081`

### Terminal 2 — Robot MCP Server
```bash
cd AppV1-main/roboas
python robot_mcp.py
```
Wait for: `Uvicorn running on http://0.0.0.0:8082`

### Terminal 3 — Node.js Orchestrator
```bash
cd AppV1-main/roboas
node server.js
```
Wait for: `Server running on port 3000`

### Terminal 4 — Flutter Web App
```bash
cd AppV1-main
flutter run -d chrome
```

---

## How to Operate

1. Open the Flutter web app in Chrome and grant microphone permission.
2. Say the wake word — **"John"**, **"Hey John"**, or **"Linda"**.
3. Wait for the listening indicator to appear.
4. Speak your command:
   - *"Pick up the red cube"*
   - *"Pick up the hat and put it in the box"*
   - *"Pick up the sponge, then the blue cube"*
   - *"Return home"*
   - *"Clear emergency stop"*
5. John will confirm and the robot will execute the task.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Robot throws `[3104]` error | Say *"Clear emergency stop"* or press/release the physical E-Stop |
| John stays silent after a task | Restart the Flutter app — a stale audio node may be blocking TTS |
| Wrong object picked | Check `object_catalogue.py` sticker mappings |
| `-32602 JSON-RPC` error | Restart `robot_mcp.py` |
| Camera not found | Plug in RealSense before starting `vision_mcp.py` |
| Wake word not triggering | Check browser mic permissions; look for `[OWW Scores]` in browser console |
| Server disconnects from Python | Heartbeat auto-reconnects within 5 seconds — retry your command |

---

## Key Files

| File | What it does |
|------|-------------|
| `AppV1-main/roboas/server.js` | Node.js orchestrator — voice commands, GPT, queue |
| `AppV1-main/roboas/vision_mcp.py` | Vision server — YOLO, obstacle checks, Qwen planning |
| `AppV1-main/roboas/robot_mcp.py` | Robot MCP server — exposes arm tools |
| `AppV1-main/roboas/nogripperref.py` | Low-level robot arm control (Neura LARA SDK) |
| `AppV1-main/roboas/camera.py` | RealSense camera thread |
| `AppV1-main/roboas/top_surface_refinement.py` | Accurate 3D grasp point calculator |
| `AppV1-main/roboas/record_and_build_xyz_correction.py` | Calibration tool |
| `AppV1-main/roboas/Public/index.html` | OpenWakeWord JS engine integration |
