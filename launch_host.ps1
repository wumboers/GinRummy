param(
    [string]$Name = "Host Player",
    [int]$Port = 43851,
    [switch]$AI,
    [Nullable[int]]$Seed = $null
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

$argsList = @(".\app.py", "--host", "--name", $Name, "--port", $Port)
if ($AI) {
    $argsList += "--ai"
}
if ($null -ne $Seed) {
    $argsList += @("--seed", $Seed)
}
python @argsList
