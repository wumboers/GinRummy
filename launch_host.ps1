param(
    [string]$Name = "Host Player",
    [int]$Port = 43851,
    [string]$Password = "",
    [switch]$AI,
    [Nullable[int]]$Seed = $null
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

$argsList = @(".\app.py", "--host", "--name", $Name, "--port", $Port)
if ($Password -ne "") {
    $argsList += @("--password", $Password)
}
if ($AI) {
    $argsList += "--ai"
}
if ($null -ne $Seed) {
    $argsList += @("--seed", $Seed)
}
python @argsList
