import asyncio
import json

import httpx

async def main():
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "http://monitor:9000/metrics",
            headers={"Authorization": "Bearer monitor-token"},
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        print(json.dumps(data, indent=2))

if __name__ == "__main__":
    asyncio.run(main())
