function claude --wraps claude
    if status is-interactive
        command homelab-agent claude $argv
    else
        command claude $argv
    end
end
