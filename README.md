## Frappe MCP Bridge

Lets **Claude Code** and **Codex** (or any MCP client) work with a Frappe / ERPNext site over the Model Context Protocol. They can read records, run reports, correct data, import rows and run patches. Every call is gated by **MCP Bridge Settings** and recorded in **MCP Bridge Log**.

It works with Frappe **v15 and v16**. The app ships switched **off** and **read only**, with only the `read` capability ticked.

### How it connects

There are two ways to connect. Both go through the same gate and write to the same log.

| | Native HTTP (recommended) | Local stdio bridge |
|---|---|---|
| What runs on your machine | Nothing | A small Python process from `mcp_server/` |
| URL | `https://<site>/api/method/frappe_mcp_bridge.api.mcp.serve` | Same site, via `frappe_mcp_bridge.api.mcp.execute` |
| Sign-in | Each person's own Frappe login (OAuth), or `Authorization: token <api_key>:<api_secret>` | The API key pair, in a `.env` |
| Extra lock | – | `FRAPPE_MCP_READ_ONLY=true` refuses writes locally |

The native endpoint is stateless Streamable HTTP: each POST carries its own credentials and gets plain JSON back.

### 1. Install on the site

```bash
bench get-app https://github.com/<you>/frappe_mcp_bridge
bench --site <site> install-app frappe_mcp_bridge
bench --site <site> migrate
```

### 2. Choose how people sign in

**OAuth (recommended).** Tick **Allow OAuth Sign-In** in **MCP Bridge Settings** and save. Each person then connects with their own Frappe account: the client opens this site's login page, they sign in and press **Allow**, and every call runs as them, so the log shows who did what. There are no keys to hand out, and access is revoked by deleting their **OAuth Bearer Token**. This works for Claude Code, Codex, Claude Desktop and claude.ai (the last two need the site on https).

What saving that checkbox does:

- **v16:** switches on the discovery and client-registration options in Frappe's own **OAuth Settings**.
- **v15:** Frappe has no discovery of its own, so the app serves those documents itself.

On both, client registration goes through this app so that the `http://localhost` redirect Claude Code and Codex use is accepted. Frappe v16's own registration refuses it outside developer mode.

**API keys.** A single shared user and key pair, set up as below. This is what the stdio bridge uses. Untick **Allow API Key Sign-In** once everyone is on OAuth.

1. Create a user (e.g. `mcp@yourcompany.com`) with **only the roles it needs**. MCP can never exceed that user's own Frappe permissions.
2. Open **MCP Bridge Settings** (or the **MCP Bridge** workspace):
   - Set **MCP User**, add one of that user's roles to **Allowed Roles** (the default is System Manager), and save.
   - Click **Generate API Keys**. The secret is shown **once**, together with ready-to-paste Claude Code and Codex config.
   - Tick **Enable MCP Access**. Leave **Read Only Mode** on until you want writes, and then use **Allow Writes For…** (see below).

With shell access you can use bench commands instead:

```bash
bench --site <site> mcp-bridge-keys --user mcp@yourcompany.com   # prints keys + client snippets
bench --site <site> mcp-bridge-enable                             # on, read only
bench --site <site> mcp-bridge-enable --allow-writes --minutes 30 # on, writes for 30 minutes
bench --site <site> mcp-bridge-enable --allow-writes              # on, writes with no time limit
bench --site <site> mcp-bridge-disable                            # off
bench --site <site> mcp-bridge-status                             # what is allowed + recent calls
```

### 3. Connect a client

**With OAuth.** Add the URL with no credentials. The client signs in on first use.

```bash
# Claude Code: then run /mcp, pick the server and choose Authenticate
claude mcp add --transport http prod https://erp.example.com/api/method/frappe_mcp_bridge.api.mcp.serve
```

```toml
# Codex, ~/.codex/config.toml: then run `codex mcp login prod`
[mcp_servers.prod]
url = "https://erp.example.com/api/method/frappe_mcp_bridge.api.mcp.serve"
```

- **claude.ai / Claude Desktop:** Settings → Connectors → Add custom connector, with the same URL.
- **Redirect port changes:** Frappe matches redirect URIs exactly. If a client later comes back on a different localhost port and the Allow step fails, clear its authentication (`/mcp` → Clear authentication in Claude Code) so it registers again.

**With an API key**, Claude Code:

```bash
claude mcp add --transport http prod \
  https://erp.example.com/api/method/frappe_mcp_bridge.api.mcp.serve \
  --header "Authorization: token <api_key>:<api_secret>"
```

Or commit a `.mcp.json` that reads the secret from your environment:

```json
{
  "mcpServers": {
    "prod": {
      "type": "http",
      "url": "https://erp.example.com/api/method/frappe_mcp_bridge.api.mcp.serve",
      "headers": { "Authorization": "token ${FRAPPE_MCP_KEY}:${FRAPPE_MCP_SECRET}" }
    }
  }
}
```

**Codex with an API key** (`~/.codex/config.toml`). Set the header value with `export FRAPPE_MCP_AUTH='token <api_key>:<api_secret>'`.

```toml
[mcp_servers.prod]
url = "https://erp.example.com/api/method/frappe_mcp_bridge.api.mcp.serve"
env_http_headers = { "Authorization" = "FRAPPE_MCP_AUTH" }
```

**Local stdio bridge**: see [mcp_server/README.md](mcp_server/README.md). Use it when you want the extra client-side read-only lock, or a client that only speaks stdio.

Once connected, ask Claude to call `site_ping`. It reports the user, the roles, and whether MCP is enabled and read only.

### Safety model

Every call must pass all of these checks:

| Lock | Where | What it does |
|---|---|---|
| Frappe roles | the signed-in user | The floor. MCP never exceeds what that user can do in the desk. |
| Enable MCP Access | MCP Bridge Settings | Master switch. Off means every tool call is refused. |
| Sign-in methods | MCP Bridge Settings | **Allow OAuth Sign-In** and **Allow API Key Sign-In**, each switchable on its own. Turning one off refuses its tokens immediately. |
| Read Only Mode | MCP Bridge Settings | Refuses every writing tool, whatever else is ticked. |
| Write window | MCP Bridge Settings | **Allow Writes For…** turns Read Only Mode off for 15 minutes to 8 hours. Writes are refused the moment it ends, and the form ticks Read Only Mode back on within 5 minutes. |
| Sensitive fields | MCP Bridge Settings | Masked values come back as `•••` (see below). |
| Capabilities | MCP Bridge Settings | `read`, `write`, `submit`, `delete`, `import`, `patch`, `sql`, `admin`, `script`. Only `read` is on by default. |
| Allowed Roles / IPs | MCP Bridge Settings | The caller must hold an allowed role, and optionally come from an allowed IP or CIDR range. |
| Doctype allow/block lists | MCP Bridge Settings | `User`, `Role`, `DocPerm`, `Server Script`, `System Settings`, this app's own settings and other permission-carrying doctypes are **always** refused. |
| Limits | MCP Bridge Settings | Rows per read, documents per write batch, calls per hour. |

- **Dry run.** Every writing tool takes `dry_run`. The work really runs inside a savepoint and is rolled back, so the errors are the ones a live call would hit. **Default To Dry Run** makes that the default. `run_patch` is the exception: a patch commits as it goes, so its dry run only reports whether it would run and whether the module imports.
- **Audit.** Every call, including refusals, is logged in **MCP Bridge Log**: tool, user, client, IP, duration, outcome and (optionally) payloads. Secrets in payloads are redacted. Old rows are cleared daily according to **Log Retention**.

### Sensitive fields

Everything a tool returns goes to an AI model. List fields that must never do so under **Sensitive Fields**: a document type plus fieldname, or just a fieldname for every document type (e.g. `salary`, `bank_ac_no`, `passport_number`).

- **Masked in every result.** Their values come back as `•••` in documents, child tables, lists, CSV exports, report rows, SQL results and before/after change reports.
- **Indirect reveals are refused.** Calls that would reveal a value some other way are refused: filtering, sorting or grouping on a masked field, using it inside an expression or alias, or naming it in SQL.
- **`describe_site` lists the masked field names**, so Claude can explain a `•••` instead of guessing.
- **`run_server_script` is not covered.** A script can read anything its user can, so keep **Allow Server Script** off on a site with masked fields.

### Tools

- **Orientation**: `site_ping`, `site_capabilities`, `describe_site`, `list_doctypes`, `get_doctype_schema`, `list_reports`
- **Read**: `get_document`, `get_single`, `list_documents`, `count_documents`, `export_records`, `run_report`, `run_sql` (SELECT only), `get_mcp_logs`, `get_error_logs`
- **Write**: `create_document`, `update_document`, `bulk_update_documents`, `import_records`, `submit_document`, `cancel_document`, `amend_document`, `delete_document`, `rename_document`
- **Maintenance**: `list_patches` (one app or all), `get_patch_log`, `run_patch`, `clear_cache`, `reload_doctype`, `force_set_values`, `run_scheduled_job`, `run_server_script`

### Troubleshooting

| Symptom | Cause |
|---|---|
| `401` with `resource_metadata` | No credentials. Either add the API key header or let the client sign in with OAuth. |
| `OAuth sign-in is off` / `API key sign-in is off` | That sign-in method is unticked in MCP Bridge Settings. |
| `The write window ended at …` | The Allow Writes For time ran out. Open a new window. |
| `… uses masked fields` | The call would reveal a sensitive field. Ask without filtering or sorting on it. |
| `MCP access is disabled` | **Enable MCP Access** is off. |
| `… holds none of the roles allowed for MCP access` | Give the MCP user one of the **Allowed Roles**, or add its role to that list. |
| `Read Only Mode is on` | Untick it, or the tool is meant to be refused. |
| `… is not ticked in MCP Bridge Settings` | That capability is off. The message names the exact checkbox. |
| `… can never be reached through MCP` | The doctype is on the built-in block list. |

### Development

Adding a tool takes three steps. The gate needs no change.

1. Add a handler in `frappe_mcp_bridge/mcp/handlers/` with `@tool(name, capability, writes=…, doctype_param=…)`.
2. Add its long description to `frappe_mcp_bridge/mcp/descriptions.py`.
3. Add a thin wrapper in `mcp_server/src/frappe_mcp_bridge_client/tools/`.

The native endpoint builds each tool's input schema from the handler's signature and type hints.

**Tests.** The suite runs against both Frappe versions. The tests swap in an in-memory settings doc and suppress commits, so they leave nothing behind on the site.

```bash
bench --site <site> set-config allow_tests true
bench --site <site> run-tests --app frappe_mcp_bridge
```

GitHub Actions (`.github/workflows/ci.yml`) runs ruff and the suite on Frappe v15 (Python 3.11) and v16 (Python 3.14) for every push and pull request.

### License

mit
