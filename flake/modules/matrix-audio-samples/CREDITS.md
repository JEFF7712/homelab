# Recorded fireworks cues

These clips are edited real recordings, released under
[CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/).
Sources and licenses were checked on 2026-11-21.

| Cues | Recording | Creator |
| --- | --- | --- |
| Bursts, crackles, salutes | [25 CC0 bang / firework SFX](https://opengameart.org/content/25-cc0-bang-firework-sfx), `fw_01.ogg` through `fw_06.ogg` | [rubberduck](https://opengameart.org/users/rubberduck) |
| Fountain and willow fizz | [Sparkling Candle #1](https://bigsoundbank.com/sparkling-candle-1-s1278.html) | Joseph SARDIN, BigSoundBank.com |
| Launch ignition | [Slow wick 1](https://bigsoundbank.com/slow-wick-1--s1306.html) | Joseph SARDIN, BigSoundBank.com |

The OpenGameArt creator identifies fireworks recordings with some additional
processed effects. Only the `fw_` recordings are used here. The launch is a
real burning fuse, and the fizz is a close sparkler recording. Neither is
represented as a recorded aerial rocket whistle. BigSoundBank's
[license page](https://bigsoundbank.com/licenses.html) permits modification
and redistribution under CC0.

`manifest.json` records download URLs, original download SHA256 hashes,
individual source-file hashes, exact crop positions, gains, output frame
counts, and output hashes. The original downloads are not needed at runtime.
Each clip is mono, 48 kHz, signed 16-bit little-endian raw PCM. Edits remove
leading silence, subtract DC offset, apply 2 ms/30 ms edge fades, and balance
levels toward RMS 0.10 while capping peaks at 0.70. No synthesized layers or
pitch shifts are added. Clips rotate independently within each cue type.

To reproduce a clip, verify its source download hash, extract the named OGG
from the OpenGameArt archive when applicable, decode with FFmpeg to mono
48 kHz float PCM, and crop using the manifest's start and duration. Subtract
the crop's mean, multiply its first 96 and last 1440 samples by inclusive
linear ramps from 0 to 1 and 1 to 0 respectively, then apply the recorded
gain. Round the result times 32767 to signed little-endian 16-bit integers.
Decoder versions can change the exact output hash; the committed output
files and manifest are the authoritative installed library.

The service validates every file's size and SHA256 before starting. Files
are installed in the immutable Nix store, with no runtime downloads, decoder,
sample generation, or writable sample cache. The fifteen clips total
1,550,400 bytes of PCM.
