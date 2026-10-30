# Role assignments the agentic retrieval tutorial needs ("Configure access"). Run once, after `az login`.
# Writes to Azure need an MFA-authenticated sign-in: `az logout; az login` and complete the MFA prompt first.
param(
    [string]$Subscription = "cd52a23c-58ec-4915-981d-2fc1112d9871",
    [string]$ResourceGroup = "rg-sandbox-storageaccount",
    [string]$SearchService = "ais-research-agentic-poc",
    [string]$FoundryAccount = "research-foundry-proj-resource",
    [string]$FoundryProject = "research-foundry-proj"
)
$ErrorActionPreference = "Stop"
$me = az ad signed-in-user show --query id -o tsv
$searchId = "/subscriptions/$Subscription/resourceGroups/$ResourceGroup/providers/Microsoft.Search/searchServices/$SearchService"
$accountId = "/subscriptions/$Subscription/resourceGroups/$ResourceGroup/providers/Microsoft.CognitiveServices/accounts/$FoundryAccount"

# The search service needs a managed identity to call the embedding deployment (index vectorizer).
$searchMi = az search service update -g $ResourceGroup -n $SearchService --identity-type SystemAssigned --query identity.principalId -o tsv
# The project managed identity authenticates the MCP connection to Azure AI Search.
$projectMi = az rest --method get --url "https://management.azure.com$accountId/projects/$FoundryProject`?api-version=2025-06-01" --query identity.principalId -o tsv

function Grant($principal, $type, $role, $scope) {
    Write-Host "$role -> $principal on $($scope.Split('/')[-1])"
    az role assignment create --assignee-object-id $principal --assignee-principal-type $type --role $role --scope $scope -o none
}

# You, on the search service: create objects, load data, read
Grant $me User "Search Service Contributor" $searchId
Grant $me User "Search Index Data Contributor" $searchId
Grant $me User "Search Index Data Reader" $searchId
# Project managed identity: read the index through the MCP tool
Grant $projectMi ServicePrincipal "Search Index Data Reader" $searchId
# You, on the Foundry resource: use models, create agents and the project connection
Grant $me User "Foundry User" $accountId
Grant $me User "Foundry Project Manager" $accountId
# Search service managed identity: call the embedding deployment / knowledge base
Grant $searchMi ServicePrincipal "Cognitive Services User" $accountId
Grant $searchMi ServicePrincipal "Cognitive Services OpenAI User" $accountId
Write-Host "Done. Role assignments can take a few minutes to take effect."
