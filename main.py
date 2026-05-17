"""
YT DROP — YouTube Downloader Backend
=====================================
Requirements:
    pip install fastapi uvicorn yt-dlp python-multipart aiofiles

Also install ffmpeg:
    Ubuntu:  sudo apt install ffmpeg
    macOS:   brew install ffmpeg
    Windows: https://ffmpeg.org/download.html

Run:
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload
"""

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
import yt_dlp
import os
import uuid
import asyncio

app = FastAPI(title="YT DROP API", version="1.0.0")

# Allow all origins (restrict this in production to your domain)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DOWNLOAD_DIR = "/tmp/ytdrop"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)


# ─── Request Models ─────────────────────────────────────────────────────────

class DownloadRequest(BaseModel):
    url: str
    format: str = "mp4"      # mp4 | webm | mp3 | m4a | wav | ogg
    quality: str = "720p"    # 2160p | 1080p | 720p | 480p | 360p | best


# ─── Helpers ────────────────────────────────────────────────────────────────

def quality_to_format_string(fmt: str, quality: str) -> str:
    """Convert format + quality to yt-dlp format string."""
    audio_formats = {"mp3", "m4a", "wav", "ogg"}

    if fmt in audio_formats:
        return "bestaudio/best"

    quality_map = {
        "2160p": "bestvideo[height<=2160]+bestaudio/best",
        "1080p": "bestvideo[height<=1080]+bestaudio/best",
        "720p":  "bestvideo[height<=720]+bestaudio/best",
        "480p":  "bestvideo[height<=480]+bestaudio/best",
        "360p":  "bestvideo[height<=360]+bestaudio/best",
        "best":  "bestvideo+bestaudio/best",
    }
    return quality_map.get(quality.lower(), "bestvideo[height<=720]+bestaudio/best")


def cleanup_file(path: str):
    """Delete temp file after response is sent."""
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


# ─── Routes ─────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"status": "ok", "message": "YT DROP API is running 🎬"}


@app.get("/info")
def get_video_info(url: str):
    """
    Fetch video metadata (title, thumbnail, duration, channel).
    Example: GET /info?url=https://youtube.com/watch?v=dQw4w9WgXcQ
    """
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            duration_sec = info.get("duration", 0)
            mins, secs = divmod(duration_sec, 60)

            return {
                "title":     info.get("title", "Unknown"),
                "thumbnail": info.get("thumbnail", ""),
                "duration":  f"{mins}:{secs:02d}",
                "channel":   info.get("uploader", "Unknown"),
                "views":     info.get("view_count", 0),
                "likes":     info.get("like_count", 0),
                "url":       url,
            }
    except yt_dlp.utils.DownloadError as e:
        raise HTTPException(status_code=400, detail=f"Could not fetch video: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/download")
def download_video(req: DownloadRequest, background_tasks: BackgroundTasks):
    """
    Download a YouTube video or audio and return the file.
    The temp file is deleted from the server after download.
    """
    audio_formats = {"mp3", "m4a", "wav", "ogg"}
    file_id = str(uuid.uuid4())
    out_template = os.path.join(DOWNLOAD_DIR, f"{file_id}.%(ext)s")

    fmt_string = quality_to_format_string(req.format, req.quality)

    ydl_opts = {
        "format":   fmt_string,
        "outtmpl":  out_template,
        "quiet":    True,
        "no_warnings": True,
        "merge_output_format": req.format if req.format not in audio_formats else None,
    }

    # Add audio post-processing for audio-only formats
    if req.format in audio_formats:
        ydl_opts["postprocessors"] = [{
            "key":            "FFmpegExtractAudio",
            "preferredcodec": req.format,
            "preferredquality": "192",
        }]

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(req.url, download=True)
            title = info.get("title", "download")

        # Find the downloaded file
        downloaded = None
        for f in os.listdir(DOWNLOAD_DIR):
            if f.startswith(file_id):
                downloaded = os.path.join(DOWNLOAD_DIR, f)
                break

        if not downloaded:
            raise HTTPException(status_code=500, detail="File not found after download")

        # Clean filename for the user
        safe_title = "".join(c for c in title if c.isalnum() or c in " -_").strip()[:60]
        ext = downloaded.rsplit(".", 1)[-1]
        download_name = f"{safe_title}.{ext}"

        # Schedule cleanup after response
        background_tasks.add_task(cleanup_file, downloaded)

        return FileResponse(
            path=downloaded,
            filename=download_name,
            media_type="application/octet-stream"
        )

    except yt_dlp.utils.DownloadError as e:
        raise HTTPException(status_code=400, detail=f"Download failed: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── Run directly ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
