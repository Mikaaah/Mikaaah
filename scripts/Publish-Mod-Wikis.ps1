#Requires -Version 5.1
<#
.SYNOPSIS
Synchronizes source-controlled mod documentation to the three native GitHub Wiki repositories.
.DESCRIPTION
Reads each project's wiki/*.md files from GitHub, adapts relative Markdown links for
GitHub Wiki routing, and pushes only those Markdown pages to the project's .wiki.git remote.
Other wiki pages are not deleted. No mod code, tags, registry IDs or releases are changed.
Requires Git, GitHub CLI, permission to write the destination wikis, and initialized wikis.
.EXAMPLE
powershell -File .\Publish-Mod-Wikis.ps1
.EXAMPLE
powershell -File .\Publish-Mod-Wikis.ps1 -Apply
#>
[CmdletBinding()]
param([switch]$Apply)

$ErrorActionPreference = 'Stop'
$owner = 'Mikaaah'
$mods = @(
  'Resonant-Arcana',
  'Terraforged-Alexs-Caves-Fix',
  'UltraTerraForged-Streams-Currents'
)

foreach ($command in @('git', 'gh')) {
  if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
    throw "Required command missing: $command"
  }
}
gh auth status --hostname github.com
if ($LASTEXITCODE -ne 0) {
  throw "Sign in first: gh auth login"
}
if (-not $Apply) {
  Write-Host "Dry run: documentation source exists under wiki/ in each mod repository."
  Write-Host "To publish the three GitHub Wikis, rerun with -Apply."
  Write-Host "A GitHub Wiki must have its first Home page created in the GitHub UI before cloning."
  return
}
gh auth setup-git
if ($LASTEXITCODE -ne 0) { throw 'Could not configure GitHub authentication for git.' }

foreach ($mod in $mods) {
  $temp = Join-Path ([System.IO.Path]::GetTempPath()) ("mod-wiki-sync-" + [guid]::NewGuid().ToString('N'))
  $source = Join-Path $temp 'source'
  $dest = Join-Path $temp 'destination'
  New-Item -ItemType Directory -Path $temp -Force | Out-Null
  try {
    Write-Host "Synchronizing $owner/$mod..." -ForegroundColor Cyan
    & git clone --quiet --depth 1 "https://github.com/$owner/$mod.git" $source
    if ($LASTEXITCODE -ne 0) { throw "Could not clone source: $mod" }

    & git clone --quiet "https://github.com/$owner/$mod.wiki.git" $dest
    if ($LASTEXITCODE -ne 0) {
      throw "Wiki not initialized or inaccessible: https://github.com/$owner/$mod/wiki . Create the first Home page from GitHub's Wiki tab and retry."
    }

    $sourceDir = Join-Path $source 'wiki'
    $pages = @(Get-ChildItem -LiteralPath $sourceDir -File -Filter '*.md')
    if (-not $pages.Count) { throw "No Markdown pages in $sourceDir" }

    foreach ($page in $pages) {
      $content = [System.IO.File]::ReadAllText($page.FullName)
      # GitHub Wiki URLs omit the .md suffix; keep source links usable in GitHub's code view.
      $content = [regex]::Replace($content, '\]\(([A-Za-z0-9_-]+)\.md(#[^)]*)?\)', {
        param($match)
        '](' + $match.Groups[1].Value + $match.Groups[2].Value + ')'
      })
      [System.IO.File]::WriteAllText(
        (Join-Path $dest $page.Name),
        $content,
        [System.Text.UTF8Encoding]::new($false)
      )
    }

    & git -C $dest add -- '*.md'
    if ($LASTEXITCODE -ne 0) { throw 'Git staging failed.' }
    & git -C $dest diff --cached --quiet
    if ($LASTEXITCODE -eq 0) {
      Write-Host "Wiki for $mod is already up to date."
      continue
    }

    & git -C $dest commit -m 'docs: synchronize mod wiki pages from main repository'
    if ($LASTEXITCODE -ne 0) { throw 'Git commit failed; check git user.name/user.email.' }
    & git -C $dest push origin HEAD
    if ($LASTEXITCODE -ne 0) { throw 'Wiki push failed; check write access.' }
    Write-Host "Published: https://github.com/$owner/$mod/wiki" -ForegroundColor Green
  }
  finally {
    if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Recurse -Force }
  }
}

Write-Host "Wiki sync complete. Review the three Wiki tabs for rendered Markdown and navigation."
