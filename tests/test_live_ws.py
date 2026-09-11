import asyncio
import json
import websockets

async def main():
    uri = "ws://127.0.0.1:8000/ws/live"
    async with websockets.connect(uri) as ws:
        print("Connected to WebSocket live feed successfully.")
        
        # Read 3 frames
        for idx in range(3):
            raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
            msg = json.loads(raw)
            print(f"Frame {idx+1}: type={msg.get('type')}, symbol={msg.get('payload', {}).get('symbol') or 'portfolio'}")
        
        # Test ping-pong
        await ws.send("ping")
        resp = await asyncio.wait_for(ws.recv(), timeout=3.0)
        print("Ping response:", resp)

if __name__ == "__main__":
    asyncio.run(main())
