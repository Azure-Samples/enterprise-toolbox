@description('Name of the Container App.')
param name string

@description('azd service name associated with the Container App.')
param serviceName string

@description('Azure region for the Container App.')
param location string

@description('Resource ID of the Container Apps environment.')
param environmentId string

@description('Container image to deploy.')
param containerImage string

@description('Ingress target port for the container.')
param targetPort int

@description('Environment variables to inject into the container.')
param env array

@description('Container registry server used for pulling the image.')
param registryServer string

@description('User-assigned managed identity resource ID used to pull images from ACR.')
param registryIdentityResourceId string

@description('Minimum replica count for the Container App.')
param minReplicas int = 1

@description('Maximum replica count for the Container App.')
param maxReplicas int = 3

@description('Readiness endpoint exposed by the container.')
param readinessPath string = '/health'

@description('Whether the Container App is reachable from outside its Container Apps environment.')
param externalIngress bool = true

@description('Whether to attach a system-assigned managed identity in addition to the registry identity.')
param enableSystemAssignedIdentity bool = false

@description('Tags applied to the Container App.')
param tags object = {}

var resolvedContainerImage = empty(containerImage) ? 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest' : containerImage
var appTags = union(tags, {
  'azd-service-name': serviceName
})

resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: name
  location: location
  tags: appTags
  identity: {
    type: enableSystemAssignedIdentity ? 'SystemAssigned,UserAssigned' : 'UserAssigned'
    userAssignedIdentities: {
      '${registryIdentityResourceId}': {}
    }
  }
  properties: {
    managedEnvironmentId: environmentId
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: externalIngress
        targetPort: targetPort
        transport: 'auto'
      }
      registries: [
        {
          server: registryServer
          identity: registryIdentityResourceId
        }
      ]
    }
    template: {
      containers: [
        {
          name: serviceName
          image: resolvedContainerImage
          env: env
          probes: [
            {
              type: 'Readiness'
              httpGet: {
                path: readinessPath
                port: targetPort
                scheme: 'HTTP'
              }
              initialDelaySeconds: 5
              periodSeconds: 10
            }
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
      }
    }
  }
}

@description('Resource ID of the Container App.')
output id string = containerApp.id

@description('Fully qualified domain name of the Container App ingress endpoint.')
output fqdn string = containerApp.properties.configuration.ingress.fqdn

@description('Principal ID of the Container App system-assigned managed identity.')
output principalId string = enableSystemAssignedIdentity ? containerApp.identity.principalId! : ''
