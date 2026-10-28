"""Classify agent shell commands that bypass the delivery and authorization contract.

A command is either forbidden outright (it defeats a protection no
authorization can grant) or requires the user's explicit authorization for a
live mutation outside `just ship`. The latter passes once the command carries
`HOMELAB_AUTHORIZED=1`, recording that it belongs to the user's authorized task.
`just ship --deploy` uses the validated catalog without a separate permission
prompt. The task request authorizes necessary deployment and recovery.

This is a lexical speed bump for honest mistakes, not a sandbox. It follows
wrappers, `bash -c`, `ssh`, `xargs`, and string literals in inline interpreter
code, but a determined agent can still assemble a command it cannot see. The
enforcement boundary is server side: the demoted `homelab-agent` identity, CI
operation policy, and branch protection.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Literal

AUTHORIZATION_MARKER = "HOMELAB_AUTHORIZED=1"

HEREDOC = re.compile(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?")
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
WRAPPERS = {"sudo", "env", "command", "exec", "nohup", "time"}

KUBECTL_MUTATIONS = {
    "annotate",
    "apply",
    "autoscale",
    "cordon",
    "create",
    "delete",
    "drain",
    "edit",
    "expose",
    "label",
    "patch",
    "replace",
    "run",
    "scale",
    "set",
    "taint",
    "uncordon",
}
KUBECTL_ROLLOUT_MUTATIONS = {"pause", "restart", "resume", "undo"}
SSH_VALUE_FLAGS = {"-b", "-c", "-E", "-F", "-i", "-J", "-l", "-o", "-p", "-S", "-W"}
SHELLS = {"bash", "sh", "zsh", "fish"}
GIT_VALUE_FLAGS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}
KUBECTL_VALUE_FLAGS = {
    "-n",
    "--namespace",
    "--context",
    "--kubeconfig",
    "--cluster",
    "--user",
    "-s",
    "--server",
    "--as",
}
FLUX_MUTATIONS = {"create", "delete", "resume", "suspend", "uninstall", "bootstrap"}
HELM_MUTATIONS = {"install", "uninstall", "upgrade", "rollback", "delete"}
TOFU_MUTATIONS = {"apply", "destroy", "import", "force-unlock"}
TOFU_STATE_MUTATIONS = {"mv", "push", "replace-provider", "rm"}
NIXOS_REBUILD_MUTATIONS = {"boot", "switch", "test"}
STDOUT_REDIRECT = re.compile(r"^(?:1|&)?>>?")
DISPLAY_FILTERS = {"bat", "cat", "head", "jq", "less", "more", "tail", "tee", "yq"}
STRING_LITERAL = re.compile(r"""(['"])((?:\\.|(?!\1).)*)\1""")
LIST_LITERAL = re.compile(r"\[([^\[\]]*)\]")
INTERPRETERS = {"python": "-c", "python3": "-c", "node": "-e", "perl": "-e"}
XARGS_VALUE_FLAGS = {"-a", "-d", "-E", "-I", "-L", "-n", "-P", "-s"}
SYSTEMCTL_MUTATIONS = {
    "disable",
    "enable",
    "isolate",
    "kill",
    "mask",
    "reload",
    "restart",
    "start",
    "stop",
    "try-restart",
}
POWER_COMMANDS = {"halt", "poweroff", "reboot", "shutdown"}
JUST_MUTATIONS = {
    "deploy-fleet",
    "ha-apply",
    "ha-revert",
    "registry-copy",
    "registry-promote",
    "registry-provision-publisher",
    "registry-reconcile-retention",
    "workspace-deprovision",
    "workspace-provision",
}


@dataclass(frozen=True)
class Verdict:
    kind: Literal["forbidden", "authorize"]
    reason: str


def _split(command: str) -> list[tuple[str, str]]:
    """Split on unquoted command separators, dropping heredoc bodies.

    Each part carries the separator that ended it: `|` for a pipe into the
    next part, `;` for any other boundary. `>&` and `&>` stay redirections.
    """
    parts: list[tuple[str, str]] = []
    current: list[str] = []
    quote = ""
    heredocs: list[str] = []
    lines = command.split("\n")
    for line_number, line in enumerate(lines):
        if heredocs:
            if line.strip() == heredocs[0]:
                heredocs.pop(0)
            continue
        index = 0
        while index < len(line):
            char = line[index]
            if quote:
                current.append(char)
                if char == "\\" and quote == '"' and index + 1 < len(line):
                    current.append(line[index + 1])
                    index += 1
                elif char == quote:
                    quote = ""
            elif char in "'\"":
                quote = char
                current.append(char)
            elif char == "\\" and index + 1 < len(line):
                current.extend(line[index : index + 2])
                index += 1
            elif char == "#" and (not current or current[-1].isspace()):
                break
            elif char == "&" and (
                line[index - 1 : index] == ">" or line[index + 1 : index + 2] == ">"
            ):
                current.append(char)
            elif char in ";&|":
                piped = char == "|" and line[index + 1 : index + 2] != "|"
                parts.append(("".join(current), "|" if piped else ";"))
                current = []
                following = line[index + 1 : index + 2]
                if following == char or (char == "|" and following == "&"):
                    index += 1
            else:
                match = HEREDOC.match(line, index)
                if match and line[index - 1 : index] != "<":
                    heredocs.append(match.group(1))
                    index = match.end()
                    continue
                current.append(char)
            index += 1
        if quote:
            current.append("\n")
        elif line_number < len(lines) - 1:
            parts.append(("".join(current), ";"))
            current = []
    parts.append(("".join(current), ";"))
    return parts


def _segments(command: str) -> list[tuple[list[str], list[str] | None]]:
    """Return each command's words and, when it pipes, the next command's."""
    split = []
    for part, separator in _split(command):
        try:
            words = shlex.split(part, comments=False, posix=True)
        except ValueError:
            words = part.split()
        split.append((words, separator))
    return [
        (
            words,
            _strip_prefix(split[index + 1][0])
            if separator == "|" and index + 1 < len(split)
            else None,
        )
        for index, (words, separator) in enumerate(split)
        if words
    ]


def _strip_prefix(words: list[str]) -> list[str]:
    index = 0
    while index < len(words):
        word = words[index]
        if ASSIGNMENT.match(word) or word in WRAPPERS:
            index += 1
        elif word == "timeout" and index + 1 < len(words):
            index += 2
        elif word == "nix" and words[index + 1 : index + 2] == ["develop"]:
            rest = words[index + 2 :]
            if "-c" in rest:
                index += 2 + rest.index("-c") + 1
            elif "--command" in rest:
                index += 2 + rest.index("--command") + 1
            else:
                return words[index:]
        else:
            break
    return words[index:]


def _positionals(words: list[str], value_flags: set[str]) -> list[str]:
    result = []
    skip = False
    for word in words:
        if skip:
            skip = False
        elif word in value_flags:
            skip = True
        elif not word.startswith("-"):
            result.append(word)
    return result


def _program(word: str) -> str:
    return word.rsplit("/", 1)[-1]


def _git(words: list[str]) -> Verdict | None:
    positionals = _positionals(words[1:], GIT_VALUE_FLAGS)
    subcommand = positionals[0] if positionals else ""
    if "--no-verify" in words or (subcommand == "commit" and "-n" in words):
        return Verdict("forbidden", "skipping git hooks (--no-verify) is never allowed")
    if subcommand != "push":
        return None
    arguments = words[words.index("push") + 1 :]
    refs = [word.lstrip("+") for word in arguments if not word.startswith("-")][1:]
    if any(re.search(r"(?:^|:)(?:refs/heads/)?main$", ref) for ref in refs):
        return Verdict(
            "forbidden",
            "pushing to main is never allowed; main changes only through a "
            "validated PR (just ship)",
        )
    forced = any(
        word in {"-f", "--force"} or word.startswith(("--force-with-lease", "+"))
        for word in arguments
    )
    if forced and not refs:
        return Verdict(
            "forbidden",
            "a force push without an explicit branch can rewrite main; name the branch",
        )
    return None


def _sops(words: list[str], piped_into: list[str] | None) -> Verdict | None:
    if not (
        {"-d", "--decrypt"} & set(words)
        or _positionals(words[1:], set())[:1] == ["decrypt"]
    ):
        return None
    if {"-i", "--in-place"} & set(words):
        return Verdict(
            "authorize", "an in-place SOPS decrypt leaves plaintext in the working tree"
        )
    if "--output" in words or any(STDOUT_REDIRECT.match(word) for word in words):
        return None
    if piped_into and _program(piped_into[0]) not in DISPLAY_FILTERS:
        return None
    return Verdict(
        "authorize",
        "a SOPS decrypt to the terminal or a display filter puts plaintext "
        "secrets in the agent transcript; redirect it to /dev/null or a file, "
        "or pipe it into a program that prints only what it derives",
    )


def _inline_code(code: str, remote: bool) -> Verdict | None:
    literals = [match.group(2) for match in STRING_LITERAL.finditer(code)]
    candidates = literals + [
        " ".join(match.group(2) for match in STRING_LITERAL.finditer(items.group(1)))
        for items in LIST_LITERAL.finditer(code)
    ]
    verdicts = [
        verdict
        for candidate in candidates
        if (verdict := _assess_all(candidate, remote)) is not None
    ]
    forbidden = next((item for item in verdicts if item.kind == "forbidden"), None)
    return forbidden or (verdicts[0] if verdicts else None)


def _classify(
    words: list[str], remote: bool = False, piped_into: list[str] | None = None
) -> Verdict | None:
    words = _strip_prefix(words)
    if not words:
        return None
    program = _program(words[0])
    if program == "xargs":
        inner = _positionals(words[1:], XARGS_VALUE_FLAGS)
        if not inner:
            return None
        return _classify(words[words.index(inner[0]) :], remote, piped_into)
    if program.startswith("$") and len(words) > 1:
        verbs = KUBECTL_MUTATIONS | FLUX_MUTATIONS | HELM_MUTATIONS | TOFU_MUTATIONS
        if verbs & set(words[1:]):
            return Verdict(
                "authorize",
                f"`{words[0]}` hides the program name, so the guard cannot "
                "tell whether this mutation is live; spell the command out",
            )
        return None
    if program in INTERPRETERS and INTERPRETERS[program] in words[1:-1]:
        code = words[words.index(INTERPRETERS[program]) + 1]
        return _inline_code(code, remote)
    if remote and program in POWER_COMMANDS:
        return Verdict("authorize", f"`{program}` on a fleet host takes it offline")
    if remote and program == "systemctl":
        positionals = _positionals(words[1:], {"-H", "--host", "-M", "--machine"})
        if positionals[:1] and positionals[0] in SYSTEMCTL_MUTATIONS | POWER_COMMANDS:
            return Verdict(
                "authorize",
                f"`systemctl {positionals[0]}` changes a live service on a fleet host",
            )
        return None
    if program == "git":
        return _git(words)
    if program == "kubectl":
        if any(word.startswith("--dry-run") for word in words):
            return None
        positionals = _positionals(words[1:], KUBECTL_VALUE_FLAGS)
        verb = positionals[0] if positionals else ""
        if verb in KUBECTL_MUTATIONS or (
            verb == "rollout"
            and positionals[1:2]
            and positionals[1] in KUBECTL_ROLLOUT_MUTATIONS
        ):
            return Verdict(
                "authorize",
                f"`kubectl {verb}` mutates the live cluster outside Flux",
            )
        return None
    if program == "flux":
        positionals = _positionals(words[1:], KUBECTL_VALUE_FLAGS)
        if positionals[:1] and positionals[0] in FLUX_MUTATIONS:
            return Verdict(
                "authorize", f"`flux {positionals[0]}` changes live Flux state"
            )
        return None
    if program == "helm":
        positionals = _positionals(words[1:], KUBECTL_VALUE_FLAGS)
        if (
            positionals[:1]
            and positionals[0] in HELM_MUTATIONS
            and "--dry-run" not in words
        ):
            return Verdict("authorize", f"`helm {positionals[0]}` bypasses Flux")
        return None
    if program in {"tofu", "terraform"}:
        positionals = [word for word in words[1:] if not word.startswith("-")]
        verb = positionals[0] if positionals else ""
        if verb in TOFU_MUTATIONS or (
            verb == "state"
            and positionals[1:2]
            and positionals[1] in TOFU_STATE_MUTATIONS
        ):
            action = " ".join(positionals[:2]) if verb == "state" else verb
            return Verdict(
                "authorize",
                f"`{program} {action}` changes infrastructure state; "
                "CI owns OpenTofu applies",
            )
        return None
    if program == "nixos-rebuild":
        if any(word in NIXOS_REBUILD_MUTATIONS for word in words[1:]) and (
            remote or "--target-host" in words
        ):
            return Verdict(
                "authorize", "`nixos-rebuild --target-host` activates a fleet host"
            )
        return None
    if program == "switch-to-configuration":
        return Verdict("authorize", "activating a NixOS generation on a fleet host")
    if program in SHELLS and "-c" in words[1:]:
        script = words[words.index("-c") + 1 : words.index("-c") + 2]
        return _assess_all(script[0], remote) if script else None
    if program == "sops":
        return _sops(words, piped_into)
    if program == "ssh":
        command = " ".join(_positionals(words[1:], SSH_VALUE_FLAGS)[1:])
        return _assess_all(command, remote=True) if command else None
    if program in {"python", "python3"} and words[1:3] == [
        "-m",
        "scripts.deploy_fleet",
    ]:
        return (
            None
            if "--dry-run" in words
            else Verdict("authorize", "`scripts.deploy_fleet` deploys the NixOS fleet")
        )
    if program == "just":
        positionals = [word for word in words[1:] if not word.startswith("-")]
        recipe = positionals[0] if positionals else ""
        if recipe in JUST_MUTATIONS and "--dry-run" not in words:
            return Verdict("authorize", f"`just {recipe}` mutates live infrastructure")
        return None
    return None


def _assess_all(command: str, remote: bool = False) -> Verdict | None:
    if re.search(r"""["']?force_merge["']?\s*[:=]\s*true""", command, re.IGNORECASE):
        return Verdict(
            "forbidden",
            "force_merge bypasses required status checks on Forgejo and is never allowed",
        )
    verdicts = [
        verdict
        for words, piped_into in _segments(command)
        if (verdict := _classify(words, remote, piped_into)) is not None
    ]
    forbidden = next((item for item in verdicts if item.kind == "forbidden"), None)
    return forbidden or (verdicts[0] if verdicts else None)


def assess(command: str) -> Verdict | None:
    verdict = _assess_all(command)
    if verdict and verdict.kind == "authorize" and AUTHORIZATION_MARKER in command:
        return None
    return verdict


def explain(verdict: Verdict) -> str:
    if verdict.kind == "forbidden":
        return f"Blocked by the homelab agent guard: {verdict.reason}."
    return (
        f"Blocked by the homelab agent guard: {verdict.reason}. "
        "AGENTS.md requires the user's explicit authorization for this. If the user "
        "has explicitly authorized this exact operation in this conversation, rerun "
        f"the command prefixed with `{AUTHORIZATION_MARKER}`. Otherwise deliver it "
        "through `just ship` or ask the user."
    )
