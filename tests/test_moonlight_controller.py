import ctypes
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUID = "0500b0124c6963332050726f20436f00"


class Guid(ctypes.Structure):
    _fields_ = [("data", ctypes.c_uint8 * 16)]


@unittest.skipIf(os.getenv("SKIP_NIX_EVAL") == "1", "Nix evaluation disabled")
@unittest.skipUnless(shutil.which("nix"), "Nix required")
class ControllerMappingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        flake_uri = json.dumps(f"git+file://{ROOT}?dir=flake")
        expression = (
            f"let flake = builtins.getFlake {flake_uri};"
            + """
            host = flake.nixosConfigurations.homelab-05;
          in {
            mapping = host.config.systemd.services.moonlight-stream.environment.SDL_GAMECONTROLLERCONFIG or "";
            session = host.config.homelab.moonlight.sessionPackage.text;
            moonlight = "${host.pkgs.moonlight-qt}/bin/moonlight";
            swaymsg = "${host.pkgs.sway}/bin/swaymsg";
            sdl = "${host.pkgs.SDL2}/lib/libSDL2.so";
            otherMappings = flake.nixosConfigurations.homelab-04.config.homelab.moonlight.controllerMappings;
          }
        """
        )
        result = subprocess.run(
            ["nix", "eval", "--impure", "--json", "--expr", expression],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
        cls.config = json.loads(result.stdout)

    def test_only_the_measured_bluetooth_guid_is_overridden(self):
        self.assertEqual(self.config["mapping"].split(",", 1)[0], GUID)
        self.assertEqual(len(self.config["mapping"].splitlines()), 1)
        self.assertEqual(self.config["otherMappings"], [])

    def test_manual_session_receives_the_supervised_stream_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            moonlight = Path(directory) / "moonlight"
            moonlight.write_text(
                '#!/bin/sh\nprintf "%s" "${SDL_GAMECONTROLLERCONFIG-unset}"\n'
            )
            moonlight.chmod(0o755)
            swaymsg = Path(directory) / "swaymsg"
            swaymsg.write_text("#!/bin/sh\nexit 1\n")
            swaymsg.chmod(0o755)
            script = (
                self.config["session"]
                .replace(self.config["moonlight"], str(moonlight))
                .replace(self.config["swaymsg"], str(swaymsg))
            )
            env = {
                k: v
                for k, v in os.environ.items()
                if k not in ("SDL_GAMECONTROLLERCONFIG", "SWAYSOCK")
            }
            result = subprocess.run(
                ["bash", "-c", script],
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
        self.assertEqual(result.stdout, self.config["mapping"])

    def test_measured_buttons_through_the_real_sdl_controller_api(self):
        if not Path(self.config["sdl"]).is_file():
            self.skipTest("Evaluated SDL library is not realized")
        sdl = ctypes.CDLL(self.config["sdl"])
        sdl.SDL_GetError.restype = ctypes.c_char_p
        sdl.SDL_JoystickOpen.restype = ctypes.c_void_p
        sdl.SDL_JoystickClose.argtypes = [ctypes.c_void_p]
        sdl.SDL_JoystickGetDeviceGUID.restype = Guid
        sdl.SDL_JoystickSetVirtualButton.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_uint8,
        ]
        sdl.SDL_GameControllerAddMapping.argtypes = [ctypes.c_char_p]
        sdl.SDL_GameControllerOpen.restype = ctypes.c_void_p
        sdl.SDL_GameControllerClose.argtypes = [ctypes.c_void_p]
        sdl.SDL_GameControllerGetButton.argtypes = [ctypes.c_void_p, ctypes.c_int]
        sdl.SDL_GameControllerGetButton.restype = ctypes.c_uint8
        sdl.SDL_GameControllerGetAxis.argtypes = [ctypes.c_void_p, ctypes.c_int]
        sdl.SDL_GameControllerGetAxis.restype = ctypes.c_int16
        self.assertEqual(sdl.SDL_Init(0x2200), 0, sdl.SDL_GetError())
        index = sdl.SDL_JoystickAttachVirtual(1, 4, 16, 1)
        self.assertGreaterEqual(index, 0, sdl.SDL_GetError())
        joystick = sdl.SDL_JoystickOpen(index)
        controller = None
        try:
            guid = bytes(sdl.SDL_JoystickGetDeviceGUID(index).data).hex()
            old_mapping = (
                guid + ",Lic3 Pro Controller,back:b10,start:b11,"
                "leftstick:b13,rightstick:b14,leftshoulder:b6,rightshoulder:b7,"
                "lefttrigger:b8,righttrigger:b9,platform:Linux,"
            )
            self.assertGreaterEqual(
                sdl.SDL_GameControllerAddMapping(old_mapping.encode()), 0
            )
            controller = sdl.SDL_GameControllerOpen(index)
            self.assertTrue(controller, sdl.SDL_GetError())
            sdl.SDL_JoystickSetVirtualButton(joystick, 11, 1)
            sdl.SDL_JoystickUpdate()
            self.assertEqual(sdl.SDL_GameControllerGetButton(controller, 6), 1)
            sdl.SDL_JoystickSetVirtualButton(joystick, 11, 0)
            sdl.SDL_JoystickUpdate()

            mapping = guid + self.config["mapping"][32:]
            self.assertGreaterEqual(
                sdl.SDL_GameControllerAddMapping(mapping.encode()), 0
            )
            measured_buttons = {
                "Minus": (8, 4),
                "Plus": (9, 6),
                "L3": (10, 7),
                "R3": (11, 8),
                "L": (4, 9),
                "R": (5, 10),
                "A": (1, 1),
                "B": (0, 0),
                "X": (3, 3),
                "Y": (2, 2),
            }
            for name, (raw, logical) in measured_buttons.items():
                with self.subTest(button=name):
                    sdl.SDL_JoystickSetVirtualButton(joystick, raw, 1)
                    sdl.SDL_JoystickUpdate()
                    pressed = [
                        b
                        for b in range(15)
                        if sdl.SDL_GameControllerGetButton(controller, b)
                    ]
                    self.assertEqual(pressed, [logical])
                    sdl.SDL_JoystickSetVirtualButton(joystick, raw, 0)
                    sdl.SDL_JoystickUpdate()
            for raw, axis in ((6, 4), (7, 5)):
                with self.subTest(trigger=raw):
                    sdl.SDL_JoystickSetVirtualButton(joystick, raw, 1)
                    sdl.SDL_JoystickUpdate()
                    self.assertEqual(
                        sdl.SDL_GameControllerGetAxis(controller, axis), 32767
                    )
                    sdl.SDL_JoystickSetVirtualButton(joystick, raw, 0)
                    sdl.SDL_JoystickUpdate()
                    self.assertEqual(sdl.SDL_GameControllerGetAxis(controller, axis), 0)
        finally:
            if controller:
                sdl.SDL_GameControllerClose(controller)
            sdl.SDL_JoystickClose(joystick)
            sdl.SDL_JoystickDetachVirtual(index)
            sdl.SDL_Quit()
