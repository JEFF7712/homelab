import json
import logging
import socket
import time

import voluptuous as vol

from ledfx.devices import NetworkedDevice
from ledfx.devices.__init__ import fps_validator
from ledfx.devices.utils.socket_singleton import SocketSingleton
from ledfx.utils import AVAILABLE_FPS

_LOGGER = logging.getLogger(__name__)

# LOCAL PATCH (homelab): upstream drives Govee bulbs through a reverse
# engineered Razer Chroma tunnel ("razer" commands carrying a base64 packet
# with header BB 00 FA B0 00). The H6004 does not implement that tunnel: it
# accepts the documented LAN API and silently discards every "razer" frame,
# while still answering devStatus, so upstream reports every device as online
# and streaming even though no color is ever applied.
#
# This fork emits "colorwc" instead. colorwc sets one flat color per device,
# so a multi-zone bulb collapses to a single averaged color; single-pixel
# devices (the H6004 bulbs here) are unaffected. See
# docs/gotchas/govee-lan-control.md for the protocol and the
# ignored-write evidence.
#
# Upstream file: ledfx/devices/govee.py at v2.1.9 (GPL-3.0), mounted over the
# installed copy rather than baked into an image so the upstream digest in
# gitops/music-assistant/ledfx.yaml stays immutable.


class Govee(NetworkedDevice):
    """
    Support for Govee devices with local API control
    """

    CONFIG_SCHEMA = vol.Schema(
        {
            vol.Required(
                "ip_address",
                description="Hostname or IP address of the device",
            ): str,
            vol.Required(
                "pixel_count",
                description="Number of segments (seen in app)",
                default=1,
            ): vol.All(int, vol.Range(min=1)),
            vol.Optional(
                "refresh_rate",
                description="Target rate that pixels are sent to the device",
                default=next(
                    (f for f in AVAILABLE_FPS if f >= 30),
                    list(AVAILABLE_FPS)[-1],
                ),
            ): fps_validator,
            vol.Optional(
                "ignore_status",
                description="Bypass check for device status check response on port 4003",
                default=False,
            ): bool,
            vol.Optional(
                "stretch_to_fit",
                description="Unused; only affected the razer packet header",
                default=False,
            ): bool,
        }
    )

    def __init__(self, ledfx, config):
        super().__init__(ledfx, config)
        self._device_type = "Govee"
        self.status = {}
        self.port = 4003  # Control Port
        self.multicast_group = "239.255.255.250"  # Multicast Address
        self.send_response_port = 4001  # Send Scanning
        self.recv_port = 4002  # Responses
        self.udp_server = None

    def send_udp(self, message, port=4003):
        data = json.dumps(message).encode("utf-8")
        try:
            self.udp_server.sendto(data, (self._config["ip_address"], port))
        except Exception as e:
            # we don't need this noise in sentry, and don't flood a standard log
            _LOGGER.info("govee:send_udp:Error sending UDP message %s", e)

    # Set Light Brightness
    def set_brightness(self, value):
        self.send_udp({"msg": {"cmd": "brightness", "data": {"value": value}}})

    def send_devstatus_enquiry(self):
        self.send_udp({"msg": {"cmd": "devStatus", "data": {}}})

    def deactivate(self):
        _LOGGER.info("Govee %s deactivate", self.name)
        if self.udp_server is not None:
            self.udp_server.close()
        super().deactivate()

    def activate(self):
        _LOGGER.info("Govee %s Activating LAN color control...", self.name)

        try:
            if self._config["ignore_status"]:
                self.udp_server = socket.socket(
                    socket.AF_INET, socket.SOCK_DGRAM
                )
            else:
                self.udp_server = SocketSingleton(recv_port=self.recv_port)
        except Exception as e:
            _LOGGER.error(
                "Error creating UDP socket, try ignore status device setting %s",
                e,
            )
            self.set_offline()
            return

        if not self._config["ignore_status"]:
            # enquiry to status is current used only to check if the device is responding adn set offline if not
            # the response information is of little use
            # example: {"msg":{"cmd":"devStatus","data":{"onOff":1,"brightness":100,
            # "color":{"r":255,"g":255,"b":255},"colorTemInKelvin":0}}}
            _LOGGER.info("Fetching govee %s device info...", self.name)
            status, active = self.get_device_status()
            _LOGGER.info("%s active: %s %s", self.name, active, status)
            if not active:
                self.set_offline()
                return
        else:
            _LOGGER.info("Ignoring Govee status check for %s", self.name)

        # the ordering and delay in this implementation is derived through trial and error only
        # incorrect order can lead to flickering of devices tested if wake from sleep
        # we have not other information as to best practice here
        delay = 0.1
        time.sleep(delay)
        self.set_brightness(100)
        super().activate()

    def flush(self, data):
        """Apply one averaged color to the device via the documented LAN API.

        colorwc addresses the whole device rather than a pixel range, so a
        multi-zone bulb renders as a single flat color. Single-pixel devices
        are exact.
        """
        rgb = data.reshape(-1, 3).mean(axis=0).round().astype(int)
        self.send_udp(
            {
                "msg": {
                    "cmd": "colorwc",
                    "data": {
                        "color": {"r": int(rgb[0]), "g": int(rgb[1]), "b": int(rgb[2])},
                        "colorTemInKelvin": 0,
                    },
                }
            }
        )

    # Get Device Status
    def get_device_status(self):
        self.send_devstatus_enquiry()
        self.udp_server.settimeout(1.0)
        try:
            # Receive Response from the device
            response, addr = self.udp_server.recvfrom(1024)
            if self._config["ip_address"] == addr[0]:
                return f"{response.decode('utf-8')}", True
            else:
                return (
                    f"Discarding packet from unknown {addr[0]} on port {addr[1]}",
                    False,
                )

        except socket.timeout:
            return "No response received within the timeout period.", False

    async def async_initialize(self):
        await super().async_initialize()

        config = {
            "name": self.config["name"],
            "pixel_count": self.config["pixel_count"],
            "refresh_rate": self.config["refresh_rate"],
        }

        self.update_config(config)
