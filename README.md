# Fibey Field Ops

Fibey is a runnable demo of an agent that connects to four different
backend systems — inventory, work orders, a knowledge base, and a status
dashboard — through **one** endpoint: the **Azure AI Foundry Toolbox**.

## What the agent can do

- Look up fiber parts, SKUs, stock levels, and inventory locations
- View, create, and update work orders
- Retrieve splicing procedures, safety protocols, and troubleshooting guidance
- Check current network or service status

## Architecture (local mode)

```text
┌──────────────┐  /api/chat  ┌──────────────────┐  in-proc  ┌──────────────────┐
│  React UI    │ ──────────► │  FastAPI Gateway │ ────────► │  Fibey Agent     │
│  + Activity  │ ◄── SSE ─── │  (:8080)         │           │  (agent-fw)      │
└──────────────┘             └──────────────────┘           └────────┬─────────┘
                                                                     │
                                                         Foundry Toolbox MCP
                                                                     │
              ┌────────────────┬───────────────────┬─────────────────┐
              │ inventory-mcp  │ work-orders-api   │ FoundryIQ KB    │
              │   (:8001)      │    (:8002)        │ (AI Search)     │
              └────────────────┴───────────────────┴─────────────────┘
```

The sample also ships **containerapp** and **hosted** modes — see
[docs/architecture.md](docs/architecture.md).

## Prerequisites

- Python 3.12+
- Node.js 20+
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- Azure CLI (`az`) + Azure Developer CLI (`azd`) — needed for cloud deployment
  and optional local knowledge-base setup
- An Azure AI Foundry project with a deployed model and a configured Toolbox

## Quickstart (local)

```bash
# 1) Install Python and UI dependencies
./scripts/setup.sh

# 2) Copy and edit environment variables
cp .env.example .env
# At minimum set FOUNDRY_PROJECT_ENDPOINT, FOUNDRY_MODEL, TOOLBOX_MCP_URL

# 3) Start the gateway + UI
./scripts/start-dev.sh

# 4) (optional) In separate terminals, start the local toolbox backends
cd services/inventory-mcp     && uv sync && uv run python server.py
cd services/work-orders-api   && uv sync && uv run python server.py
cd services/status-dashboard/public && python -m http.server 8003
```

Open the UI at <http://localhost:5173>.

| Service | Local URL |
|---|---|
| UI | `http://localhost:5173` |
| Gateway | `http://localhost:8080` |
| Inventory MCP | `http://localhost:8001` |
| Work Orders API | `http://localhost:8002` |
| Status Dashboard | `http://localhost:8003` |

> **Toolbox URL gotcha:** `TOOLBOX_MCP_URL` should **not** include
> `?api-version=v1`. The agent code auto-appends it. The Toolbox MCP
> endpoint requires `api-version=v1` (not a date-based version).

## Deploy to Azure

Cloud deployment has two independent azd projects. The GitHub Actions workflows
keep those lifecycles separate:

- [Foundation workflow](.github/workflows/deploy-foundation.yml): manually run
  **Build → Deploy**. Build runs an infrastructure what-if from Bicep source
  with the official Azure deployment action and publishes that source and the
  checked-in ARM parameter file. Deploy downloads the artifact and runs the
  same action against those files. Both jobs use the `fibey-foundation`
  environment, so configured approvals apply to each job. Bicep is compiled
  in both jobs with the same pinned version. Deploy publishes/promotes a new toolbox version
  but does not configure or refresh the knowledge base. There is no separate
  preview operation.
- [Agent workflow](.github/workflows/deploy-agent.yml): independently preflight
  or deploy only the hosted `fibey-agent`. Preflight is the default.
- [UI and gateway script](scripts/deploy-fibey-ui-gateway.sh): manually preview or
  deploy only the React UI and FastAPI gateway into the existing Fibey Container
  Apps environment. The gateway is private to the environment; the UI Nginx
  proxy forwards `/api/*` to it.
- [PR validation](.github/workflows/validate.yml): runs automatically without
  Azure credentials.

Each workflow owns a checked-in parameter file:

- [`foundation/infra/main.parameters.json`](foundation/infra/main.parameters.json):
  standard ARM parameters for infrastructure, model/version/capacity, and external
  inventory/work-orders URLs. Both foundation jobs use this file directly, as does local `azd provision`.
  ARM's `deployer()` supplies the deployment principal by default.
- [`agent/parameters.json`](agent/parameters.json): agent environment, target
  project ID/endpoint, registry connection/hostname, model name, and toolbox URL.

Foundation infrastructure deployment consumes the artifact produced by Build in
the same run. After initial provisioning, configure the knowledge base locally
using `./scripts/setup.sh --setup-knowledge-base`; the toolbox step may fail or
knowledge retrieval may be unavailable until that is done. See the
[knowledge-base setup instructions](docs/deployment.md#foundryiq-knowledge-base-setup).
The agent targets the existing resources explicitly configured in its own file;
it never discovers or imports another deployment's outputs. The two workflows do
not share parameter files or local azd state.

Both foundation stages authenticate through the `fibey-foundation` GitHub
Environment using OIDC. Configure `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, and
`AZURE_SUBSCRIPTION_ID` as environment or repository secrets; environment
secrets take precedence. Configure environment-subject federation and protect
the environment with appropriate reviewers and branch restrictions. The agent
continues using the `fibey-agent` environment and its own Azure authentication.
Foundation uses official login, deployment, azd setup, and artifact actions
directly. KB setup is explicitly opt-in locally, including after local azd
provisioning. Toolbox promotion uses native azd commands in the postdeploy hook.
Generated local azd state is ignored rather than checked in.
See [docs/deployment.md](docs/deployment.md) for the workflow
operations, required configuration, permissions, and local azd commands.

## Documentation

| Doc | When to read it |
|---|---|
| [`docs/toolbox-integration.md`](docs/toolbox-integration.md) | The integration recipe (custom `httpx.Auth`, headers, MCP gotchas) |
| [`docs/architecture.md`](docs/architecture.md) | Full system diagram, components, streaming protocol, agent modes |
| [`docs/local-development.md`](docs/local-development.md) | All env vars, running individual services, testing the gateway API |
| [`docs/deployment.md`](docs/deployment.md) | Azure deployment via `azd`, hosted-agent deployment, knowledge base setup, RBAC |

## Project layout

```text
src/fibey/                # Python package: agent + gateway
services/
  inventory-mcp/          # MCP inventory server (port 8001)
  work-orders-api/        # FastAPI work-orders service (port 8002)
  status-dashboard/       # Static service-status dashboard (port 8003)
  foundry-iq-docs/        # Markdown source for the FoundryIQ knowledge base
ui/                       # React + TypeScript + Tailwind frontend
foundation/               # Foundry infra (Bicep), toolbox definition, KB setup (azd project 1)
agent/                    # Hosted agent deployment (azd project 2)
scripts/                  # setup.sh, start-dev.sh
docs/                     # Architecture, deployment, local-dev, integration docs
```

## Contributing

This project welcomes contributions and suggestions. See
[CONTRIBUTING.md](CONTRIBUTING.md) for details.

## License

Licensed under the MIT License — see [LICENSE](LICENSE).

## Trademarks

This project may contain trademarks or logos for projects, products, or
services. Authorized use of Microsoft trademarks or logos is subject to and
must follow [Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/legal/intellectualproperty/trademarks/usage/general).
Use of Microsoft trademarks or logos in modified versions of this project
must not cause confusion or imply Microsoft sponsorship. Any use of
third-party trademarks or logos is subject to those third-party's policies.
