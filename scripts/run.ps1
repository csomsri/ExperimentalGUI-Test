[CmdletBinding()]
param(
    [string]$VenvPath = '.venv',
    [switch]$Check
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
if (-not [IO.Path]::IsPathRooted($VenvPath)) { $VenvPath = Join-Path $projectRoot $VenvPath }
$venvPython = Join-Path $VenvPath 'Scripts/python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) { throw 'Windows environment missing. Run .\scripts\install.ps1 first.' }
. (Join-Path $VenvPath 'Scripts/Activate.ps1')
$arguments = @((Join-Path $projectRoot 'main.py'))
if ($Check) { $arguments += '--check' }
& $venvPython @arguments
if ($LASTEXITCODE -ne 0) { throw "ExperimentalGUI exited with code $LASTEXITCODE" }
