targetScope = 'resourceGroup'

@minLength(1)
@maxLength(64)
@description('Environment name used by the foundation deployment.')
param environmentName string

@description('Azure region for the service resources.')
param location string

@description('Immutable tag used for both service images.')
param imageTag string

@description('Deploy the apps after their images have been pushed to ACR.')
param deployApps bool = false

var sanitizedEnvironmentName = replace(toLower(environmentName), '-', '')
var resourceToken = toLower(uniqueString(subscription().subscriptionId, resourceGroup().id, environmentName))
var registryName = 'acr${take(sanitizedEnvironmentName, 15)}${take(resourceToken, 8)}'
var tags = {
  'azd-env-name': environmentName
  'service-lifecycle': 'fibey-services'
}

var acrPullRoleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '7f951dda-4ed3-4680-a7ca-43fe172d538d'
)

module containerRegistry '../../foundation/infra/modules/container-registry.bicep' = {
  name: 'container-registry'
  params: {
    name: registryName
    location: location
    tags: tags
  }
}

module logAnalytics '../../foundation/infra/modules/log-analytics.bicep' = {
  name: 'services-log-analytics'
  params: {
    name: '${environmentName}-services-logs'
    location: location
    tags: tags
  }
}

module containerAppsEnvironment 'modules/container-apps-environment.bicep' = {
  name: 'services-container-apps-environment'
  params: {
    name: '${environmentName}-services-cae'
    location: location
    logAnalyticsWorkspaceCustomerId: logAnalytics.outputs.customerId
    logAnalyticsWorkspaceSharedKey: logAnalytics.outputs.sharedKey
    tags: tags
  }
}

resource registryIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${environmentName}-services-acr-pull'
  location: location
  tags: tags
}

resource existingRegistry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: registryName
}

resource registryPullRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(existingRegistry.id, registryIdentity.id, acrPullRoleDefinitionId)
  scope: existingRegistry
  properties: {
    roleDefinitionId: acrPullRoleDefinitionId
    principalId: registryIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
  dependsOn: [
    containerRegistry
  ]
}

module inventoryMcp 'modules/container-app.bicep' = if (deployApps) {
  name: 'inventory-mcp'
  params: {
    name: '${environmentName}-inventory-mcp'
    serviceName: 'inventory-mcp'
    location: location
    environmentId: containerAppsEnvironment.outputs.id
    containerImage: '${containerRegistry.outputs.loginServer}/inventory-mcp:${imageTag}'
    targetPort: 8001
    env: []
    registryServer: containerRegistry.outputs.loginServer
    registryIdentityResourceId: registryIdentity.id
    minReplicas: 0
    maxReplicas: 3
    tags: tags
  }
  dependsOn: [
    registryPullRoleAssignment
  ]
}

module workOrdersApi 'modules/container-app.bicep' = if (deployApps) {
  name: 'work-orders-api'
  params: {
    name: '${environmentName}-work-orders'
    serviceName: 'work-orders-api'
    location: location
    environmentId: containerAppsEnvironment.outputs.id
    containerImage: '${containerRegistry.outputs.loginServer}/work-orders-api:${imageTag}'
    targetPort: 8002
    env: []
    registryServer: containerRegistry.outputs.loginServer
    registryIdentityResourceId: registryIdentity.id
    minReplicas: 0
    maxReplicas: 3
    tags: tags
  }
  dependsOn: [
    registryPullRoleAssignment
  ]
}

output registryName string = containerRegistry.outputs.registryName
output registryLoginServer string = containerRegistry.outputs.loginServer
output registryResourceId string = containerRegistry.outputs.id
output registryPullIdentityId string = registryIdentity.id
output inventoryMcpUrl string = deployApps
  ? 'https://${inventoryMcp!.outputs.fqdn}/mcp?api-version=v1'
  : ''
output workOrdersApiUrl string = deployApps
  ? 'https://${workOrdersApi!.outputs.fqdn}'
  : ''
