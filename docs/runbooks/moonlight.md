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

`homelab.moonlight.controllerMappings` applies exact-GUID overrides to both
`moonlight-session` and the supervised stream. homelab-05 overrides the Bluetooth
`Lic3 Pro Controller` (`0500b0124c6963332050726f20436f00`), whose generic HID
usages do not describe its physical Switch-style layout. A raw joystick capture
on 2026-11-20 measured Minus/Plus as buttons 8/9, stick clicks as 10/11,
L/R as 4/5, ZL/ZR as 6/7, and A/B/X/Y as 1/0/3/2. The mapping uses positional
SDL face buttons; Moonlight's existing `swapfacebuttons` preference controls
Nintendo-style labels. Other controller GUIDs retain their mappings.

Measure physical buttons before adding an override. `/dev/input/jsN` reports
raw joystick indices; do not infer the physical layout from Linux key names on
controllers with incorrect HID descriptors. A consistent mapping string alone
does not prove that it matches the physical controller. Moonlight saves supplied
overrides into its settings. To undo a mapping after it has launched, supply the
previous mapping for the same GUID or remove its saved entry in Moonlight;
removing the Nix override alone can leave the saved mapping active.

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
