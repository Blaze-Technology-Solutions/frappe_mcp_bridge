## Frappe MCP Bridge

Lets **Claude Code** and **Codex** (or any MCP client) work with a Frappe / ERPNext site over the Model Context Protocol. They can read records, run reports, correct data, import rows and run patches. Every call is gated by **MCP Bridge Settings** and recorded in **MCP Bridge Log**.

It works with Frappe **v15 and v16**. The app ships switched **off** and **read only**, with only the `read` capability ticked.

It also adds **[AI Chat](#ai-chat)**: a chat inside Desk that talks to Claude, ChatGPT, Gemini, DeepSeek or any OpenAI-compatible model with your company's API keys. It uses the same tools and rules as MCP, and keeps every message, token and cost for audit.

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
| Capabilities | MCP Bridge Settings | `read`, `write`, `submit`, `delete`, `import`, `patch`, `sql`, `admin`, `script`, `files`, `backup`. Only `read` is on by default. |
| Allowed Roles / IPs | MCP Bridge Settings | The caller must hold an allowed role, and optionally come from an allowed IP or CIDR range. |
| Doctype allow/block lists | MCP Bridge Settings | `User`, `Role`, `DocPerm`, `Server Script`, `System Settings`, this app's own settings and other permission-carrying doctypes are **always** refused. |
| Limits | MCP Bridge Settings | Rows per read, documents per write batch, calls per hour. |

- **Dry run.** Every writing tool takes `dry_run`. The work really runs inside a savepoint and is rolled back, so the errors are the ones a live call would hit. Anything it queued for after commit, such as Frappe's clean-up job after a delete, is discarded with it. Effects outside the database are not undone: a deleted document's attachment files are removed from disk, and on a developer-mode site saving a DocType, Report, Workspace or similar rewrites app files. **Default To Dry Run** makes that the default. `run_patch` is the exception: a patch commits as it goes, so its dry run only reports whether it would run and whether the module imports.
- **Audit.** Every call, including refusals, is logged in **MCP Bridge Log**: tool, user, client, IP, duration, outcome and (optionally) payloads. Secrets in payloads are redacted. Old rows are cleared daily according to **Log Retention**.

### Sensitive fields

Everything a tool returns goes to an AI model. List fields that must never do so under **Sensitive Fields**: a document type plus fieldname, or just a fieldname for every document type (e.g. `salary`, `bank_ac_no`, `passport_number`).

- **Masked in every result.** Their values come back as `•••` in documents, child tables, lists, CSV exports, report rows, SQL results and before/after change reports.
- **Indirect reveals are refused.** Calls that would reveal a value some other way are refused: filtering, sorting or grouping on a masked field, using it inside an expression or alias, or naming it in SQL.
- **`describe_site` lists the masked field names**, so Claude can explain a `•••` instead of guessing.
- **`run_server_script` is not covered.** A script can read anything its user can, so keep **Allow Server Script** off on a site with masked fields.
- **Server files and backups are not covered.** Masking applies to documents, not to logs, source files or the database dump a backup holds.

### Server files

Tick **Allow Server Files** to let Claude read the code and logs on the server, and export your apps. It is off by default, and only a System Manager can use it, whatever **Allowed Roles** says.

- **Reach.** Everything under the bench directory (`apps/`, `sites/`, `logs/`, `config/`) and nothing outside it. A symlink cannot lead out.
- **Always refused.** `site_config.json` and `common_site_config.json`, `.env` files, private keys and certificates (`*.pem`, `*.key`), Redis ACLs, `.git`, bench backups, and each site's `private/` folder. `.env.example` is allowed.
- **Export.** `export_apps` zips every installed app that Frappe does not maintain itself (anything whose publisher is not Frappe Technologies), or the apps you name. The zip leaves out `.git`, `node_modules`, virtualenvs and caches. It returns a download link that works in a browser or with `curl` for 60 minutes without signing in, so treat the link as a password. Switching off MCP access or **Allow Server Files** stops every open link, the link respects **Allowed IP Addresses**, and each download is logged in **MCP Bridge Log**. The zips are deleted within an hour of their link expiring.

### Backups

Tick **Allow Backups** to have Claude take a backup of the site on the server and hand you a download link, so you can restore a copy of production on your own machine. It is off by default, and only a System Manager can use it, whatever **Allowed Roles** says. After updating the app, run `bench --site <site> migrate` so the checkbox appears.

> "Back up production and restore it here as `shop.localhost`."

1. **`create_backup`** starts Frappe's own backup in the background, the same code as `bench backup` and the scheduled backup, for the database or, with `with_files`, the public and private files too. A small site answers within the call; for a large one `list_backups` with `wait_seconds=30` follows it.
2. **`list_backups`** shows every backup on the server however it was made, scheduled ones included, with sizes and the free disk space.
3. **`get_backup_links`** returns one link per file, valid for 30 minutes, with the restore steps. Claude fetches them with `curl` to your machine and, once you name the local site, runs `bench restore` on it. Restoring **replaces that site's database**.

- **A link is a password.** Anyone holding it can download the file for 30 minutes without signing in, so Claude shows it only to you. It stops working when MCP access or **Allow Backups** is switched off, it respects **Allowed IP Addresses**, and every download is logged in **MCP Bridge Log** with the address that fetched it.
- **Backups are not masked.** A backup holds every record, including the fields listed under Sensitive Fields and every user's password hash. Keep it on an encrypted disk and delete it when you are done.
- **The site config is never offered.** Frappe leaves a copy of `site_config.json`, with the database password and encryption key, beside every backup. The app never lists or serves it. So Password fields and two-factor secrets on the restored copy cannot be decrypted, which also stops the copy from using the server's mail, payment and other integrations. If you need them, copy `encryption_key` from the server's `site_config.json` into the local one yourself.
- **Before the restored copy runs:** `bench --site <site> set-config mute_emails 1` and `bench --site <site> disable-scheduler`. The database holds the server's unsent emails and integration settings.
- **Disk.** A backup is refused when the free space is less than the estimated size plus 1 GB.
- **Retention.** Like any Frappe backup, starting one removes this site's backup files older than 23 hours (`keep_backups_for_hours` in `site_config.json`).
- **It needs a worker.** The backup runs on the `long` queue. If it stays `queued`, no worker is running on the server.
- **Encrypted and partial backups.** If the site encrypts its backups (System Settings), restore with `--encryption-key`; the key is not part of the download. A partial backup, from a `backup` entry in `site_config.json`, is restored with `bench partial-restore`.

### AI Chat

A chat page at `/app/ai-chat`, and an **Ask AI** button on every form, for the people you choose. Each provider is connected with an API key the company owns. Personal claude.ai or ChatGPT subscriptions cannot be used: the providers do not allow other apps to sign in with them.

**Set up**

1. Run `bench --site <site> migrate` and `bench build --app frappe_mcp_bridge`. The server needs the `anthropic` and `openai` Python packages (`bench setup requirements` installs them), a worker on the `default` queue, and socketio for live streaming.
2. **AI Provider**: add one per account. Pick the type (Anthropic, OpenAI, Azure OpenAI, Google Gemini, DeepSeek, xAI, Mistral, Groq, OpenRouter, Ollama or any OpenAI-compatible server) and paste the API key. Press **Test Connection**, then **Fetch Models**.
3. **AI Model**: enter each model's prices per million tokens and enable it. A model cannot be enabled without prices unless it is ticked **Free / Self-Hosted**. Use **Allowed Roles** to limit who can pick it.
4. **AI Chat Settings**: tick **Enable AI Chat**, choose the default model, the currency to show costs in (with its rate to USD) and the budgets.
5. Give people the **AI Chat User** role. Nobody without it sees the page, the workspace or the button.

**Who can do what**

| Role | Can |
|---|---|
| AI Chat User | Chat with the models their roles allow; see and archive their own chats and their own spend. |
| AI Chat Manager | All of that, plus read (never change) everyone's chats, and see the **AI Chat Usage** report. |
| System Manager | Set up providers, keys, models, prices, budgets and templates. Cannot read chats without the manager role. |
| Administrator (the account) | The only one who can delete a chat. Deleting it removes its messages and approvals, which stay restorable from Deleted Documents. Its attached files are deleted for good. |

**What the AI may do with your data**

- **Off by default per model.** The AI sees ERP data only through models ticked **May Read ERP Data**, only when **ERP Data Access** allows it, and only when MCP Bridge Settings is enabled and the user holds one of its Allowed Roles. With ERP Data Access off, no tools, form context or attachments reach any model and the Ask AI button is hidden.
- **Data stays with the models allowed it.** Once a chat holds company data (a tool result, the opened form, an attachment), it cannot switch or fall back to a model that may not read company data.
- **Same rules as MCP, fewer tools.** Chat offers only document and report tools, from a fixed list in `chat/tools.py`. These run as the signed-in user with their own permissions, Read Only Mode, write windows, the doctype lists, sensitive-field masking and the IP list, and every call is written to MCP Bridge Log with client `AI Chat`. The log tools, `run_sql`, patches, admin actions, server scripts, server files and backups stay with MCP clients, because they read across users.
- **Changes wait for the user.** Nothing is saved until the person chatting presses **Approve** on the card. Before that, a change runs as a dry run that is rolled back, together with anything it queued for after commit. Two kinds are previewed without running at all: deletes, which remove attachment files as they go, and, on developer-mode sites, records that Frappe writes to app files. Approvals expire after 30 minutes by default, and are checked again against the chat's settings when approved.
- **Nothing to reach through.** Provider keys are stored encrypted and never sent to the browser. Every AI Chat doctype is on the MCP block list, so no tool can read keys or other people's chats. Model output is cleaned of HTML and of images from other sites before it is shown.
- **Secrets are scrubbed.** API keys, tokens, passwords and card numbers typed into a message are removed before it is stored or sent. Attachments and form data are not scrubbed.

**Billing**

- **Each reply is billed as it happens.** Every reply stores its input, cached, cache-write, output and reasoning tokens, and its cost in USD and in your display currency. Both are frozen at that day's prices and rate.
- **Users** see the cost under each reply, the chat's total, and their month against their budget.
- **Managers** get the **AI Chat Usage** report (by user, model, provider, day or month) and the **AI Assistant** workspace with spend cards and charts.
- **Budgets** are set per role or per user, as a monthly USD amount and a daily message count. At the limit the chat stops. The user and the alert recipients are emailed at 80% and 100%.

**Extras**

- **Ask AI** on any form sends that document, masked and read with the user's own permissions, along with the first message.
- **Attachments**: images go to models that read images, PDFs natively where the model supports it and as text otherwise, CSV and text files as text.
- **AI Prompt Template**: ready-made prompts with `{{ doc.fieldname }}` placeholders, by role, optionally shown under Ask AI on one doctype. Placeholders are filled in plainly; templates are not Jinja.
- **Fallback Model**: tried when a provider fails or declines.
- **Stop** ends a reply mid-stream, and the tokens already used are still billed.
- **Continue in a new chat**: when a chat nears the model's context window, the chat offers a fresh one that starts with the end of the old transcript.
- **Stable requests**: a chat's system prompt and tool list are fixed at its first reply and sent unchanged afterwards, as current Claude models require; each message carries the time it was sent instead. A tool switched off later stays listed but is refused when called.

**Running it in production**: replies run on the `default` queue, so they need a worker. Text streams over socketio; without it the page checks for progress every few seconds instead.

### Tools

- **Orientation**: `site_ping`, `site_capabilities`, `describe_site`, `list_doctypes`, `get_doctype_schema`, `list_reports`
- **Read**: `get_document`, `get_single`, `list_documents`, `count_documents`, `export_records`, `run_report`, `run_sql` (SELECT only), `get_mcp_logs`, `get_error_logs`
- **Write**: `create_document`, `update_document`, `bulk_update_documents`, `import_records`, `submit_document`, `cancel_document`, `amend_document`, `delete_document`, `rename_document`
- **Maintenance**: `list_patches` (one app or all), `get_patch_log`, `run_patch`, `clear_cache`, `reload_doctype`, `force_set_values`, `run_scheduled_job`, `run_server_script`
- **Server files**: `list_server_files`, `read_server_file`, `search_server_files`, `export_apps`
- **Backups**: `create_backup`, `list_backups`, `get_backup_links`

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
| `… holds credentials or private files` | The file is on the built-in block list for server files. |
| `This download link is invalid or has expired` | The link's time is up, or the capability behind it was switched off. Run `export_apps` or `get_backup_links` again. |
| `Not enough free disk space for a backup` | The server's disk cannot hold the backup plus the 1 GB margin. Free some space or leave out the files. |
| A backup stays `queued` | No `long` queue worker is running on the server. |
| AI Chat says "Thinking…" and never answers | No worker is running on the `default` queue. |
| AI Chat answers without using the site's data | The model is not ticked **May Read ERP Data**, ERP Data Access is off, MCP access is off, or the user holds none of MCP's Allowed Roles. The chat shows which. |
| `You have used your AI Chat budget for this month` | Raise the budget in **AI Chat Settings**, for the role or for that user. |
| `Enter the input and output prices before enabling …` | Every enabled model needs prices so its usage can be billed. Tick **Free / Self-Hosted** for local models. |

### Development

Adding a tool takes three steps. The gate needs no change.

1. Add a handler in `frappe_mcp_bridge/mcp/handlers/` with `@tool(name, capability, writes=…, doctype_param=…)`.
2. Add its long description to `frappe_mcp_bridge/mcp/descriptions.py`.
3. Add a thin wrapper in `mcp_server/src/frappe_mcp_bridge_client/tools/`.

The native endpoint builds each tool's input schema from the handler's signature and type hints. AI Chat offers a new tool only once it is added to `CHAT_READ_TOOLS` or `CHAT_WRITE_TOOLS` in `chat/tools.py`; do that only for tools that respect the caller's own permissions.

**AI Chat code.** The server side is in `frappe_mcp_bridge/chat/`:
- `providers/` holds one adapter for Claude and one for every OpenAI-compatible API;
- `orchestrator.py` runs a reply;
- `tools.py` covers tools and approvals;
- `billing.py` covers costs and budgets;
- `access.py` holds the permission rules.

The page is a Vue bundle in `public/js/ai_chat/`. Rebuild it with `bench build --app frappe_mcp_bridge`; Frappe v16 needs Node 24 for that.

**Tests.** The suite runs against both Frappe versions. The tests swap in an in-memory settings doc and suppress commits, so they leave nothing behind on the site.

```bash
bench --site <site> set-config allow_tests true
bench --site <site> run-tests --app frappe_mcp_bridge
```

GitHub Actions (`.github/workflows/ci.yml`) runs ruff and the suite on Frappe v15 (Python 3.11) and v16 (Python 3.14) for every push and pull request.

### License

mit
