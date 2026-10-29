# ZenRows Batch API MCP server

ZenRows Batch API is available as a hosted [Model Context Protocol](https://modelcontextprotocol.io) (MCP)
server, so AI agents such as Claude, ChatGPT, Cursor and VS Code can call it as tools.

| | |
| --- | --- |
| Server URL | `https://app.getelva.ai/mcp/elvademo/zenrows-batch-api` |
| Install page | https://app.getelva.ai/mcp/elvademo/zenrows-batch-api/install |
| Tools | 33, one per operation in [`openapi.yaml`](docs/openapi.yaml) |
| Calls | `https://async.api.zenrows.com/v1` |
| Authentication | Your own API key, sent in the `X-API-Key` header. Your MCP client asks for it when you connect. |

## Install

**[Open the install page](https://app.getelva.ai/mcp/elvademo/zenrows-batch-api/install)** for step-by-step setup in Claude, ChatGPT,
Cursor, VS Code and other clients, or use one of the options below.

[![Install in Cursor](https://cursor.com/deeplink/mcp-install-dark.svg)](https://cursor.com/en/install-mcp?name=ZenRows%20Batch%20API&config=eyJ1cmwiOiJodHRwczovL2FwcC5nZXRlbHZhLmFpL21jcC9lbHZhZGVtby96ZW5yb3dzLWJhdGNoLWFwaSJ9)
[![Install in VS Code](https://img.shields.io/badge/VS_Code-Install_MCP-0098FF?logo=visualstudiocode&logoColor=white)](https://insiders.vscode.dev/redirect/mcp/install?name=zenrows-batch-api&config=%7B%22type%22%3A%22http%22%2C%22url%22%3A%22https%3A%2F%2Fapp.getelva.ai%2Fmcp%2Felvademo%2Fzenrows-batch-api%22%7D)

### Claude Code

```bash
claude mcp add --transport http zenrows-batch-api https://app.getelva.ai/mcp/elvademo/zenrows-batch-api
```

### Claude (claude.ai and Claude Desktop)

Settings → Connectors → **Add custom connector**, then paste the server URL.

### ChatGPT

Settings → Apps & Connectors → Advanced settings → turn on **Developer mode**, then
**Create** a connector and paste the server URL.

### Any other MCP client

```json
{
  "mcpServers": {
    "zenrows-batch-api": {
      "url": "https://app.getelva.ai/mcp/elvademo/zenrows-batch-api"
    }
  }
}
```

---

Generated from this repository's OpenAPI spec and hosted by [Elva](https://getelva.ai?utm_source=github&utm_medium=ZenRows/zenrows-python-sdk&utm_campaign=githubprs).
