import asyncio
import websockets

async def test():
    try:
        async with websockets.connect('ws://127.0.0.1:8003') as ws:
            print('SUCCESS')
    except Exception as e:
        print(f'ERROR: {e}')

asyncio.run(test())
