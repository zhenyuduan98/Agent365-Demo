# 🤖 Agent365-Demo

## 📖 Overview

This is a demo project showcasing **Microsoft Agent 365 (A365)** — Microsoft's next-generation agent platform for building AI agents that integrate with Microsoft 365 services. The agent uses Azure OpenAI GPT models and MCP (Model Context Protocol) MailTools to read and manage Outlook emails.

> 🎤 Built for a TechTalk presentation demonstrating end-to-end Agent 365 deployment.

## 🏗️ Architecture Overview

```
┌──────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Microsoft   │────▶│  Agent Host      │────▶│  Azure OpenAI   │
│  Teams       │◀────│  (Python/aiohttp) │◀────│  (GPT-5.2-chat) │
└──────────────┘     └────────┬─────────┘     └─────────────────┘
                              │
                     ┌────────▼─────────┐
                     │  MCP MailTools   │
                     │  (Outlook Email) │
                     └──────────────────┘

┌──────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Web Browser │────▶│  Web Chat Server │────▶│  Azure OpenAI   │
│              │◀────│  (aiohttp:3979)  │◀────│  (GPT-5.2-chat) │
└──────────────┘     └──────────────────┘     └─────────────────┘
```

## 📦 Components

| Component | File | Description |
|---|---|---|
| Agent Core | `agent.py` | Main agent logic with Azure OpenAI + MCP integration |
| Agent Interface | `agent_interface.py` | Abstract base class for agent implementations |
| Agent Host | `host_agent_server.py` | Generic host server for Teams messaging (port 3978) |
| Web Chat | `web_chat.py` | Standalone web chat UI with SQLite history (port 3979) |
| Auth Options | `local_authentication_options.py` | Local/dev authentication configuration |
| Token Cache | `token_cache.py` | In-memory token caching for observability |
| Tooling Manifest | `ToolingManifest.json` | MCP server configuration (MailTools) |
| Teams Manifest | `manifest/manifest.json` | Teams app manifest for deployment |
| A365 Config | `a365.config.json` | Agent 365 CLI configuration |

## 🌐 Deployment Locations

| Component | Location | Notes |
|---|---|---|
| Agent Host Server | Azure VM (Ubuntu 22.04) | Runs on port 3978 |
| Web Chat Server | Azure VM (Ubuntu 22.04) | Runs on port 3979 |
| Azure OpenAI | Azure East US 2 | GPT-5.2-chat deployment |
| MCP MailTools | Microsoft Cloud | `agent365.svc.cloud.microsoft` |
| Teams App | M365 Admin Center | Published via manifest.zip |
| HTTPS Tunnel | ngrok | Exposes localhost:3978 to Teams |

## 🔄 Complete Message Workflow

### Teams Channel Flow
1. User sends message in Teams → Microsoft 365 platform
2. M365 routes to registered HTTPS endpoint (ngrok tunnel → localhost:3978)
3. `host_agent_server.py` receives the activity via Microsoft Agents SDK
4. JWT authentication validates the request
5. Agent host extracts user message and passes to `agent.py`
6. `agent.py` initializes MCP servers (first message only) — connects to MCP MailTools
7. Agent sends user message + MCP tools to Azure OpenAI
8. Azure OpenAI processes and may call MCP tools (e.g., read emails)
9. MCP tool results are fed back to Azure OpenAI for final response
10. Response is sent back through the Agent SDK → Teams

### Web Chat Flow
1. User opens browser → `http://<VM_IP>:3979`
2. `web_chat.py` serves the HTML/JS chat UI
3. User sends message → POST `/api/chat`
4. Message saved to SQLite, sent to Azure OpenAI via Agent Framework
5. Response saved to SQLite, returned as JSON
6. UI renders the response

## ✅ Prerequisites

- Python 3.11+
- Azure subscription with Azure OpenAI deployed
- Microsoft 365 developer tenant (for Teams integration)
- Agent 365 CLI (`a365`) installed
- ngrok (for HTTPS tunneling to Teams)

## 🚀 Step-by-Step Deployment Guide

### 1. Clone and Setup

```bash
git clone https://github.com/zhenyuduan98/Agent365-Demo.git
cd Agent365-Demo
python -m venv .venv
source .venv/bin/activate
uv sync  # or: pip install -e .
```

### 2. Configure Environment

```bash
cp .env.template .env
# Edit .env with your values:
# - AZURE_OPENAI_API_KEY, ENDPOINT, DEPLOYMENT, API_VERSION
# - CLIENT_ID, CLIENT_SECRET, TENANT_ID (from Agent 365 Blueprint)
# - MCP_BEARER_TOKEN (from `a365 develop get-token`)
```

### 3. Deploy Azure OpenAI

1. Create Azure OpenAI resource in Azure Portal
2. Deploy a GPT model (e.g., gpt-4o or gpt-5.2-chat)
3. Copy the endpoint and API key to `.env`

### 4. Register an App in Microsoft Entra ID

Before setting up the Agent 365 Blueprint, you need to register an application in Microsoft Entra ID (Azure AD). This provides the `CLIENT_ID` and `CLIENT_SECRET` for authentication.

#### 4.1 Create the App Registration

1. Go to [Azure Portal](https://portal.azure.com) → **Microsoft Entra ID** → **App registrations** → **+ New registration**
2. Fill in:
   - **Name**: e.g., `Agent365-CLI-Client`
   - **Supported account types**: **Accounts in this organizational directory only** (Single tenant)
   - **Redirect URI**: Select **Public client/native (mobile & desktop)**, enter `http://localhost`
3. Click **Register**
4. On the **Overview** page, copy:
   - **Application (client) ID** → this is your `CLIENT_ID`
   - **Directory (tenant) ID** → this is your `TENANT_ID`

#### 4.2 Create a Client Secret

1. Go to **Certificates & secrets** → **Client secrets** → **+ New client secret**
2. Enter a description (e.g., `agent-dev-secret`), choose expiration
3. Click **Add**
4. **Copy the secret Value immediately** (not the Secret ID) → this is your `CLIENT_SECRET`

> ⚠️ The secret value is only displayed once. If you miss it, you'll need to create a new one.

#### 4.3 Configure API Permissions (Delegated)

Go to **API permissions** → **+ Add a permission**:

**Microsoft Graph (Delegated):**

| Permission | Purpose |
|---|---|
| `User.Read` | Sign in and read user profile |
| `Mail.Read` | Read user's mailbox (for MCP MailTools) |
| `Mail.Send` | Send mail on behalf of the user |

**MCP Server API (Delegated):**

1. Click **+ Add a permission** → **APIs my organization uses**
2. Search for the MCP server app by its audience ID: `05879165-0320-489e-b644-f72b33f3edf0`
3. Select **Delegated permissions** and add:

| Permission | Purpose |
|---|---|
| `McpServers.Mail.All` | Access MCP Mail tool server |
| `McpServersMetadata.Read.All` | Read MCP server metadata |

> 💡 These scopes match the `scope` and `audience` in `ToolingManifest.json`.

#### 4.4 Grant Admin Consent

Click **Grant admin consent for [your tenant]** (requires Global Admin or Privileged Role Admin).

Verify all permissions show ✅ green checkmarks under the **Status** column.

#### 4.5 Configure Service Connection in `.env`

```env
# App Registration
CLIENT_ID=<your-application-client-id>
CLIENT_SECRET=<your-client-secret-value>
TENANT_ID=<your-directory-tenant-id>

# Service Connection (used by M365 Agents SDK for outbound auth)
CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID=<same-client-id>
CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET=<same-client-secret>
CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID=<same-tenant-id>
CONNECTIONS__SERVICE_CONNECTION__SETTINGS__SCOPES=https://graph.microsoft.com/.default
CONNECTIONSMAP_0_SERVICEURL=*
CONNECTIONSMAP_0_CONNECTION=SERVICE_CONNECTION
```

> The `CONNECTIONS__*` variables map to the M365 Agents SDK connection config. `CONNECTIONSMAP_0_SERVICEURL=*` means this connection handles all outbound service URLs.

### 5. Setup Agent 365 Blueprint

```bash
# Install A365 CLI
npm install -g @agent365/cli

# Login
a365 login

# Create Blueprint
a365 setup blueprint \
  --tenant-id <YOUR_TENANT_ID> \
  --subscription-id <YOUR_SUBSCRIPTION_ID> \
  --resource-group <YOUR_RESOURCE_GROUP>

# Get MCP token (for MailTools)
a365 develop get-token --scope McpServers.Mail.All
# Copy the token to MCP_BEARER_TOKEN in .env
```

### 6. Run the Agent

```bash
# Option A: Teams integration (port 3978)
python -m host_agent_server

# Option B: Web Chat only (port 3979)
python web_chat.py
```

### 7. Setup HTTPS Tunnel (for Teams)

```bash
# Install ngrok
ngrok http 3978

# Register the HTTPS URL with Agent 365
a365 setup blueprint --endpoint-only --endpoint https://<ngrok-url>/api/messages
```

### 8. Publish to Teams

```bash
# Generate Teams manifest
a365 publish

# Upload manifest.zip to M365 Admin Center:
# admin.microsoft.com → Settings → Integrated apps → Upload custom apps
```

### 9. Create Agent Instance (for authenticated responses)

```bash
# Create agent instance for Teams authentication
a365 setup instance \
  --blueprint-id <YOUR_BLUEPRINT_ID> \
  --tenant-id <YOUR_TENANT_ID>
```

## ⚙️ Key Configuration Files

### `.env` (from `.env.template`)
Contains all secrets — Azure OpenAI keys, Client credentials, MCP tokens.

### `ToolingManifest.json`
Defines MCP server connections. Used when `ENVIRONMENT=Development`.

### `a365.config.json`
Agent 365 CLI configuration — tenant, subscription, resource group, blueprint details.

## ⚠️ Known Issues & Notes

1. **MCP Token Expiry**: The MCP bearer token expires after 1 hour. Run `a365 develop get-token` to refresh.
2. **ngrok URL Changes**: Free ngrok URLs change on restart. Re-register endpoint with `a365 setup blueprint --endpoint-only`.
3. **Teams 401 Error**: Agent needs an Agent Instance + Agent User for authenticated replies in Teams.
4. **SDK Naming**: The `ChatAgent` class was renamed to `Agent` in recent SDK versions.

5. **`AADSTS65001` consent error**: Go to API permissions → click **Grant admin consent**.
6. **`AADSTS7000215` invalid client secret**: Make sure you copied the **Value**, not the **Secret ID**. Regenerate if needed.
7. **`Directory.AccessAsUser.All` blocking blueprint creation**: This permission may be injected by WAM. Clear token cache and re-authenticate.

## 🛠️ Tech Stack

- **Runtime**: Python 3.11+
- **AI Model**: Azure OpenAI (GPT-5.2-chat)
- **Agent SDK**: Microsoft Agent Framework + Agent 365 SDK
- **MCP Tools**: MailTools (Outlook email read/write)
- **Web Framework**: aiohttp
- **Database**: SQLite (chat history for web UI)
- **Hosting**: Azure VM (Ubuntu 22.04)
- **Tunnel**: ngrok (HTTPS for Teams)

## 📄 License

This project is for demonstration purposes. Microsoft Agent 365 SDK components are subject to Microsoft's license terms.
