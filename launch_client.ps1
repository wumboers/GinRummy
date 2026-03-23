param(
    [Parameter(Mandatory = $true)][string]$Host,
    [string]$Name = "Guest Player",
    [int]$Port = 43851
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir
python .\app.py --join $Host --name $Name --port $Port
