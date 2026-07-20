# KD Chatbot

An AI assistant embedded directly inside Odoo. Chat with it from a floating
button on every screen, and it can read and act on your Odoo data, navigate
you around the system, and use external tools connected via MCP (Model
Context Protocol) - like GitHub.

## Features

- **Floating chat widget** ("KD Bot") pinned to the corner of every Odoo
  screen. Minimize (keeps your conversation) or close (starts fresh) -
  your choice.
- **Multiple AI providers**: Anthropic (Claude), OpenAI (ChatGPT), Google
  Gemini, Groq, and Cerebras. Gemini/Groq/Cerebras have free tiers with no
  billing setup required.
- **Full Odoo data access**: the AI can search, create, update, and delete
  records on any model you allow, call button/workflow methods, add new
  custom fields, and surface them in views - all under the logged-in user's
  own permissions, never bypassing Odoo's access rights.
- **Screen navigation**: ask it to open a menu, a filtered list, or a
  specific record, and it actually navigates you there.
- **Voice input/output**: a microphone button for speech-to-text, and an
  optional speaker toggle to have replies read aloud (browser-native, no
  extra API needed).
- **MCP integration**: connect any remote MCP server (Streamable HTTP
  transport) - e.g. GitHub's official MCP server - and the AI gets new
  tools automatically, discovered fresh every conversation turn. Supports
  both simple Bearer tokens (PATs) and a full OAuth 2.0 "Connect" flow for
  providers that require it.

## Requirements

- Odoo 19
- An API key for at least one AI provider (see Setup below)

## Setup

### 1. Install the module

Copy this folder into your Odoo `addons` path, then in Odoo:
**Apps -> Update Apps List -> search "KD Chatbot" -> Install.**

### 2. Configure an AI provider

Go to **Settings -> AI Chatbot**:

1. Pick a **Provider**. For a completely free option with no billing setup,
   choose **Gemini**, **Groq**, or **Cerebras**.
2. Paste your **API Key**:
   - Gemini: ai.google.dev (no card required)
   - Groq: console.groq.com (no card required)
   - Cerebras: cloud.cerebras.ai (no card required)
   - Anthropic: console.anthropic.com (requires billing)
   - OpenAI: platform.openai.com (requires billing)
3. Pick a **Model** from the dropdown (it updates based on the Provider you
   chose). If you need a model newer than what's listed, use **Custom Model
   Override** instead - it always takes priority over the dropdown.
4. (Optional) **Allowed Odoo Models** - restrict which models the AI can
   touch at all, e.g. `res.partner,sale.order,product.product`. Leave blank
   to allow anything the logged-in user already has access to.

### 3. (Optional) Connect MCP servers

Go to **Configuration -> KD Chatbot -> MCP Servers -> New**:

- **Name**: a short label, e.g. `GitHub`. This becomes part of the AI's
  tool names (`mcp__github__create_branch`, etc.), so keep it simple.
- **URL**: the MCP server's HTTP(S) endpoint, e.g.
  `https://api.githubcopilot.com/mcp/` for GitHub's official server.
  Only **remote, Streamable HTTP** MCP servers are supported - stdio-based
  local servers are not.
- **Auth Token**: paste a token directly (e.g. a GitHub Personal Access
  Token) if the server accepts one, **or** fill in the **OAuth** section
  below and click **Connect via OAuth** if the provider requires real
  OAuth (Google, Slack, etc. typically do).
- **Context for the AI** (optional): static facts the AI should already
  know without calling a lookup tool every time, e.g.
  `GitHub username: yourname`.

Click **Test Connection** - it runs the real MCP handshake and reports how
many tools it found. If it works, ask the AI in chat: "What MCP
servers/tools are connected?"

#### Using the OAuth "Connect" flow

Only needed for providers that don't support a simple static token:

1. Register an OAuth App on the provider's developer console.
2. Set its **redirect URI** to the exact value shown in this server's
   **OAuth Redirect URI** field.
3. Fill in **OAuth Authorize URL**, **OAuth Token URL**, **Client ID**,
   **Client Secret**, and **Scope** from the provider's docs.
4. Save, then click **Connect via OAuth** - you'll be redirected to the
   provider's login/consent page, then back to Odoo with the token saved
   automatically.

Note: this doesn't currently refresh expired tokens automatically - if a
provider's token expires, click **Connect via OAuth** again to get a fresh
one.

## Using it

Click the floating **KD** button in the bottom-right corner of any screen.
Type, or use the microphone button to speak. Examples:

- "Show me this month's sale orders"
- "Create a sale order for Azure Interior with 3 office chairs"
- "Open the Invoices menu"
- "Create a field called delivery_date on account.move, type date"
- "List my GitHub repos" (if GitHub MCP is connected)
- "Create a branch called feature-x in owner/repo"

The **(three dots)** menu in the chat header has quick links to
**Settings** and **History** (past conversations).

## Architecture notes

- All Odoo data operations run under the **logged-in user's own
  permissions** - the AI never uses `sudo()` for anything that touches
  user data, so Odoo's normal access rights and record rules always apply.
- Secrets (API keys, MCP tokens) are never rendered in any view, even to
  the user who owns them.
- MCP tools are discovered fresh on every conversation turn (no caching
  yet) - fine for a handful of servers, adds latency if you connect many.
- Token storage is plain text in the database, consistent with most Odoo
  connector modules - not a hardened secrets vault.

## Known limitations

- Voice input relies on the browser's native Web Speech API - works well
  in Chrome/Edge, unsupported in Firefox, partial in Safari.
- MCP support is HTTP-transport only; no stdio/local MCP servers.
- No automatic OAuth token refresh for MCP servers.
- Model dropdown choices are a curated subset and may lag behind a
  provider's latest releases - use **Custom Model Override** in the
  meantime.
