# PIT WALL starten (Windows PowerShell). Legt beim ersten Mal die Umgebung an.
#   .\start.ps1                  → Auto-Erkennung, Dashboard im Browser
#   .\start.ps1 --sim simulator  → Testrennen ohne Spiel
#   .\start.ps1 doctor           → Setup prüfen
# Falls Skripte blockiert sind:  Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "uv fehlt. Installieren mit:  powershell -ExecutionPolicy ByPass -c `"irm https://astral.sh/uv/install.ps1 | iex`""
    exit 1
}
$extras = if ($env:PITWALL_EXTRAS) { $env:PITWALL_EXTRAS } else { "llm,audio,stt,tts,ptt-sdl,fast" }
$py = ".venv\Scripts\python.exe"
$stamp = ".venv\.pitwall-installed"
if (-not (Test-Path ".venv")) {
    Write-Host "-> Lege Python-Umgebung an (.venv) ..."
    uv venv --python 3.12 .venv
}
if (-not (Test-Path $stamp) -or ((Get-Item "pyproject.toml").LastWriteTime -gt (Get-Item $stamp).LastWriteTime)) {
    Write-Host "-> Installiere Abhaengigkeiten [$extras] ..."
    uv pip install --python $py -e ".[$extras]"
    New-Item -ItemType File -Force -Path $stamp | Out-Null
}
$voice = & $py -c "from pitwall.config import Config; c=Config.load(); print(c.get('voice.piper_voice' if c.language=='de' else 'voice.piper_voice_en') if c.get('voice.tts_provider')=='piper' else '')"
if ($voice) {
    $vdir = & $py -c "from pitwall.config import Config; from pitwall.voice.tts import voices_dir; print(voices_dir(Config.load()))"
    if (-not (Test-Path (Join-Path $vdir "$voice.onnx"))) {
        Write-Host "-> Lade Stimme $voice ..."
        & ".venv\Scripts\pitwall.exe" voices download $voice
    }
}
$sub = @("doctor", "dry-run", "record", "voices", "say", "devices")
if ($args.Count -gt 0 -and $sub -contains $args[0]) {
    & ".venv\Scripts\pitwall.exe" @args
} else {
    & ".venv\Scripts\pitwall.exe" run @args
}
