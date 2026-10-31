# Runs docs/RUNBOOK.md steps 1 to 6 in order (roles in step 0 are scripts/setup_access.ps1, run once before).
# Stops at the first failing command. Everything is also written to runbook-<timestamp>.log, which is what to send
# back for review. Needs .env filled in and an `az login` session.
#
#   .\scripts\runbook.ps1              steps 1-6 with the 10-question smoke evaluation (about 15 minutes)
#   .\scripts\runbook.ps1 -Full        also the full evaluation, both arms, both modes (add 30-40 minutes)
#   .\scripts\runbook.ps1 -From 3      resume from a step (1 upload, 2 ingest, 3 index, 4 knowledge+agents, 5 ask, 6 eval)
param(
    [switch]$Full,
    [int]$From = 1,
    [int]$WordIngestWaitMinutes = 5
)
$ErrorActionPreference = "Stop"
$log = "runbook-$(Get-Date -Format yyyyMMdd-HHmmss).log"
Start-Transcript -Path $log | Out-Null

function Step($title) { Write-Host "`n=== $title" -ForegroundColor Cyan }
function Run([string[]]$cmd) {
    Write-Host ("> uv run python -m research_qa " + ($cmd -join " ")) -ForegroundColor DarkGray
    & uv run python -m research_qa @cmd
    if ($LASTEXITCODE -ne 0) { Stop-Transcript | Out-Null; throw "stopped: '$($cmd[0])' exited with $LASTEXITCODE (log: $log)" }
}

if ($From -le 1) {
    Step "1. Upload the Word files into raw/ in the store"
    $files = Get-ChildItem data\raw\*.docx | ForEach-Object { $_.FullName }
    if (-not $files) { throw "no .docx files in data\raw" }
    Run (@("upload") + $files)
    Run @("status")
}
if ($From -le 2) {
    Step "2. Ingest: Word -> Markdown+, pictures described, facts, chunks (3-5 minutes the first time)"
    Run @("ingest")
    Step "2b. Retention: evidence must be retained for every answerable question, lost must be 0"
    Run @("retention")
}
if ($From -le 3) {
    Step "3. Markdown+ index: embed and push the chunks (about 2 minutes)"
    Run @("index")
}
if ($From -le 4) {
    Step "4. Knowledge sources and bases (the Word arm starts ingesting raw/ inside Azure)"
    Run @("knowledge")
    Step "4b. Agents"
    Run @("agents")
    Write-Host "waiting $WordIngestWaitMinutes minutes for the Word arm's Azure-side ingestion" -ForegroundColor DarkGray
    Start-Sleep -Seconds (60 * $WordIngestWaitMinutes)
    Run @("status")
}
if ($From -le 5) {
    Step "5. First answers (expected: -4.7% of GDP via lookup_facts; the three cans from the description; ECB cut then hold)"
    Run @("ask", "In Fig 27 of the April Money Map, what is Germany's cyclically adjusted government balance forecast for 2030?", "--arm", "markdown-plus", "--mode", "agent")
    Run @("ask", "In Fig 27 of the April Money Map, what is Germany's cyclically adjusted government balance forecast for 2030?", "--arm", "word", "--mode", "agent")
    Run @("ask", "Which three cans are shown side by side in Fig 22, and what is printed on them?", "--arm", "markdown-plus", "--mode", "agent")
    Run @("ask", "What did the Money Map expect the ECB to do at its next meeting in April and in July 2025?", "--arm", "markdown-plus", "--mode", "direct")
    Step "5b. One raw Word-arm answer with verbose logging (shows the field names Azure stores for its chunks)"
    Run @("-v", "ask", "How many production lines does the Wakefield plant have?", "--arm", "word", "--mode", "direct")
}
if ($From -le 6) {
    Step "6. Smoke evaluation: 10 questions, Markdown+, agent (about 2 minutes; 9 of 10 or better expected)"
    Run @("eval", "--arm", "markdown-plus", "--mode", "agent", "--limit", "10", "--run", "smoke")
    if ($Full) {
        Step "6b. Full evaluation: both arms, both modes (30-40 minutes; resume with: eval --resume --run baseline-1)"
        Run @("eval", "--arm", "word,markdown-plus", "--mode", "agent,direct", "--run", "baseline-1")
    }
}
Step "Done"
Write-Host "Send back: $log" -ForegroundColor Green
Write-Host "Demo: uv run streamlit run research_qa/app/Overview.py"
Stop-Transcript | Out-Null
