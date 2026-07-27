# 📺 TV-90

> **Built with GenAI** — this entire project was created through natural conversation with an AI agent (Hermes Agent by Nous Research). Every feature, from the CRT effects to the schedule system to the Docker packaging, was designed and implemented via conversational prompting.

A configurable TV show player with a **90's CRT television aesthetic**, pulling content from [TorBox](https://torbox.app) and metadata from [TVDB](https://thetvdb.com). Navigate using simple TV-remote inputs (arrow keys, Enter, Backspace). Works on desktop browsers and Android TV (PWA).

## Features

- **📡 TV Schedule** — generates a randomized TV-style schedule from your shows, with per-episode timestamps. Tune in mid-episode and catch whatever's currently "airing."
- **📺 TV Guide Grid** — full cable-style grid view showing all channels, now/next, with a live time indicator. D-pad navigable.
- **🔍 TVDB Integration** — search for shows, add multiple shows per channel, episodes auto-shuffle across all shows.
- **📥 Pre-caching** — upcoming episodes are automatically searched and injected into TorBox ahead of air time. No buffering.
- **🎮 D-Pad Navigation** — full TV remote support. ↑↓ channels, ←→ time scroll, Enter to play, Back to return. Focus management for Android TV.
- **📱 PWA** — installable on Android TV as a standalone app. Manifest, service worker, fullscreen support.
- **🐳 Docker** — single-container deployment with persistent config volumes.
- **🔤 Auto Subtitles** — embedded English subtitles enabled by default.
- **⏱️ Idle Timer** — overlays fade out after 5s, return on any remote input.
- **Retro CRT look** — scanlines, phosphor glow, static transitions.

## Quick Start

```bash
cd tv90
python3 server.py
```

Then open **http://localhost:8090** in your browser. Admin panel at `/admin`.

### Docker

```bash
docker-compose up -d
```

## Configuration

All setup is done through the admin panel at `/admin`:

1. Enter your **TorBox API key** and **TVDB API key** (saved once, persists forever)
2. Create channels and search TVDB to add shows
3. Click **Generate Schedule** to create a randomized episode timeline
4. Press **📥 Pre-cache** to download upcoming episodes into TorBox

Keys are stored in `apikey.json` (gitignored). Channel config in `channels.json`. Schedule data in `schedule.json`.

## Controls

| Key | Action |
|-----|--------|
| **↑ / ↓** | Change channel / Guide rows |
| **← / →** | Scroll timeline (guide mode) |
| **Enter** | Play channel / Tune in (guide) |
| **Backspace / Esc** | Stop / Go back |
| **G** | Toggle TV Guide / Channel list |
| **F** | Fullscreen |

## Architecture

- **Backend**: Single-file Python HTTP server (`server.py`) — stdlib only, no dependencies
- **Frontend**: Single-page HTML with embedded CSS/JS — CRT effects, schedule grid, video player
- **APIs**: TorBox for content streaming, TVDB for show metadata, optional Jackett for external torrent search
- **Persistence**: JSON files for config, API keys, and schedule data

## How It Works

1. **Admin panel** (`/admin`) lets you create channels and search TVDB for shows
2. **Schedule generation** shuffles episodes from all shows into a continuous timeline
3. **Press Play/Enter** on a channel — the server finds the currently-airing episode, auto-matches content from your TorBox library (or externally via Jackett/public search), and streams it
4. **Mid-episode seeking** — if an episode started 14 minutes ago, you join 14 minutes in
5. **Pre-caching** downloads upcoming episodes into TorBox so they're ready before air time

## Requirements

- Python 3.8+ (stdlib only)
- TorBox account + API key
- TVDB API key (free from [thetvdb.com/api-information](https://thetvdb.com/api-information))
- Optional: Jackett instance for broader torrent search
