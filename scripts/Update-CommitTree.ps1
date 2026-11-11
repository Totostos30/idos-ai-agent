# Rebuild the local commit-tree data used by commit-tree/index.html.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$outFile = Join-Path $Root "commit-tree\commits.json"
$gitSafe = "safe.directory=$Root"

try {
  $records = @(git -c $gitSafe log --all --date=iso --pretty=format:"%H%x1f%h%x1f%P%x1f%an%x1f%ad%x1f%s%x1e")
  if ($LASTEXITCODE -ne 0) { throw "git log failed with exit code $LASTEXITCODE" }

  $refs = @{}
  $branches = @()
  @(git -c $gitSafe for-each-ref '--format=%(refname:short)%09%(objectname)' refs/heads refs/remotes) | ForEach-Object {
    $parts = $_ -split "`t", 2
    if ($parts.Count -eq 2) {
      $branches += [ordered]@{ name = $parts[0]; tip = $parts[1] }
      if (-not $refs.ContainsKey($parts[1])) { $refs[$parts[1]] = @() }
      $refs[$parts[1]] += $parts[0]
    }
  }

  $commits = @(
    ($records -join "") -split ([char]0x1e) | Where-Object { $_.Trim() } | ForEach-Object {
      $parts = $_ -split ([char]0x1f), 6
      if ($parts.Count -lt 6) { return }
      $hash = $parts[0]
      [ordered]@{
        hash = $hash
        short = $parts[1]
        parents = @($parts[2] -split " " | Where-Object { $_ })
        author = $parts[3]
        date = $parts[4]
        subject = $parts[5]
        refs = if ($refs.ContainsKey($hash)) { @($refs[$hash]) } else { @() }
      }
    }
  )

  $payload = [ordered]@{
    generatedAt = (Get-Date).ToUniversalTime().ToString("o")
    repository = Split-Path -Leaf $Root
    branches = @($branches | Sort-Object name)
    commits = $commits
  }
  $json = $payload | ConvertTo-Json -Depth 8
  $dir = Split-Path -Parent $outFile
  New-Item -ItemType Directory -Force -Path $dir | Out-Null
  $tmp = "$outFile.tmp"
  [IO.File]::WriteAllText($tmp, $json, [Text.UTF8Encoding]::new($false))
  Move-Item -Force $tmp $outFile
  Write-Host "Commit tree updated: $($commits.Count) commits -> $outFile"
} catch {
  Write-Warning "Could not update commit tree: $($_.Exception.Message)"
  exit 0
}
