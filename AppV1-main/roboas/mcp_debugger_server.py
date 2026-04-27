import json
import os
import sys
from mcp.server.fastmcp import FastMCP

# Initialize MCP Server for "Claude Desktop Monitoring"
mcp = FastMCP("Roboas Monitor")

LOG_FILE = os.path.join(os.path.dirname(__file__), "gpt_tool_log.json")

@mcp.tool()
def get_gpt_tool_logs(limit: int = 10):
    """
    Get the most recent tool calls made by the Shadow Brain (OpenAI/Claude Dual-Brain).
    Use this to see exactly what is happening in the Roboas Web App.
    """
    if not os.path.exists(LOG_FILE):
        return "No tool logs found yet. Start chatting in the app!"

    try:
        with open(LOG_FILE, "r") as f:
            logs = json.load(f)
            # Reverse to get most recent at top
            recent = logs[-limit:]
            recent.reverse()
            return json.dumps(recent, indent=2)
    except Exception as e:
        return f"Error reading logs: {str(e)}"

@mcp.tool()
def get_roboas_status():
    """
    Return the current persona, state (idle/answering), and emoji of the Roboas system.
    Outputs a premium status update with SGT timing and a happy signature.
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

if __name__ == "__main__":
    mcp.run()
