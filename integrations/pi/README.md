# AgentOdoo confirmation extension for gentle-pi

Pi's MCP client has no elicitation support, so the AgentOdoo MCP server cannot ask you before a write. This extension asks instead.

## What it does

It handles every `tool_call` for `mcp__agentodoo__apply_plan` and leaves all other tools untouched. For that tool it:

1. Deletes any `host_confirmation` the model put in the arguments.
2. Reads the plan `<state dir>/plans/<plan_id>.json` and the key `<state dir>/host-gate.key`. The state dir is `$XDG_STATE_HOME/agentodoo`, or `~/.local/state/agentodoo` when that variable is unset.
3. Shows a dialog with the plan: kind, profile, project, task, stage, name/description, date, hours and the day total before and after.
4. Only if you approve, adds `host_confirmation` = HMAC-SHA256 of the plan id with the key. The server checks it and writes.

The call is blocked, with nothing added, when you decline, when there is no UI, when the plan id is not 32 lowercase hex characters, or when the plan or key is missing or invalid.

## Requirements

- Create the key once: `agentodoo host-gate init`.
- Register the MCP server under the name `agentodoo` in `~/.pi/agent/mcp.json`. Pi names its tools `mcp__<server>__<tool>`; under another name the extension does not see the calls and the server refuses the write.

## Install

Install this directory as a local Pi package (it loads from its path, without copying):

```bash
pi install /path/to/AgentOdoo/integrations/pi
```

This writes the package to `~/.pi/agent/settings.json`. Check with `pi list`; remove with `pi remove /path/to/AgentOdoo/integrations/pi`. To try it for one run only: `pi -e /path/to/AgentOdoo/integrations/pi`.

## Tests

```bash
cd integrations/pi && node --test
```

## Known limit

In Pi the agent also has a shell. It could read the key file or run `agentodoo apply` itself. This gate stops accidental or unconfirmed writes, not a deliberately malicious agent (stage 4, L6).
