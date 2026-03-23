param(
    [string]$Name = "Host Player",
    [int]$Port = 43851,
    [Nullable[int]]$Seed = $null
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

if ($null -ne $Seed) {
    python .\app.py --host --name $Name --port $Port --seed $Seed
}
else {
    python .\app.py --host --name $Name --port $Port
}
