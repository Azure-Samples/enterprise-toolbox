targetScope = 'resourceGroup'

@description('Tags that will be applied to all resources')
param tags object = {}

@description('Bing custom grounding resource name')
param resourceName string

@description('AI Services account name for the project parent')
param aiServicesAccountName string = ''

@description('AI project name for creating the connection')
param aiProjectName string = ''

@description('Name for the AI Foundry Bing Custom Search connection')
param connectionName string

// Get reference to the AI Services account and project to access their managed identities
// The provider advertises these API versions before they are present in Bicep's type catalog.
#disable-next-line BCP081
resource aiAccount 'Microsoft.CognitiveServices/accounts@2026-11-15-preview' existing = if (!empty(aiServicesAccountName) && !empty(aiProjectName)) {
  name: aiServicesAccountName

  #disable-next-line BCP081
  resource aiProject 'projects' existing = {
    name: aiProjectName
  }
}

// Bing Search resource for grounding capability
#disable-next-line BCP081
resource bingCustomSearch 'Microsoft.Bing/accounts@2025-05-01-preview' = {
  name: resourceName
  location: 'global'
  tags: tags
  sku: {
    name: 'G1'
  }
  properties: {
    statisticsEnabled: false
  }
  kind: 'Bing.CustomGrounding'
}

// Role assignment to allow AI project to use Bing Search
#disable-next-line BCP081
resource bingCustomSearchRoleAssignment 'Microsoft.Authorization/roleAssignments@2026-08-01-preview' = if (!empty(aiServicesAccountName) && !empty(aiProjectName)) {
  scope: bingCustomSearch
  name: guid(subscription().id, resourceGroup().id, 'bing-search-role', aiServicesAccountName, aiProjectName)
  properties: {
    principalId: aiAccount::aiProject!.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: resourceId('Microsoft.Authorization/roleDefinitions', 'a97b65f3-24c7-4388-baec-2e87135dc908') // Cognitive Services User
  }
}

// Create the Bing Custom Search connection using the centralized connection module
module aiSearchConnection '../ai/connection.bicep' = if (!empty(aiServicesAccountName) && !empty(aiProjectName)) {
  name: 'bing-custom-search-connection-creation'
  params: {
    aiServicesAccountName: aiServicesAccountName
    aiProjectName: aiProjectName
    connectionConfig: {
      name: connectionName
      category: 'GroundingWithCustomSearch'
      target: bingCustomSearch.properties.endpoint
      authType: 'ApiKey'
      isSharedToAll: true
      metadata: {
        Location: 'global'
        ResourceId: bingCustomSearch.id
        ApiType: 'Azure'
        type: 'bing_custom_search'
      }
    }
    credentials: {
      key: bingCustomSearch.listKeys().key1
    }
  }
  dependsOn: [
    bingCustomSearchRoleAssignment
  ]
}

// Outputs
output bingCustomGroundingName string = bingCustomSearch.name
output bingCustomGroundingConnectionName string = !empty(aiServicesAccountName) && !empty(aiProjectName) ? aiSearchConnection!.outputs.connectionName : ''
output bingCustomGroundingResourceId string = bingCustomSearch.id
output bingCustomGroundingConnectionId string = !empty(aiServicesAccountName) && !empty(aiProjectName) ? aiSearchConnection!.outputs.connectionId : ''
