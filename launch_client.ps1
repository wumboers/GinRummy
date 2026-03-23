param(
    [Parameter(Mandatory = $true)][string]$Host,
    [string]$Name = "Guest Player",
    [int]$Port = 43851,
    [string]$Password = ""
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir
$argsList = @(".\app.py", "--join", $Host, "--name", $Name, "--port", $Port)
if ($Password -ne "") {
    $argsList += @("--password", $Password)
}
python @argsList
