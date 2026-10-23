/**
 * ask_supervisor: the only way a worker reaches a human or the supervisor.
 *
 * The tool turns into an RPC extension UI dialog. The orchestrator stores it
 * as a durable question and answers it, possibly much later. No timeout is
 * set, because Pi would otherwise auto-resolve the dialog with no answer.
 */

import { Type } from "@earendil-works/pi-ai";
import { defineTool, type ExtensionAPI } from "@earendil-works/pi-coding-agent";

const askSupervisor = defineTool({
	name: "ask_supervisor",
	label: "Ask supervisor",
	description:
		"Ask the supervisor a question when requirements are ambiguous or a decision is not yours to make. " +
		"Provide options when the answer is a choice. Blocks until answered.",
	parameters: Type.Object({
		question: Type.String({ description: "The question, self-contained" }),
		options: Type.Optional(Type.Array(Type.String(), { description: "Allowed answers, if it is a choice" })),
	}),
	executionMode: "sequential",
	exposure: "model-only",

	async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
		if (!ctx.hasUI) {
			throw new Error("ask_supervisor needs the RPC client; no supervisor is attached");
		}
		const options = params.options ?? [];
		const answer = options.length > 0 ? await ctx.ui.select(params.question, options) : await ctx.ui.input(params.question, "");
		if (answer === undefined) {
			throw new Error("The supervisor declined to answer; stop and report what is blocked");
		}
		return {
			content: [{ type: "text", text: `Supervisor answered: ${answer}` }],
			details: { question: params.question, options, answer },
		};
	},
});

export default function (pi: ExtensionAPI) {
	pi.registerTool(askSupervisor);
}
