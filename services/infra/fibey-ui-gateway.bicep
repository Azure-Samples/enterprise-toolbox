targetScope = 'resourceGroup'

@minLength(1)
@maxLength(32)
@description('Prefix used by the existing Fibey Container Apps resources.')
param environmentName string

@description('Name of the existing Azure Container Registry.')
param registryName string

@description('Name of the existing Container Apps environment.')
param containerAppsEnvironmentName string

@description('Name of the existing user-assigned identity with AcrPull on the registry.')
param registryIdentityName string

@description('Name of the existing Microsoft Foundry account and project.')
param foundryAccountName string
param foundryProjectName string

@description('Microsoft Foundry project endpoint used by the existing hosted agent.')
param hostedAgentEndpoint string

@description('Name of the existing hosted agent to invoke.')
param hostedAgentName string = 'fibey-agent'

@description('Unique image tag shared by the UI and gateway images.')
param imageTag string

var tags = {
  'azd-env-name': environmentName
  'service-lifecycle': 'fibey-ui-gateway'
}

var foundryAgentConsumerRoleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  'eed3b665-ab3a-47b6-8f48-c9382fb1dad6'
)

// The provider advertises these API versions before they are present in Bicep's type catalog.
#disable-next-line BCP081
resource registry 'Microsoft.ContainerRegistry/registries@2026-10-15-preview' existing = {
  name: registryName
}

resource containerAppsEnvironment 'Microsoft.App/managedEnvironments@2026-07-01' existing = {
  name: containerAppsEnvironmentName
}

resource registryIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2025-05-31-preview' existing = {
  name: registryIdentityName
}

#disable-next-line BCP081
resource foundryAccount 'Microsoft.CognitiveServices/accounts@2026-11-15-preview' existing = {
  name: foundryAccountName
}

#disable-next-line BCP081
resource foundryProject 'Microsoft.CognitiveServices/accounts/projects@2026-11-15-preview' existing = {
  parent: foundryAccount
  name: foundryProjectName
}

module gateway 'modules/container-app.bicep' = {
  name: 'fibey-gateway'
  params: {
    name: '${environmentName}-gateway'
    serviceName: 'fibey-gateway'
    location: resourceGroup().location
    environmentId: containerAppsEnvironment.id
    containerImage: '${registry.properties.loginServer}/fibey-gateway:${imageTag}'
    targetPort: 8000
    env: [
      {
        name: 'AGENT_MODE'
        value: 'hosted'
      }
      {
        name: 'HOSTED_AGENT_ENDPOINT'
        value: hostedAgentEndpoint
      }
      {
        name: 'HOSTED_AGENT_NAME'
        value: hostedAgentName
      }
    ]
    registryServer: registry.properties.loginServer
    registryIdentityResourceId: registryIdentity.id
    minReplicas: 0
    maxReplicas: 3
    readinessPath: '/api/health'
    externalIngress: false
    enableSystemAssignedIdentity: true
    tags: tags
  }
}

#disable-next-line BCP081
resource foundryAgentConsumerAssignment 'Microsoft.Authorization/roleAssignments@2026-08-01-preview' = {
  name: guid(
    resourceId('Microsoft.App/containerApps', '${environmentName}-gateway'),
    foundryProject.id,
    foundryAgentConsumerRoleDefinitionId
  )
  scope: foundryProject
  properties: {
    roleDefinitionId: foundryAgentConsumerRoleDefinitionId
    principalId: gateway.outputs.principalId
    principalType: 'ServicePrincipal'
  }
}

module ui 'modules/container-app.bicep' = {
  name: 'fibey-ui'
  params: {
    name: '${environmentName}-ui'
    serviceName: 'fibey-ui'
    location: resourceGroup().location
    environmentId: containerAppsEnvironment.id
    containerImage: '${registry.properties.loginServer}/fibey-ui:${imageTag}'
    targetPort: 80
    env: [
      {
        name: 'GATEWAY_URL'
        value: 'https://${gateway.outputs.fqdn}/api/'
      }
      {
        name: 'GATEWAY_HOST'
        value: gateway.outputs.fqdn
      }
    ]
    registryServer: registry.properties.loginServer
    registryIdentityResourceId: registryIdentity.id
    minReplicas: 0
    maxReplicas: 3
    readinessPath: '/'
    externalIngress: true
    tags: tags
  }
  dependsOn: [
    foundryAgentConsumerAssignment
  ]
}

output uiUrl string = 'https://${ui.outputs.fqdn}'
output gatewayInternalUrl string = 'https://${gateway.outputs.fqdn}'
output gatewayPrincipalId string = gateway.outputs.principalId
