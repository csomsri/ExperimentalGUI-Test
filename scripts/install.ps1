[CmdletBinding()]
param(
    [string]$PythonExecutable,
    [string]$VenvPath = '.venv'
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
if (-not [IO.Path]::IsPathRooted($VenvPath)) { $VenvPath = Join-Path $projectRoot $VenvPath }
$venvPython = Join-Path $VenvPath 'Scripts/python.exe'

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $Executable $Arguments" }
}
function Get-ProjectPython {
    param([string]$Requested)
    if ($Requested) { return $Requested }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($version in @('-3.13', '-3.12', '-3.11')) {
            try {
                $resolvedPython = & py $version -c 'import sys; print(sys.executable)' 2>$null
                if ($LASTEXITCODE -eq 0 -and $resolvedPython) { return ([string]$resolvedPython).Trim() }
            } catch { }
        }
    }
    $candidates = @()
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand -and $pythonCommand.Source -notlike '*\WindowsApps\*') {
        $candidates += $pythonCommand.Source
    }
    $localRoots = @($env:LOCALAPPDATA)
    if ($env:USERPROFILE) { $localRoots += Join-Path $env:USERPROFILE 'AppData/Local' }
    foreach ($localRoot in ($localRoots | Select-Object -Unique)) {
        if (-not $localRoot) { continue }
        $pythonInstallRoot = Join-Path $localRoot 'Programs/Python'
        if (Test-Path -LiteralPath $pythonInstallRoot) {
            $candidates += Get-ChildItem -LiteralPath $pythonInstallRoot -Directory -Filter 'Python3*' |
                Sort-Object Name -Descending | ForEach-Object { Join-Path $_.FullName 'python.exe' }
        }
    }
    foreach ($candidate in $candidates) {
        if (-not (Test-Path -LiteralPath $candidate)) { continue }
        try {
            & $candidate -c "import sys,struct; sys.exit(0 if (3,11) <= sys.version_info[:2] < (3,14) and struct.calcsize('P') == 8 else 1)" 2>$null
            if ($LASTEXITCODE -eq 0) { return $candidate }
        } catch { }
    }
    throw 'Install 64-bit Python 3.11-3.13, or pass -PythonExecutable with its full path.'
}
$versionCheck = "import sys,struct; assert (3,11) <= sys.version_info[:2] < (3,14) and struct.calcsize('P') == 8, 'Use 64-bit Python 3.11-3.13'"
if (-not (Test-Path -LiteralPath $venvPython)) {
    if (Test-Path -LiteralPath $VenvPath) { throw 'Existing venv is incomplete or belongs to Ubuntu. Choose a different -VenvPath; it will not be overwritten.' }
    $selectedPython = Get-ProjectPython -Requested $PythonExecutable
    Invoke-Checked -Executable $selectedPython -Arguments @('-c', $versionCheck)
    Invoke-Checked -Executable $selectedPython -Arguments @('-m', 'venv', $VenvPath)
}
Invoke-Checked -Executable $venvPython -Arguments @('-c', $versionCheck)
. (Join-Path $VenvPath 'Scripts/Activate.ps1')
Invoke-Checked -Executable $venvPython -Arguments @('-m', 'pip', 'install', '--upgrade', 'pip')
Invoke-Checked -Executable $venvPython -Arguments @('-m', 'pip', 'install', '-r', (Join-Path $projectRoot 'requirements.txt'))
Invoke-Checked -Executable $venvPython -Arguments @('-m', 'pip', 'check')
Invoke-Checked -Executable $venvPython -Arguments @((Join-Path $projectRoot 'main.py'), '--check')
Write-Host 'Installation complete. Start with .\scripts\run.ps1.'
Write-Host "To activate in your current shell: . '$VenvPath\Scripts\Activate.ps1'"
