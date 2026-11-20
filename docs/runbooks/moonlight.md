# Moonlight controller input

`flake/modules/moonlight-client.nix` owns the client package, controller
permissions and `moonlight-stream.service`. Its startup diagnostic runs as the
configured Moonlight user and reports only input preferences, saved controller
mapping overrides and SDL's default bindings. Diagnostic failures do not prevent
Moonlight from starting. Pairing keys, host certificates and other settings are
excluded.

Read `Moonlight input settings:` and `Moonlight SDL defaults:` in the service
journal, then compare them with the `Gamepad ... mapping:` lines emitted when
a stream opens. Saved mappings can override SDL's defaults. USB, Bluetooth,
HIDAPI and Linux evdev may use different GUIDs and button indices for the same
controller; do not apply one backend's mapping to another.

Inspect `/proc/bus/input/devices`, controller device ACLs and the user's groups
before changing permissions. An active session's `uaccess` ACL can already grant
hidraw access even when the owning group is `root`.

Validate changes with `just check-changed` and the focused Moonlight tests, then
deploy through `just ship`. After deployment, verify the service is active and
review fresh input diagnostics. Physical acceptance requires checking Start,
Select, shoulders, triggers and stick clicks on each controller during a stream.
Holding Start for more than 750 ms toggles mouse emulation when Start is released,
provided `gamepadmouse` is enabled. Look for `Mouse emulation active` and
`Mouse emulation deactivated` in the journal. Source checks and mapping strings
do not substitute for this physical check.
