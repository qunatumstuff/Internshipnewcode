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
