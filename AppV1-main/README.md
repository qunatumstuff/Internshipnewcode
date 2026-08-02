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
pip install -r requirements.txt
```

Or manually:

```bash
# Core vision
pip install opencv-python          # Computer vision
pip install numpy                  # Numerical computation
pip install pyrealsense2           # Intel RealSense camera SDK

# AI / ML
pip install ultralytics            # YOLO object detection (YOLOv8/v11)

# MCP Server framework
pip install mcp                    # Model Context Protocol SDK

# Web server
pip install uvicorn                # ASGI server for MCP endpoints

# Robot control (proprietary)
pip install neurapy                # Neura LARA robot SDK

# Optional utilities
pip install keyboard               # Keyboard shortcut support
```

**Full pip install line:**
```bash
pip install opencv-python numpy pyrealsense2 ultralytics mcp uvicorn keyboard
```

---

## Node.js Dependencies

Navigate to the `roboas/` directory and run:

```bash
cd roboas
npm install
```

This installs all packages from `package.json`:

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
| `pdf-parse` | PDF reading for document Q&A |

---

## Flutter Dependencies

Navigate to the project root and run:

```bash
flutter pub get
```

Key packages from `pubspec.yaml`:

| Package | Purpose |
|---------|---------|
| `http` | HTTP requests to Node.js backend |
| `record` | Microphone audio recording |
| `video_player` | John/Linda avatar animation videos |
| `google_fonts` | UI typography |
| `flutter_markdown` | Render markdown in chat responses |
| `web_socket_channel` | WebSocket communications |

---

## Environment Setup

### Node.js `.env` file
Create `roboas/.env` with the following:

```env
OPENAI_API_KEY=sk-...           # Your OpenAI API key
FIREBASE_PROJECT_ID=...         # Firebase project (for auth)
PORT=3000                       # Server port (default 3000)
```

### Python Server Ports
| Server | Default Port |
|--------|-------------|
| `vision_mcp.py` | `8081` |
| `robot_mcp.py` | `8082` |

---

## YOLO Model Files

Place model files in the `roboas/` directory:

| File | Model | Purpose |
|------|-------|---------|
| `best29.pt` | YOLOv11 OBB | Primary object orientation detection |
| `best28.pt` | YOLOv11 Segment | Object segmentation for precise centre-of-mass |
| `best (14).pt` | Discontinuity | Depth-edge fallback detection |

> Download models using `download_models.py` or obtain from your project supervisor.

---

## Startup Instructions

Open **4 separate terminals** and run each in order:

### Terminal 1 — Vision Server
```bash
cd roboas
python vision_mcp.py
```
Wait for: `Uvicorn running on http://0.0.0.0:8081`

### Terminal 2 — Robot MCP Server
```bash
cd roboas
python robot_mcp.py
```
Wait for: `Uvicorn running on http://0.0.0.0:8082`

### Terminal 3 — Node.js Orchestrator
```bash
cd roboas
node server.js
# or for auto-reload during development:
npm run dev
```
Wait for: `Server running on port 3000`

### Terminal 4 — Flutter Web App
```bash
# From the project root
flutter run -d chrome
```
Or to serve the pre-built web app:
```bash
flutter build web
cd build/web
python -m http.server 8080
```
Then open: `http://localhost:8080`

---

## How to Operate

1. **Open the Flutter web app** in Chrome.
2. **Grant microphone permission** when prompted.
3. **Say the wake word** — either **"John"** or **"Hey John"** (or **"Linda"** for the second persona).
4. **Wait for the listening indicator** to appear on screen.
5. **Speak your command**, for example:
   - *"Pick up the red cube"*
   - *"Pick up the hat and put it in the box"*
   - *"Pick up the sponge, then the blue cube"*
   - *"Return home"*
   - *"Clear emergency stop"*
6. **John will confirm** your command verbally and the robot will execute the task.
7. If the robot encounters an issue, **John will say so out loud** — you do not need to watch the server logs.

---

## Calibration

If the camera is moved or the robot's coordinate accuracy drops, run the calibration tool:

```bash
cd roboas
python record_and_build_xyz_correction.py
```

Follow the on-screen instructions to tap the robot to calibration points. The script will automatically compute a new correction matrix.

---

## Key Files Reference

| File | What it does |
|------|-------------|
| `server.js` | Main Node.js orchestrator — handles voice commands, GPT, queues |
| `vision_mcp.py` | Python vision server — YOLO detection, Qwen planning, obstacle checks |
| `robot_mcp.py` | Python robot server — exposes robot tools via MCP protocol |
| `nogripperref.py` | Low-level robot arm control using the Neura LARA SDK |
| `camera.py` | RealSense camera thread — depth frames, RGB, coordinate transforms |
| `top_surface_refinement.py` | 3D geometric centre-of-mass calculator for accurate grasping |
| `object_catalogue.py` | Master list of all graspable objects and their sticker mappings |
| `record_and_build_xyz_correction.py` | Camera-to-robot coordinate calibration tool |
| `Public/index.html` | Flutter web shell + OpenWakeWord JS engine integration |
| `Public/WakeWordEngine.js` | Wake word detection using ONNX models in the browser |
| `Public/models/` | ONNX wake word models for John, Linda, Hey John, Abort Mission |

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Robot throws `[3104]` error | The arm is in a fault state. Say *"Clear emergency stop"* or press the physical E-Stop button and release it |
| John stays silent after a task | Restart the Flutter app — a stale audio node may be blocking TTS |
| Wrong object picked | Check `object_catalogue.py` sticker mappings are correct for your physical setup |
| `-32602 JSON-RPC` error in logs | `robot_mcp.py` needs a restart; the schema handshake failed |
| Camera not found | Ensure the RealSense camera is plugged in before starting `vision_mcp.py` |
| Wake word not triggering | Check browser microphone permissions; look for `[OWW Scores]` in browser console |
| Node server disconnects from Python | The heartbeat auto-reconnects within 5 seconds — wait and retry your command |

---

## Architecture Notes

- The system uses **MCP (Model Context Protocol)** for structured communication between Node.js and Python servers.
- Wake word detection runs entirely **in the browser** using ONNX Runtime WebAssembly — no data is sent to external servers for wakeword processing.
- Messages are always **plain text only** — no HTML is rendered in the chat.
- The vision pipeline runs **deterministic safety checks first**, then calls the AI vision model (Qwen) only when needed.
