# Remote interactive coding sessions

The laptop opts `/home/rupan/homelab` and all projects below
`/home/rupan/projects` into `agent-rupan` over SSH.
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
  ],
  "roots": [
    {
      "local": "/home/rupan/projects",
      "host": "agent-rupan",
      "remote": "/home/developer/workspace/projects"
    }
  ]
}
```

Interactive coding sessions in those locations connect to the VM. Root mappings
also cover newly created projects, nested repositories, directories without Git,
and laptop worktrees inside the configured root. The projects directory itself
stays local. Other locations stay local unless explicitly mapped.
Help, version, login, and noninteractive CLI
commands retain their native local behavior. `HOMELAB_AGENT_LOCAL=1` explicitly
bypasses routing. Existing fish abbreviations still supply their normal flags.

A root-mapped project is initialized on its first remote launch. The server
checkout is `REMOTE_ROOT/PROJECT_PATH/.checkout`; the extra directory keeps a
container folder and its nested repositories independent. For example,
`projects/sites` and `projects/sites/rupan.dev` have separate server checkouts.
The first copy includes the current Git HEAD, its reachable history, tracked
working changes (including staged changes and deletions), and untracked source
that Git does not ignore. Staging state stays local; imported edits remain
uncommitted on the server. Git worktrees are exported independently, so their
laptop `.git` pointers are not copied.

For a directory without Git, the server creates an empty Git baseline and
imports source files as uncommitted work. The laptop directory is unchanged.
Generated dependency/build directories, untracked `.env` files, common private
key/auth files, and symlinks outside the source directory are omitted. Tracked
files and history retain ordinary Git clone semantics. Recreate dependencies
and sign into additional services on the VM as needed. Origin URLs retain the
repository address, with HTTP credentials and query strings stripped; laptop
Git configuration, hooks, SSH agents and credential stores are not copied.

Each client receives the initial uncommitted source in its separate server
worktree. Initial copies are created atomically and an existing server checkout
is never refreshed from the laptop. Later laptop edits are not sent on launch.
Git fetch/push authentication for additional repositories must be configured
separately; the existing homelab deploy key grants only homelab access.

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

Every Codex launch creates a fresh process, tmux session, branch, and server
worktree at `.worktrees/interactive-codex-<unique-id>`. Multiple terminals can
run agents independently. Existing worktrees and uncommitted files are retained.
New worktrees start from the server checkout's HEAD, including its initial
source snapshot for seeded projects; they do not inherit other agents' edits.
Use Codex `/resume` for conversation history. Resuming a conversation does not
switch to its previous worktree or restore its uncommitted files.

Claude retains its fixed `.worktrees/interactive-claude` worktree and reconnects
to its existing tmux session. Startup arguments apply only to new sessions.
The helper prints the actual server directory and tmux session name.
Disconnecting or detaching leaves clients running. To reconnect to a running
Codex process after an SSH disconnect, SSH into the VM, run `tmux list-sessions`,
and use `tmux attach -t <session-name>`. Running `codex` starts a new process.
Exiting a client ends its tmux session but preserves its worktree.
The SSH terminal uses `xterm-256color`, which is supported by the headless guest.

Tmux sessions survive laptop sleep and SSH disconnects. They do not survive a
VM or host reboot. Repository files and provider sign-ins persist on the VM's
disk; after a reboot, launch again and use the client's resume command for a
saved conversation. This was verified after the 2026-10-24 host reboot.

Files are edited on the server. There is no automatic synchronization with the
laptop checkout. Remote editors must open the indicated server worktree.
The VM has separate provider sign-ins. Laptop credentials, SSH agents,
infrastructure access, and repository writer credentials are not forwarded.
Git uses the internal Forgejo SSH endpoint at `10.0.30.20:2222` with a fresh
VM-generated `~/.ssh/id_ed25519_homelab` key registered only for this repository.
The public NAS Git host key is pinned from the laptop's existing trusted entry.
The repository's SSH command selects that identity explicitly and disables agent
use. Protected branches still require the normal PR and CI workflow; this key
does not provide API tokens, cluster access, or deployment credentials.

The host-owned network policy grants only the Rupan guest access to NAS TCP
2222 (Git) and 8080 (signed Nix cache). Other private addresses and ports remain
denied. The VM's NixOS configuration installs the cache signing key, keeps
signature verification enabled and `trusted-users` limited to root, and accepts
flake settings so `direnv allow` does not wait for an interactive Nix prompt.
The first development-shell load can download dependencies; later loads use
nix-direnv's cached profile.
