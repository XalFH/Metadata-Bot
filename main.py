import asyncio
import html
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from pyrogram import Client, filters
from pyrogram.types import Message
from pymediainfo import MediaInfo
from pymongo import MongoClient, ASCENDING

# =========================================================
# CONFIGURATION — KEEP YOUR EXISTING API CREDENTIALS
# =========================================================

API_ID = 123456  # Keep your existing API_ID
API_HASH = "YOUR_EXISTING_API_HASH"
BOT_TOKEN = "YOUR_EXISTING_BOT_TOKEN"

# Paste your existing MongoDB connection URI here.
MONGODB_URI = "YOUR_EXISTING_MONGODB_URI"

BOT_NAME = "MediaInfo Bot"
MAX_FILE_SIZE_GB = 4
MAX_CONCURRENT_JOBS = 1

DOWNLOAD_TIMEOUT = 3600
ANALYSIS_TIMEOUT = 900

DATABASE_NAME = "mediainfo_bot"
COLLECTION_NAME = "video_headers"

# =========================================================

MAX_FILE_SIZE = MAX_FILE_SIZE_GB * 1024**3

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("MediaInfoBot")

app = Client(
    "mediainfo_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
)

mongo_client = MongoClient(
    MONGODB_URI,
    serverSelectionTimeoutMS=10000,
    connectTimeoutMS=10000,
)

database = mongo_client[DATABASE_NAME]
collection = database[COLLECTION_NAME]

semaphore = asyncio.Semaphore(MAX_CONCURRENT_JOBS)

VIDEO_EXTENSIONS = {
    ".mkv", ".mp4", ".mov", ".avi", ".webm",
    ".m4v", ".mpeg", ".mpg", ".ts", ".mts",
    ".wmv", ".3gp", ".m2ts", ".vob", ".flv",
}


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
        hours, remaining = divmod(seconds, 3600)
        minutes, seconds = divmod(remaining, 60)

        if hours:
            return f"{hours}h {minutes}m {seconds}s"

        return f"{minutes}m {seconds}s"

    except (TypeError, ValueError):
        return "Unknown"


def get_value(track, *fields, default="Unknown"):
    for field in fields:
        value = getattr(track, field, None)

        if value not in (None, ""):
            return str(value)

    return default


def get_language(track):
    for field in ("other_language", "other_language_ietf"):
        value = getattr(track, field, None)

        if isinstance(value, (list, tuple)):
            value = value[0] if value else None

        if value:
            return str(value)

    return get_value(track, "language", default="Undetermined")


def get_track_data(track):
    """Convert MediaInfo track details into MongoDB-safe data."""

    fields = (
        "track_type",
        "format",
        "format_profile",
        "codec_id",
        "codec_id_info",
        "width",
        "height",
        "display_aspect_ratio",
        "frame_rate",
        "bit_rate",
        "bit_rate_mode",
        "duration",
        "language",
        "other_language",
        "other_language_ietf",
        "title",
        "channel_s",
        "sampling_rate",
        "bit_depth",
        "hdr_format",
        "stream_size",
        "default",
        "forced",
        "encoded_library",
        "encoded_application",
        "overall_bit_rate",
        "file_size",
    )

    data = {}

    for field in fields:
        value = getattr(track, field, None)

        if value is None:
            continue

        if isinstance(value, (str, int, float, bool, list, dict)):
            data[field] = value

        elif isinstance(value, tuple):
            data[field] = list(value)

        else:
            data[field] = str(value)

    return data


def create_report(file_name, file_size, media_info):
    tracks = media_info.tracks

    general = next(
        (
            track for track in tracks
            if (getattr(track, "track_type", "") or "").lower() == "general"
        ),
        None,
    )

    videos = []
    audios = []
    subtitles = []
    other_tracks = []

    for track in tracks:
        kind = (getattr(track, "track_type", "") or "").lower()

        if kind == "general":
            continue

        if kind == "video":
            videos.append({
                "codec": get_value(track, "format", "codec_id"),
                "width": get_value(track, "width"),
                "height": get_value(track, "height"),
                "fps": get_value(track, "frame_rate"),
                "bitrate": get_value(track, "bit_rate"),
                "hdr": get_value(track, "hdr_format", default=""),
                "language": get_language(track),
            })

        elif kind == "audio":
            audios.append({
                "language": get_language(track),
                "title": get_value(track, "title", default=""),
                "codec": get_value(track, "format", "codec_id"),
                "channels": get_value(track, "channel_s"),
                "sample_rate": get_value(track, "sampling_rate"),
                "bitrate": get_value(track, "bit_rate"),
            })

        elif kind in ("text", "subtitle"):
            subtitles.append({
                "language": get_language(track),
                "title": get_value(track, "title", default=""),
                "codec": get_value(track, "format", "codec_id"),
                "default": get_value(track, "default"),
                "forced": get_value(track, "forced"),
            })

        else:
            other_tracks.append(get_track_data(track))

    report = [
        "<b>🎬 MEDIAINFO REPORT</b>",
        "",
        f"<b>📁 File:</b> <code>{html.escape(file_name)}</code>",
        f"<b>💾 Size:</b> {human_size(file_size)}",
    ]

    if general:
        container = get_value(general, "format")
        duration = get_value(general, "duration")
        bitrate = get_value(general, "overall_bit_rate")

        report.extend([
            f"<b>📦 Container:</b> {html.escape(container)}",
            f"<b>⏱ Duration:</b> {human_duration(duration) if duration != 'Unknown' else 'Unknown'}",
            f"<b>📊 Overall bitrate:</b> {html.escape(bitrate)} bit/s",
        ])

    report.extend(["", f"<b>🎥 VIDEO TRACKS ({len(videos)})</b>"])

    if videos:
        for index, video in enumerate(videos, 1):
            report.extend([
                "",
                f"<b>Video #{index}</b>",
                f"• Codec: {html.escape(video['codec'])}",
                f"• Resolution: {html.escape(video['width'])} × {html.escape(video['height'])}",
                f"• FPS: {html.escape(video['fps'])}",
                f"• Bitrate: {html.escape(video['bitrate'])}",
            ])

            if video["hdr"] and video["hdr"] != "Unknown":
                report.append(f"• HDR: {html.escape(video['hdr'])}")
    else:
        report.append("No video tracks detected.")

    report.extend(["", f"<b>🔊 AUDIO TRACKS ({len(audios)})</b>"])

    if audios:
        for index, audio in enumerate(audios, 1):
            report.extend([
                "",
                f"<b>Audio #{index}</b>",
                f"• Language: {html.escape(audio['language'])}",
                f"• Title: {html.escape(audio['title'])}",
                f"• Codec: {html.escape(audio['codec'])}",
                f"• Channels: {html.escape(audio['channels'])}",
                f"• Sample rate: {html.escape(audio['sample_rate'])} Hz",
                f"• Bitrate: {html.escape(audio['bitrate'])}",
            ])
    else:
        report.append("No audio tracks detected.")

    report.extend(["", f"<b>💬 SUBTITLE TRACKS ({len(subtitles)})</b>"])

    if subtitles:
        for index, subtitle in enumerate(subtitles, 1):
            report.extend([
                "",
                f"<b>Subtitle #{index}</b>",
                f"• Language: {html.escape(subtitle['language'])}",
                f"• Title: {html.escape(subtitle['title'])}",
                f"• Format: {html.escape(subtitle['codec'])}",
                f"• Default: {html.escape(subtitle['default'])}",
                f"• Forced: {html.escape(subtitle['forced'])}",
            ])
    else:
        report.append("No subtitle tracks detected.")

    if other_tracks:
        report.extend([
            "",
            f"<b>📚 OTHER TRACKS ({len(other_tracks)})</b>",
        ])

        for track in other_tracks:
            report.append(
                "• " + html.escape(
                    f"{track.get('track_type', 'Other')}: "
                    f"{track.get('format', 'Unknown')}"
                )
            )

    report.extend([
        "",
        "━━━━━━━━━━━━━━━━━━",
        f"<b>{html.escape(BOT_NAME)}</b>",
    ])

    return "\n".join(report), {
        "general": get_track_data(general) if general else {},
        "video_tracks": videos,
        "audio_tracks": audios,
        "subtitle_tracks": subtitles,
        "other_tracks": other_tracks,
        "raw_tracks": [get_track_data(track) for track in tracks],
    }


async def save_to_mongodb(document):
    """Use a worker thread so database operations don't block the bot."""

    def save():
        collection.update_one(
            {
                "chat_id": document["chat_id"],
                "message_id": document["message_id"],
            },
            {"$set": document},
            upsert=True,
        )

    await asyncio.to_thread(save)


async def send_long_message(message, text):
    """Split large reports into Telegram-sized messages."""

    max_length = 3800

    if len(text) <= max_length:
        await message.reply_text(text, disable_web_page_preview=True)
        return

    chunks = []
    current = ""

    for line in text.split("\n"):
        if len(current) + len(line) + 1 > max_length:
            if current:
                chunks.append(current)

            current = line
        else:
            current = f"{current}\n{line}" if current else line

    if current:
        chunks.append(current)

    for chunk in chunks:
        await message.reply_text(
            chunk,
            disable_web_page_preview=True,
        )


async def process_video(message: Message):
    media = message.video or message.document

    if not media:
        return

    file_name = getattr(media, "file_name", None) or "video.mkv"
    mime_type = getattr(media, "mime_type", "") or ""
    extension = Path(file_name).suffix.lower()

    if message.document:
        if not mime_type.startswith("video/") and extension not in VIDEO_EXTENSIONS:
            return

    file_size = int(getattr(media, "file_size", 0) or 0)

    if file_size > MAX_FILE_SIZE:
        await message.reply_text(
            f"❌ File exceeds the configured {MAX_FILE_SIZE_GB} GB limit.\n"
            f"Received: {human_size(file_size)}"
        )
        return

    async with semaphore:
        status = await message.reply_text("⏳ Processing video and extracting MediaInfo...")

        temp_path = None

        try:
            suffix = Path(file_name).suffix or ".mkv"

            fd, temp_path = tempfile.mkstemp(
                prefix="mediainfo_",
                suffix=suffix,
            )
            os.close(fd)

            downloaded = await asyncio.wait_for(
                message.download(file_name=temp_path),
                timeout=DOWNLOAD_TIMEOUT,
            )

            if not downloaded or not os.path.isfile(downloaded):
                await status.edit_text("❌ Video download failed.")
                return

            temp_path = downloaded
            actual_size = os.path.getsize(temp_path)

            if actual_size > MAX_FILE_SIZE:
                await status.edit_text("❌ Downloaded file exceeds the configured size limit.")
                return

            media_info = await asyncio.wait_for(
                asyncio.to_thread(MediaInfo.parse, temp_path),
                timeout=ANALYSIS_TIMEOUT,
            )

            if not media_info or not media_info.tracks:
                await status.edit_text("❌ Could not read MediaInfo from this video.")
                return

            report, tracks_data = create_report(
                file_name,
                actual_size,
                media_info,
            )

            # Save video header and extracted track information in MongoDB.
            document = {
                "chat_id": message.chat.id,
                "message_id": message.id,
                "user_id": message.from_user.id if message.from_user else None,
                "file_name": file_name,
                "file_id": media.file_id,
                "file_unique_id": media.file_unique_id,
                "file_size": actual_size,
                "file_size_human": human_size(actual_size),
                "mime_type": mime_type,
                "created_at": datetime.now(timezone.utc),
                "media_info": tracks_data,
            }

            await save_to_mongodb(document)

            await status.delete()

            await send_long_message(message, report)

            logger.info(
                "Processed %s (%s) and saved metadata to MongoDB",
                file_name,
                human_size(actual_size),
            )

        except asyncio.TimeoutError:
            await status.edit_text(
                "❌ Processing timed out. Try again with another video."
            )

        except Exception:
            logger.exception("Video processing failed")

            try:
                await status.edit_text(
                    "❌ Something went wrong while processing the video. "
                    "Check the deployment logs."
                )
            except Exception:
                pass

        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    logger.warning("Could not remove temporary file.")


@app.on_message(filters.command(["start", "help"]))
async def start_handler(client: Client, message: Message):
    await message.reply_text(
        "<b>🎬 MediaInfo Bot</b>\n\n"
        "Send me a video to extract its MediaInfo instantly.\n\n"
        "I can show:\n"
        "• Video codec, resolution, FPS and bitrate\n"
        "• Audio languages, codecs and channels\n"
        "• Subtitle tracks\n"
        "• Container and duration\n\n"
        "Video metadata is stored in MongoDB."
    )


@app.on_message(filters.video | filters.document)
async def video_handler(client: Client, message: Message):
    await process_video(message)


@app.on_message(filters.command(["mediainfo", "mi"]) & filters.reply)
async def reply_handler(client: Client, message: Message):
    replied = message.reply_to_message

    if replied and (replied.video or replied.document):
        await process_video(replied)
    else:
        await message.reply_text("Reply to a video with /mediainfo.")


async def check_mongodb():
    await asyncio.to_thread(mongo_client.admin.command, "ping")
    await asyncio.to_thread(
        collection.create_index,
        [("chat_id", ASCENDING), ("message_id", ASCENDING)],
        unique=True,
    )


if __name__ == "__main__":
    if not MONGODB_URI or MONGODB_URI == "YOUR_EXISTING_MONGODB_URI":
        raise RuntimeError("Configure MONGODB_URI in main.py.")

    check_loop = asyncio.new_event_loop()

    try:
        check_loop.run_until_complete(check_mongodb())
        logger.info("MongoDB connection successful.")
    finally:
        check_loop.close()

    logger.info("Starting %s...", BOT_NAME)
    app.run()
