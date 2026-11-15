"""Read-only live acceptance for the satellite's source and imported players."""

from __future__ import annotations

import argparse
import base64
import json
import subprocess

# Execute beside HA so the existing Music Assistant integration token stays in
# its owning pod. Feed the HA token over stdin; neither token is logged or placed
# in process arguments. Imports of the MA client happen in HA's pinned image.
REMOTE_CHECK = r"""
import asyncio
import json
import sys
import time

from aiohttp import ClientSession
from music_assistant_client import MusicAssistantClient

async def main():
    token = sys.stdin.read().strip()
    data = json.load(open('/config/.storage/core.config_entries'))
    config = next(entry['data'] for entry in data['data']['entries']
                  if entry['domain'] == 'music_assistant')
    deadline = time.monotonic() + int(sys.argv[1])
    source = 'media_player.homelab_05_satellite_media_player'
    imported = source + '_2'
    async with ClientSession() as session:
        client = MusicAssistantClient(config['url'], session, token=config['token'])
        try:
            await client.connect()
            while True:
                player = await client.send_command('players/get', player_id=source)
                states = {}
                for entity in (source, imported):
                    async with session.get('http://127.0.0.1:8123/api/states/' + entity,
                            headers={'Authorization': 'Bearer ' + token}) as response:
                        response.raise_for_status()
                        states[entity] = (await response.json())['state']
                healthy = (player is not None and player['available']
                           and all(states.get(entity) not in (None, 'unknown', 'unavailable')
                                   for entity in (source, imported)))
                if healthy or time.monotonic() >= deadline:
                    print(json.dumps({'music_assistant_available': bool(player and player['available']),
                                      'source': states.get(source),
                                      'imported': states.get(imported)}))
                    return 0 if healthy else 1
                await asyncio.sleep(2)
        finally:
            await client.disconnect()

try:
    raise SystemExit(asyncio.run(main()))
except Exception as exc:
    print('Music player health read failed: ' + type(exc).__name__, file=sys.stderr)
    raise SystemExit(1)
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wait", type=int, default=0, help="Recovery wait in seconds")
    args = parser.parse_args()
    if not 0 <= args.wait <= 180:
        parser.error("--wait must be between 0 and 180 seconds")
    result = subprocess.run(
        [
            "kubectl",
            "get",
            "secret",
            "-n",
            "home-assistant",
            "home-assistant-token",
            "-o",
            "json",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    token = (
        base64.b64decode(json.loads(result.stdout)["data"]["token"]).decode().strip()
    )
    return subprocess.run(
        [
            "kubectl",
            "exec",
            "-i",
            "-n",
            "home-assistant",
            "deploy/home-assistant",
            "-c",
            "home-assistant",
            "--",
            "python",
            "-c",
            REMOTE_CHECK,
            str(args.wait),
        ],
        input=token,
        text=True,
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
