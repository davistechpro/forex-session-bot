# Agent login setup -- run from the forex-session-bot folder AFTER agent_review_setup.ps1:
#   cd C:\Users\devda\Downloads\forex-session-bot
#   powershell -ExecutionPolicy Bypass -File .\agent_login_setup.ps1
#
# What it does (you sign in once in the browser; nothing secret is printed):
#   1. Installs Azure CLI if missing
#   2. Finds your Foundry resource and Opus deployment
#   3. Creates the "OPT Trade Reviewer" app login + a 12-month secret
#   4. Gives it the Foundry User role on that resource only
#   5. Writes the AGENT_REVIEW / AZURE_* lines into .env
#   6. Runs the agent test
$ErrorActionPreference = "Stop"
if (-not (Test-Path .\watch.py)) { Write-Host "STOP -- run this from the forex-session-bot folder"; exit 1 }
if (-not (Test-Path .\modules\agent_reviewer.py)) { Write-Host "STOP -- run agent_review_setup.ps1 first"; exit 1 }
if (-not (Test-Path .\.env)) { Write-Host "STOP -- .env not found in this folder"; exit 1 }

function Pick($label, $items) {
    $items = @($items | Where-Object { $_ })
    if ($items.Count -eq 0) { return $null }
    if ($items.Count -eq 1) { Write-Host "$label`: $($items[0])"; return $items[0] }
    Write-Host "Several found -- pick the $label`:"
    for ($i = 0; $i -lt $items.Count; $i++) { Write-Host "  [$($i+1)] $($items[$i])" }
    $n = [int](Read-Host "Number")
    return $items[$n - 1]
}

# --- 1. Azure CLI
if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    Write-Host "Installing Azure CLI (needs ~1 GB free disk)..."
    winget install -e --id Microsoft.AzureCLI --accept-source-agreements --accept-package-agreements
    $env:Path += ";C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin;C:\Program Files (x86)\Microsoft SDKs\Azure\CLI2\wbin"
    if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
        Write-Host "STOP -- Azure CLI installed but not found. Close PowerShell, open a new one, and run this script again."; exit 1
    }
}

# --- 2. Sign in + find the Foundry resource and deployment
Write-Host "A browser window will open -- sign in with the account that owns the Foundry resource."
az login --only-show-errors | Out-Null
$tenant = az account show --query tenantId -o tsv

$subs = @(az account list --query "[?state=='Enabled'].name" -o tsv)
$sub = Pick "subscription" $subs
az account set --subscription "$sub"

$accts = @(az cognitiveservices account list --query "[?kind=='AIServices'].[name, resourceGroup, id]" -o tsv)
if ($accts.Count -eq 0) { Write-Host "STOP -- no Foundry resource found in '$sub'"; exit 1 }
$acct = Pick "Foundry resource" $accts
$parts = $acct -split "`t"
$resName, $rg, $resId = $parts[0], $parts[1], $parts[2]

$deps = @(az cognitiveservices account deployment list -g $rg -n $resName --query "[].name" -o tsv)
$opus = @($deps | Where-Object { $_ -match "opus" })
$choices = if ($opus.Count) { $opus } else { $deps }
$model = Pick "Opus deployment" $choices
if (-not $model) { Write-Host "STOP -- no model deployments on $resName"; exit 1 }

# --- 3. App login + secret
$appName = "OPT Trade Reviewer"
$appId = az ad app list --display-name $appName --query "[0].appId" -o tsv
if (-not $appId) {
    $appId = az ad app create --display-name $appName --sign-in-audience AzureADMyOrg --query appId -o tsv
    Write-Host "App '$appName' created"
} else { Write-Host "App '$appName' already exists -- reusing it" }
$sp = az ad sp list --filter "appId eq '$appId'" --query "[0].id" -o tsv
if (-not $sp) { az ad sp create --id $appId --only-show-errors | Out-Null; Start-Sleep 15 }
$secret = az ad app credential reset --id $appId --append --display-name "trade-bot" --years 1 --query password -o tsv --only-show-errors
if (-not $secret) { Write-Host "STOP -- couldn't create the app secret"; exit 1 }
Write-Host "Secret created (expires in 12 months -- put a reminder on your calendar)"

# --- 4. Role on this resource only
$granted = $false
$ErrorActionPreference = "Continue"
foreach ($role in @("Foundry User", "Azure AI User", "Cognitive Services User")) {
    az role assignment create --assignee $appId --role "$role" --scope $resId --only-show-errors 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Host "Role '$role' granted on $resName"; $granted = $true; break }
}
$ErrorActionPreference = "Stop"
if (-not $granted) { Write-Host "STOP -- couldn't assign a role. Your account may need Owner or User Access Administrator on the resource."; exit 1 }

# --- 5. Write .env (replaces these lines if they exist, keeps everything else)
$set = [ordered]@{
    "AGENT_REVIEW" = "on"; "ANTHROPIC_FOUNDRY_RESOURCE" = $resName; "AGENT_REVIEW_MODEL" = $model
    "AZURE_TENANT_ID" = $tenant; "AZURE_CLIENT_ID" = $appId; "AZURE_CLIENT_SECRET" = $secret
}
Copy-Item .env ".env.backup_$(Get-Date -Format yyyyMMdd_HHmmss)"
$lines = @(Get-Content .env | Where-Object { $_ -notmatch '^\s*(AGENT_REVIEW|ANTHROPIC_FOUNDRY_RESOURCE|ANTHROPIC_FOUNDRY_API_KEY|AGENT_REVIEW_MODEL|AZURE_TENANT_ID|AZURE_CLIENT_ID|AZURE_CLIENT_SECRET)\s*=' })
$lines += ""
$lines += "# Trade Reviewer agent (Foundry, Entra app login)"
foreach ($k in $set.Keys) { $lines += "$k=$($set[$k])" }
$enc = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllLines((Join-Path (Get-Location) ".env"), $lines, $enc)
$secret = $null
if (-not (Select-String -Path .gitignore -Pattern "env.backup" -Quiet)) { Add-Content .gitignore ".env.backup_*" }
Write-Host ".env updated (backup saved next to it)"

# --- 6. Test (role can take a few minutes to kick in)
Write-Host "Waiting 90 seconds for the role to take effect..."
Start-Sleep 90
py test_agent_review.py
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "If you saw 401/403: wait 5 minutes and run:  py test_agent_review.py"
}
