"""Report Moonlight input settings and SDL bindings without pairing material."""

import configparser
import ctypes
import json
import os
import sys
from pathlib import Path


def input_settings(path: Path) -> dict[str, object]:
    settings = configparser.ConfigParser(interpolation=None)
    settings.read(path)
    general = settings["General"] if settings.has_section("General") else {}
    return {
        "gamepadmouse": general.get("gamepadmouse", "true"),
        "multicontroller": general.get("multicontroller", "true"),
        "swapfacebuttons": general.get("swapfacebuttons", "false"),
        "backgroundgamepad": general.get("backgroundgamepad", "false"),
        "mappings": dict(settings["gcmapping"])
        if settings.has_section("gcmapping")
        else {},
    }


def controllers(library: str) -> list[dict[str, object]]:
    sdl = ctypes.CDLL(library)
    sdl.SDL_GetError.restype = ctypes.c_char_p
    sdl.SDL_GameControllerNameForIndex.restype = ctypes.c_char_p
    sdl.SDL_GameControllerMappingForDeviceIndex.restype = ctypes.c_void_p
    sdl.SDL_GameControllerOpen.restype = ctypes.c_void_p
    sdl.SDL_GameControllerMapping.argtypes = [ctypes.c_void_p]
    sdl.SDL_GameControllerMapping.restype = ctypes.c_void_p
    sdl.SDL_GameControllerClose.argtypes = [ctypes.c_void_p]
    sdl.SDL_free.argtypes = [ctypes.c_void_p]
    if sdl.SDL_Init(0x2200) != 0:
        raise RuntimeError(sdl.SDL_GetError())
    result = []
    try:
        for index in range(sdl.SDL_NumJoysticks()):
            controller = sdl.SDL_GameControllerOpen(index)
            if not controller:
                continue
            try:
                mapping = sdl.SDL_GameControllerMapping(controller)
                try:
                    result.append(
                        {
                            "name": sdl.SDL_GameControllerNameForIndex(index).decode(),
                            "mapping": ctypes.string_at(mapping).decode()
                            if mapping
                            else None,
                        }
                    )
                finally:
                    sdl.SDL_free(mapping)
            finally:
                sdl.SDL_GameControllerClose(controller)
    finally:
        sdl.SDL_Quit()
    return result


def main(library: str) -> None:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    settings = input_settings(
        config_home / "Moonlight Game Streaming Project" / "Moonlight.conf"
    )
    print("Moonlight input settings: " + json.dumps(settings), flush=True)
    print("Moonlight SDL defaults: " + json.dumps(controllers(library)), flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
