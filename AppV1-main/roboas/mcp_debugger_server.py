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
                f"Robot MCP:    {robot} (IP: 192.168.2.13, Port 8080)\n"
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
