import asyncio
import logging
import json
import urllib.request
import os

from mcp.server import Server
from mcp.types import Tool, TextContent
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.requests import Request
import uvicorn

import vision_core

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vision-mcp")

# ==========================================
# CONFIGURATION
# ==========================================
LAPTOP_A_IP   = os.environ.get("LAPTOP_A_IP",   "172.22.33.143")
QWEN_MODEL    = os.environ.get("QWEN_MODEL",    "qwen3-vl:2b")
ROBOT_MCP_URL = os.environ.get("ROBOT_MCP_URL", "http://localhost:8002/messages")
MAX_PLANNING_ITERATIONS = 5

# ==========================================
# OBJECT CATALOGUE
# Known physical heights — sent to Qwen so it can reason about whether
# a detected Z value makes sense for the object or implies stacking.
# ==========================================
OBJECT_CATALOGUE = {
    "black marker": {"size": "134 x 20.53 x 20.53 mm", "height_m": 0.02053},
    "blue marker":  {"size": "134 x 20.53 x 20.53 mm", "height_m": 0.02053},
    "cube":         {"size": "40 x 40 x 40 mm",         "height_m": 0.040},
    "green marker": {"size": "134 x 20.53 x 20.53 mm", "height_m": 0.02053},
    "medicine":     {"size": "115.72 x 51.17 x 18.95 mm", "height_m": 0.01895},
    "nut":          {"size": "34.6 x 30 x 17 mm",       "height_m": 0.017},
    "pipe":         {"size": "120 x 110 x 54.5 mm",     "height_m": 0.0545,
                     "notes": "Smart grasp via segmentation mask"},
    "sponge":       {"size": "112.58 x 80 x 15.4 mm",   "height_m": 0.01540,
                     "notes": "Angled grasp configuration"},
}

server = Server("vision-mcp-server")

# ==========================================
# QWEN COMMUNICATION
# ==========================================
async def ask_qwen_vision(prompt: str, base64_image: str) -> str:
    """Send image + prompt to Qwen3-VL on Laptop A via Ollama API."""
    logger.info(f"Connecting to Qwen at {LAPTOP_A_IP}...")
    payload = {
        "model":  QWEN_MODEL,
        "prompt": prompt,
        "stream": False,
        "images": [base64_image],
    }
    url = f"http://{LAPTOP_A_IP}:11434/api/generate"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    loop = asyncio.get_event_loop()
    def fetch():
        with urllib.request.urlopen(req, timeout=45) as response:
            return json.loads(response.read().decode("utf-8"))
    try:
        result = await loop.run_in_executor(None, fetch)
        return result.get("response", "No response from Qwen")
    except Exception as e:
        logger.error(f"Qwen network error: {e}")
        return f"Qwen Network Error: {e}"


async def qwen_plan_next_action(
    target: str,
    base64_image: str,
    detections: list[dict],
    action_history: list[str],
    user_context: str = "",
) -> dict:
    """
    Ask Qwen to look at the current scene and decide ONE next action.

    Called in a loop — after each robot action the scene is re-read and
    Qwen is asked again. Qwen sees:
      - The current live image
      - All YOLO detections with XYZ coordinates
      - Known physical heights from the catalogue — so it can judge whether
        a detected Z value is realistic or implies stacking/hiding
      - What the user originally said (user_context) — e.g. "cube is below sponge"
      - What actions have already been taken this cycle

    Hidden object logic:
      Qwen compares each detected object's Z height against its known physical
      height. If Z is significantly higher than expected, the object is likely
      resting on top of something. Qwen uses this to decide whether to relocate
      that object first even if it's not directly touching the target.

    Returns a dict:
        next_action   — "pick", "relocate", or "abort"
        obstacle_name — which object to relocate (if next_action is relocate)
        reasoning     — one sentence
    """
    target_info = OBJECT_CATALOGUE.get(target, {})
    item_details = f"Size: {target_info.get('size', 'unknown')}."
    if "notes" in target_info:
        item_details += f" Notes: {target_info['notes']}"

    # Build catalogue height reference so Qwen can do its own stacking analysis
    catalogue_heights = "\n".join(
        f"  - {name}: known height = {info['height_m']*1000:.1f}mm"
        for name, info in OBJECT_CATALOGUE.items()
    )

    # Build detection summary with smart elevation analysis.
    # For each elevated object, compute which catalogue objects could plausibly
    # be underneath based on the excess height, then rank them by fit.
    # This gives Qwen concrete candidates rather than just "something is below".
    detection_lines = []
    for d in detections:
        known_h  = OBJECT_CATALOGUE.get(d["object_name"], {}).get("height_m", None)
        elevation_note = ""
        if known_h is not None:
            expected_z = known_h / 2   # centre height if sitting flat on table
            actual_z   = d["z"]
            excess     = actual_z - expected_z   # how much higher than expected

            if excess > 0.020:   # more than 20mm above expected centre height
                # Work out which catalogue objects could plausibly fill the gap.
                # The hidden object's height should roughly match the excess*2
                # (since excess is from centre, the full stacking height ≈ excess*2).
                implied_height = excess * 2
                MATCH_TOLERANCE = 0.015   # 15mm tolerance either side

                plausible = []
                for obj_name, obj_info in OBJECT_CATALOGUE.items():
                    if obj_name == d["object_name"]:
                        continue   # can't be hiding itself
                    h = obj_info["height_m"]
                    if abs(h - implied_height) <= MATCH_TOLERANCE:
                        fit_mm = abs(h - implied_height) * 1000
                        plausible.append((fit_mm, obj_name, h))

                plausible.sort()   # best fit first

                if plausible:
                    candidates = ", ".join(
                        f"{name} ({h*1000:.0f}mm, off by {fit:.0f}mm)"
                        for fit, name, h in plausible
                    )
                    elevation_note = (
                        f"\n    *** ELEVATED: actual Z={actual_z*1000:.0f}mm, "
                        f"expected ~{expected_z*1000:.0f}mm, "
                        f"implied hidden object height ~{implied_height*1000:.0f}mm. "
                        f"Plausible hidden objects from catalogue: {candidates} ***"
                    )
                else:
                    elevation_note = (
                        f"\n    *** ELEVATED: actual Z={actual_z*1000:.0f}mm, "
                        f"expected ~{expected_z*1000:.0f}mm, "
                        f"implied hidden object height ~{implied_height*1000:.0f}mm. "
                        f"No catalogue object closely matches this height — "
                        f"elevation may be measurement noise or an unknown object. "
                        f"Do NOT relocate unless you have other evidence of blocking. ***"
                    )

        line = (
            f"  - {d['object_name']}: "
            f"X={d['x']:.3f}m Y={d['y']:.3f}m Z={d['z']:.3f}m "
            f"angle={d.get('angle_deg', 0):.1f}deg "
            f"confidence={d.get('confidence', 0):.2f}"
            f"{elevation_note}"
        )
        if d["object_name"] == "pipe" and d.get("grasp_label"):
            line += f" [grasp_end={d['grasp_label']}]"
        detection_lines.append(line)

    detection_summary = "\n".join(detection_lines) if detection_lines else "  (none detected)"
    catalogue_list    = ", ".join(OBJECT_CATALOGUE.keys())

    history_summary = (
        "No actions taken yet."
        if not action_history
        else "Actions already taken this cycle:\n" + "\n".join(
            f"  {i+1}. {a}" for i, a in enumerate(action_history)
        )
    )

    user_context_section = ""
    if user_context:
        user_context_section = (
            f"USER INSTRUCTION CONTEXT:\n"
            f"  The user said: \"{user_context}\"\n"
            f"  Use this as a hint when the scene is ambiguous. "
            f"  If the user named a specific object as being below another, "
            f"  check whether the elevation data supports this before acting.\n\n"
        )

    prompt = (
        f"You are the planning brain for a robotic arm on a flat table workspace.\n\n"

        f"GOAL: Pick up the '{target}' ({item_details})\n\n"

        f"{user_context_section}"

        f"KNOWN PHYSICAL HEIGHTS FROM CATALOGUE:\n"
        f"{catalogue_heights}\n\n"

        f"CURRENT SCENE — YOLO DETECTIONS (robot base frame):\n"
        f"{detection_summary}\n\n"

        f"HOW TO REASON ABOUT ELEVATED OBJECTS:\n"
        f"  Table surface = Z=0.0m. Each detected Z is the object's centre height.\n"
        f"  For objects marked *** ELEVATED ***, the pre-calculated implied hidden height\n"
        f"  and plausible catalogue matches are provided above.\n\n"
        f"  BEFORE deciding to relocate an elevated object, you MUST verify:\n"
        f"    1. At least one plausible catalogue object matches the implied hidden height.\n"
        f"    2. Either (a) the plausible hidden object IS the target, OR\n"
        f"               (b) the elevated object is physically blocking the gripper path.\n"
        f"  If neither condition is met — for example, the elevation note says 'no catalogue\n"
        f"  object matches' — do NOT relocate. The elevation is likely measurement noise.\n"
        f"  Blindly relocating objects without justification wastes cycles and risks knocking\n"
        f"  the actual target out of position.\n\n"

        f"HISTORY OF ACTIONS TAKEN SO FAR:\n"
        f"{history_summary}\n\n"

        f"RULES:\n"
        f"  1. Only command interactions with detected objects in the list above.\n"
        f"     Approved catalogue: {catalogue_list}.\n"
        f"  2. Only relocate if you can justify it via height matching or direct blocking.\n"
        f"  3. Do not re-relocate an object already moved (check history).\n"
        f"  4. If the target is clear, say 'pick'.\n"
        f"  5. If unresolvable, say 'abort' with a clear reason.\n\n"

        f"AVAILABLE NEXT ACTIONS (choose exactly one):\n"
        f"  - relocate: move one justified blocking object to a safe workspace spot.\n"
        f"  - pick: pick the target — path is clear.\n"
        f"  - abort: cannot safely reach the target. Explain why.\n\n"

        f"Look at the image and coordinates. Decide the single best next action.\n\n"

        f"Reply ONLY with valid JSON, no extra text:\n"
        f"{{\n"
        f'  "next_action": "pick" or "relocate" or "abort",\n'
        f'  "obstacle_name": "name of object to relocate, or null",\n'
        f'  "reasoning": "one sentence — if relocating, state which height match justifies it"\n'
        f"}}"
    )

    raw = await ask_qwen_vision(prompt, base64_image)
    logger.info(f"[Qwen] Raw: {raw[:300]}")

    try:
        clean = raw.strip()
        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
        plan = json.loads(clean.strip())
    except Exception as e:
        logger.warning(f"Qwen non-JSON ({e}). Defaulting to direct pick.")
        plan = {
            "next_action":   "pick",
            "obstacle_name": None,
            "reasoning":     f"Could not parse Qwen response. Raw: {raw[:100]}",
        }

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
    loop = asyncio.get_event_loop()
    def fetch():
        with urllib.request.urlopen(req, timeout=60) as response:
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
            name="locate_and_pick_object",
            description=(
                "Full autonomous pick pipeline with Qwen agentic planning. "
                "Reads the live camera, sends the scene image plus all YOLO detections "
                "with real-world coordinates and known catalogue heights to Qwen, then "
                "enters a planning loop where Qwen decides one action at a time: relocate "
                "a blocker, pick the target, or abort. After each robot action the scene "
                "is re-read and Qwen re-plans. Handles stacked objects (e.g. cube below "
                "sponge) by comparing detected Z heights against known object heights to "
                "identify elevation anomalies. User context (e.g. 'the cube is under the "
                "sponge') is forwarded to Qwen as an additional planning hint."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "target_name": {
                        "type":        "string",
                        "description": "The object to pick up.",
                        "enum":        list(OBJECT_CATALOGUE.keys()),
                    },
                    "user_context": {
                        "type":        "string",
                        "description": (
                            "Optional: anything the user said about the scene that might "
                            "help Qwen plan — e.g. 'the cube is below the sponge', "
                            "'medicine is hidden behind the pipe'. Forwarded directly "
                            "to Qwen as a planning hint alongside the camera image."
                        ),
                    },
                },
                "required": ["target_name"],
            },
        ),

        Tool(
            name="capture_and_detect",
            description=(
                "Read the current live camera frame and return all detected objects "
                "with robot-frame coordinates, angles, and Z heights."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict | None) -> list[TextContent]:
    args = arguments or {}

    # ── capture_and_detect ────────────────────────────────────────────────────
    if name == "capture_and_detect":
        detections = vision_core.get_latest_detections()
        frame_b64  = vision_core.get_current_frame_as_base64()
        return [TextContent(type="text", text=json.dumps({
            "status":     "ok",
            "detections": detections,
            "has_frame":  frame_b64 is not None,
        }))]

    # ── locate_and_pick_object — Qwen agentic loop ────────────────────────────
    if name == "locate_and_pick_object":
        target       = args.get("target_name", "").strip().lower()
        user_context = args.get("user_context", "").strip()

        if target not in OBJECT_CATALOGUE:
            return [TextContent(type="text", text=json.dumps({
                "status":        "REJECTED",
                "message":       f"'{target}' is not in the approved object catalogue.",
                "allowed_items": list(OBJECT_CATALOGUE.keys()),
            }))]

        action_history = []
        iteration      = 0

        while iteration < MAX_PLANNING_ITERATIONS:
            iteration += 1
            logger.info(f"[Qwen loop iteration {iteration}] Reading scene...")

            detections = vision_core.get_latest_detections()
            frame_b64  = vision_core.get_current_frame_as_base64()

            if not detections:
                return [TextContent(type="text", text=json.dumps({
                    "status":  "FAILED",
                    "message": "No objects detected in current scene.",
                    "history": action_history,
                }))]

            target_detections = [d for d in detections if d["object_name"] == target]
            if not target_detections:
                return [TextContent(type="text", text=json.dumps({
                    "status":           "FAILED",
                    "message":          f"'{target}' not found in current scene.",
                    "detected_objects": [d["object_name"] for d in detections],
                    "history":          action_history,
                }))]

            # Ask Qwen for next action
            if frame_b64 is None:
                plan = {"next_action": "pick", "obstacle_name": None,
                        "reasoning": "No frame available."}
            else:
                plan = await qwen_plan_next_action(
                    target, frame_b64, detections,
                    action_history, user_context,
                )

            logger.info(f"[Qwen iteration {iteration}] Plan: {plan}")
            next_action   = plan.get("next_action", "pick")
            obstacle_name = (plan.get("obstacle_name") or "").strip().lower()
            reasoning     = plan.get("reasoning", "")

            if next_action == "abort":
                return [TextContent(type="text", text=json.dumps({
                    "status":    "ABORTED",
                    "target":    target,
                    "reasoning": reasoning,
                    "history":   action_history,
                }))]

            elif next_action == "relocate":
                if not obstacle_name:
                    logger.warning("Qwen said relocate but gave no obstacle name. Attempting pick.")
                    next_action = "pick"
                else:
                    obstacle_dets = [d for d in detections if d["object_name"] == obstacle_name]
                    if not obstacle_dets:
                        logger.warning(
                            f"Qwen said relocate '{obstacle_name}' but YOLO didn't detect it. "
                            "Attempting pick."
                        )
                        next_action = "pick"
                    else:
                        obs = obstacle_dets[0]
                        logger.info(f"[iteration {iteration}] Relocating '{obstacle_name}' — {reasoning}")

                        reloc_result = await call_robot_tool("relocate_object", {
                            "obstacle_name":      obs["object_name"],
                            "obstacle_x":         obs["x"],
                            "obstacle_y":         obs["y"],
                            "obstacle_z":         obs["z"],
                            "obstacle_angle_deg": obs["angle_deg"],
                            "detections":         detections,
                        })

                        if reloc_result.get("error"):
                            return [TextContent(type="text", text=json.dumps({
                                "status":  "ERROR",
                                "stage":   f"relocate iteration {iteration}",
                                "message": reloc_result["error"],
                                "history": action_history,
                            }))]

                        action_history.append(f"Relocated '{obstacle_name}' — {reasoning}")
                        await asyncio.sleep(1.0)
                        continue

            if next_action == "pick":
                target_det = target_detections[0]
                logger.info(f"[iteration {iteration}] Picking '{target}' — {reasoning}")

                pick_args = {
                    "object_name": target_det["object_name"],
                    "x":           target_det["x"],
                    "y":           target_det["y"],
                    "z":           target_det["z"],
                    "angle_deg":   target_det["angle_deg"],
                    "detections":  detections,
                }

                if target_det["object_name"] == "pipe" and target_det.get("grasp_label"):
                    pick_args["grasp_label"] = target_det["grasp_label"]

                pick_result = await call_robot_tool("pick_and_place_object", pick_args)
                action_history.append(f"Picked '{target}' — {reasoning}")

                return [TextContent(type="text", text=json.dumps({
                    "status":      "SUCCESS" if not pick_result.get("error") else "ERROR",
                    "target":      target,
                    "pick_result": pick_result,
                    "history":     action_history,
                    "iterations":  iteration,
                }))]

        return [TextContent(type="text", text=json.dumps({
            "status":  "FAILED",
            "message": f"Reached maximum planning iterations ({MAX_PLANNING_ITERATIONS}) without picking '{target}'.",
            "history": action_history,
        }))]

    raise ValueError(f"Unknown tool: {name}")


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
])

if __name__ == "__main__":
    logger.info("Starting vision thread (D435i + YOLO)...")
    vision_core.start_vision_thread()
    logger.info("📷 Vision MCP Server listening on port 8001...")
    logger.info("Tools: locate_and_pick_object | capture_and_detect")
    uvicorn.run(app, host="0.0.0.0", port=8001)
