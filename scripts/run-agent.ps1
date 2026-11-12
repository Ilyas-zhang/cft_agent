<#
.SYNOPSIS
  Run the CTF agent container with the host Docker socket and project mount.

.DESCRIPTION
  The container talks to the host Docker daemon to create challenge sandboxes.
  Bind-mount sources for those sibling containers must be host-visible paths,
  so WORKSPACE_ROOT=/work (container view) and SANDBOX_BIND_ROOT=<host path>
  are passed explicitly.

  Secrets are NOT passed on the command line. Configure them in .env
  (mounted at /work/.env) or in the host environment.

.EXAMPLES
  .\scripts\run-agent.ps1 -Challenge /work/testbed/challenges/http-easy
  .\scripts\run-agent.ps1 -NoSubmit -Challenge /work/testbed/challenges/tcp-menu
  .\scripts\run-agent.ps1 -Models "anthropic/GLM-5.1"
  .\scripts\run-agent.ps1 -BuildAgent -BuildSandbox
#>
param(
    [string]$Image = "ctf-agent:latest",
    [string]$SandboxImage = "ctf-sandbox",
    [string]$Models = "",
    [string]$Challenge = "",
    [switch]$NoSubmit,
    [switch]$BuildAgent,
    [switch]$BuildSandbox,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ExtraArgs
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$HostPath = $ProjectRoot -replace '\\', '/'

Write-Host "Project : $ProjectRoot"
Write-Host "Agent   : $Image"
Write-Host "Sandbox : $SandboxImage"

docker version --format '{{.Server.Version}}' | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Docker daemon is not reachable."
}

if ($BuildAgent) {
    Write-Host "Building agent image..."
    docker build -f (Join-Path $ProjectRoot "Dockerfile.agent") -t $Image $ProjectRoot
    if ($LASTEXITCODE -ne 0) { throw "Agent image build failed." }
}

if ($BuildSandbox) {
    Write-Host "Building sandbox image..."
    docker build -f (Join-Path $ProjectRoot "sandbox/Dockerfile.minimal") -t $SandboxImage $ProjectRoot
    if ($LASTEXITCODE -ne 0) { throw "Sandbox image build failed." }
}

docker image inspect $Image | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Agent image '$Image' not found. Run with -BuildAgent first."
}
docker image inspect $SandboxImage | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Sandbox image '$SandboxImage' not found. Run with -BuildSandbox first."
}

if (-not (Test-Path (Join-Path $ProjectRoot ".env"))) {
    Write-Warning ".env not found in $ProjectRoot — configure credentials in the host environment."
}

$RunArgs = @(
    "run", "--rm", "-i",
    "-v", "${ProjectRoot}:/work",
    "-v", "//var/run/docker.sock:/var/run/docker.sock",
    "-e", "WORKSPACE_ROOT=/work",
    "-e", "SANDBOX_BIND_ROOT=$HostPath",
    "-e", "SANDBOX_IMAGE=$SandboxImage",
    "-w", "/work",
    $Image
)

if ($Models) {
    foreach ($m in $Models.Split(",")) {
        $RunArgs += @("--models", $m.Trim())
    }
}
if ($Challenge) {
    $RunArgs += @("--challenge", $Challenge)
}
if ($NoSubmit) {
    $RunArgs += "--no-submit"
}
if ($ExtraArgs) {
    $RunArgs += $ExtraArgs
}

Write-Host "Starting agent (API keys are read from .env/env, never printed)..."
& docker @RunArgs
exit $LASTEXITCODE
