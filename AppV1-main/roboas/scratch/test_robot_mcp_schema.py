import asyncio
from mcp.client.session import ClientSession
from mcp.client.sse import sse_client
from mcp.types import CallToolRequest

async def main():
    async with sse_client("http://192.168.2.99:8002/sse") as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("Connected to robot MCP.")
            try:
                # Test the schema with dummy values
                args = {
                    "object_name": "red cube",
                    "x": 0.1,
                    "y": -0.2,
                    "z": -0.05,
                    "angle_deg": None,
                    "detections": [{"object_name": "red cube", "x": 0.1, "y": 0.1, "z": 0.1}]
                }
                result = await session.call_tool("pick_and_place_object", args)
                print(result)
            except Exception as e:
                print("Error:", repr(e))

asyncio.run(main())
