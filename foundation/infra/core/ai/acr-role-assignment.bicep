targetScope = 'resourceGroup'

@description('Name of the existing container registry')
param acrName string

@description('Principal ID to grant AcrPull role')
param principalId string

@description('Full resource ID of the ACR (for generating unique GUID)')
param acrResourceId string

// Reference the existing ACR in this resource group
// The provider advertises these API versions before they are present in Bicep's type catalog.
#disable-next-line BCP081
resource acr 'Microsoft.ContainerRegistry/registries@2026-10-15-preview' existing = {
  name: acrName
}

// Grant AcrPull role to the AI project's managed identity
#disable-next-line BCP081
resource acrPullRoleAssignment 'Microsoft.Authorization/roleAssignments@2026-08-01-preview' = {
  scope: acr
  name: guid(acrResourceId, principalId, '7f951dda-4ed3-4680-a7ca-43fe172d538d')
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    // AcrPull role
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
  }
}
