from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

EXIT_STATUS = re.compile(
    r"(?im)^(?:exit status|exit code:?|process exited with code:?)\s+(\d+)\b"
)


def normalized_payload(payload: dict[str, Any], event: str) -> dict[str, Any]:
    normalized: dict[str, Any] = {
        "session_id": payload.get("conversationId", "unknown"),
        "hook_event_name": event,
    }
    if event == "PostToolUse":
        call = payload.get("toolCall")
        args = call.get("args") if isinstance(call, dict) else None
        command = args.get("CommandLine") if isinstance(args, dict) else None
        normalized["tool_input"] = {"command": command}
        normalized["tool_name"] = call.get("name") if isinstance(call, dict) else None
        error = payload.get("error")
        if isinstance(error, str) and error:
            normalized["hook_event_name"] = "PostToolUseFailure"
            normalized["error"] = error
            match = EXIT_STATUS.search(error)
            if match:
                normalized["tool_response"] = {"exit_code": int(match.group(1))}
    return normalized


def adapt_hook(root: Path, mode: str, payload: dict[str, Any]) -> dict[str, Any]:
    if mode == "context" and payload.get("invocationNum", 0) != 0:
        return {}
    if mode == "stop" and payload.get("fullyIdle") is False:
        return {}
    script, event = {
        "context": ("session-start", "SessionStart"),
        "failure": ("validation-result", "PostToolUse"),
        "stop": ("stop", "Stop"),
    }[mode]
    result = subprocess.run(
        ["bash", str(root / "hooks" / script)],
        cwd=root,
        input=json.dumps(normalized_payload(payload, event)),
        capture_output=True,
        text=True,
        timeout=8,
        check=False,
        env={**os.environ, "AGENT_HOOK_CLIENT": "antigravity"},
    )
    if result.returncode:
        return {}
    response = json.loads(result.stdout)
    if not isinstance(response, dict):
        return {}
    if mode == "context":
        extra = response.get("hookSpecificOutput")
        context = extra.get("additionalContext") if isinstance(extra, dict) else None
        return (
            {"injectSteps": [{"ephemeralMessage": context}]}
            if isinstance(context, str)
            else {}
        )
    if mode == "stop":
        message = response.get("systemMessage")
        if isinstance(message, str) and message:
            print(f"[agent-harness] {message}", file=sys.stderr)
        return {"decision": "stop"}
    return {}


def main() -> None:
    response: dict[str, Any] = {}
    try:
        payload = json.load(sys.stdin)
        if isinstance(payload, dict):
            response = adapt_hook(
                Path(__file__).resolve().parents[2], sys.argv[1], payload
            )
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        subprocess.TimeoutExpired,
    ):
        pass
    print(json.dumps(response))


if __name__ == "__main__":
    main()
