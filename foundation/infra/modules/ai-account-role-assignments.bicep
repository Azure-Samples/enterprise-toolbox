targetScope = 'resourceGroup'

@description('Name of the AI Foundry (AI Services) account.')
param aiAccountName string

@description('Principal ID granted access to the account.')
param principalId string

// Roles required to call models and agents through the Foundry project endpoint
var roleIds = [
  '53ca6127-db72-4b80-b1b0-d745d6d5456d' // Azure AI User (Foundry User)
  'a97b65f3-24c7-4388-baec-2e87135dc908' // Cognitive Services User
  '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd' // Cognitive Services OpenAI User
]

// The provider advertises these API versions before they are present in Bicep's type catalog.
#disable-next-line BCP081
resource aiAccount 'Microsoft.CognitiveServices/accounts@2026-11-15-preview' existing = {
  name: aiAccountName
}

#disable-next-line BCP081
resource roleAssignments 'Microsoft.Authorization/roleAssignments@2026-08-01-preview' = [
  for roleId in roleIds: {
    name: guid(aiAccount.id, principalId, roleId)
    scope: aiAccount
    properties: {
      roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleId)
      principalId: principalId
      principalType: 'ServicePrincipal'
    }
  }
]
