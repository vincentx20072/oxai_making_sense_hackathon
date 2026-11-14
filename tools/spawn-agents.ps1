<#
Launch several Claude Code agents in parallel, each on its own task, in its own
git worktree and branch. They can't overwrite each other's files. Their work
reaches branch `Vincent` only through temp/ + a second agent's APPROVED_BY +
tools/promote.py (see temp/README.md).

Run from the repo root (on Windows PowerShell 5+ or pwsh 7):

  .\tools\spawn-agents.ps1 -Person vincent -Tasks T08,T10          # build agents
  .\tools\spawn-agents.ps1 -Person vincent -Review                 # one reviewer agent
  .\tools\spawn-agents.ps1 -Person vincent -Tasks T03 -Mode bypassPermissions
  .\tools\spawn-agents.ps1 -List                                   # show tasks
  .\tools\spawn-agents.ps1 -Cleanup                                # remove worktrees with no changes

Each agent gets ..\<repo>-agents\<task>-<slug>\ on branch <person>/claude/<slug>,
cut from origin/Vincent, plus a .agent-prompt.md (git-excluded). A new window
(Windows Terminal tab when `wt` exists, else a PowerShell window) runs:
  claude --permission-mode <Mode> "Read .agent-prompt.md and follow it exactly."

Needs: git, gh (logged in), claude (Claude Code CLI) on PATH, and GROQ_API_KEY
in the environment if the task runs the agent.
#>
param(
  [string]$Person = "vincent",
  [string[]]$Tasks = @(),
  [switch]$Review,
  [switch]$List,
  [switch]$Cleanup,
  [ValidateSet("default", "acceptEdits", "plan", "bypassPermissions")]
  [string]$Mode = "acceptEdits"
)

$ErrorActionPreference = "Stop"
$root = (git rev-parse --show-toplevel).Trim()
Set-Location $root
$cfg = Get-Content (Join-Path $root "tools\tasks.json") -Raw | ConvertFrom-Json
$base = $cfg.integration_branch
$agentsDir = Join-Path (Split-Path $root -Parent) ((Split-Path $root -Leaf) + "-agents")

if ($List) {
  $cfg.tasks | ForEach-Object {
    "{0}  {1,-18} {2,-5} deps:{3,-12} {4}" -f $_.id, $_.slug, $_.role, (($_.depends) -join ","), $_.title
  }
  return
}

if ($Cleanup) {
  git worktree list --porcelain | Select-String "^worktree " | ForEach-Object {
    $wt = $_.Line.Substring(9)
    if ($wt -like "$agentsDir*") {
      $dirty = git -C $wt status --porcelain
      $ahead = git -C $wt rev-list --count "origin/$base..HEAD"
      if (-not $dirty -and $ahead -eq "0") { git worktree remove $wt; "removed $wt" }
      else { "kept $wt (uncommitted or unpushed work)" }
    }
  }
  return
}

# The prompt for each window lives in a file, so quotes and newlines survive Start-Process.
$exclude = Join-Path (git rev-parse --git-common-dir).Trim() "info\exclude"
if (-not (Select-String -Path $exclude -Pattern "^\.agent-prompt\.md$" -Quiet -ErrorAction SilentlyContinue)) {
  Add-Content $exclude ".agent-prompt.md"
}
git fetch origin $base | Out-Null
New-Item -ItemType Directory -Force $agentsDir | Out-Null

function Start-AgentWindow($dir, $title) {
  $cmd = "claude --permission-mode $Mode `"Read .agent-prompt.md and follow it exactly.`""
  if (Get-Command wt -ErrorAction SilentlyContinue) {
    Start-Process wt -ArgumentList @("-w", "0", "new-tab", "--title", $title, "-d", $dir,
      "powershell", "-NoExit", "-Command", $cmd)
  } else {
    Start-Process powershell -WorkingDirectory $dir -ArgumentList @("-NoExit", "-Command",
      "`$host.UI.RawUI.WindowTitle='$title'; $cmd")
  }
}

function New-AgentWorktree($name, $branch) {
  $dir = Join-Path $agentsDir $name
  if (-not (Test-Path $dir)) {
    $exists = git ls-remote --heads origin $branch
    if ($exists) { git fetch origin $branch | Out-Null; git worktree add $dir -B $branch "origin/$branch" | Out-Null }
    else { git worktree add $dir -b $branch "origin/$base" | Out-Null }
  }
  return $dir
}

$common = @"
Rules for every agent on this team (Jenga: Cited but Not Used?, OxAI Making Sense Hackathon):
- Read docs/PLAN.md (sections 4, 5, 8) and temp/README.md first. Never re-decide what they settle.
- Hard close 00:00 Sun 15 Nov (Europe/London); feature freeze 22:30. Stop at 22:30 unless told otherwise.
- Never edit project files directly. Stage your change in temp/<task-id>-<slug>/ with MANIFEST.json, PROPOSAL.md
  and full copies of the files, exactly as temp/README.md says. Only files in your task scope.
- contracts.py changes need Vincent's agent to approve.
- Never commit secrets, .env, __pycache__ or real people's CVs. Never push to $base or main directly.
- Run before pushing: $($cfg.test_cmd)
- Do not ask the human questions. If blocked, write BLOCKED.md in your temp folder, commit, push and stop.
"@

if ($Review) {
  $dir = New-AgentWorktree "review-$Person" "$Person/claude/review"
  @"
You are the REVIEWER agent (identity $Person.$env:COMPUTERNAME.claude-review). You approve or reject staged work.

$common

Loop until 22:30 or until nothing is left to review, checking every 5 minutes:
1. gh pr list --base $base --state open --json number,title,headRefName,author
2. For each PR whose branch contains temp/<task>/MANIFEST.json without APPROVED_BY, and whose author
   identity (MANIFEST.json "author") is not yours:
   a. gh pr checkout <number> (in this worktree), then: python tools/promote.py --check temp/<folder>
   b. Read PROPOSAL.md and the diff against $base. Check it stays in the task scope from tools/tasks.json,
      matches contracts.py, and that the tests pass: $($cfg.test_cmd)
   c. If you agree: write "$Person.$env:COMPUTERNAME.claude-review" into temp/<folder>/APPROVED_BY, commit
      "APPROVE <task>: <reason>", push, then run python tools/promote.py temp/<folder>, commit the promotion
      with the message it prints, push, and gh pr comment <number> --body "APPROVE <task>: <reason>; promoted".
   d. If you don't: gh pr comment <number> --body "CHANGES <task>: <specific fixes>". Do not approve.
3. Never approve your own work. Never edit the proposed files yourself.
"@ | Set-Content (Join-Path $dir ".agent-prompt.md") -Encoding UTF8
  Start-AgentWindow $dir "review"
  "reviewer -> $dir"
}

foreach ($id in $Tasks) {
  $t = $cfg.tasks | Where-Object { $_.id -eq $id }
  if (-not $t) { Write-Warning "unknown task $id (see -List)"; continue }
  $name = "$($t.id)-$($t.slug)"
  $branch = "$Person/claude/$($t.slug)"
  $dir = New-AgentWorktree $name $branch
  @"
You are an autonomous build agent. Identity: $Person.$env:COMPUTERNAME.claude-$($t.slug). Branch: $branch (already checked out).

$common

Your task: $($t.id) $($t.title)
Scope (the only project paths you may stage): $(($t.scope) -join ", ")
Depends on: $(if ($t.depends) { ($t.depends) -join ", " } else { "nothing" }). If a dependency isn't promoted into $base yet,
build against the stub/contract in contracts.py and say so in PROPOSAL.md.
Done when: $($t.done_when)

Steps:
1. git fetch origin; git rebase origin/$base. Read the files in your scope.
2. Implement in place to test, then copy the final versions into temp/$name/ with MANIFEST.json
   (author = your identity above; tests = the narrowest command that checks your task) and PROPOSAL.md
   (what, why, test output). Then git restore every project file so only temp/$name/ differs from $base.
3. Run python tools/promote.py --check temp/$name. It should refuse only with 'not approved' (that's expected).
4. Commit, git push -u origin $branch, then:
   gh pr create --base $base --title "$($t.id): $($t.title)" --body "Staged in temp/$name. Needs approval from a second agent."
5. Finish with a 5-line report: task, branch, PR URL, test result, open issues.
"@ | Set-Content (Join-Path $dir ".agent-prompt.md") -Encoding UTF8
  Start-AgentWindow $dir $t.id
  "$id -> $dir ($branch)"
}

if (-not $Review -and $Tasks.Count -eq 0) {
  "Nothing to do. Use -Tasks T08,T10 and/or -Review, or -List."
}
