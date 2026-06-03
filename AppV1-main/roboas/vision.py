import asyncio
import logging
import base64
import json
import urllib.request
from typing import Any
import camera
import mcp

from mcp.server import Server
from mcp.types import Tool, TextContent
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.requests import Request
import uvicorn
import numpy as np


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vision-mcp")

import os
import math

# ==========================================
# CONFIGURATION
# ==========================================
LAPTOP_A_IP  = os.environ.get("LAPTOP_A_IP",  "192.168.2.10")
QWEN_MODEL   = os.environ.get("QWEN_MODEL",   "qwen3-vl:2b")

# Robot MCP server address
ROBOT_MCP_URL = os.environ.get("ROBOT_MCP_URL", "http://localhost:8002/messages")


# Image path used for current detection cycle
CAPTURE_PATH = "capture.jpg"

server = Server("vision-mcp-server")

OBJECT_CATALOGUE = {
    "black marker": {"size": "134 x 20.53 x 20.53 mm"},
    "blue marker":  {"size": "134 x 20.53 x 20.53 mm"},
    "cube":         {"size": "40 x 40 x 40 mm"},
    "green marker": {"size": "134 x 20.53 x 20.53 mm"},
    "medicine":     {"size": "115.72 x 51.17 x 18.95 mm"},
    "nut":          {"size": "34.6 x 30 x 17 mm"},
    "pipe":         {"size": "120 x 110 x 54.5 mm",  "notes": "Includes custom grasp region and grip offsets"},
    "sponge":       {"size": "112.58 x 80 x 15.4 mm", "notes": "Includes angled grasp configuration"},
}



@mcp.tool()
def analyse_surroundings(prompt: str) -> str:
    """Send image + prompt to Qwen3-VL on Laptop A via Ollama API."""
    import urllib.request
    import json
    
    payload = {
        "model": QWEN_MODEL,
        "prompt": prompt,
        "stream": False,
        "images": [camera.get_camera_snapshot()],
    }
    url = f"http://{LAPTOP_A_IP}:11434/api/generate"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            result = json.loads(response.read().decode("utf-8"))
            return result.get("response", "No response from Qwen")
    except Exception as e:
        print(f"Qwen network error: {e}")
        return f"Qwen Network Error: {e}"
    
    return prompt











