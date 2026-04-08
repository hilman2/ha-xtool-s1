"""Interactive WebSocket probe for the xTool S1.

This script is the v2 reverse-engineering tool: it opens the S1's
WebSocket on port 8081, sends one or more raw M-codes you give it,
then prints every frame the device pushes back for a few seconds.
Use it to figure out which M-codes the S1 actually accepts as
*write* commands (set light brightness, pause/resume a job, ...) and
to spot frames the integration doesn't yet parse (real exhaust-fan
state, etc.).

Usage examples (run from inside WSL Ubuntu, with the project venv):

    # Just listen for 10 seconds, no commands sent
    python scripts/poke.py --host 192.168.4.42

    # Pull the full status snapshot
    python scripts/poke.py --host 192.168.4.42 --send M2003

    # Try setting light brightness to 50 % (HYPOTHESIS — please verify
    # against the laser before trusting it)
    python scripts/poke.py --host 192.168.4.42 --send "M13 A50 B50"

    # Send several commands in order, with 1 s between them
    python scripts/poke.py --host 192.168.4.42 \\
        --send M2003 --send "M13 A20 B20" --send "M13 A80 B80"

    # Listen during a real engraving job to spot push frames the
    # integration doesn't yet recognise (e.g. exhaust-fan state)
    python scripts/poke.py --host 192.168.4.42 --listen 60

The script is intentionally untested and lives outside the integration
package — it must never be loaded by Home Assistant. It is a tool for
the human in the loop.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import sys

import aiohttp


async def _listen(ws: aiohttp.ClientWebSocketResponse, duration: float) -> None:
    deadline = asyncio.get_running_loop().time() + duration
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return
        try:
            msg = await asyncio.wait_for(ws.receive(), timeout=remaining)
        except TimeoutError:
            return
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        if msg.type == aiohttp.WSMsgType.TEXT:
            print(f"[{ts}] TEXT  {msg.data!r}")
        elif msg.type == aiohttp.WSMsgType.BINARY:
            print(f"[{ts}] BIN   {msg.data!r}")
        elif msg.type in (
            aiohttp.WSMsgType.CLOSE,
            aiohttp.WSMsgType.CLOSED,
            aiohttp.WSMsgType.ERROR,
        ):
            print(f"[{ts}] CLOSE {msg.type.name}")
            return


async def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="S1 IP or hostname")
    parser.add_argument("--port", type=int, default=8081, help="WebSocket port")
    parser.add_argument(
        "--send",
        action="append",
        default=[],
        help="M-code line to send (repeatable; newline appended automatically)",
    )
    parser.add_argument(
        "--listen",
        type=float,
        default=10.0,
        help="Seconds to listen for incoming frames after the last send",
    )
    parser.add_argument(
        "--gap",
        type=float,
        default=1.0,
        help="Seconds to wait between successive --send commands",
    )
    args = parser.parse_args()

    url = f"ws://{args.host}:{args.port}/"
    print(f"connecting to {url}")
    async with aiohttp.ClientSession() as session:
        try:
            ws = await session.ws_connect(url, timeout=8.0, heartbeat=30.0)
        except (TimeoutError, aiohttp.ClientError, OSError) as err:
            print(f"connect failed: {err}", file=sys.stderr)
            return 1

        async with ws:
            for command in args.send:
                line = command.rstrip("\n") + "\n"
                ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                print(f"[{ts}] SEND  {line!r}")
                await ws.send_str(line)
                await asyncio.sleep(args.gap)

            print(f"listening for {args.listen}s ...")
            await _listen(ws, args.listen)

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
