@description('Name of the storage account.')
param name string

@description('Azure region for the storage account.')
param location string

@description('Tags applied to the storage account.')
param tags object = {}

// The provider advertises these API versions before they are present in Bicep's type catalog.
#disable-next-line BCP081
resource storageAccount 'Microsoft.Storage/storageAccounts@2026-09-01' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
}

#disable-next-line BCP081
resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2026-09-01' = {
  name: 'default'
  parent: storageAccount
}

resource foundryIqDocsContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2026-06-01' = {
  name: 'foundry-iq-docs'
  parent: blobService
  properties: {
    publicAccess: 'None'
  }
}

@description('Resource ID of the storage account.')
output id string = storageAccount.id

@description('Name of the storage account.')
output name string = storageAccount.name

@description('Name of the blob container used for FoundryIQ documents.')
output containerName string = foundryIqDocsContainer.name
