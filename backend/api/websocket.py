"""Live telemetry and camera over WebSocket (spec sections 53/54).

One socket carries both: per-tick telemetry every tick, and a JPEG frame on the
ticks where the camera actually produced one. Splitting them into two sockets
would mean re-synchronising them in the browser for no benefit.

The worker publishes into a Broadcaster from its own thread; this endpoint
drains it. A slow client is dropped frames, never allowed to stall the
simulation.
"""

from __future__ import annotations

import asyncio
import queue

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.websocket("/ws/experiments/{experiment_id}/telemetry")
async def telemetry_socket(websocket: WebSocket, experiment_id: str) -> None:
    await websocket.accept()
    manager = websocket.app.state.manager
    broadcaster = manager.streams.get(experiment_id)

    if broadcaster is None:
        await websocket.send_json({
            "type": "error",
            "detail": f"no live stream for {experiment_id!r}; "
                      "start the experiment first",
        })
        await websocket.close()
        return

    subscription = broadcaster.subscribe()
    # Whoever joins mid-episode should see the current state, not a blank panel.
    if broadcaster.latest is not None:
        await websocket.send_json(broadcaster.latest)

    try:
        while True:
            try:
                # to_thread keeps the blocking queue read off the event loop.
                message = await asyncio.wait_for(
                    asyncio.to_thread(subscription.get, True, 1.0), timeout=2.0
                )
            except (queue.Empty, asyncio.TimeoutError):
                # Nothing this second: prove the socket is alive and loop.
                await websocket.send_json({"type": "heartbeat"})
                continue

            await websocket.send_json(message)
            if message.get("type") == "end":
                break
    except WebSocketDisconnect:
        pass
    finally:
        broadcaster.unsubscribe(subscription)
