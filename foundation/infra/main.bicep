targetScope = 'subscription'

@minLength(1)
@maxLength(64)
@description('Unique environment name used for resource naming.')
param environmentName string

@minLength(0)
@maxLength(90)
@description('Name of the resource group to use or create. Defaults to rg-<environmentName>.')
param resourceGroupName string = ''

@minLength(1)
@description('Primary Azure region for all resources.')
param location string

@description('Azure region for the AI Foundry account and model deployments. Defaults to the primary location.')
param aiDeploymentsLocation string = ''

@description('Azure region for the AI Search service. Defaults to the primary location. Override when the primary region is out of search capacity.')
param searchServiceLocation string = ''

@description('Id of the user or app to assign application roles.')
param principalId string = ''

@description('Principal type of user or app.')
param principalType string = 'User'

@description('Optional salt to diversify resource names across project recreations.')
param resourceTokenSalt string = ''

@description('Optional. Name of the AI Foundry (AI Services) account. If not provided, a name is generated.')
param aiFoundryResourceName string = ''

@description('Name of the AI Foundry project.')
param aiFoundryProjectName string = 'fibey-project'

@description('Model deployments requested by the azd AI agent extension (JSON).')
param aiProjectDeploymentsJson string = '[]'

@description('Project connections requested by the azd AI agent extension (JSON).')
param aiProjectConnectionsJson string = '[]'

@secure()
@description('JSON map of connection name to credentials object.')
param aiProjectConnectionCredentialsJson string = '{}'

@description('Dependent resources requested by the azd AI agent extension (JSON).')
param aiProjectDependentResourcesJson string = '[]'

@description('Enable hosted agent support (ACR connection + capability host) on the Foundry project.')
param enableHostedAgents bool = true

@description('Enable the capability host for hosted agents.')
param enableCapabilityHost bool = true

@description('Enable monitoring (Application Insights) for the Foundry project.')
param enableMonitoring bool = true

@description('Model deployment name used by the agent and gateway.')
param foundryModel string = 'gpt-4.1-mini'

@description('Version of the Foundry model to deploy.')
param foundryModelVersion string = '2025-04-14'

@description('Capacity (TPM, in thousands) for the Foundry model deployment.')
param foundryModelCapacity int = 100

@description('Name of the Foundry Toolbox the agent connects to (created post-provision).')
param toolboxName string = 'fibey-toolbox'

@description('Name of the AI Search knowledge base (created post-provision).')
param knowledgeBaseName string = 'fibey-field-ops-kb'

@description('Name of the AI Search index for FoundryIQ documents.')
param searchIndexName string = 'foundry-iq-docs-index'

var tags = {
  'azd-env-name': environmentName
}

var resolvedResourceGroupName = empty(resourceGroupName) ? 'rg-${environmentName}' : resourceGroupName
var resolvedAiDeploymentsLocation = empty(aiDeploymentsLocation) ? location : aiDeploymentsLocation
var resolvedSearchServiceLocation = empty(searchServiceLocation) ? location : searchServiceLocation

resource rg 'Microsoft.Resources/resourceGroups@2021-04-01' = {
  name: resolvedResourceGroupName
  location: location
  tags: tags
}

module resources 'resources.bicep' = {
  scope: rg
  name: 'resources'
  params: {
    environmentName: environmentName
    location: location
    aiDeploymentsLocation: resolvedAiDeploymentsLocation
    searchServiceLocation: resolvedSearchServiceLocation
    tags: tags
    principalId: principalId
    principalType: principalType
    resourceTokenSalt: resourceTokenSalt
    aiFoundryResourceName: aiFoundryResourceName
    aiFoundryProjectName: aiFoundryProjectName
    aiProjectDeploymentsJson: aiProjectDeploymentsJson
    aiProjectConnectionsJson: aiProjectConnectionsJson
    aiProjectConnectionCredentialsJson: aiProjectConnectionCredentialsJson
    aiProjectDependentResourcesJson: aiProjectDependentResourcesJson
    enableHostedAgents: enableHostedAgents
    enableCapabilityHost: enableCapabilityHost
    enableMonitoring: enableMonitoring
    foundryModel: foundryModel
    foundryModelVersion: foundryModelVersion
    foundryModelCapacity: foundryModelCapacity
    toolboxName: toolboxName
    knowledgeBaseName: knowledgeBaseName
  }
}

// Resource group and Foundry outputs consumed by the azd AI agent extension
output AZURE_TENANT_ID string = tenant().tenantId
output AZURE_RESOURCE_GROUP string = resolvedResourceGroupName
output AZURE_AI_ACCOUNT_ID string = resources.outputs.aiAccountId
output AZURE_AI_PROJECT_ID string = resources.outputs.aiProjectId
output AZURE_AI_FOUNDRY_PROJECT_ID string = resources.outputs.aiProjectId
output AZURE_AI_ACCOUNT_NAME string = resources.outputs.aiAccountName
output AZURE_AI_PROJECT_NAME string = resources.outputs.aiProjectName
output AZURE_AI_PROJECT_ENDPOINT string = resources.outputs.foundryProjectEndpoint
output FOUNDRY_PROJECT_ENDPOINT string = resources.outputs.foundryProjectEndpoint
output AZURE_OPENAI_ENDPOINT string = resources.outputs.azureOpenAiEndpoint
output APPLICATIONINSIGHTS_CONNECTION_STRING string = resources.outputs.applicationInsightsConnectionString
output APPLICATIONINSIGHTS_RESOURCE_ID string = resources.outputs.applicationInsightsResourceId
output AZURE_AI_PROJECT_ACR_CONNECTION_NAME string = resources.outputs.acrConnectionName
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = resources.outputs.registryLoginServer

// Agent and toolbox configuration
output FOUNDRY_MODEL string = foundryModel
output TOOLBOX_NAME string = toolboxName
output TOOLBOX_MCP_URL string = resources.outputs.toolboxMcpUrl
output KB_NAME string = knowledgeBaseName
output KB_MCP_URL string = resources.outputs.knowledgeBaseMcpEndpoint
output KB_CONNECTION_NAME string = resources.outputs.knowledgeBaseConnectionName

// Knowledge base resources
output AZURE_SEARCH_ENDPOINT string = resources.outputs.searchServiceEndpoint
output AZURE_SEARCH_INDEX string = searchIndexName
output searchServiceName string = resources.outputs.searchServiceName
output searchServiceEndpoint string = resources.outputs.searchServiceEndpoint
output storageAccountName string = resources.outputs.storageAccountName

output registryLoginServer string = resources.outputs.registryLoginServer
