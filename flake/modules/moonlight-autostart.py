"""Supervise Moonlight while waiting for Sunshine to return."""

import socket
import subprocess
import sys
import time


def is_sunshine_reachable(host: str) -> bool:
    for port in (47984, 47989):
        try:
            with socket.create_connection((host, port), timeout=2):
                return True
        except OSError:
            pass
    return False


def supervise(moonlight: str, host: str, app: str) -> None:
    was_online = is_sunshine_reachable(host)
    if was_online:
        subprocess.run([moonlight, "stream", host, app], check=False)
        was_online = is_sunshine_reachable(host)
    while True:
        gui = subprocess.Popen([moonlight])
        while gui.poll() is None:
            time.sleep(2)
            online = is_sunshine_reachable(host)
            if online and not was_online:
                gui.terminate()
                try:
                    gui.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    gui.kill()
                    gui.wait()
                time.sleep(3)
                subprocess.run([moonlight, "stream", host, app], check=False)
                was_online = is_sunshine_reachable(host)
                break
            was_online = online
        else:
            return


if __name__ == "__main__":
    supervise(*sys.argv[1:4])
