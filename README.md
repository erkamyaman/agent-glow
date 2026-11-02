# agent-glow

[![CI](https://github.com/f/agent-glow/actions/workflows/ci.yml/badge.svg)](https://github.com/f/agent-glow/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)
![macOS, Linux, Windows](https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey)

**Your keyboard blinks when your coding agent is waiting for you.**

Claude Code, Codex and GitHub Copilot regularly stop to ask for permission, ask a question or present a
plan, then sit there until you notice. agent-glow turns your keyboard into the notification: it blinks in
the agent's color until you answer, then puts your own lighting back exactly as it was.

| Agent waiting | Keyboard blinks | Mouse vibrates (optional) |
| --- | --- | --- |
| Claude Code | 🔴 red | `happy_alert` |
| Codex | 🔵 blue | `angry_alert` |
| GitHub Copilot (CLI and app) | 🟢 green | `ringing` |

- **Hooks can't get in the way.** They only write a tiny marker file, so an unplugged keyboard or a bug
  never slows down or blocks an agent.
- **Your lighting is safe.** Alerts use temporary commands, and your own effect, color and brightness come
  back afterwards, even after a crash.
- **Several agents at once.** If more than one agent is waiting, each flash shows the next one's color.
- **Small and dependency-light.** One Python file plus [hidapi](https://pypi.org/project/hidapi/),
  installed into its own venv. Colors, speed, brightness and an optional "turn finished" flash are
  configurable.

## Supported devices

| Device | Connection | Alert |
| --- | --- | --- |
| **Omnitype** and any **QMK keyboard with VIA** and RGB lighting | USB cable | Blinks in each agent's color |
| **Logitech MX Keys** | Bluetooth, Bolt or Unifying | Blinks the white backlight |
| **MacBook built-in keyboard** | Built in | Blinks the white backlight, even from dark |
| **Logitech MX Master 4** (opt-in) | Bluetooth, Bolt or Unifying | A different vibration per agent |

agent-glow runs on **macOS, Linux and Windows** with Python 3.9 or newer. So far only macOS has been tested
on real hardware. Linux and Windows pass the automated tests, so please
[open an issue](https://github.com/f/agent-glow/issues) if anything misbehaves. See [Device notes](#device-notes)
for each device's details and limits.

## Install

Paste this prompt into **Claude Code**, **Codex** or **GitHub Copilot**. Your agent clones the repository,
runs the installer, checks your keyboard and walks you through the rest.

```text
Install agent-glow (https://github.com/f/agent-glow) so my keyboard blinks when a coding agent is waiting for me.

1. Check that git and Python 3.9 or newer are installed. Clone the repository into ~/agent-glow, or run
   `git pull` there if it already exists. Read its README.md before continuing.
2. From the clone, run `python3 install.py` (`py install.py` on Windows). It is not interactive.
   If I only want some agents, pass `--agents`, for example `--agents claude,codex`.
3. Run `~/.agent-glow/bin/agent-glow devices` (on Windows: `%USERPROFILE%\.agent-glow\bin\agent-glow.cmd devices`).
   - If it lists more than one usable keyboard, ask me which one to use. For a VIA or Logitech keyboard,
     run the installer again with `--keyboard VID:PID`. For the MacBook keyboard, set
     "keyboard": {"lighting": "macbook"} in ~/.agent-glow/config.json.
   - If no keyboard is usable, explain the reason it prints and how I can fix it.
4. On Linux, if the installer says a udev rule is needed, show me the sudo commands it printed and run them
   only after I agree. Then ask me to unplug and replug the keyboard.
5. Run `~/.agent-glow/bin/agent-glow test` and ask me whether the keyboard blinked
   (red, blue and green on an RGB keyboard, white on a backlit one).
6. Ask me whether I have a Logitech mouse with haptics, such as an MX Master 4. If I want it to vibrate,
   add "haptics": {"enabled": true} to ~/.agent-glow/config.json, keeping any keys already there.
7. Finally, tell me to restart my running Claude Code, Codex and Copilot sessions, and to run /hooks once
   in Codex and trust the agent-glow hooks.

Don't edit my agent settings files yourself. The installer adds its hooks safely and makes a backup.
```

The installer:

1. Copies `agent_glow.py` to `~/.agent-glow` and creates a private Python venv there with hidapi. Your
   system Python is left alone.
2. Checks that it can drive your keyboard.
3. Adds hooks to each agent's settings, keeping your existing settings and hooks, and makes a one-time
   `*.agent-glow.bak` backup of:
   - `~/.claude/settings.json`
   - `~/.codex/hooks.json`
   - `~/.copilot/hooks/agent-glow.json`
4. Starts a small background daemon at login:
   - **macOS:** a LaunchAgent (`io.github.f.agent-glow`).
   - **Linux:** a systemd user service (`agent-glow.service`).
   - **Windows:** an HKCU `Run` entry.

If `~/.local/bin` is on your `PATH`, it also links `agent-glow` there so the plain command works.

<details>
<summary>Install without an agent</summary>

```sh
git clone https://github.com/f/agent-glow ~/agent-glow
cd ~/agent-glow
python3 install.py          # on Windows: py install.py
~/.agent-glow/bin/agent-glow test
```

Then restart running agent sessions, and in Codex run `/hooks` once and trust the agent-glow hooks.

| Installer option | |
| --- | --- |
| `--agents claude,codex` | Only register hooks for these agents. The default is all three. |
| `--keyboard 4d4c:6505` | Use this keyboard instead of the first usable one found. It's saved in `config.json`. |
| `--no-service` | Don't start the daemon at login. Run `agent-glow daemon` yourself. |
| `--no-venv` | Use the Python running the installer, which must have `hidapi` installed. |
| `--uninstall` | Remove everything the installer added. |

</details>

### Update

Ask your agent:

```text
Update agent-glow: run `git pull` in ~/agent-glow, then run `python3 install.py` there again with the same --agents option I installed with, if any.
```

Reinstalling is safe and doesn't duplicate hooks.

### Uninstall

Ask your agent:

```text
Uninstall agent-glow: run `python3 install.py --uninstall` in ~/agent-glow, then tell me if a Linux udev rule is left to delete.
```

This removes the daemon, the hooks (your other settings and hooks are kept) and `~/.agent-glow`. If an alert
is showing, your lighting is restored first. A Linux udev rule, if you added one, stays until you delete
`/etc/udev/rules.d/60-agent-glow.rules`.

## Commands

```
agent-glow devices                  list usable keyboards and whether agent-glow can drive them
agent-glow test [AGENT|all|done] [SECONDS]
agent-glow status                   daemon, keyboard and pending alerts
agent-glow stop                     stop the daemon (the lighting is put back)
agent-glow restore                  put the lighting back if a crash left it changed
agent-glow udev-rule [VID:PID ...]  print the Linux udev rule
agent-glow daemon                   run the daemon in the foreground
```

The command lives at `~/.agent-glow/bin/agent-glow` (`%USERPROFILE%\.agent-glow\bin\agent-glow.cmd` on
Windows).

## Configuration

Create `~/.agent-glow/config.json`. Every key is optional, and the daemon picks up changes without a restart.

```json
{
  "keyboard": { "vid": "0x4D4C", "pid": "0x6505", "lighting": "auto" },
  "agents": ["claude", "codex", "copilot"],
  "blink_seconds": 0.4,
  "brightness": 255,
  "needs_input": {
    "ttl_seconds": 600,
    "colors": {
      "claude":  { "hue": 0,   "sat": 255 },
      "codex":   { "hue": 170, "sat": 255 },
      "copilot": { "hue": 85,  "sat": 255 }
    }
  },
  "done": { "enabled": false, "hue": 85, "sat": 255, "seconds": 5 },
  "haptics": {
    "enabled": false,
    "repeat_seconds": 30,
    "waveforms": { "claude": "happy_alert", "codex": "angry_alert", "copilot": "ringing", "done": "completed" }
  }
}
```

| Key | Meaning |
| --- | --- |
| `keyboard.vid` / `pid` | Which keyboard to use, as hex strings. Without them, the first usable keyboard is used (VIA first, then Logitech, then a MacBook's built-in keyboard). |
| `keyboard.lighting` | `auto`, `rgblight`, `rgb_matrix` or `macbook`. Set it if the keyboard has both RGB systems and `auto` picks the wrong one. Logitech keyboards are only used with `auto`. |
| `agents` | Agents that may blink the keyboard. |
| `blink_seconds` | Time on and time off per blink, from 0.1 to 5. |
| `brightness` | Alert brightness, from 1 to 255. |
| `needs_input.colors` | Color per agent, as QMK hue and saturation (0–255). Red is 0, yellow 43, green 85, cyan 128, blue 170, magenta 213. |
| `needs_input.ttl_seconds` | Stop blinking for a question nobody answered after this long. |
| `done` | Flash this color for `seconds` when an agent finishes its turn. Off by default. |
| `haptics.enabled` | Vibrate a Logitech haptic mouse when an agent is waiting. Off by default. |
| `haptics.repeat_seconds` | Vibrate again this often while an agent is waiting, from 0 (never) to 3600. |
| `haptics.waveforms` | Vibration per agent, plus `done` for the optional "turn finished" alert. One of `sharp_state_change`, `damp_state_change`, `sharp_collision`, `damp_collision`, `subtle_collision`, `happy_alert`, `angry_alert`, `completed`, `square`, `wave`, `firework`, `mad`, `knock`, `jingle`, `ringing`. |

## Device notes

### Omnitype and other QMK keyboards

- The keyboard needs **VIA enabled** and **RGB Light or RGB Matrix** lighting, and must be connected
  **by USB cable**. Wireless connections usually do not carry VIA.
- Its firmware must speak **VIA protocol 12 or newer** (QMK from 2023 on). `agent-glow devices` tells you
  if it doesn't.
- **Your saved lighting is never overwritten.** Every write uses VIA's non-saving "set" command, so the
  keyboard's EEPROM is untouched, and unplugging it always brings your own lighting back.
- Tested on an Omnitype keyboard (Mechlovin Zed65 Rev1 PCB, `4d4c:6505`).

### Logitech MX Keys

MX Keys keyboards connected by Bluetooth or a Bolt/Unifying receiver are driven over Logitech's HID++
protocol (feature `0x1982`). No firmware or VIA setup is needed, but they differ from RGB keyboards:

- **The backlight is white only.** Every agent blinks the same way, and the color settings are ignored.
- **It only blinks while the backlight is lit.** If the backlight has gone dark, whether from idling or
  the brightness keys, agent-glow cannot turn it back on, so the alert is not visible.
- **Blinking may write to the keyboard's flash.** Logitech's backlight command is reported to save its
  settings, so long alerts could add wear. Lower `needs_input.ttl_seconds` to limit how long they run.
- **Some models only switch on and off.** On these, including the MX Keys Mac, `brightness` has no effect.
- **Short pauses can happen.** The keyboard sometimes stops answering over Bluetooth. agent-glow
  reconnects by itself after a few seconds.
- **Lighting changes are not guaranteed to be temporary.** agent-glow reads your backlight mode and level
  before an alert and writes them back afterwards. If a crash interrupts this, the next daemon start or
  `agent-glow restore` puts them back.
- **A VIA keyboard takes priority** when both are connected. Pin one with `keyboard.vid` and `keyboard.pid`.
- **Logi Options+ may also change the backlight** while it is running.

Tested on an MX Keys Mac (`046d:b361`) over Bluetooth on macOS.

### MacBook keyboards

A MacBook's built-in backlight is used when no VIA or Logitech keyboard is usable and `keyboard.vid` /
`pid` aren't set. To use it even with another keyboard connected, set `"keyboard": { "lighting": "macbook" }`.

- **The backlight is white only.** Every agent blinks the same way, and the color settings are ignored.
- **It can light up from dark,** so the alert still shows when the backlight is off.
- **It relies on a private macOS API** (`KeyboardBrightnessClient`). If an update removes it, agent-glow
  reports no MacBook keyboard instead of failing. Writes do not save, and your brightness is put back
  after each alert.
- **Apple's external Magic Keyboards are not supported,** because they have no backlight.

Tested on MacBook Pros with M4 (`Mac16,1`) and M5 Pro (`Mac17,8`) on macOS 27.2.

### Mouse vibration

A Logitech mouse with haptics, such as the MX Master 4, can vibrate when an agent is waiting, with or
without a keyboard connected. It's off by default:

```json
{ "haptics": { "enabled": true } }
```

Each agent has its own vibration (see the table at the top), so you can tell them apart by feel. The mouse
vibrates when an agent starts waiting, then again every `haptics.repeat_seconds` until you answer.

- The haptic command is **reverse-engineered**, so a Logitech firmware update could change it.
- It doesn't change your mouse's settings, and the mouse must have **haptics turned on in Logi Options+**.

Tested on an MX Master 4 (`046d:b042`) over Bluetooth on macOS.

### Linux: allow access to the device

Linux only lets root open raw HID devices unless a udev rule says otherwise. The installer tells you if
this is needed:

```sh
~/.agent-glow/bin/agent-glow udev-rule | sudo tee /etc/udev/rules.d/60-agent-glow.rules
sudo udevadm control --reload-rules && sudo udevadm trigger
```

Then unplug and replug the keyboard. The rule covers your keyboard's VID:PID only (for a Logitech device,
also every Logitech Bluetooth device) and uses `TAG+="uaccess"`, so it grants access to the logged-in user.

## How it works

```
agent hook ──► ~/.agent-glow/sessions/*.json ──► daemon ──VIA, HID++ or macOS──► keyboard / mouse
```

- **Hooks never touch the keyboard.** A hook only writes or deletes a tiny marker file and always exits 0
  with `{}`. It can't slow an agent down, block it or change its decisions, even if the keyboard is
  unplugged or agent-glow is broken.
- **What counts as waiting:**
  - A **permission prompt**, a **question** (`AskUserQuestion`, `request_user_input`, `ask_user`) or a
    **plan to approve** marks the session as waiting.
  - The next tool result, a new prompt, an interrupt or the session ending clears it.
  - The end of a turn (`Stop` / `agentStop`) feeds the optional `done` flash.
- **The daemon** watches the markers, saves the keyboard's current effect, color, brightness and speed,
  blinks the alert color, and restores your lighting when nothing is waiting any more.
- **Crash recovery.** If the daemon dies mid-alert, the saved snapshot is restored the next time it starts,
  or when you run `agent-glow restore`.

## Troubleshooting

- **Nothing blinks.** Run `agent-glow devices`. It lists every usable keyboard and why one can't be used.
  Then run `agent-glow test` and check `~/.agent-glow/daemon.log`.
- **`test` works but an agent doesn't trigger it.** Restart that agent's session. For Codex, trust the
  hooks in `/hooks`. `agent-glow status` shows the markers the hooks have written.
- **`devices` does not list the MacBook keyboard.** It needs a Mac with a backlit built-in keyboard and a
  macOS version that still has the private API it uses.
- **"VIA protocol N is too old".** Flash a current QMK build of your keyboard's `via` keymap.
- **"does not expose RGB Light or RGB Matrix over VIA".** The firmware doesn't include VIA lighting
  control, so it can't be driven without a firmware change.
- **macOS: the VIA app can't connect.** On macOS only one program can hold the keyboard's VIA interface.
  agent-glow holds it only while an alert is showing.
- **Windows: Claude Code hooks.** Claude Code runs hooks with Git Bash, which it already requires on
  Windows.

## Contributing

Issues and pull requests are welcome, especially reports from Linux and Windows and support for more
keyboards.

```sh
python3 -m unittest discover -s tests
```

The tests run without a keyboard or mouse. They use fake Logitech devices and a fake VIA device that
behaves like QMK's `via.c`. Everything lives in `agent_glow.py` and `install.py` with no dependency
besides hidapi, so please keep new device support in the same style and cover it with a fake device.

## License

[MIT](LICENSE)
