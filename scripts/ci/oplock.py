"""Per-resource locks for CI operations, held for the life of a connection.

The policy asks Woodpecker to run one operation per catalog group at a time,
but Woodpecker has no such setting and the CI host has two deploy slots: on
2026-11-10 three fleet deployments of one commit switched hosts at once.
Every deploy-tier operation therefore takes its group's lock here first
(scripts/ci/operation.py).

A lock is held exactly as long as the TCP connection that asked for it, so a
job that dies, as when the CI host restarts its own agent mid-deployment,
releases it at once instead of leaving a stale lock. Waiters are served in
arrival order. The service runs on the CI host (flake/modules/woodpecker.nix),
and the firewall accepts its port only from the deploy agent's Docker network,
so sandbox jobs running pull request code cannot hold a lock to stall
deployments.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import contextlib
import json
import logging
import re
import socket
import sys
import time
from collections.abc import Callable, Iterator

ADDRESS = "10.255.31.1"
PORT = 8031
GROUP = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

logger = logging.getLogger("oplock")


class LockError(RuntimeError):
    """The lock was refused or not granted in time."""


def keepalive(sock: socket.socket) -> None:
    # A peer that vanishes without closing (a host losing power) is noticed
    # within a minute instead of the kernel's two hours.
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    for option, value in (
        ("TCP_KEEPIDLE", 30),
        ("TCP_KEEPINTVL", 10),
        ("TCP_KEEPCNT", 3),
    ):
        if hasattr(socket, option):
            sock.setsockopt(socket.IPPROTO_TCP, getattr(socket, option), value)


class LockServer:
    def __init__(self) -> None:
        self.queues: dict[str, collections.deque[asyncio.Future[None]]] = {}
        self.holders: dict[str, str] = {}

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        sock = writer.get_extra_info("socket")
        if sock is not None:
            keepalive(sock)
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=10)
            request = json.loads(line)
        except (TimeoutError, ValueError):
            request = None
        if not isinstance(request, dict):
            writer.close()
            return
        if request.get("status"):
            writer.write((json.dumps({"holders": self.holders}) + "\n").encode())
            await writer.drain()
            writer.close()
            return
        group = str(request.get("group", ""))
        holder = str(request.get("holder", "unknown"))[:200]
        if not GROUP.match(group):
            writer.write(b'{"error": "invalid group"}\n')
            await writer.drain()
            writer.close()
            return
        await self.hold(group, holder, reader, writer)

    async def hold(
        self,
        group: str,
        holder: str,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        queue = self.queues.setdefault(group, collections.deque())
        turn: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        queue.append(turn)
        if len(queue) == 1:
            turn.set_result(None)
        # The client sends nothing after its request, so this completes when
        # it disconnects.
        gone = asyncio.ensure_future(reader.read())
        try:
            if not turn.done():
                waiting = {
                    "waiting": self.holders.get(group),
                    "position": len(queue) - 1,
                }
                writer.write((json.dumps(waiting) + "\n").encode())
                await writer.drain()
                done, _ = await asyncio.wait(
                    {turn, gone}, return_when=asyncio.FIRST_COMPLETED
                )
                if gone in done:
                    return
            self.holders[group] = holder
            logger.info("%s granted to %s", group, holder)
            writer.write(b'{"granted": true}\n')
            await writer.drain()
            await gone
        except (ConnectionError, OSError):
            pass
        finally:
            gone.cancel()
            self.release(group, turn, holder)
            writer.close()

    def release(self, group: str, turn: asyncio.Future[None], holder: str) -> None:
        queue = self.queues[group]
        if queue and queue[0] is turn:
            queue.popleft()
            if self.holders.get(group) == holder:
                del self.holders[group]
                logger.info("%s released by %s", group, holder)
            if queue:
                queue[0].set_result(None)
        else:
            queue.remove(turn)
        if not queue:
            del self.queues[group]


async def serve(host: str, port: int) -> None:
    server = await asyncio.start_server(LockServer().handle, host, port)
    logger.info("Serving operation locks on %s:%d", host, port)
    async with server:
        await server.serve_forever()


@contextlib.contextmanager
def held(
    group: str,
    holder: str,
    *,
    address: tuple[str, int] = (ADDRESS, PORT),
    wait_seconds: float = 3600.0,
    log: Callable[[str], None] = print,
) -> Iterator[None]:
    """Hold group's lock for the duration of the block.

    An unreachable lock service is reported and the block runs anyway: a
    broken service must not also block deploy-ci-host, the operation that
    deploys its fix. Fleet changes keep their own cluster Lease
    (scripts/deploy_fleet.py) either way.
    """
    try:
        sock = socket.create_connection(address, timeout=5)
    except OSError as error:
        log(
            f"WARNING: operation lock service {address[0]}:{address[1]} is "
            f"unreachable ({error}); running {group} without serialization"
        )
        yield
        return
    with sock:
        keepalive(sock)
        sock.sendall((json.dumps({"group": group, "holder": holder}) + "\n").encode())
        reply = sock.makefile("r", encoding="utf-8")
        deadline = time.monotonic() + wait_seconds
        while True:
            sock.settimeout(max(1.0, deadline - time.monotonic()))
            try:
                line = reply.readline()
            except TimeoutError as error:
                raise LockError(
                    f"waited {wait_seconds:.0f}s for the {group} lock"
                ) from error
            if not line:
                raise LockError(f"lock service closed the connection for {group}")
            message = json.loads(line)
            if message.get("granted"):
                break
            if "error" in message:
                raise LockError(f"lock service refused {group}: {message['error']}")
            log(
                f"Waiting for {group}: held by {message.get('waiting')}, "
                f"{message.get('position')} ahead"
            )
        sock.settimeout(None)
        log(f"Holding the {group} lock")
        yield


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Per-resource locks for CI operations."
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args(argv)
    asyncio.run(serve(args.host, args.port))
    return 0


if __name__ == "__main__":
    sys.exit(main())
