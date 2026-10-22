"""Claude Desktop bundle launcher: runs `agentodoo mcp --config-from-host` (stage 6 S1, S2)."""
import sys

from agentodoo.cli import main

if __name__ == "__main__":
    sys.exit(main(["mcp", "--config-from-host"]))
