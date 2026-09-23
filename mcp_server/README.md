# Frappe MCP Bridge: local stdio server

Lets Claude Code, Codex or Claude Desktop read and change data on a Frappe site: query and correct records, import data, run patches and
maintenance actions.

Two halves:

- **On the site**, in the `frappe_mcp_bridge` app: `frappe_mcp_bridge.api.mcp.execute` is the single entry
  point. It gates every call against **MCP Bridge Settings**, runs it, and writes a
  **MCP Bridge Log** row whatever the outcome.
- **Here**, a standalone Python process: it holds no database connection and no
  business logic. It reads `.env`, authenticates with an API key pair, and forwards
  tool calls over HTTPS.

Everything that decides what is allowed lives on the site, so the answer is the same no
matter which machine or client connects.

## Setup

### 1. On the site

```bash
bench --site <site> migrate
bench --site <site> mcp-bridge-keys --user mcp@example.com --show-env
```

The secret is shown once. The user you name is the user MCP acts as — its own Frappe
roles still bound what it can see, so a purpose-made user with only the roles it needs
is a better idea than Administrator.

Then open **MCP Bridge Settings** in the desk (or the **MCP Bridge** workspace) and tick
**Enable MCP Access**. It ships off, read-only, with only `read` allowed.

### 2. Here

```bash
cd apps/frappe_mcp_bridge/mcp_server
python3 -m venv .venv
.venv/bin/pip install -e .
cp .env.example .env    # then fill in the three FRAPPE_MCP_URL / _API_KEY / _API_SECRET values
.venv/bin/frappe-mcp-bridge --check
```

`--check` prints the site it reached, the user it authenticated as, and whether MCP is
enabled. Fix anything it reports before wiring up a client.

### 3. Connect Claude Code

```bash
claude mcp add frappe \
  --env FRAPPE_MCP_ENV_FILE=/abs/path/to/apps/frappe_mcp_bridge/mcp_server/.env \
  -- /abs/path/to/apps/frappe_mcp_bridge/mcp_server/.venv/bin/frappe-mcp-bridge
```

Or commit a `.mcp.json` in the repo that should have access:

```json
{
  "mcpServers": {
    "frappe": {
      "command": "/abs/path/to/apps/frappe_mcp_bridge/mcp_server/.venv/bin/frappe-mcp-bridge",
      "env": { "FRAPPE_MCP_ENV_FILE": "/abs/path/to/apps/frappe_mcp_bridge/mcp_server/.env" }
    }
  }
}
```

Claude Desktop takes the same shape in its `claude_desktop_config.json`.

Pass `FRAPPE_MCP_ENV_FILE` explicitly: the client launches the server with whatever
working directory it happens to have, so a relative `.env` may not be found.

### Connecting claude.ai

Set `FRAPPE_MCP_TRANSPORT=streamable-http` and the server listens on
`http://<host>:<port>/mcp`. claude.ai custom connectors need a **public HTTPS URL**,
and this server does **no authentication of its own on that port** — anyone who can
reach it gets the API key's access. Put it behind a reverse proxy that terminates TLS
and authenticates callers, and bind it to `127.0.0.1` so only the proxy can reach it.
For a single operator, stdio with Claude Code is both simpler and safer.

## Safety model

Four independent locks. A call has to pass all of them.

| Lock | Where | What it does |
| --- | --- | --- |
| Frappe roles | the API key's user | The floor. MCP can never exceed what that user could do in the desk. |
| `FRAPPE_MCP_READ_ONLY` | this server's `.env` | Refuses every writing tool locally, before the network. Defaults to `true`. |
| Read Only Mode | MCP Bridge Settings | Same, on the site, for every client at once. |
| Capability checkboxes | MCP Bridge Settings | `read`, `write`, `submit`, `delete`, `import`, `patch`, `sql`, `admin`, `script`, each off by default except `read`. |

On top of those: a doctype allow/block list, an IP allow list, a role allow list, a rows-per-read
ceiling, a documents-per-batch ceiling and an optional hourly rate limit. `MCP Bridge Settings`,
`User`, `Role`, `Server Script` and the other permission-carrying doctypes are refused
whatever the settings say, so MCP cannot widen its own access.

**Dry run.** Every writing tool takes `dry_run`. A dry run does the real work inside a
savepoint and then rolls it back, so validation errors are the ones a live call would
hit — not a guess. Tick **Default To Dry Run** to make that the default for every write.
The one exception is `run_patch`: a patch commits as it goes, so its dry run reports
whether it would run and whether the module imports, without running it.

**Audit.** Every call, including refusals, lands in **MCP Bridge Log** with the tool,
user, client name, IP, duration, outcome and — unless you turn payload logging off — the
arguments and the response. Old rows are cleared daily according to **Log Retention**.

## Tools

**Orientation** — `site_ping`, `site_capabilities`, `describe_site`, `list_doctypes`,
`get_doctype_schema`, `list_reports`

**Read** — `get_document`, `get_single`, `list_documents`, `count_documents`,
`export_records`, `run_report`, `run_sql` (SELECT only), `get_mcp_logs`, `get_error_logs`

**Write** — `create_document`, `update_document`, `bulk_update_documents`,
`import_records`, `submit_document`, `cancel_document`, `amend_document`,
`delete_document`, `rename_document`

**Maintenance** — `list_patches`, `get_patch_log`, `run_patch`, `clear_cache`,
`reload_doctype`, `force_set_values`, `run_scheduled_job`, `run_server_script`

`run_server_script` runs in Frappe's Server Script sandbox and needs
`server_script_enabled` in the site config as well as the capability. `force_set_values`
writes straight to the table and skips controller validation — the repair tool for data
a normal update refuses, and the one most worth dry-running first.

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| `Missing FRAPPE_MCP_URL, ...` | No `.env` found. Pass `--env-file` or set `FRAPPE_MCP_ENV_FILE`. |
| `rejected the credentials (401)` | Key pair wrong or regenerated. Re-run `mcp-bridge-keys`. |
| `MCP access is disabled` | **Enable MCP Access** is not ticked in MCP Bridge Settings. |
| `Read Only Mode is on` | Untick it on the site, or the tool is meant to be refused. |
| `... is not ticked in MCP Bridge Settings` | That capability is off. The message names the exact checkbox. |
| `would change data, but this MCP server is in read-only mode` | `FRAPPE_MCP_READ_ONLY=true` here. Change the `.env` and restart the server. |
| `did not answer within Ns` | Long import or patch. Raise `FRAPPE_MCP_TIMEOUT`, or pass `background=true` to `run_patch`. |
| `frappe_mcp_bridge.api.mcp.execute is not available` | The `frappe_mcp_bridge` app is missing or out of date on the site. Deploy and `bench migrate` there. |

`site_capabilities` asked from the client answers most "why was that refused" questions
in one call. On the site, `bench --site <site> mcp-bridge-status` prints the same picture.

## Development

```bash
.venv/bin/frappe-mcp-bridge --check                      # connectivity and permissions
.venv/bin/frappe-mcp-bridge --log-level DEBUG            # run stdio with request logging
```

The SDK pin is `mcp>=2.0,<3`; 2.x renamed `FastMCP` to `MCPServer`, and this code targets
2.x. Adding a tool means one handler in `frappe_mcp_bridge/mcp/handlers/` with an `@tool` decorator
declaring its capability, a thin wrapper in `src/frappe_mcp_bridge_client/tools/`, and the same docstring
in `frappe_mcp_bridge/mcp/descriptions.py` for the native HTTP endpoint. The gate needs no edit.
