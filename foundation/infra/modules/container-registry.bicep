@description('Name of the Azure Container Registry.')
param name string

@description('Azure region for the Azure Container Registry.')
param location string

@description('Tags applied to the Azure Container Registry.')
param tags object = {}

resource containerRegistry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
  }
}

@description('Resource ID of the Azure Container Registry.')
output id string = containerRegistry.id

@description('Name of the Azure Container Registry.')
output registryName string = containerRegistry.name

@description('Login server for the Azure Container Registry.')
output loginServer string = containerRegistry.properties.loginServer
