# Gin Rummy LAN

A host-authoritative, two-player LAN Gin Rummy implementation built in Python using only the standard library.

## Versions

Older playable versions are preserved in Git tags:

- `v1` - initial baseline
- `v2` - gameplay and UI improvements
- `v3` - current version with built-in AI support

To inspect or run an older version:

```powershell
git checkout v1
```

```powershell
git checkout v2
```

```powershell
git checkout v3
```

To resume normal development from the latest code:

```powershell
git checkout master
```

For new work, use a feature branch and merge it back after committing:

```powershell
git checkout master
git pull
git checkout -b codex/your-change
```

## What this is

This project converts the original single-process Gin Rummy concept into a real two-player LAN game:

- one machine **hosts** the match and owns the authoritative deck, discard pile, turns, scoring, and rules
- the second machine **joins** over the network
- each player only sees their own hand during normal play
- the UI is implemented with **tkinter**, so there are no third-party GUI dependencies

## Files

- `app.py` - entry point for host or client mode
- `engine.py` - pure Gin Rummy rules and scoring engine
- `net.py` - socket protocol helpers
- `server.py` - host-authoritative game server
- `client_ui.py` - tkinter LAN client
- `launch_host.ps1` - PowerShell helper for hosting
- `launch_client.ps1` - PowerShell helper for joining
- `ai_client.py` - built-in computer opponent that connects as a local second player

## Requirements

- Python 3.10+
- Windows, macOS, or Linux with tkinter available

## How to run on Windows PowerShell

### 1. Host machine

Open PowerShell in the project folder and run:

```powershell
python .\app.py --host --name "Host Player"
```

Or use:

```powershell
.\launch_host.ps1 -Name "Host Player"
```

The host window will show the IP address that the other player should connect to.

### 2. Solo play versus the built-in computer

Open PowerShell in the project folder and run:

```powershell
python .\app.py --host --ai --name "Host Player"
```

Or use:

```powershell
.\launch_host.ps1 -AI -Name "Host Player"
```

This starts the normal host UI and automatically connects a local computer opponent.

### 3. Joining machine

Open PowerShell in the same project folder and run:

```powershell
python .\app.py --join 192.168.1.50 --name "Guest Player"
```

Or use:

```powershell
.\launch_client.ps1 -Host 192.168.1.50 -Name "Guest Player"
```

Replace `192.168.1.50` with the host machine's LAN IP.

## Internet Play with Tailscale

This game can also be played over the internet with [Tailscale](https://tailscale.com/) without changing the basic host/join flow.

### 1. Install Tailscale on both computers

- install Tailscale on both machines
- sign in to the same tailnet

### 2. Find the host machine's Tailscale IP

On the host machine:

```powershell
tailscale ip -4
```

This will usually return an IP like `100.x.y.z`.

### 3. Start the host with a shared password

```powershell
python .\app.py --host --name "Dad" --password "shared-secret"
```

Or:

```powershell
.\launch_host.ps1 -Name "Dad" -Password "shared-secret"
```

### 4. Join from the other computer

```powershell
python .\app.py --join 100.x.y.z --name "You" --password "shared-secret"
```

Or:

```powershell
.\launch_client.ps1 -Host 100.x.y.z -Name "You" -Password "shared-secret"
```

Replace `100.x.y.z` with the host machine's Tailscale IP.

### Notes

- no router port forwarding is required
- the game still uses the same TCP host/client model as LAN play
- the shared password must match on both sides
- if connection fails, confirm both machines are online in Tailscale with `tailscale status`

## Controls

- **Draw from stock**: click the stock pile
- **Draw from discard**: click the discard pile
- **Discard**: click a card in your hand when the game says you must discard
- **Sort by suit / rank**: use the sort buttons
- **Knock**: click `Knock` during your discard phase, then discard a card with deadwood `<= 10`
- **Continue**: after a round ends, click `Continue Round`
- **New match**: after the match ends, click `New Match`

## Rules implemented

- 10-card Gin Rummy
- dealer alternates by round
- opening discard decline flow
- draw from stock or discard on your turn
- knock with deadwood `<= 10`
- gin handling
- layoff when the knocker did not go gin
- undercut bonus: `10`
- gin bonus: `20`
- match ends when a player reaches `100`

## Security / trust model

This is a LAN game, not a hardened internet game.

- the **server** is authoritative
- the **client** cannot directly mutate deck or score state
- messages are JSON over TCP with a newline-delimited protocol

This is appropriate for a trusted home LAN. It is not designed as an anti-cheat internet service.

## Notes

- This is a fresh multiplayer implementation, not a tiny patch on the original single-window code.
- The UI is intentionally practical and maintainable instead of trying to preserve the old SimpleGUI animation model.
