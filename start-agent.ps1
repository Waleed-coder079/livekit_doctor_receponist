param(
    [ValidateSet("console","dev","start")]
    [string]$Mode = "console"
)

$ErrorActionPreference = "Stop"

# Resolve venv Python if available, otherwise fall back to 'python'
try {
    $venvPythonPath = Join-Path $PSScriptRoot "..\venv\Scripts\python.exe"
    if (Test-Path $venvPythonPath) {
        $python = (Resolve-Path $venvPythonPath).Path
    } else {
        $python = "python"
    }
} catch {
    $python = "python"
}

Push-Location $PSScriptRoot
try {
    & $python .\main.py $Mode
} finally {
    Pop-Location
}