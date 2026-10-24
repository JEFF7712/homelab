function codex --wraps codex
    if status is-interactive
        command homelab-agent codex $argv
    else
        command codex $argv
    end
end
