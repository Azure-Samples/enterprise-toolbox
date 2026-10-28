# Deployment

## Two lifecycles: foundation, then agent

Deployment is split into two independent azd projects, driven by separate
GitHub Actions workflows.

| Stage | Folder | Owns | Deploy with |
|-------|--------|------|-------------|
| 1. Foundation | [`foundation/`](../foundation) | Foundry account, project, model, ACR, App Insights, AI Search, Storage, knowledge base + connection, and the **Foundry Toolbox** | `azd provision`, then `azd deploy fibey-toolbox` |
| 2. Agent | [`agent/`](../agent) | The hosted `fibey-agent` only | `azd deploy fibey-agent` |

The foundation can exist without an agent. The toolbox is versioned
independently of the agent: a toolbox deployment does not redeploy the agent,
and agent deployments never touch the toolbox. Successful toolbox deployments
promote the new default, so the agent immediately uses the new tools through its
unversioned toolbox endpoint.

## GitHub Actions

The workflows are manual for deployment and automatically validate pull
requests. They target only the existing Azure Developer CLI environment
`fibey-jf-1026`:

| Workflow | Operations |
|---|---|
| [Foundation](../.github/workflows/deploy-foundation.yml) | `preview` (default) or `provision-and-toolbox` |
| [Hosted agent](../.github/workflows/deploy-agent.yml) | `preflight` (default) or `deploy` |
| [PR validation](../.github/workflows/validate.yml) | Stable, path-selected checks; no Azure credentials |

The PR workflow always reports the same required check, including for changes
outside these deployment lifecycles. Relevant changes select workflow/action
linting, foundation Bicep and toolbox-manifest checks, offline lifecycle tests,
or an agent dependency-lock, image-build, and importability check. Agent image
validation builds locally on the GitHub runner and does not push an image.

Foundation preview and agent preflight are read-only. Foundation provisioning
updates existing resources, so enable required reviewers on the
`fibey-foundation` GitHub Environment to approve each run; it
preserves the Search region in `centralus` while Foundry is in `eastus2`.
Toolbox promotion does not trigger an agent deployment. The hosted-agent
workflow never provisions foundation resources or deploys the toolbox.
Every foundation deployment provisions infrastructure before deploying the
toolbox. Toolbox deployment creates a new version even when its definition is
unchanged; there is no content-based skip. The hooks promote that new version to
the default. Preview creates no toolbox version.
After a toolbox deployment, the workflow summary records the exact version
whose promotion hook verified the active default. After an agent deployment,
the workflow checks the hosted-agent status and issues a new-session,
read-only inventory smoke prompt for `FIB-002`; it explicitly does not request
work-order creation or updates.

The foundation workflow uses the protected GitHub Environment
`fibey-foundation`; the hosted-agent workflow uses `fibey-agent`. Each creates
its own ephemeral azd environment named `fibey-jf-1026` from its environment
variables and a successful ARM deployment. The deployment name is optional:
when omitted, the context loader discovers the newest successful deployment
matching the configured subscription, tenant, resource group, Foundry account,
and project. If multiple matches have the same timestamp, the workflow asks
for an explicit name. Neither
consumes local `.azure` state or artifacts from an earlier workflow.

Each job checks out source, then calls the shared
[deployment setup action](../.github/actions/setup-deployment/action.yml) for
OIDC login and pinned azd/extension installation. The context action creates
the azd environment and applies all validated values in one bulk
`azd env set` call. Generated state under `foundation/.azure/` and
`agent/.azure/` is ignored, not versioned, so clean runners need no state
cleanup step. Local azd state remains on your machine.

Multi-line shell logic lives in [`.github/scripts/`](../.github/scripts/)
(bootstrap validation, context loading, preview, `azd deploy`/`azd provision` via the shared `run-azd` action with optional promotion check, and agent smoke test). Workflows and
actions pass inputs as environment variables and call them with
`bash "$GITHUB_WORKSPACE/.github/scripts/<name>.sh"`; one-line commands such as
`azd deploy fibey-agent` stay inline. The scripts are ShellCheck-linted in PR
validation; focused offline tests cover toolbox promotion and sensitive-output
handling.

Configure these GitHub Environment secrets in both environments:

- `AZURE_CLIENT_ID` — the client ID for that environment's separately scoped
  deployment identity.
- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`

Configure these GitHub Environment variables in both environments:

- `AZURE_RESOURCE_GROUP=rg-fibey-jf-1026`
- `AZURE_AI_ACCOUNT_NAME=ai-account-sidzt6mr5x5qu`
- `AZURE_AI_PROJECT_NAME=fibey-project`
- `AZURE_PRIMARY_LOCATION=eastus2`
- `AZURE_SEARCH_LOCATION=centralus`
- `FOUNDRY_MODEL_VERSION=2025-04-14`
- `FOUNDRY_MODEL_CAPACITY=100`

The foundation environment additionally needs `INVENTORY_MCP_URL`,
`WORK_ORDERS_API_URL`, and `AZURE_PRINCIPAL_ID`. The principal ID is the
foundation deployment identity's **object ID**, not its client ID;
`AZURE_PRINCIPAL_TYPE` is optional and defaults to `ServicePrincipal`.
Foundation preview and provisioning use these values to model the Bicep role
assignments. Both deployment workflows accept an optional successful
subscription-scope ARM deployment name. If none is provided, the latest
matching deployment is selected automatically. At least one successful
matching deployment must already exist; an empty subscription needs a separate
bootstrap path. The resolver validates the selected deployment's subscription,
resource group, account, and project before initializing azd state.

Configure one environment-subject federated credential for each deployment
identity:

| GitHub Environment | OIDC subject |
|---|---|
| `fibey-foundation` | `repo:JFolberth/fibey:environment:fibey-foundation` |
| `fibey-agent` | `repo:JFolberth/fibey:environment:fibey-agent` |

Use issuer `https://token.actions.githubusercontent.com` and audience
`api://AzureADTokenExchange`. Protect both environments with required
approvals and deployment-branch restrictions. The workflows do not create or
configure deployment identities or federated credentials. Resource role
assignments declared by foundation Bicep or the hosted-agent extension may
still be applied to workload identities; the workflows do not grant additional
permissions to their own identities.

The workflows share a non-cancelling GitHub concurrency group so their
mutations do not overlap with each other. This does not serialize deployments
or mutations started outside GitHub Actions. See the workflow files for the
operation details and [Azure permission guidance](#github-actions-permissions).

### GitHub Actions permissions

Grant only the roles needed for the selected workflow operation. OIDC avoids
stored client secrets, but it does not reduce the Azure permissions required by
the operations themselves.

- PR validation uses `contents: read` only and has no Azure login.
- Deployment jobs use `contents: read` and `id-token: write`.
- Use separate deployment identities: foundation provisioning needs broader
  resource and role-assignment permissions than the hosted-agent deployment.
- Toolbox-only deployment needs Foundry project data-plane permission to create
  and promote toolbox versions.
- Agent deployment needs Foundry deployment permission and the ACR remote-build
  and image-push permissions used by the hosted-agent extension, plus
  permission to invoke the agent for the read-only post-deployment smoke test.
  Verify whether that extension also assigns runtime roles before deciding how
  to grant narrowly scoped role-assignment authority.
- Foundation provisioning needs resource-write permissions in the target
  resource group, subscription deployment permissions, and constrained
  permission to create the role assignments declared by Bicep. The KB hook
  additionally needs Storage Blob Data Contributor and Search Service
  Contributor permissions to upload documents, retrieve a Search admin key, and
  configure/run the Search indexer.

An authorized administrator configures the workflow identities, federated
credentials, and their deployment permissions separately. Foundation Bicep and
the hosted-agent extension may create the workload role assignments explicitly
declared by their deployment definitions; the workflows do not grant additional
permissions to the workflow identities.

```bash
# Stage 1: foundation (from foundation/)
cd foundation
azd env new <env-name>          # then set AZURE_LOCATION, AZURE_SUBSCRIPTION_ID
azd provision                   # Foundry infra + knowledge base (postprovision hook)
azd env set INVENTORY_MCP_URL https://<inventory-fqdn>/mcp
azd env set WORK_ORDERS_API_URL https://<work-orders-fqdn>
azd deploy fibey-toolbox        # creates and promotes a toolbox version

# Stage 2: agent (from agent/)
cd ../agent
./scripts/sync-foundation-env.sh <env-name>   # copy project endpoint, ACR, model, TOOLBOX_MCP_URL
azd deploy fibey-agent --environment <env-name>
```

Use the same environment name in both projects. The sync script is for local
development only. In CI, each workflow bootstraps an ephemeral azd environment
from its GitHub Environment variables and the selected successful foundation
deployment; see the workflow files for their exact variable contracts.

The toolbox definition, including its OpenAPI schema, lives inline in
[`foundation/azure.yaml`](../foundation/azure.yaml). Its inventory and
work-orders endpoints are inputs, because those apps are no longer deployed by
either flow. Each successful `azd deploy fibey-toolbox` creates a new immutable
toolbox version and promotes that version to the default. The agent uses the
unversioned toolbox endpoint, so this immediately changes its available tools
without redeploying the agent. To inspect versions or roll back, run:

```bash
azd ai toolbox versions list fibey-toolbox
azd ai toolbox publish fibey-toolbox <previous-version>
```

Promotion is handled by the toolbox service's azd deploy hooks. If the deploy
creates a version but promotion fails, the deploy command reports failure and
the previous default remains active unless the publish operation itself succeeded.
The Container Apps Bicep modules are parked in [`services/infra/`](../services/infra)
and are not wired into either deployment.

## Legacy all-in-one deployment reference

The remaining sections describe the original all-in-one deployment and are
retained for the Container Apps services. The current Foundry foundation and
hosted-agent deployments are described above.

## Overview

Fibey Field Ops supports multiple deployment modes:

1. **Container Apps Mode** (Recommended) - Self-hosted agent in Azure Container Apps
2. **Foundry Hosted Mode** - Uses Foundry's managed agent hosting
3. **Local Development Mode** - For development and testing

This guide focuses on **Container Apps deployment** using `azd`.

## Architecture

The deployment creates all resources in a single resource group (`rg-<env-name>`):

| Component | Azure Service | Source | Purpose |
|-----------|---------------|--------|---------|
| Chat UI | Container App | `ui/` | React frontend with activity sidebar |
| Gateway | Container App | `src/fibey/gateway/` | FastAPI proxy (supports 3 modes) |
| **Agent Service** | **Container App** | **src/fibey/agent/** | **Foundry agent + Toolbox MCP** |
| Work Orders API | Container App | `services/work-orders-api/` | Work order CRUD backend |
| Inventory MCP | Container App | `services/inventory-mcp/` | Inventory MCP server |
| Status Dashboard | Container App | `services/status-dashboard/` | Network/service status page |
| AI Foundry account + project | Microsoft Foundry | `foundation/infra/core/ai/` | Hosts the model deployment, hosted agent, and toolbox |
| Model deployment | Foundry | `azure.yaml` | `gpt-4.1-mini` (configurable) |
| Foundry Toolbox | Foundry (azd service) | `foundation/azure.yaml` | Single MCP endpoint dispatching to backend tools |
| AI Search | Azure AI Search | `services/foundry-iq-docs/` | Knowledge base index + KB MCP endpoint |
| Container Registry | ACR | — | Docker image storage (apps + hosted agent) |
| Storage Account | Blob Storage | `services/foundry-iq-docs/` | Document storage |
| Log Analytics / App Insights | Workspace | — | Logging and monitoring |

## Prerequisites

- Azure subscription with Owner or Contributor + RBAC Admin roles
- Azure CLI (`az`) and Azure Developer CLI (`azd`)
- The azd AI agents extension: `azd extension install azure.ai.agents`
- Docker Desktop (for local image builds if needed)

The hosted agent definition uses service-level properties in `azure.yaml`
(`kind`, `container`, `protocols`, and `environmentVariables`), as required by
current versions of the AI agents extension. Model deployments are provisioned
by Bicep using `FOUNDRY_MODEL` and `FOUNDRY_MODEL_CAPACITY`.
Foundry injects `FOUNDRY_PROJECT_ENDPOINT` into the hosted agent; do not declare
reserved `FOUNDRY_*` or `AGENT_*` variables in its `environmentVariables`.
Both agent images install dependencies from `uv.lock` with `uv sync --frozen`
so remote builds use the same Agent Framework and MCP versions as development.
The inventory MCP image also uses its service lockfile and does not resolve
dependencies at startup, preserving compatibility with the MCP v1 server API.

The knowledge-base setup assumes the upload hook and AI Search indexer can reach
the storage endpoint. If subscription policy enforces private-only Storage
access, this demo requires additional private networking for both clients before
knowledge-base ingestion can complete. Do not disable the policy to run the
hook. Anonymous blob access and shared-key authentication remain disabled.
For a limited deployment without knowledge retrieval, remove the
`knowledge_base` MCP tool from the `fibey-toolbox` service in
`foundation/azure.yaml`, then run `azd deploy fibey-toolbox` from `foundation/`.
Restore the tool and deploy again after knowledge-base ingestion is available.

## Quick Deployment

```bash
# 1. Clone repository
git clone https://github.com/Azure-Samples/fibey.git
cd fibey

# 2. Login
az login
azd auth login

# 3. Deploy everything
azd up
```

`azd up` performs the full deployment:

1. **Provision** — creates the resource group, AI Foundry account + project,
   model deployment, AI Search, storage, ACR, Container Apps environment, all
   six container apps, the Foundry knowledge-base connection, and all RBAC
   assignments (Bicep in `foundation/infra/`).
2. **Postprovision hook** — uploads FoundryIQ docs, builds the search index,
   and creates the knowledge source/base (`foundation/scripts/setup-knowledge-base.sh`).
3. **Deploy** — builds and pushes container images for all apps and deploys
   the hosted agent to the Foundry project.
4. **Toolbox deployment (separate command)** — run `azd deploy fibey-toolbox`
   from `foundation/` after the knowledge-base connection and backing service
   endpoints are available.

**Important:** Do NOT include `?api-version=v1` in the `TOOLBOX_MCP_URL`. The agent code automatically appends this.

## Environment variables

All values are produced as Bicep outputs and stored in the azd environment
automatically — no manual `azd env set` steps are required. Useful overrides:

| Variable | Default | Description |
|----------|---------|-------------|
| `FOUNDRY_MODEL` | `gpt-4.1-mini` | Model deployment name |
| `FOUNDRY_MODEL_CAPACITY` | `100` | Model deployment capacity (TPM, in thousands). Preserved across `azd up` runs |
| `TOOLBOX_NAME` | `fibey-toolbox` | Foundry Toolbox name |
| `KB_NAME` | `fibey-field-ops-kb` | Knowledge base name |
| `AZURE_SEARCH_INDEX` | `foundry-iq-docs-index` | Search index name |
| `AZURE_AI_PROJECT_NAME` | `fibey-project` | Foundry project name |
| `AZURE_AI_DEPLOYMENTS_LOCATION` | primary location | Region for the Foundry account/models |

## FoundryIQ Knowledge Base Setup

The deployed knowledge path is:

```text
services/foundry-iq-docs/docs/
→ Blob Storage container
→ AI Search indexer
→ AI Search index
→ Knowledge Source
→ Knowledge Base
→ MCP endpoint
→ Foundry connection
```

This pipeline is configured **automatically** by the azd postprovision hook
(`foundation/scripts/setup-knowledge-base.sh`):

1. **Documents uploaded to blob storage** from `services/foundry-iq-docs/docs/`.
2. **Search index and indexer created** — blob data source, `foundry-iq-docs-index`,
   semantic configuration `default`, and the indexer that ingests the markdown files.
3. **Knowledge Source created** — `fibey-field-ops-ks` (`kind: searchIndex`,
   `api-version=2026-04-01`) pointing at `foundry-iq-docs-index`.
4. **Knowledge Base created** — `fibey-field-ops-kb`, referencing `fibey-field-ops-ks`.
5. **Foundry connection** — `kb-fibey-field-ops-kb` is created by Bicep
   (`RemoteTool` + `ProjectManagedIdentity`, target = KB MCP endpoint).
6. **RBAC** — Bicep grants the Foundry project and account managed identities
   `Search Index Data Reader` on the search service.

To re-run manually: `./scripts/setup-knowledge-base.sh` (requires an azd environment).

### Deployed components

| Layer | Name | Resource Group / Scope | Notes |
|-------|------|-------------------------|-------|
| Search service | `<env>-search` | `<resource-group>` | Azure AI Search, Basic SKU |
| Search index | `foundry-iq-docs-index` | Search service | 8 text documents, semantic ranking only, no vectors |
| Semantic config | `default` | `foundry-iq-docs-index` | `titleField=metadata_storage_name`, `contentField=content` |
| Knowledge source | `fibey-field-ops-ks` | AI Search REST API | `kind: searchIndex` via `2026-04-01` |
| Knowledge base | `fibey-field-ops-kb` | AI Search REST API | References `fibey-field-ops-ks` |
| MCP endpoint | `https://<search-service>.search.windows.net/knowledgebases/fibey-field-ops-kb/mcp` | AI Search | Exposed by the knowledge base |
| Foundry connection | `kb-fibey-field-ops-kb` | AI Services account | `RemoteTool` + `ProjectManagedIdentity` |
| RBAC | `Search Index Data Reader` | AI Services managed identity | Required on the search service |

The knowledge base retrieval was validated with semantic `intents` requests against `fibey-field-ops-ks`, returning references and source data:

```json
{
  "intents": [{"search": "How do I splice a fiber optic cable?", "type": "semantic"}],
  "knowledgeSourceParams": [{"knowledgeSourceName": "fibey-field-ops-ks", "kind": "searchIndex", "includeReferences": true, "includeReferenceSourceData": true}]
}
```

> **Note:** `2026-04-01` is the GA API version used here for knowledge sources and knowledge bases.
>
> **Note:** Azure AI Foundry workspaces currently allow up to **120 connections**. Plan for cleanup or capacity management if your workspace is near the limit.

## Notes

- All Container Apps are configured with **minReplicas: 1** to avoid cold starts.
- The FoundryIQ documents are uploaded to blob storage and indexed separately — they are not part of the container deployment.
- The status dashboard can be set to internal-only ingress if browser automation is the only consumer.
- Infrastructure definitions live in `foundation/infra/` (Bicep). Deploy the Foundry Toolbox separately with `azd deploy fibey-toolbox` from `foundation/`.

## RBAC (automated)

All managed identity permissions are assigned by Bicep during `azd provision`:

| Identity | Role(s) | Scope |
|----------|---------|-------|
| agent-service Container App | Azure AI User (Foundry User), Cognitive Services User, Cognitive Services OpenAI User | AI Foundry account |
| gateway Container App | Azure AI User (Foundry User), Cognitive Services User, Cognitive Services OpenAI User | AI Foundry account |
| Foundry project + account | Search Index Data Reader | AI Search service |
| AI Search | Storage Blob Data Reader | Storage account |
| Foundry project | AcrPull | Container registry |

### Foundry RBAC Roles (Reference)

**Note:** Foundry roles were recently renamed. Use role definition IDs (GUIDs) instead of role names:

| Role Name | Role ID (GUID) | Purpose |
|-----------|----------------|---------|
| Foundry User | `53ca6127-db72-4b80-b1b0-d745d6d5456d` | Basic Foundry access |
| Foundry Owner | `c883944f-8b7b-4483-af10-35834be79c4a` | Full Foundry management |
| Foundry Account Owner | `e47c6f54-e4a2-4754-9501-8e0985b135e1` | Account-level management |
| Foundry Project Manager | `eadc314b-1a2d-4efa-be10-5d325db5065e` | Project management |

**Current Implementation:** The Fibey Agent uses Cognitive Services roles (not Foundry-specific roles). This may change if Foundry Toolbox requires specific Foundry roles in future updates.

## Hosted Agent Deployment

The Foundry-hosted agent is deployed via `azd` using the `azure.ai.agent` host type.

### Prerequisites

```bash
# Install the azd AI agents extension
azd extension install azure.ai.agents

# Log in
azd auth login
az login
```

### Deploy

```bash
# From the repo root — builds Dockerfile.agent (remote build), deploys the
# agent to the Foundry project, and provisions the model deployment if needed
azd up

# Or deploy only the agent (skip Container Apps)
azd deploy fibey-agent
```

### Hosted agent environment variables

| Variable | Source | Description |
|----------|--------|-------------|
| `FOUNDRY_PROJECT_ENDPOINT` | Auto-injected | Foundry project endpoint URL |
| `FOUNDRY_MODEL` | Auto-injected | Model deployment name (from `azure.yaml`) |
| `TOOLBOX_MCP_URL` | `agent/azure.yaml` (copied from foundation outputs) | Toolbox MCP endpoint URL |

> **Note:** All `FOUNDRY_*` and `AGENT_*` env vars are reserved by the platform.
> Do not set them in `agent.yaml` — they are auto-injected from the deployment config.

### Local vs Hosted mode

| | Local Mode | Hosted Mode |
|---|-----------|-------------|
| **Entrypoint** | `src/fibey/agent/agent.py` | `src/fibey/agent/hosted.py` |
| **Server** | FastAPI gateway (`api_server.py`) | `ResponsesHostServer` |
| **History** | In-memory `AgentSession` | Managed by Foundry platform |
| **Toolbox auth** | Azure CLI credential | Bearer token from managed identity |
| **Skills** | `SkillsProvider` | `SkillsProvider` |

### Foundry Toolbox

The Toolbox lives in the same Foundry project and provides a single MCP
endpoint that dispatches to the backend tools. Deploy it explicitly from
`foundation/` with `azd deploy fibey-toolbox`:

| Tool | Type | Backend |
|------|------|---------|
| Tool Search | `toolbox_search_preview` | Toolbox built-in tool discovery |
| Work Orders | OpenAPI (anonymous) | Container App (`work-orders-api`) |
| Inventory | MCP | Container App (`inventory-mcp`) |
| Knowledge Base | MCP via `kb-fibey-field-ops-kb` connection | AI Search knowledge base (`fibey-field-ops-kb`) |

**Tool search** is enabled on the toolbox: the initial `tools/list` exposes
only `tool_search` and `call_tool`, and the agent discovers backend tools on
demand by searching their descriptions. This keeps the model's tool list small
as more tools are added.

Each deployment creates and automatically promotes a new toolbox version.
Inspect available versions and the active default with:

```bash
azd ai toolbox versions list fibey-toolbox
```

## Gateway Modes

The gateway supports three deployment modes configured via `AGENT_MODE` environment variable:
### 1. Container App Mode (Current Default)
```bash
AGENT_MODE=containerapp
CONTAINERAPP_AGENT_URL=https://<env>-agent-service...
```
- Self-hosted agent in Container Apps
- Full control over deployment and scaling
- Direct Toolbox MCP integration with api-version=v1
- Managed identity authentication

### 2. Foundry Hosted Mode
```bash
AGENT_MODE=hosted
HOSTED_AGENT_ENDPOINT=https://<account>.services.ai.azure.com/api/projects/<project>
HOSTED_AGENT_NAME=fibey-agent
```
- Uses Foundry's managed agent hosting
- No container management needed
- Requires hosted agent deployment in Foundry project

### 3. Local Mode (Development Only)
```bash
AGENT_MODE=local
```
- Agent runs in-process with gateway
- For local development and testing
- Not suitable for production

## Troubleshooting

### Toolbox MCP Connection Errors

**Symptom:** `400 BadRequest` from Toolbox MCP endpoint during initialization

**Solution:** The Toolbox MCP endpoint requires `api-version=v1` (not date-based versions). The agent code in `src/fibey/agent/agent.py` automatically appends `?api-version=v1` to the `TOOLBOX_MCP_URL`. Verify:

1. `TOOLBOX_MCP_URL` does NOT include `?api-version=...`
2. Agent code includes the URL modification in `_create_toolbox_mcp()` function

Test directly:
```bash
TOKEN=$(az account get-access-token --resource https://ai.azure.com --query accessToken -o tsv)
curl "https://<account>.services.ai.azure.com/api/projects/<project>/toolboxes/<name>/mcp?api-version=v1" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Foundry-Features: Toolboxes=V1Preview" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

### Authentication Errors

**Symptom:** `DefaultAzureCredential failed to retrieve a token`

**Solution:** Verify managed identity has required roles:
```bash
az role assignment list \
  --assignee "$AGENT_MI_ID" \
  --query "[].{role:roleDefinitionName,scope:scope}" \
  --output table
```

### Container App Logs

```bash
# Application logs
az containerapp logs show \
  --name <env>-agent-service \
  --resource-group <resource-group> \
  --type console \
  --tail 100

# System logs
az containerapp logs show \
  --name <env>-agent-service \
  --resource-group <resource-group> \
  --type system \
  --tail 50
```

### Manual Image Rebuild

If you need to rebuild and redeploy the agent-service:

```bash
# Build for linux/amd64 (required for Container Apps)
docker build --platform linux/amd64 \
  -f Dockerfile.agent-service \
  -t <acr-name>.azurecr.io/fibey-agent-service:latest .

# Push to ACR
az acr login --name <acr-name>
docker push <acr-name>.azurecr.io/fibey-agent-service:latest

# Update container app
az containerapp update \
  --name <env>-agent-service \
  --resource-group <resource-group> \
  --image <acr-name>.azurecr.io/fibey-agent-service:latest
```

## Verification

After deployment, test the stack:

```bash
# Test UI
curl https://<env>-ui.<env-subdomain>.azurecontainerapps.io/

# Test agent-service health
curl https://<env>-agent-service.<env-subdomain>.azurecontainerapps.io/api/health

# Test end-to-end chat via gateway
curl -X POST https://<env>-gateway.<env-subdomain>.azurecontainerapps.io/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"What tools do you have?","session_id":"test-123"}'
```

## Cleanup

```bash
# Remove all deployed resources
azd down

# Or manually delete resource group
az group delete --name <resource-group> --yes --no-wait
```
