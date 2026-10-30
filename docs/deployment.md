# Deployment

## Deployment lifecycles

Foundation and hosted-agent deployment are split into independent azd projects.
The UI and gateway have a third, isolated Container Apps deployment lifecycle.

| Stage | Folder | Owns | Deploy with |
|-------|--------|------|-------------|
| 1. Foundation | [`foundation/`](../foundation) | Foundry account, project, model, ACR, App Insights, AI Search, Storage, knowledge-base connection, and the **Foundry Toolbox**; knowledge-base setup runs locally | [Build → Deploy workflow](../.github/workflows/deploy-foundation.yml); locally, `azd provision`, then `azd deploy fibey-toolbox` |
| 2. Agent | [`agent/`](../agent) | The hosted `fibey-agent` only | `azd deploy fibey-agent` |
| 3. UI and gateway | [`ui/`](../ui), [`src/fibey/gateway/`](../src/fibey/gateway) | React frontend and private FastAPI gateway in the existing Container Apps environment | [Operator-invoked script](../scripts/deploy-fibey-ui-gateway.sh) |

The foundation can exist without an agent. The toolbox is versioned
independently of the agent: a toolbox deployment does not redeploy the agent,
and agent deployments never touch the toolbox. Successful toolbox deployments
promote the new default, so the agent immediately uses the new tools through its
unversioned toolbox endpoint.
The agent can target any compatible existing project, registry, and toolbox
configured in its own parameter file. It does not depend on a foundation
workflow run or import another deployment's outputs.

## GitHub Actions

The workflows are manual for deployment and automatically validate pull
requests. Each deployment owns a checked-in parameter file:

- Foundation uses
  [`foundation/infra/main.parameters.json`](../foundation/infra/main.parameters.json)
  directly in both jobs.
- Agent uses [`agent/parameters.json`](../agent/parameters.json), selected by its
  `parameter_file` input.

| Workflow | Operations |
|---|---|
| [Foundation](../.github/workflows/deploy-foundation.yml) | Build → Deploy, with infrastructure what-if in Build |
| [Hosted agent](../.github/workflows/deploy-agent.yml) | `preflight` (default) or `deploy` |
| [PR validation](../.github/workflows/validate.yml) | Stable, path-selected checks; no Azure credentials |

The PR workflow always reports the same required check, including for changes
outside these deployment lifecycles. Relevant changes select workflow/action
linting, foundation Bicep and toolbox-manifest checks, offline lifecycle tests,
or an agent dependency-lock, image-build, and importability check. Agent image
validation builds locally on the GitHub runner and does not push an image.

### Foundation Build → Deploy

Every foundation dispatch runs two dependent jobs:

1. **Build** uses the `fibey-foundation` environment and runs an authenticated
   subscription-scope ARM what-if from `main.bicep` using the checked-in
   official [`Azure/bicep-deploy`](https://github.com/Azure/bicep-deploy) action.
   The action compiles the source at the pinned Bicep version.
   Review its formatted change details in the **Preview infrastructure changes**
   step log. Build publishes the `foundation-deployment` artifact
   with `actions/upload-artifact` only after what-if succeeds.
   A compilation or what-if failure blocks Deploy.
2. **Deploy** waits for Build and any configured `fibey-foundation` approval for
   its own job, then downloads that same run's artifact with
   `actions/download-artifact`, without checking out source, and authenticates
   again. It uses `Azure/bicep-deploy` to compile
   and deploy the same Bicep source and checked-in parameter file that were previewed.
   The action compiles in each job; the build output is not separately published.
   It imports the successful deployment outputs into a fresh runner-local azd
   environment, then deploys/promotes the toolbox through its azd postdeploy hook.
   It does not configure or ingest the knowledge base.

The artifact contains the complete `foundation/infra/` tree (including all
transitive Bicep modules, support JSON, and `main.parameters.json`), plus
`foundation/azure.yaml`.
Because the upload paths are all under `foundation/`,
Deploy downloads the artifact into that directory to restore the expected paths.
Knowledge-base setup and documents are not included. There is no directory-staging
tree, copied `.github` helper, parameter renderer, identity manifest, or custom
archive/checksum machinery. Native artifact actions handle storage and download.
Deploy does not check out repository source, but the Bicep action compiles the
downloaded source again at the same pinned version before deployment. Both jobs
therefore use identical artifact inputs; this workflow does not promise a
compile-once binary/template handoff. `.azure` state, credentials, raw deployment
outputs, and diagnostic logs are not published. Deploy does not run `azd provision`.

There is no standalone preview operation. Build's what-if does not apply
infrastructure changes, but it does require Azure authentication and permissions.
What-if is not a locked saved plan: Azure state, permissions, policy, and
capacity can change before approval. It covers infrastructure only, not later
knowledge-base ingestion/configuration or toolbox publication/promotion.
Local `azd provision` remains supported but does not run knowledge-base setup.
For a new or recreated foundation deployed locally or through GitHub Actions,
configure the knowledge base locally after infrastructure exists. Until then, toolbox
publication may fail or knowledge retrieval may be unavailable; if publication
fails, rerun the workflow after local setup. See
[FoundryIQ knowledge-base setup](#foundryiq-knowledge-base-setup).

Foundation deployment creates resources on day 0 and updates them on subsequent
runs. Configure required reviewers and deployment-branch restrictions on the
`fibey-foundation` GitHub Environment; the workflow cannot configure protection
rules for you. Environment protection applies separately to both jobs, so
required reviewers must approve Build before its what-if and Deploy after
reviewing the preview.
Regions come from parameters; the checked-in defaults keep Search in
`centralus` and Foundry in `eastus2`.
Toolbox promotion does not trigger an agent deployment. The hosted-agent
workflow never provisions foundation resources or deploys the toolbox.
Every foundation deployment provisions infrastructure before deploying the
toolbox. Toolbox deployment creates a new version even when its definition is
unchanged; there is no content-based skip. The postdeploy hook promotes that new
version to the default. Build's what-if creates no toolbox version.
After a toolbox deployment, the step log records the exact version
published by the hook, which also verifies the active default. After an agent
deployment, the workflow checks the hosted-agent status and issues a new-session,
read-only inventory smoke prompt for `FIB-002`; it explicitly does not request
work-order creation or updates.

### Deploy UI and gateway with the script

The UI and gateway are deployed only through the operator-invoked
[`scripts/deploy-fibey-ui-gateway.sh`](../scripts/deploy-fibey-ui-gateway.sh);
there is no GitHub Actions deployment workflow for them. Sign in with Azure CLI
and select the subscription named by [`agent/parameters.json`](../agent/parameters.json),
then run these commands from the repository root:

```bash
bash scripts/deploy-fibey-ui-gateway.sh plan
bash scripts/deploy-fibey-ui-gateway.sh deploy
```

The script reads its explicit target settings from
[`services/infra/fibey-ui-gateway.parameters.json`](../services/infra/fibey-ui-gateway.parameters.json)
and uses the subscription and Foundry project already named by
[`agent/parameters.json`](../agent/parameters.json). `plan` (the default) runs ARM
what-if only. `deploy` builds unique `fibey-ui` and `fibey-gateway` tags in the
existing ACR, then deploys only those two Container Apps and a
`Foundry Agent Consumer` assignment for the gateway's system identity at the
existing Foundry project scope. This is the least-privilege permission needed
for the gateway to invoke the already deployed hosted agent; no agent,
inventory, work-orders, foundation, or shared environment resources are
provisioned by this script.

The UI is the only externally accessible app. Nginx in the UI container
proxies `/api/*` to the gateway's internal Container Apps endpoint; the
gateway's health endpoint is checked through that same proxy after deployment.
Both apps scale to zero while idle. PR validation still checks the UI build,
deployment script, and infrastructure without deploying them.

### Deploy inventory and work-orders services separately

The Inventory MCP and Work Orders API are separate Container Apps; the
foundation workflow does not build or deploy them. Before the foundation
deployment, sign in to the intended Azure subscription and run this
operator-invoked prerequisite script:

```bash
AZURE_SUBSCRIPTION_ID=<subscription-id> bash scripts/deploy-prerequisites.sh
```

The script uses `AZURE_ENV_NAME` and `AZURE_LOCATION` from the foundation
parameter file, creates only the service resources in the derived resource
group, and uses the same deterministic ACR name as the foundation. It builds
both `linux/amd64` images in ACR, deploys the two apps, and waits for their
health endpoints. After both checks succeed, it writes the resulting HTTPS URLs
to `parameters.inventoryMcpUrl.value` and `parameters.workOrdersApiUrl.value` in the foundation ARM parameter
file. It is not called by either GitHub deployment workflow; run it manually
before dispatching the foundation workflow. The current Azure identity receives
`AcrPush`; a user-assigned managed identity receives `AcrPull`. ACR admin
credentials are disabled. The apps scale to zero when idle, so the first
request after idle may have a cold start.

The script does not provision Foundry, AI Search, the model, or the toolbox. The
standalone ACR name is intentionally shared with the later foundation
deployment to avoid creating a second registry.

### Independent parameter-driven deployments

1. Configure OIDC and the three authentication secrets below.
2. Edit the foundation ARM parameter file. The template defaults `principalId`
   to `deployer().objectId` and selects User or ServicePrincipal based on the
   deployer's UPN. No workflow token decoding or Microsoft Graph lookup is needed.
   Explicit principal overrides remain available for intentional deployments.
   Run `scripts/deploy-prerequisites.sh` first when deploying the Inventory MCP
   and Work Orders API from this repository; it writes their HTTPS endpoints to
   the parameter file. Keep these endpoints HTTPS and free of credentials.
3. Dispatch the foundation workflow, approve Build if environment protection
   requires it, review Build's what-if step log, and approve Deploy if required.
   The foundation infrastructure can be provisioned without local `.azure`
   state or a prior-run artifact. Its outputs configure toolbox deployment on
   the runner. After initial provisioning, configure the knowledge base locally;
   retry the workflow if toolbox publication failed because it was not yet
   available.
4. Independently configure the agent parameter file with its target project ARM
   ID, project endpoint, ACR connection name, registry hostname, deployed model
   name, and toolbox URL. Then run agent `preflight` and `deploy`. These targets
   must exist, but need not have been created by this foundation workflow.
   Preflight validates configuration and initializes azd; it does not prove
   remote resource readiness or import deployment history.

Both foundation jobs use GitHub Environment `fibey-foundation`; the agent workflow
uses `fibey-agent`. Their azd environment names do not have to match. Foundation
creates its ephemeral azd environment after ARM deployment, using the native
deployment outputs.
For foundation, an empty `resourceGroupName` derives
`rg-<environmentName>`; an empty `aiFoundryResourceName` lets Bicep generate the
account name. An empty AI or Search region inherits `location`.
When adopting an existing deployment, copy its intended name/salt/region
overrides into the file before provisioning; the foundation no longer imports
an older deployment to reconstruct these choices.

The agent reads its endpoint and resource settings only from its file. The
loader derives resource-name/ID aliases locally from `AZURE_AI_PROJECT_ID`
and checks that the ID belongs to the authenticated subscription and agrees
with the project endpoint. No Azure resource or ARM deployment discovery is
performed. There is no deployment-name input, output-import action, or sync
script between these lifecycles.

Foundation Build and the agent jobs check out source; foundation Deploy instead
downloads only the same-run runtime artifact. Foundation calls official
`Azure/login`, `Azure/bicep-deploy`, and artifact actions directly, with
`Azure/setup-azd` only in Deploy. Build installs neither azd nor the toolbox extension.
The agent continues using the shared
[deployment setup action](../.github/actions/setup-deployment/action.yml) and
[initialization action](../.github/actions/initialize-deployment/action.yml)
to validate its parameters and initialize its own azd settings.
Generated state under `foundation/.azure/` and
`agent/.azure/` is ignored, not versioned, so clean runners need no state
cleanup step. Local azd state remains on your machine.

The official Azure action owns ARM what-if, deployment, diagnostics, and output
capture. A short inline CLI handoff installs the toolbox extension and bulk-loads
native deployment outputs into azd; it does not duplicate ARM parameter validation.
The [toolbox postdeploy hook](../foundation/azure.yaml) uses native azd commands
to publish the numeric version from `TOOLBOX_FIBEY_TOOLBOX_MCP_ENDPOINT` and verify
the default. It uses the endpoint written by this deployment, not the latest
version from a list; no predeploy baseline or Python helper is needed. It no
longer rejects a deployment solely because another new version exists.
[KB setup](../foundation/scripts/setup-knowledge-base.sh) runs only when explicitly
requested locally. Toolbox deployment is a direct
`azd deploy fibey-toolbox --no-prompt` command; GitHub handles logs and exit codes.
The manually invoked services prerequisite remains
[`scripts/deploy-prerequisites.sh`](../scripts/deploy-prerequisites.sh).

ARM failures and what-if details are surfaced directly by the Azure action.
The Application Insights connection-string output is masked using its
`masked-outputs` input. Successful ARM output values are imported without
printing them or publishing them in artifacts.
An ARM, output import, toolbox hook, or toolbox failure fails the step
explicitly and prevents later steps.
Toolbox deployment does not require a separate promoted-version confirmation
in a wrapper; promotion remains handled by the configured toolbox lifecycle
hook in `foundation/azure.yaml`.

Configure these secrets in the `fibey-foundation` GitHub Environment, or as
repository secrets if shared:

- `AZURE_CLIENT_ID` — the client ID for the foundation deployment identity.
- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`

GitHub environment secrets override same-name repository secrets. Both jobs use
the same environment and secret names; ensure they identify the intended
foundation identity and subscription. There is no custom cross-job identity or
target comparison. The agent workflow continues using `fibey-agent`, where the
same secret names can be configured for a separately scoped identity.

No GitHub Environment deployment variables are required. Foundation's parameter
file supplies names, regions, model/version/capacity, feature
flags, knowledge-base/index names, non-secret extension inputs, and external
service URLs. Use native JSON booleans, integers, and arrays. Do not put
credentials, connection secrets, or credential-bearing URLs in this file.
`aiProjectDeployments`, `aiProjectConnections`, and
`aiProjectDependentResources` are native ARM array parameters. Keep
empty values as `[]`, not quoted JSON strings or ARM-escaped bracket strings.
Omit principal ID/type parameters to use the native deployer defaults.
`toolboxName` must remain `fibey-toolbox`: the pinned extension uses the
manifest's service key as the deployed toolbox name.

Agent's file contains only agent deployment inputs:

| Parameter | Value to configure |
|---|---|
| `AZURE_ENV_NAME` | Independent agent azd environment name |
| `AZURE_LOCATION` | Agent deployment region |
| `AZURE_AI_PROJECT_ID` | Full ARM ID of the target Foundry project |
| `AZURE_AI_PROJECT_ENDPOINT` | `https://<account>.services.ai.azure.com/api/projects/<project>` |
| `AZURE_AI_PROJECT_ACR_CONNECTION_NAME` | ACR connection on that project |
| `AZURE_CONTAINER_REGISTRY_ENDPOINT` | Registry hostname, without `https://` |
| `FOUNDRY_MODEL` | Model deployment name already available to the agent |
| `TOOLBOX_MCP_URL` | Explicit toolbox MCP URL, without `?api-version=v1` |

Blank target settings in the checked-in agent file must be filled before
preflight/deploy; they are not populated from foundation. The agent has no
foundation principal, Search, inventory, work-orders, model-capacity, or
provisioning settings.
The initializer derives the resource group, account/project names and IDs, and
the `FOUNDRY_PROJECT_ENDPOINT` alias from the canonical project ID and endpoint.
It checks the project ID format and subscription, requires HTTPS endpoints, and
ensures the project endpoint path matches the project ID and the registry value
is a hostname.

[`main.parameters.json`](../foundation/infra/main.parameters.json) is the
foundation configuration source for both CI and local azd. Set values under
`parameters.<name>.value`; it is not an environment-variable interpolation adapter.
Changing an azd environment variable no longer overrides a foundation parameter.
For local provisioning, edit this file, select the intended subscription, and
use an azd environment name matching its `environmentName`:

```bash
cd foundation
azd env new fibey-ent --subscription <subscription-id> --location eastus2
azd provision
# From foundation/, explicitly configure the knowledge base before toolbox deployment.
../scripts/setup.sh --setup-knowledge-base
azd deploy fibey-toolbox
```

Local provisioning still imports outputs, but knowledge-base setup is opt-in.
The agent initialization shell script uses `jq` to read the selected JSON directly,
check the minimum required values for the selected operation, and pass those
values to azd. To validate a file offline without Azure or azd:

```bash
GITHUB_WORKSPACE="$PWD" PARAMETER_FILE=agent/parameters.json \
  OPERATION=agent-validate bash .github/scripts/initialize-deployment.sh
```

Native ARM parameter validation, agent project/subscription matching,
what-if preview, local knowledge-base ingestion checks, toolbox promotion verification,
and agent smoke tests remain. First toolbox publication starts with an empty
baseline only after a successful project toolbox listing confirms absence;
authorization/network/list failures are not treated as an empty foundation.

Configure an environment-subject federated credential for each workflow identity.
Recent successful runs of this repository used these immutable subjects:

| Job context | OIDC subject |
|---|---|
| Foundation Build and Deploy, `fibey-foundation` | `repo:Azure-Samples@1844662/enterprise-toolbox@1431382983:environment:fibey-foundation` |
| Agent, `fibey-agent` | `repo:Azure-Samples@1844662/enterprise-toolbox@1431382983:environment:fibey-agent` |

Use issuer `https://token.actions.githubusercontent.com` and audience
`api://AzureADTokenExchange`. Protect both environments with required
approvals and deployment-branch restrictions: an environment subject does not
itself restrict the branch. For another repository or custom subject template,
use the actual subject reported by `Azure/login` rather than copying these values.
See GitHub's [OIDC subject reference](https://docs.github.com/en/actions/reference/security/oidc#immutable-subject-claims).
The workflows do not create or configure deployment identities or federated
credentials. Resource role assignments declared by
foundation Bicep or the hosted-agent extension may still be applied to workload
identities; the workflows do not grant additional permissions to their own
identities.

The workflows share the non-cancelling `fibey-azure-mutation` GitHub concurrency
group, including when different parameter files select the same target, so their
mutations do not overlap with each other. This conservatively serializes all
targets in the repository. It does not serialize deployments
or mutations started outside GitHub Actions. See the workflow files for the
operation details and [Azure permission guidance](#github-actions-permissions).

### GitHub Actions permissions

Grant only the roles needed for the selected workflow operation. OIDC avoids
stored client secrets, but it does not reduce the Azure permissions required by
the operations themselves.

- PR validation uses `contents: read` only and has no Azure login.
- Deployment jobs use `contents: read` and `id-token: write`.
- Foundation Build also authenticates to run ARM what-if. Reader alone is not
  sufficient; what-if requires deployment and template resource-operation
  permissions. Both foundation jobs use the same environment and secret names,
  so Build's non-mutating behavior does not imply a read-only identity.
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
  permission to create the role assignments declared by Bicep. The separate
  local KB operator needs Storage Blob Data Contributor, permission to read the
  storage account, and Search Service Contributor permissions to upload
  documents, retrieve a Search admin key, and configure/run the Search indexer.

An authorized administrator configures the workflow identities, federated
credentials, and their deployment permissions separately. Foundation Bicep and
the hosted-agent extension may create the workload role assignments explicitly
declared by their deployment definitions; the workflows do not grant additional
permissions to the workflow identities.

```bash
# Provision the foundation from its own configured parameters.
gh workflow run deploy-foundation.yml

# Separately deploy the agent once its configured target resources are ready.
gh workflow run deploy-agent.yml \
  -f operation=deploy -f parameter_file=agent/parameters.json
```

These commands dispatch asynchronous workflow runs; they do not orchestrate one
deployment after another. Both foundation jobs use
`foundation/infra/main.parameters.json` from the artifact. The agent workflow
uses its own selectable file. Local foundation uses the same standard ARM
parameter file; local agent deployment still uses its own azd environment
settings. Do not copy azd state between projects.

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
- `jq` for toolbox promotion and local setup scripts
- The azd AI agents extension: `azd extension install azure.ai.agents`
- Docker Desktop (for local image builds if needed)

The hosted agent definition uses service-level properties in `azure.yaml`
(`kind`, `container`, `protocols`, and `environmentVariables`), as required by
current versions of the AI agents extension. Model deployments are provisioned
by Bicep using `FOUNDRY_MODEL`, `FOUNDRY_MODEL_VERSION`, and `FOUNDRY_MODEL_CAPACITY`.
Foundry injects `FOUNDRY_PROJECT_ENDPOINT` into the hosted agent; do not declare
reserved `FOUNDRY_*` or `AGENT_*` variables in its `environmentVariables`.
Both agent images install dependencies from `uv.lock` with `uv sync --frozen`
so remote builds use the same Agent Framework and MCP versions as development.
The inventory MCP image also uses its service lockfile and does not resolve
dependencies at startup, preserving compatibility with the MCP v1 server API.

The knowledge-base setup assumes the local setup script and AI Search indexer can
reach the storage endpoint. If subscription policy enforces private-only Storage
access, this demo requires additional private networking for both clients before
knowledge-base ingestion can complete. Do not disable the policy to run the
setup script. Anonymous blob access and shared-key authentication remain disabled.
For a limited deployment without knowledge retrieval, remove the
`knowledge_base` MCP tool from the `fibey-toolbox` service in
`foundation/azure.yaml`, then run `azd deploy fibey-toolbox` from `foundation/`.
Restore the tool and deploy again after knowledge-base ingestion is available.

## Quick Deployment (legacy)

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

`azd up` combines provisioning and deployment for the selected project. The
legacy all-in-one sequence below is not the current deployment path; knowledge-base
setup is now an explicit local step, not a postprovision hook:

1. **Provision** — creates the resource group, AI Foundry account + project,
   model deployment, AI Search, storage, ACR, Container Apps environment, all
   six container apps, the Foundry knowledge-base connection, and all RBAC
   assignments (Bicep in `foundation/infra/`).
2. **Explicit knowledge-base setup** — run `./scripts/setup.sh --setup-knowledge-base`
   after provisioning to upload FoundryIQ docs, build the search index, and create
   the knowledge source/base.
3. **Deploy** — builds and pushes container images for all apps and deploys
   the hosted agent to the Foundry project.
4. **Toolbox deployment (separate command)** — run `azd deploy fibey-toolbox`
   from `foundation/` after the knowledge-base connection and backing service
   endpoints are available.

**Important:** Do NOT include `?api-version=v1` in the `TOOLBOX_MCP_URL`. The agent code automatically appends this.

## Environment variables (legacy)

These overrides describe the original all-in-one environment model, not the
current foundation workflow. For current foundation inputs, edit
`parameters.<name>.value` in
[`main.parameters.json`](../foundation/infra/main.parameters.json) instead;
`azd env set` does not override them.

| Variable | Default | Description |
|----------|---------|-------------|
| `FOUNDRY_MODEL` | `gpt-4.1-mini` | Model deployment name |
| `FOUNDRY_MODEL_VERSION` | `2025-04-14` | Model version provisioned by Bicep |
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

Run [`setup-knowledge-base.sh`](../foundation/scripts/setup-knowledge-base.sh)
on demand using `./scripts/setup.sh --setup-knowledge-base`.
Neither local `azd provision`/`azd up` nor the GitHub foundation workflow runs
knowledge-base setup automatically.

1. **Documents uploaded to blob storage** from `services/foundry-iq-docs/docs/`.
2. **Search index and indexer created** — blob data source, `foundry-iq-docs-index`,
   semantic configuration `default`, and the indexer that ingests the markdown files.
3. **Knowledge Source created** — `fibey-field-ops-ks` (`kind: searchIndex`,
   `api-version=2026-05-01-preview`) pointing at `foundry-iq-docs-index`.
4. **Knowledge Base created** — `fibey-field-ops-kb`, referencing `fibey-field-ops-ks`.
5. **Foundry connection** — `kb-fibey-field-ops-kb` is created by Bicep
   (`RemoteTool` + `ProjectManagedIdentity`, target = KB MCP endpoint).
6. **RBAC** — Bicep grants the Foundry project and account managed identities
   `Search Index Data Reader` on the search service.

### Configure and run locally

The foundation's Storage account, Search service, model, and project must
already exist. Sign in with `az login`, select the intended subscription with
`az account set --subscription <subscription-id>`, and use an identity with
Storage Blob Data Contributor, permission to read the storage account, and
Search Service Contributor access. The local machine and the Search indexer
must be able to reach the storage endpoint; Search must also be reachable from
the local machine. The CI identity's permissions do not automatically grant
the local operator access.

If local `azd provision` created the foundation, its azd environment already
contains the deployment outputs; run the explicit setup option after provisioning.
For a foundation created by GitHub Actions, the runner's azd environment is
ephemeral. After the **Deploy foundation Bicep** step succeeds, retrieve only
the non-secret values needed for local setup, using that step's deployment
name (`foundation-<run-id>-<run-attempt>`):

```bash
az deployment sub show \
  --name "foundation-<run-id>-<run-attempt>" \
  --query 'properties.outputs.{AZURE_ENV_NAME:AZURE_ENV_NAME.value,AZURE_LOCATION:AZURE_LOCATION.value,AZURE_RESOURCE_GROUP:AZURE_RESOURCE_GROUP.value,storageAccountName:storageAccountName.value,searchServiceName:searchServiceName.value,AZURE_OPENAI_ENDPOINT:AZURE_OPENAI_ENDPOINT.value,AZURE_SEARCH_INDEX:AZURE_SEARCH_INDEX.value,KB_NAME:KB_NAME.value,FOUNDRY_MODEL:FOUNDRY_MODEL.value}' \
  --output json
```

From `foundation/`, create a local azd environment using the returned
`AZURE_ENV_NAME` and `AZURE_LOCATION`, or select it if it already exists:

```bash
cd foundation
azd env new "<environment-name>" --subscription "<subscription-id>" --location "<location>"
# If it already exists, use: azd env select <environment-name>
```

Populate it with the corresponding values from the filtered deployment output:

```bash
azd env set AZURE_RESOURCE_GROUP "<resource-group>"
azd env set storageAccountName "<storage-account-name>"
azd env set searchServiceName "<search-service-name>"
azd env set AZURE_OPENAI_ENDPOINT "<model-endpoint>"
azd env set AZURE_SEARCH_INDEX "<index-name>"
azd env set KB_NAME "<knowledge-base-name>"
azd env set FOUNDRY_MODEL "<model-deployment-name>"
cd ..
```

Then, from the repository root, run:

```bash
./scripts/setup.sh --setup-knowledge-base
```

This also installs the local Python and UI dependencies. To rerun only ingestion,
use `(cd foundation && bash ./scripts/setup-knowledge-base.sh)`. The script
also accepts `AZURE_RESOURCE_GROUP`, `STORAGE_ACCOUNT`, `SEARCH_SERVICE`, and
`AZURE_OPENAI_ENDPOINT` directly as environment variables; `AZURE_SEARCH_INDEX`,
`KB_NAME`, and `FOUNDRY_MODEL` override the corresponding optional values.
Exported values take precedence over azd settings, so ensure they refer to the
intended foundation.

Run ingestion again when documents or knowledge-base configuration change.
The indexer has no schedule, and the upload overwrites files but does not
delete blobs removed from the repository, so removed documents can remain
searchable until handled separately. If the workflow's toolbox deployment
failed because the knowledge base was missing, rerun it after setup completes.

### Deployed components

| Layer | Name | Resource Group / Scope | Notes |
|-------|------|-------------------------|-------|
| Search service | `<env>-search` | `<resource-group>` | Azure AI Search, Basic SKU |
| Search index | `foundry-iq-docs-index` | Search service | 8 text documents, semantic ranking only, no vectors |
| Semantic config | `default` | `foundry-iq-docs-index` | `titleField=metadata_storage_name`, `contentField=content` |
| Knowledge source | `fibey-field-ops-ks` | AI Search REST API | `kind: searchIndex` via `2026-05-01-preview` |
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

> **Note:** The setup script uses the `2026-05-01-preview` API for knowledge
> sources and knowledge bases, including the configured reasoning model.
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
# Run azd from the agent project directory. Its Docker context points to the
# repository root so the image can use the shared source and lockfiles.
cd agent

# Builds Dockerfile.agent remotely, deploys the agent, and provisions the
# model deployment if needed.
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
