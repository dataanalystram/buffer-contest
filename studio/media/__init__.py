"""HuhQueue media agent: find the real launch video, edit it in a brand style, make images and music when there is
no media. docs/MEDIA_PIPELINE.md is the design; ``studio.media.tools`` is the tool interface agents call.

Import-light on purpose: importing the package needs only httpx and Pillow. FFmpeg, yt-dlp and faster-whisper are
loaded when a function that needs them runs, so the free web dyno can import this without them.
"""
from .find import find_launch_video  # noqa: F401  (agent-core: from .media import find_launch_video)
