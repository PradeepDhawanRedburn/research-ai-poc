# Runs docs/RUNBOOK.md steps 1 to 6 in order for the engine named in .env (RETRIEVAL_BACKEND=azure|langchain).
# Roles (step 0) are scripts/setup_access.ps1, run once per environment before. Safe to run on a machine that
# shares a store other machines already filled: upload is skipped when data\raw has no Word files, ingest skips
# reports that are up to date in the store, and the langchain engine only rebuilds its local Chroma collections.
# Stops at the first failing command. Everything is also written to runbook-<timestamp>.log, which is what to send
# back for review. Needs .env filled in and an `az login` session (or AZURE_OPENAI_API_KEY).
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

# the engine, from .env (the same file research_qa reads); default azure
$engine = "azure"
if (Test-Path .env) {
    $line = Get-Content .env | Where-Object { $_ -match '^\s*RETRIEVAL_BACKEND\s*=' } | Select-Object -Last 1
    if ($line) { $engine = ($line -split "=", 2)[1].Trim().Trim('"').ToLower() }
}
if ($engine -notin @("azure", "langchain")) { throw "RETRIEVAL_BACKEND in .env must be azure or langchain, got '$engine'" }
$runName = "baseline-$engine"
Write-Host "Engine: $engine (RETRIEVAL_BACKEND in .env); evaluation runs are named smoke-$engine and $runName" -ForegroundColor Cyan

if ($From -le 1) {
    Step "1. Upload the Word files into raw/ in the store (skipped when data\raw has none: another machine did it)"
    $files = @(Get-ChildItem data\raw\*.docx -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })
    if ($files.Count -gt 0) { Run (@("upload") + $files) } else { Write-Host "no .docx in data\raw; using what is in the store" -ForegroundColor DarkGray }
    Run @("status")
}
if ($From -le 2) {
    Step "2. Ingest: Word -> Markdown+, pictures described, facts, chunks (3-5 minutes the first time; up-to-date reports are skipped)"
    Run @("ingest")
    Step "2b. Retention: evidence must be retained for every answerable question, lost must be 0"
    Run @("retention")
}
if ($engine -eq "langchain") {
    if ($From -le 4) {
        Step "3. Chroma collections for both arms on this machine (about 5 minutes: stock conversion of the Word files, embeddings)"
        Run @("index", "--reset")
        Run @("status")
    }
} else {
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
        Step "4c. Word arm: what Azure ingested (files above the tier's size cap, 16 MB on Basic, are skipped)"
        Run @("word-sync", "--wait", "0")
    }
}
if ($From -le 5) {
    Step "5. First answers (expected: -4.7% of GDP via lookup_facts; the three cans from the description; ECB cut then hold)"
    Run @("ask", "In Fig 27 of the April Money Map, what is Germany's cyclically adjusted government balance forecast for 2030?", "--arm", "markdown-plus", "--mode", "agentic")
    Run @("ask", "In Fig 27 of the April Money Map, what is Germany's cyclically adjusted government balance forecast for 2030?", "--arm", "word", "--mode", "agentic")
    Run @("ask", "Which three cans are shown side by side in Fig 22, and what is printed on them?", "--arm", "markdown-plus", "--mode", "agentic")
    Run @("ask", "What did the Money Map expect the ECB to do at its next meeting in April and in July 2025?", "--arm", "markdown-plus", "--mode", "single-pass")
    Step "5b. Word arm, single-pass, verbose (expected: Bank of England to 4%; shows the passages the Word arm retrieves)"
    Run @("-v", "ask", "Which major central bank does the July Money Map expect to cut rates, and to what level?", "--arm", "word", "--mode", "single-pass")
}
if ($From -le 6) {
    Step "6. Smoke evaluation: 10 questions, Markdown+, agent (about 2 minutes; 9 of 10 or better expected)"
    Run @("eval", "--arm", "markdown-plus", "--mode", "agentic", "--limit", "10", "--run", "smoke-$engine")
    if ($Full) {
        Step "6b. Full evaluation: both arms, both modes (30-40 minutes; resume with: eval --resume --run $runName)"
        Run @("eval", "--arm", "word,markdown-plus", "--mode", "agentic,single-pass", "--run", $runName)
    }
}
Step "Done"
Write-Host "Send back: $log" -ForegroundColor Green
Write-Host "Demo: uv run streamlit run research_qa/app/Overview.py"
Stop-Transcript | Out-Null
