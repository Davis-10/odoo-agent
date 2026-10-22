# Install AgentOdoo

AgentOdoo lets your AI agent read your Odoo tasks and log your hours. Nothing is written to Odoo until you confirm it.

## What you install

1. **The MCP server** — a small program on your computer that connects your agent to Odoo.
2. **The skill** — instructions that teach the agent how to work safely (plan first, then ask you).
3. **Your Odoo API key** — your personal credential. Create it in Odoo: your avatar → **My Profile** → **Account Security** → **New API Key**. Odoo shows it only once. **Never paste it into a chat.**

## Which apps work today

| App | Status | How writes are confirmed |
|---|---|---|
| Claude Code (terminal) | Supported | Claude Code asks you through an MCP confirmation dialog |
| gentle-pi / Pi | Supported | A small confirmation extension shows the plan and asks you |
| Claude Desktop | Planned | One-click `.mcpb` bundle. Not yet: Desktop does not show MCP confirmations and needs a bundled runtime |
| ChatGPT | Planned | Needs a hosted server |

## Before you start

- Python 3.11 or newer (`python3 --version`)
- git (`git --version`)
- A terminal

Commands below are for macOS and Linux. **Windows:** use `python -m venv .venv` and replace `.venv/bin/...` with `.venv\Scripts\...` (for example `.venv\Scripts\agentodoo.exe`).

## Step 1 — Install the server

Pick a folder for the code. This guide uses `~/tools`. Wherever you see `/ABS/PATH/odoo-agent`, use the **full path** to your copy (print it with `pwd` inside the folder, for example `/Users/you/tools/odoo-agent`). Apps start the server from anywhere, so short paths like `~/...` or `./...` do not work there.

```bash
mkdir -p ~/tools && cd ~/tools
git clone https://github.com/Davis-10/odoo-agent.git
cd odoo-agent
python3 -m venv .venv
.venv/bin/pip install -e '.[mcp]'
pwd    # this is your /ABS/PATH/odoo-agent
```

## Step 2 — Connect your Odoo

Replace the URL, database and email with yours. `acme` is just a short name you choose for this Odoo.

```bash
.venv/bin/agentodoo profile add acme --url https://odoo.example.com --db acme --username you@example.com
.venv/bin/agentodoo profile set-key acme
.venv/bin/agentodoo doctor
```

- Run `profile set-key` **yourself** in a terminal. It asks for the API key, hides what you type, and stores it in your system keychain. Not in a file, not in the chat.
- `doctor` should print `"status": "ok"`. If not, see [Troubleshooting](#troubleshooting).

## Step 3 — Connect your app

### Claude Code

```bash
claude mcp add --scope user agentodoo -- /ABS/PATH/odoo-agent/.venv/bin/agentodoo mcp
mkdir -p ~/.claude/skills
ln -s /ABS/PATH/odoo-agent/skills/agentodoo ~/.claude/skills/agentodoo
```

The symlink keeps the skill updated with the code. To copy instead: `cp -R /ABS/PATH/odoo-agent/skills/agentodoo ~/.claude/skills/`.

Restart Claude Code, then type `/mcp`: `agentodoo` should show as connected.

**First questions to try:**

- "What Odoo tasks do I have open in Acme?"
- "Which unassigned tasks are there in Acme?"
- "Log 2 hours yesterday on my Acme task for the website meeting."

#### Let Claude Code do the setup for you

Paste this into Claude Code. It does Step 1 and Step 3; you still do Step 2 yourself.

```text
Install AgentOdoo for me from https://github.com/Davis-10/odoo-agent.git:
1. Clone it into ~/tools/odoo-agent, create a .venv there, and run .venv/bin/pip install -e '.[mcp]'.
2. Register the MCP server: claude mcp add --scope user agentodoo -- <absolute path>/.venv/bin/agentodoo mcp
3. Symlink <absolute path>/skills/agentodoo to ~/.claude/skills/agentodoo.
4. Tell me the absolute path you used.
Do NOT ask me for my Odoo API key, do not read it, and do not run `agentodoo profile set-key`.
I will run the profile and key commands myself in my own terminal.
```

### gentle-pi / Pi

```bash
/ABS/PATH/odoo-agent/.venv/bin/agentodoo host-gate init
pi mcp add agentodoo -- /ABS/PATH/odoo-agent/.venv/bin/agentodoo mcp
pi install /ABS/PATH/odoo-agent
pi mcp list
```

- `host-gate init` creates the key the confirmation extension uses. It is never printed.
- The server name must be exactly `agentodoo`; the extension only watches that name.
- `pi install` loads both the confirmation extension and the skill.
- `pi mcp list` should show `agentodoo` as connected. Start a new Pi session.

## How writes are confirmed

AgentOdoo writes only two things: a new task and a new timesheet line. It never edits or deletes anything in Odoo.

1. The agent prepares a **plan** (what, where, when, how many hours).
2. Your app shows you the plan and asks you to approve it.
3. Only after you approve does AgentOdoo write to Odoo and return a receipt.

If your app cannot ask you, AgentOdoo refuses and gives you a command to run yourself (`agentodoo apply <plan> --confirm <plan>`).

**Honest limit:** when the agent can also run terminal commands, these checks stop *accidental* or unconfirmed writes. They cannot stop an agent that deliberately works around them. Read each plan before you approve it.

## Troubleshooting

| What you see | What to do |
|---|---|
| `doctor` does not say `"status": "ok"` | Check URL, database and email with `agentodoo profile list`. Create a new API key in Odoo and run `profile set-key` again. |
| `No API key stored for profile 'acme'` | Run `/ABS/PATH/odoo-agent/.venv/bin/agentodoo profile set-key acme` in your own terminal. |
| `agentodoo` not connected in `/mcp` or `pi mcp list` | Check the command uses the full absolute path and that `.venv/bin/agentodoo mcp` exists. Re-run Step 1 and restart the app. |
| Write refused, with a command to run | Your app could not ask for confirmation. Read the plan, then run the command it shows in your terminal. |
| Refused: daily limit | The day would go over the hours limit (8 by default). Check the date and hours, or change it: `agentodoo profile set-max-daily-hours acme 9`. |
| Refused: stage is closing or not open | Pick an open stage. `agentodoo stages --profile acme` lists stages; `profile set-closed-stages` sets which ones count as closing. |

## Update

```bash
cd /ABS/PATH/odoo-agent
git pull
.venv/bin/pip install -e '.[mcp]'
```

Restart your app afterwards. With a symlinked skill there is nothing else to do; if you copied it, copy it again.

## Uninstall

1. Remove the MCP entry: `claude mcp remove --scope user agentodoo` (Pi: `pi mcp remove agentodoo` and `pi remove /ABS/PATH/odoo-agent`).
2. Remove the skill: `rm ~/.claude/skills/agentodoo` (the symlink or copied folder).
3. Remove your profile and its stored key: `/ABS/PATH/odoo-agent/.venv/bin/agentodoo profile remove acme`.
4. In Odoo, delete the API key: avatar → **My Profile** → **Account Security**.
5. Delete the `/ABS/PATH/odoo-agent` folder.
