# Role assignments the system needs. Run once, after `az login` (writes need an MFA-authenticated sign-in:
# `az logout; az login` and complete the prompt first). Idempotent: existing assignments are reported, not duplicated.
param(
    [Parameter(Mandatory)][string]$Subscription,
    [Parameter(Mandatory)][string]$ResourceGroup,
    [Parameter(Mandatory)][string]$SearchService,      # e.g. ais-research-agentic-poc
    [Parameter(Mandatory)][string]$FoundryAccount,     # e.g. research-foundry-proj-resource
    [Parameter(Mandatory)][string]$FoundryProject,     # e.g. research-foundry-proj
    [Parameter(Mandatory)][string]$StorageAccount      # the account that holds the store container
)
$ErrorActionPreference = "Stop"
$me = az ad signed-in-user show --query id -o tsv
$searchId = "/subscriptions/$Subscription/resourceGroups/$ResourceGroup/providers/Microsoft.Search/searchServices/$SearchService"
$accountId = "/subscriptions/$Subscription/resourceGroups/$ResourceGroup/providers/Microsoft.CognitiveServices/accounts/$FoundryAccount"
$storageId = "/subscriptions/$Subscription/resourceGroups/$ResourceGroup/providers/Microsoft.Storage/storageAccounts/$StorageAccount"

# The search service needs a managed identity: to call the embedding deployment (vectorizer), to verbalise images
# (Word arm) and to read raw/ in the store (Blob knowledge source).
$searchMi = az search service update -g $ResourceGroup -n $SearchService --identity-type SystemAssigned --query identity.principalId -o tsv
# The project managed identity authenticates the MCP connection from the agent to Azure AI Search.
$projectMi = az rest --method get --url "https://management.azure.com$accountId/projects/$FoundryProject`?api-version=2025-06-01" --query identity.principalId -o tsv

function Grant($principal, $type, $role, $scope) {
    Write-Host "$role -> $principal on $($scope.Split('/')[-1])"
    az role assignment create --assignee-object-id $principal --assignee-principal-type $type --role $role --scope $scope -o none
}

# You, on the store: upload Word files, write Markdown+, assets, facts, chunks, evaluation runs
Grant $me User "Storage Blob Data Contributor" $storageId
# You, on the search service: create objects, load data, read
Grant $me User "Search Service Contributor" $searchId
Grant $me User "Search Index Data Contributor" $searchId
Grant $me User "Search Index Data Reader" $searchId
# You, on the Foundry resource: use models, create agents and the project connection
Grant $me User "Foundry User" $accountId
Grant $me User "Foundry Project Manager" $accountId
# Project managed identity: read the knowledge bases through the MCP tool
Grant $projectMi ServicePrincipal "Search Index Data Reader" $searchId
# Search service managed identity: models for the vectorizer and image verbalisation, and the Word files in the store
Grant $searchMi ServicePrincipal "Cognitive Services User" $accountId
Grant $searchMi ServicePrincipal "Cognitive Services OpenAI User" $accountId
Grant $searchMi ServicePrincipal "Storage Blob Data Reader" $storageId
Write-Host "Done. Role assignments can take a few minutes to take effect."
Write-Host "STORAGE_RESOURCE_ID=$storageId"
