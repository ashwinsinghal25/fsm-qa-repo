# fsm-qa-mcp-server

Minimal FastMCP server exposing FSM QA tools over MCP.

## Setup

```bash
cd /Users/ashwinsinghal/Documents/fsm_mcp/fsm-qa-mcp-server
uv sync
```

## Run as an MCP server (stdio)

- Many MCP clients (Cursor/Claude) launch a server via a command; configure:

```json
{
  "mcpServers": {
    "fsm-qa-mcp-server": {
      "command": "/Users/ashwinsinghal/Documents/fsm_mcp/fsm-qa-mcp-server/.venv/bin/python",
      "args": ["server.py"]
    }
  }
}
```

## Tools
- `beat_create(payload)`
- `fse_checkin(payload)`
- `fse_checkout(payload)`

These are placeholders; wire them to real APIs with auth as needed.
