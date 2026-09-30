import { execFileSync } from "node:child_process";

const VALIDATION_COMMAND =
  /(?:^|[\s;&|])(?:[^\s;&|]*\/)?(?:just|nix|tofu|ruff|pyright|yamllint|kubeconform|kubectl|python(?:3)?|bash|gh)(?:\s|$)/;

// NOTE: keep these module-private. OpenCode loads every exported function in
// this file as a plugin entrypoint, so any extra export breaks startup.
function sessionIdentity(sessionID) {
  const clean = String(sessionID ?? "unknown").replace(/[^A-Za-z0-9._-]/g, "").slice(0, 64);
  return clean || "unknown";
}

function validationFailureRecord(input, output) {
  if (!input || input.tool !== "bash") return null;
  const command = input.args?.command;
  const exit = output?.metadata?.exit;
  if (typeof command !== "string" || !Number.isInteger(exit) || exit === 0) return null;
  if (!VALIDATION_COMMAND.test(command)) return null;
  return { command, exit_code: exit };
}

function sharedHook(root, name, payload, timeout) {
  return JSON.parse(execFileSync("bash", [`${root}/hooks/${name}`], {
    input: JSON.stringify(payload) + "\n",
    cwd: root,
    timeout,
    maxBuffer: 262144,
    encoding: "utf8",
    stdio: ["pipe", "pipe", "ignore"],
    env: { ...process.env, AGENT_HOOK_CLIENT: "opencode" },
  }));
}

export const AgentHarness = async ({ worktree, directory, client }) => {
  const root = worktree || directory;
  const contexts = new Map();
  const active = new Set();
  return {
    "experimental.chat.system.transform": async (input, output) => {
      try {
        if (typeof input?.sessionID !== "string" || !Array.isArray(output?.system)) return;
        active.add(input.sessionID);
        if (!contexts.has(input.sessionID)) {
          contexts.set(input.sessionID, "");
          const result = sharedHook(root, "session-start", {
            session_id: sessionIdentity(input.sessionID),
            hook_event_name: "SessionStart",
          }, 6000);
          const context = result?.hookSpecificOutput?.additionalContext;
          if (typeof context === "string") contexts.set(input.sessionID, context);
        }
        const context = contexts.get(input.sessionID);
        if (context && !output.system.includes(context)) output.system.push(context);
      } catch {
        // Optional context must not prevent a model request.
      }
    },
    event: async ({ event }) => {
      try {
        const sessionID = event?.properties?.sessionID ?? event?.properties?.info?.id;
        if (event?.type === "session.deleted") {
          active.delete(sessionID);
          contexts.delete(sessionID);
          return;
        }
        if (event?.type === "session.compacted") {
          contexts.delete(sessionID);
          return;
        }
        if (event?.type !== "session.idle" || !active.has(sessionID)) return;
        active.delete(sessionID);
        contexts.delete(sessionID);
        const result = sharedHook(root, "stop", {
          session_id: sessionIdentity(sessionID),
          hook_event_name: "Stop",
        }, 8000);
        const reminder = result?.systemMessage;
        if (typeof reminder !== "string" || !reminder) return;
        console.error(`[agent-harness] ${reminder}`);
        if (client?.tui?.showToast) {
          await client.tui.showToast({
            body: { title: "Agent checkpoint", message: reminder, variant: "warning" },
            signal: AbortSignal.timeout(1000),
          });
        }
      } catch {
        // Advisory reminders must not prevent session completion.
      }
    },
    dispose: async () => {
      contexts.clear();
      active.clear();
    },
    "tool.execute.after": async (input, output) => {
      try {
        const record = validationFailureRecord(input, output);
        if (!record) return;
        const payload = JSON.stringify({
          session_id: sessionIdentity(input.sessionID),
          hook_event_name: "PostToolUse",
          tool_input: { command: record.command },
          tool_response: { exit_code: record.exit_code },
        });
        execFileSync("bash", [`${root}/hooks/validation-result`], {
          input: payload + "\n",
          cwd: root,
          timeout: 5000,
          stdio: ["pipe", "ignore", "ignore"],
          env: { ...process.env, AGENT_HOOK_CLIENT: "opencode" },
        });
      } catch {
        // Fail open: recording must never block tool results.
      }
    },
  };
};
