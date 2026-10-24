# Remote interactive coding sessions

The laptop pilot opts only `/home/rupan/homelab` into `agent-rupan` over SSH.
The server checkout is `/home/developer/workspace/homelab`. It was seeded from
Forgejo main, without copying the laptop's uncommitted or untracked files.

`scripts/agent/remote_session.py` is installed as `~/.local/bin/homelab-agent`
on the laptop and VM. Install the functions from `scripts/agent/fish/` into
the laptop's `~/.config/fish/functions/`. These `codex` and `claude` functions
invoke the helper; they do not replace the CLI binaries in `PATH`.

The laptop mapping is `~/.config/homelab-agent/repos.json`:

```json
{
  "repos": [
    {
      "local": "/home/rupan/homelab",
      "host": "agent-rupan",
      "remote": "/home/developer/workspace/homelab"
    }
  ]
}
```

Interactive coding sessions in that checkout or its subdirectories connect to
the VM. Other checkouts, including separately created laptop worktrees, stay
local unless explicitly mapped. Help, version, login, and noninteractive CLI
commands retain their native local behavior. `HOMELAB_AGENT_LOCAL=1` explicitly
bypasses routing. Existing fish abbreviations still supply their normal flags.

Before connecting, the launcher checks SSH access without prompting for a
password, with an eight-second total timeout (including the jump host). If the
server cannot be reached or SSH authentication fails, it prints an explicit
local fallback message and starts the native client in the current laptop
directory with the original arguments. Install and sign into both clients on
the laptop as well as the VM. Local sessions require the laptop to stay awake.

Fallback starts a separate local session. It does not copy server changes or
conversation history. Once a remote session is connected, losing SSH does not
start another client locally. Launch again to reconnect when the server is
available, or to start locally if it remains unavailable. A local fallback
session stays local when the server recovers; the next launch checks again.

Each client gets its own server worktree and tmux session:

- Codex: `/home/developer/workspace/homelab/.worktrees/interactive-codex`
- Claude: `/home/developer/workspace/homelab/.worktrees/interactive-claude`

The helper prints the actual server directory. Repeating the launch reconnects
to the existing session; startup arguments apply when a new session starts.
Disconnecting or detaching leaves the client running. Exiting the client ends
that tmux session, and the next launch starts a new client. Native client
resume options can select an earlier saved conversation.
The SSH terminal uses `xterm-256color`, which is supported by the headless guest.

Tmux sessions survive laptop sleep and SSH disconnects. They do not survive a
VM or host reboot. Repository files and provider sign-ins persist on the VM's
disk; after a reboot, launch again and use the client's resume command for a
saved conversation. This was verified after the 2026-10-24 host reboot.

Files are edited on the server. There is no automatic synchronization with the
laptop checkout. Remote editors must open the indicated server worktree.
The VM has separate provider sign-ins. Laptop credentials, SSH agents,
infrastructure access, and repository writer credentials are not forwarded.
The current pilot's Git HTTPS origin is behind Cloudflare Access; network fetch
and publication need a separately configured access path. Offline checkout
inspection, edits, commits, and credential-free tests remain available.
