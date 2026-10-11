"""
Advanced Telegraph MediaInfo Bot
Fill in the four settings below, then run: python main.py
"""

import asyncio
import html
import logging
import os
import tempfile
from pathlib import Path

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
from pymediainfo import MediaInfo
from telegraph import Telegraph

# ============================================================
# CONFIGURATION — put your values here (no .env required)
# ============================================================
API_ID = 32541562                 # Integer from https://my.telegram.org
API_HASH = "e37e4432298d5a5eb4a6e32c18804283"
BOT_TOKEN = "8958744545:AAFIWwJh5nrVMOnoyKEUqdysNktP0g2eS8k"
TELEGRAPH_ACCESS_TOKEN = "6288219713e387a679586a9a184b6be21b795770e11f60d6006bd0005342"  # Optional; "" auto-creates account

BOT_NAME = "MediaInfo Bot"
AUTHOR_NAME = "ASVM"
MAX_FILE_SIZE_GB = 4            # Requested limit: 4 GB
MAX_CONCURRENT_JOBS = 1         # Keep low for large files / limited hosting
DOWNLOAD_TIMEOUT_SECONDS = 60 * 60
ANALYSIS_TIMEOUT_SECONDS = 15 * 60
TELEGRAPH_TIMEOUT_SECONDS = 90
# ============================================================

MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_GB * 1024 * 1024 * 1024
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("telegraph_mediainfo_bot")

if not isinstance(API_ID, int) or API_ID <= 0:
    raise RuntimeError("Set API_ID in the CONFIGURATION section of main.py.")
if not API_HASH or API_HASH == "PASTE_API_HASH_HERE":
    raise RuntimeError("Set API_HASH in the CONFIGURATION section of main.py.")
if not BOT_TOKEN or BOT_TOKEN == "PASTE_BOT_TOKEN_HERE":
    raise RuntimeError("Set BOT_TOKEN in the CONFIGURATION section of main.py.")

app = Client(
    "telegraph_mediainfo_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    sleep_threshold=60,
)
job_semaphore = asyncio.Semaphore(max(1, MAX_CONCURRENT_JOBS))
telegraph = Telegraph(access_token=TELEGRAPH_ACCESS_TOKEN) if TELEGRAPH_ACCESS_TOKEN else Telegraph()


def clean(value, fallback="Unknown"):
    if value is None:
        return fallback
    value = str(value).strip()
    return value if value else fallback


def track_language(track):
    for attr in ("other_language", "other_language_ietf"):
        value = getattr(track, attr, None)
        if value:
            if isinstance(value, (list, tuple)):
                value = value[0] if value else None
            if value:
                return str(value).strip()
    return clean(getattr(track, "language", None), "Undetermined")


def human_size(size):
    size = float(size or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TB"


def human_duration(milliseconds):
    try:
        seconds = max(0, int(float(milliseconds) / 1000))
        hours, rem = divmod(seconds, 3600)
        minutes, seconds = divmod(rem, 60)
        return f"{hours}h {minutes}m {seconds}s" if hours else f"{minutes}m {seconds}s"
    except (TypeError, ValueError):
        return "Unknown"


def get_value(track, *names, fallback="Unknown"):
    for name in names:
        value = getattr(track, name, None)
        if value not in (None, ""):
            return str(value)
    return fallback


def build_html_page(file_name, file_size, media_info):
    general = next((t for t in media_info.tracks if (getattr(t, "track_type", "") or "").lower() == "general"), None)
    video_tracks, audio_tracks, subtitle_tracks, other_tracks = [], [], [], []

    for track in media_info.tracks:
        kind = (getattr(track, "track_type", "") or "").lower()
        if kind == "general":
            continue

        if kind == "video":
            width = get_value(track, "width", fallback="?")
            height = get_value(track, "height", fallback="?")
            codec = get_value(track, "format", "codec_id")
            fps = get_value(track, "frame_rate", fallback="")
            bitrate = get_value(track, "bit_rate", fallback="")
            hdr = get_value(track, "hdr_format", fallback="")
            details = [f"{codec} · {width}×{height}"]
            if fps:
                details.append(f"{fps} FPS")
            if bitrate:
                details.append(f"{bitrate} bit/s")
            if hdr:
                details.append(hdr)
            video_tracks.append(" · ".join(details))

        elif kind == "audio":
            lang = track_language(track)
            title = get_value(track, "title", fallback="")
            codec = get_value(track, "format", "codec_id", fallback="")
            channels = get_value(track, "channel_s", fallback="")
            sample_rate = get_value(track, "sampling_rate", fallback="")
            bitrate = get_value(track, "bit_rate", fallback="")
            label = lang if lang not in ("Undetermined", "Unknown", "und") else (title or "Undetermined")
            details = [x for x in (codec, channels, f"{sample_rate} Hz" if sample_rate else "", f"{bitrate} bit/s" if bitrate else "") if x]
            audio_tracks.append((label, " · ".join(details)))

        elif kind in ("text", "subtitle"):
            lang = track_language(track)
            title = get_value(track, "title", fallback="")
            codec = get_value(track, "format", "codec_id", fallback="")
            label = lang if lang not in ("Undetermined", "Unknown", "und") else (title or "Undetermined")
            subtitle_tracks.append((label, codec))

        else:
            other_tracks.append((kind or "Other", get_value(track, "format", "codec_id")))

    parts = [
        "<h2>Media Track Information</h2>",
        "<p><b>File:</b> " + html.escape(file_name) + "</p>",
        "<p><b>File size:</b> " + html.escape(human_size(file_size)) + "</p>",
    ]

    if general:
        info = []
        for label, names in (
            ("Container", ("format",)),
            ("Duration", ("duration",)),
            ("Overall bitrate", ("overall_bit_rate",)),
            ("Encoded application", ("encoded_application",)),
            ("Encoded library", ("encoded_library",)),
        ):
            value = None
            for name in names:
                value = getattr(general, name, None)
                if value not in (None, ""):
                    break
            if value not in (None, ""):
                if label == "Duration":
                    value = human_duration(value)
                elif label == "Overall bitrate":
                    value = f"{value} bit/s"
                info.append(f"<b>{html.escape(label)}:</b> {html.escape(str(value))}")
        if info:
            parts.extend(["<h3>General Information</h3>", "<p>" + "<br>".join(info) + "</p>"])

    def add_section(title, entries, empty_text):
        parts.append(f"<h3>{html.escape(title)} ({len(entries)})</h3>")
        if not entries:
            parts.append(f"<p>{html.escape(empty_text)}</p>")
            return
        for item in entries:
            if isinstance(item, tuple):
                name, details = item
                line = html.escape(str(name))
                if details:
                    line += " — " + html.escape(str(details))
            else:
                line = html.escape(str(item))
            parts.append("<blockquote>" + line + "</blockquote>")

    add_section("Video Tracks", video_tracks, "No video tracks detected.")
    add_section("Audio Tracks", audio_tracks, "No audio tracks detected.")
    add_section("Subtitle Tracks", subtitle_tracks, "No subtitle tracks detected.")
    if other_tracks:
        add_section("Other Streams", other_tracks, "No other streams detected.")

    if len(audio_tracks) > 1:
        note = "This file contains multiple audio tracks. Use VLC Media Player to switch audio tracks."
    elif len(audio_tracks) == 1:
        note = "This file contains one detected audio track."
    else:
        note = "No audio track was detected. If playback fails, try VLC Media Player."
    parts.append("<h3>Note</h3><p>" + html.escape(note) + "</p>")
    parts.append(f"<hr><p><b>{html.escape(AUTHOR_NAME)}</b><br>{html.escape(BOT_NAME)}</p>")
    return "".join(parts)


def initialize_telegraph():
    global telegraph
    if TELEGRAPH_ACCESS_TOKEN and TELEGRAPH_ACCESS_TOKEN != "PASTE_TELEGRAPH_TOKEN_HERE":
        telegraph = Telegraph(access_token=TELEGRAPH_ACCESS_TOKEN)
        return
    account = telegraph.create_account(short_name="MediaInfoBot")
    token = account.get("access_token")
    if not token:
        raise RuntimeError("Telegraph account creation failed. Add TELEGRAPH_ACCESS_TOKEN in main.py.")
    telegraph = Telegraph(access_token=token)
    logger.warning("Telegraph account was created automatically; its token may not persist after a restart.")


async def analyze_video(message: Message):
    media = message.video or message.document
    if not media:
        await message.reply_text("Send a video file as a video or document.")
        return

    file_name = getattr(media, "file_name", None) or "video.mkv"
    mime_type = getattr(media, "mime_type", "") or ""
    extensions = {
        ".mkv", ".mp4", ".mov", ".avi", ".webm", ".m4v", ".mpeg",
        ".mpg", ".ts", ".mts", ".wmv", ".3gp", ".m2ts", ".vob", ".flv",
    }
    if message.document and not mime_type.startswith("video/") and Path(file_name).suffix.lower() not in extensions:
        await message.reply_text("This doesn't look like a supported video file.")
        return

    size = int(getattr(media, "file_size", 0) or 0)
    if size > MAX_FILE_SIZE_BYTES:
        await message.reply_text(
            f"File is too large. Configured limit: {MAX_FILE_SIZE_GB} GB "
            f"({human_size(MAX_FILE_SIZE_BYTES)})."
        )
        return

    async with job_semaphore:
        status = await message.reply_text("Downloading video for MediaInfo analysis...")
        temp_path = None
        try:
            suffix = Path(file_name).suffix or ".mkv"
            fd, temp_path = tempfile.mkstemp(prefix="mediainfo_", suffix=suffix)
            os.close(fd)

            # Pyrogram streams the download to disk; no full video is loaded into RAM.
            download_task = asyncio.create_task(message.download(file_name=temp_path))
            try:
                downloaded = await asyncio.wait_for(download_task, timeout=DOWNLOAD_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                download_task.cancel()
                await status.edit_text("Download timed out. Try again or use a host with more bandwidth.")
                return

            if not downloaded or not os.path.isfile(downloaded):
                await status.edit_text("Video download failed.")
                return
            temp_path = downloaded
            actual_size = os.path.getsize(temp_path)
            if actual_size <= 0:
                await status.edit_text("The downloaded file is empty.")
                return
            if actual_size > MAX_FILE_SIZE_BYTES:
                await status.edit_text("The downloaded file exceeded the configured 4 GB limit.")
                return

            await status.edit_text("Analyzing container, video, audio and subtitle tracks...")
            try:
                media_info = await asyncio.wait_for(
                    asyncio.to_thread(MediaInfo.parse, temp_path, full=False),
                    timeout=ANALYSIS_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                await status.edit_text("MediaInfo analysis timed out. The file may be damaged or unusually complex.")
                return

            if not media_info or not media_info.tracks:
                await status.edit_text("MediaInfo couldn't read tracks from this file.")
                return

            await status.edit_text("Publishing report to Telegraph...")
            page_html = build_html_page(file_name, actual_size, media_info)
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    telegraph.create_page,
                    title=("MediaInfo - " + file_name)[:200],
                    html_content=page_html,
                    author_name=AUTHOR_NAME,
                    author_url=None,
                ),
                timeout=TELEGRAPH_TIMEOUT_SECONDS,
            )
            url = response.get("url")
            if not url:
                await status.edit_text("Telegraph didn't return a report URL.")
                return

            await status.edit_text(
                "<b>MediaInfo report created.</b>\n\n"
                f"<b>File:</b> <code>{html.escape(file_name)}</code>\n"
                f"<b>Size:</b> {human_size(actual_size)}\n\n"
                "Open the report to view video, audio and subtitle tracks.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("View MediaInfo Report", url=url)]]
                ),
            )
        except Exception as exc:
            logger.exception("Video processing failed")
            try:
                message_text = html.escape(str(exc))[:700]
                await status.edit_text(f"Could not process this video.\n<code>{message_text}</code>")
            except Exception:
                pass
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    logger.warning("Could not remove temporary file: %s", temp_path)


@app.on_message(filters.command(["start", "help"]))
async def start_handler(client: Client, message: Message):
    await message.reply_text(
        f"<b>Welcome to {html.escape(BOT_NAME)}.</b>\n\n"
        "Send a video file and I'll inspect its container, video, audio and subtitle tracks "
        "and publish a Telegraph report.\n\n"
        "You can also reply to a video with <code>/mediainfo</code>.\n"
        f"Configured file-size limit: <b>{MAX_FILE_SIZE_GB} GB</b>."
    )


@app.on_message(filters.command(["mediainfo", "mi"]) & filters.reply)
async def mediainfo_reply_handler(client: Client, message: Message):
    replied = message.reply_to_message
    if not replied or not (replied.video or replied.document):
        await message.reply_text("Reply to a video file with /mediainfo.")
        return
    await analyze_video(replied)


@app.on_message(filters.video | filters.document)
async def video_handler(client: Client, message: Message):
    await analyze_video(message)


if __name__ == "__main__":
    initialize_telegraph()
    logger.info("Starting %s; configured max file size: %s GB", BOT_NAME, MAX_FILE_SIZE_GB)
    app.run()
