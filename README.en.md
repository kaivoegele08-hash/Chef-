# PIT WALL 🏁 – your AI race engineer

**[Deutsche Version → README.md](README.md)**

PIT WALL sits on the pit wall while you drive: it reads live telemetry from your racing sim, talks to you over
**team radio** about gaps, fuel, tyres, strategy and hazards – and answers your questions when you press the
**radio button on your wheel**. Its brain is **Claude** (Anthropic); every number it says comes from real data.
Without internet or an API key the radio keeps working (phrasebook mode).

![Dashboard during a simulator race](docs/images/dashboard.png)

*Dashboard in the built-in simulator ("undercut" scenario). The OBS overlay shows the current radio call:*

![Overlay](docs/images/overlay.png)

*(GIF placeholder: radio + overlay in-game – after the first live tests.)*

## Features

- **Proactive radio** in German or English (`--lang en` or Settings) with a real radio sound: gaps and trends,
  undercut threats, pit window, safety car ("Box, box – the stop only costs eleven seconds under the SC"),
  fuel saving targets with lift-and-coast, tyre temperatures, weather changes, damage, penalties, fastest laps.
- **Real engineer timing**: talks on straights, stays quiet in braking zones and the final corners, rations
  info per lap, critical calls interrupt everything. Modes *chatty / normal / important only / silent* –
  also by voice ("quiet please").
- **Push-to-talk questions** (wheel button via evdev/SDL): instant answers for common questions
  ("gap ahead?", "fuel?", "when do I pit?"), free questions via Claude with tool use.
- **Strategy engine**: robust fuel measurement, fuel-corrected tyre degradation, 0–3 stop plans, rejoin
  prediction, undercut/overcut, SC stop gain, weather crossover.
- **Coaching**: corner by corner against your best lap – braking point, minimum speed, throttle, lock-ups, wheelspin.
- **Long-term memory** (SQLite): consumption, pit loss, weak corners and notes per track/car.
- **Debrief**, **dashboard** (tablet/phone on your LAN) and **OBS overlay**.
- **Recorder & replay**: record every race and replay it through the engineer later.
- **ETS2/ATS**: dispatcher mode with job, deadline, breaks, fuel range, speed limit and damage.

## Supported games

| Game | Connection | Guide (German) |
|---|---|---|
| F1 24 / F1 25 | UDP telemetry | [docs/setup-f1.md](docs/setup-f1.md) |
| Gran Turismo 7 (PS5) | UDP on LAN (encrypted, heartbeat) | [docs/setup-gt7.md](docs/setup-gt7.md) |
| Assetto Corsa | shared memory + UDP + in-game app | [docs/setup-assetto-corsa.md](docs/setup-assetto-corsa.md) |
| Assetto Corsa Competizione | broadcasting API + shared memory | [docs/setup-acc.md](docs/setup-acc.md) |
| iRacing | irsdk shared memory | [docs/setup-iracing.md](docs/setup-iracing.md) |
| rFactor 2 / Le Mans Ultimate | shared memory (plugin / `LMU_Data`) | [docs/setup-rfactor2-lmu.md](docs/setup-rfactor2-lmu.md) |
| Euro Truck Simulator 2 / ATS | SCS telemetry plugin | [docs/setup-ets2.md](docs/setup-ets2.md) |

Linux with Proton (e.g. Bazzite): **[docs/linux-proton.md](docs/linux-proton.md)** – including the bundled
bridge `pitwall-relay.exe`, which forwards shared memory from the Proton prefix to PIT WALL. Add
`~/.local/bin/pitwall-launch %command%` to the game's Steam launch options.

## Quick start

```bash
# Linux
brew install uv            # or: curl -LsSf https://astral.sh/uv/install.sh | sh
git clone <this-repo> ~/pitwall && cd ~/pitwall
./start.sh --sim simulator --lang en   # test race without a game
./start.sh doctor                      # checks audio, PTT, ports, Proton and live data
./start.sh                             # auto-detect: just start your game
```

```powershell
# Windows
irm https://astral.sh/uv/install.ps1 | iex
.\start.ps1 --sim simulator
```

For English voice output set the language to English in the dashboard; the English Piper voice is
`en_GB-alan-medium` (`pitwall voices download en_GB-alan-medium`).

**Claude (optional):** Dashboard → Settings → Claude → enter your API key. It is stored locally in
`~/.config/pitwall/.env`, never in the browser. Defaults: Claude Haiku for fast radio answers, Claude Sonnet for
strategy updates; the dashboard shows token usage and cost, and a daily token limit caps spending.

**Radio button:** Dashboard → Settings → *learn radio button* → press the button on your wheel. The microphone
is only open while the button is held (local speech recognition with faster-whisper).

## Commands

| Command | Purpose |
|---|---|
| `pitwall run [--sim f1]` | start engineer + dashboard |
| `pitwall run --sim simulator --scenario sc_window` | simulator scenarios: `default`, `undercut`, `sc_window`, `fuel_critical`, `rain`, `flashback`, `restart`, `quali`, `gt`, `damage`, `hot_tyres` |
| `pitwall run --replay race.pwrec --speed 4` | replay a recording |
| `pitwall dry-run --laps 20 --debrief` | whole race as a text radio log |
| `pitwall doctor [--sim acc]` | setup and data check |
| `pitwall say "radio check"` · `pitwall devices` · `pitwall voices list` | audio tests |

## Privacy & cost

Microphone only while the button is held. Telemetry stays local; Claude only receives your question, the
radio history and tool results. Paid services (Claude, optional ElevenLabs) are only used once you enter a key.

## Development

See [CLAUDE.md](CLAUDE.md) (architecture, conventions; German) and [docs/PLAN.md](docs/PLAN.md).

```bash
uv pip install -e '.[all,dev]'
.venv/bin/python -m pytest -q && .venv/bin/ruff check pitwall tests
```

## License

Own code: MIT. Telemetry protocols were re-implemented from public documentation (sources in
`docs/protocols/`). Piper voices and optional packages keep their own licenses. Games and trademarks belong to
their owners; PIT WALL is not an official product of any game publisher.
