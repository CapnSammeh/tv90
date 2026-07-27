# 📺 TV-90

A configurable TV show player with a **90's CRT television aesthetic**, pulling content from [TorBox](https://torbox.app). Navigate using simple TV-remote inputs (arrow keys, Enter, Backspace).

## Features

- **Retro CRT look** — scanlines, phosphor glow, screen curvature, static transitions
- **Channel guide** — browse channels like an old-school cable box
- **TV remote navigation** — ↑↓ change channel, ←→ switch shows, Enter to play, Backspace to go back
- **TorBox-powered** — streams content directly from your TorBox library
- **Fully configurable** — JSON-based channel lineup

## Quick Start

```bash
cd tv90
python3 server.py
```

Then open **http://localhost:8090** in your browser.

To use a custom port:
```bash
python3 server.py 8080
```

## Configuration

Edit `channels.json`:

```json
{
  "torbox_api_key": "your-torbox-api-key-here",
  "channels": [
    {
      "number": 1,
      "name": "Comedy Central",
      "logo": "CC",
      "shows": [
        {
          "name": "The Office - Pilot",
          "torrent_id": 12345,
          "file_id": 0
        }
      ]
    }
  ]
}
```

- `torbox_api_key` — Your TorBox API key (get it from [TorBox settings](https://torbox.app/settings))
- `channels[].number` — Channel number (displayed on screen)
- `channels[].shows[].torrent_id` — TorBox torrent ID
- `channels[].shows[].file_id` — File within the torrent (0 = first file)

## Controls

| Key | Action |
|-----|--------|
| **↑ / ↓** | Change channel |
| **← / →** | Browse shows within channel |
| **Enter** | Select channel / Play show |
| **Backspace / Esc** | Go back / Stop playback |
| **G** | Jump to guide |

## How It Works

1. Server reads `channels.json` for configuration
2. Frontend fetches channel lineup via `/api/channels`
3. When you press Play, server calls TorBox's stream API:
   - `POST /stream/createstream` — gets stream tokens
   - `GET /stream/getstreamdata` — resolves to an HLS stream URL
4. Video plays in the CRT-themed player

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/channels` | Returns channel configuration (no API key exposed) |
| `GET /api/play?torrent_id=X&file_id=Y` | Creates a stream and returns the playable URL |
| `GET /api/torrents` | Lists all torrents in your TorBox account (for debugging) |
