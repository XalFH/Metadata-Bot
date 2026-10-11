# Advanced Telegraph MediaInfo Bot

## Setup
1. Open `main.py`.
2. Fill `API_ID`, `API_HASH`, and `BOT_TOKEN` in the CONFIGURATION section.
3. Optionally set `TELEGRAPH_ACCESS_TOKEN`. If left blank, the bot creates a Telegraph account at startup; the generated token may not persist across restarts.
4. Deploy with Docker so the native MediaInfo library is installed.

## Features
- Accepts video messages and video documents.
- Reports general/container details, video streams, audio streams, subtitles and other streams.
- Creates a Telegraph report and replies with a button.
- Temporary video files are deleted after processing.
- Configured maximum file size is 4 GB.

## Important 4 GB note
The bot's configured size check is 4 GB, but this does not override Telegram's upload/download limits, Pyrogram's protocol constraints, or your hosting provider's disk and timeout limits. A 4 GB file needs at least 4 GB of free temporary disk space (preferably more). A platform/API path that cannot download files this large will fail regardless of the configured limit. Use a Docker worker with sufficient disk space and verify your Telegram delivery path supports the file size.

## Run
```bash
pip install -r requirements.txt
python main.py
```

Never commit your real API hash, bot token, or Telegraph token to a public repository.
