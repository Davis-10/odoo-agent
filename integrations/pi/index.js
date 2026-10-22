// gentle-pi extension: asks before AgentOdoo writes and signs the approved plan (S8).
import { handleToolCall } from "./gate.mjs";

export default function (pi) {
  pi.on("tool_call", (event, ctx) => handleToolCall(event, ctx));
}
