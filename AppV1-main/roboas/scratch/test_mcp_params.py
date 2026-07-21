import asyncio
from mcp.client.session import ClientSession
from mcp.client.sse import sse_client
import sys

async def main():
    async with sse_client("http://192.168.2.99:8001/sse") as (read1, write1):
        async with ClientSession(read1, write1) as vision_session:
            await vision_session.initialize()
            print("Connected to Vision MCP.")
            try:
                print("Testing locate_object with 'medicine'...")
                # We won't actually wait for a full scan, we just want to see if it immediately rejects the parameters.
                # Actually, locate_object will block if it accepts. Let's timeout quickly.
                result = await asyncio.wait_for(vision_session.call_tool("locate_object", {"target_name": "medicine"}), timeout=1.0)
                print("Vision returned early:", result)
            except asyncio.TimeoutError:
                print("Vision accepted parameters (timeout).")
            except Exception as e:
                print("Vision Error:", repr(e))

    async with sse_client("http://192.168.2.99:8002/sse") as (read2, write2):
        async with ClientSession(read2, write2) as robot_session:
            await robot_session.initialize()
            print("Connected to Robot MCP.")
            
            try:
                print("Testing return_home...")
                result = await asyncio.wait_for(robot_session.call_tool("return_home", {}), timeout=5.0)
                print("return_home result:", result)
            except asyncio.TimeoutError:
                print("return_home accepted parameters (timeout).")
            except Exception as e:
                print("return_home Error:", repr(e))

if __name__ == "__main__":
    asyncio.run(main())
