$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$meterlessPath = Join-Path $repoRoot '.tools/meterless'
$pin = '1742dec56a66e58eef9dda2d2b865823758ca8cd'
if (-not (Test-Path -LiteralPath $meterlessPath)) {
    git clone --filter=blob:none --sparse https://github.com/Meterless/Meterless.git $meterlessPath
    if ($LASTEXITCODE -ne 0) { throw 'Meterless clone failed' }
}
git -C $meterlessPath fetch origin $pin --depth=1
if ($LASTEXITCODE -ne 0) { throw 'Meterless pinned fetch failed' }
git -C $meterlessPath checkout --detach $pin
if ($LASTEXITCODE -ne 0) { throw 'Meterless pinned checkout failed; preserve local changes before retrying' }
git -C $meterlessPath sparse-checkout set --no-cone '/engines/world-model/reference/' '/engines/world-model/conformance/' '/engines/world-model/AGENTS.md' '/engines/world-model/README.md' '/engines/world-model/LICENSE' '/LICENSE'
if ($LASTEXITCODE -ne 0) { throw 'Meterless sparse checkout failed' }
Push-Location (Join-Path $meterlessPath 'engines/world-model/reference')
try {
    npm ci --ignore-scripts --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw 'Meterless npm setup failed' }
} finally { Pop-Location }
Write-Output "Meterless World Model ready at pinned commit $pin"
