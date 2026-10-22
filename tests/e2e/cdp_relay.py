"""A tiny DevTools relay so Playwright can drive an Android WebView.

Playwright's connect_over_cdp() sends Browser.setDownloadBehavior while
attaching; Android WebView rejects it ("Browser context management is not
supported") and the attach fails. The relay answers that call itself and
passes every other message through unchanged.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time

import websockets

FAKE_OK = {"Browser.setDownloadBehavior"}


def start(upstream: str, port: int = 9223) -> str:
    async def handler(client):
        async with websockets.connect(upstream, max_size=None, ping_interval=None) as up:
            async def client_to_up():
                async for msg in client:
                    m = json.loads(msg)
                    if m.get("method") in FAKE_OK:
                        reply = {"id": m["id"], "result": {}}
                        if "sessionId" in m:
                            reply["sessionId"] = m["sessionId"]
                        await client.send(json.dumps(reply))
                        continue
                    await up.send(msg)

            async def up_to_client():
                async for msg in up:
                    await client.send(msg)

            done, pending = await asyncio.wait(
                [asyncio.ensure_future(client_to_up()), asyncio.ensure_future(up_to_client())],
                return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()

    def run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def main():
            async with websockets.serve(handler, "127.0.0.1", port, max_size=None, ping_interval=None):
                await asyncio.Future()

        loop.run_until_complete(main())

    threading.Thread(target=run, name="cdp-relay", daemon=True).start()
    time.sleep(0.5)
    return f"ws://127.0.0.1:{port}"
