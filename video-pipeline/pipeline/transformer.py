"""
Stage 4: FFmpeg-based video transformation.

Operations performed:
    1. Re-encode to H.264 + AAC (clean codec, no YouTube fingerprint metadata)
    2. Strip original metadata (privacy + cleanliness)
    3. Scale to max 1080p height (preserve aspect ratio)
    4. Optional: prepend intro, append outro
    5. Optional: overlay brand watermark (graceful fallback if drawtext unavailable)

We use ffmpeg-python for the high-level API, with a graceful fallback to
direct ffmpeg CLI if the Python wrapper hits a serialization edge case.
"""
from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import ffmpeg

from logging_setup import get_logger

log = get_logger(__name__)


# Common Windows / Linux font paths we can hand to drawtext as fontfile=
DEFAULT_FONT_CANDIDATES = [
    # Windows
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\segoeui.ttf",
    r"C:\Windows\Fonts\verdana.ttf",
    # Linux
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    # macOS
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial.ttf",
]


def find_available_font() -> Optional[str]:
    """Return a path to a font that exists, or None if none found."""
    for f in DEFAULT_FONT_CANDIDATES:
        if Path(f).exists():
            return f
    return None


@dataclass
class TransformResult:
    success: bool
    video_id: str
    output_path: Optional[Path] = None
    file_size_bytes: int = 0
    duration_sec: float = 0.0
    elapsed_sec: float = 0.0
    error: str = ""
    watermark_applied: bool = False


def _ffprobe_duration(path: Path) -> float:
    """Get the duration of a media file via ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True, text=True, timeout=10,
        )
        return float(result.stdout.strip())
    except (ValueError, subprocess.TimeoutExpired, FileNotFoundError):
        return 0.0


def render_watermark_png(
    text: str,
    out_path: Path,
    font_size: int = 28,
    padding: int = 14,
) -> Optional[Path]:
    """Render watermark text to a transparent PNG using Pillow.

    This exists because `drawtext` requires ffmpeg to be built with
    libfreetype, and common builds are not - Homebrew's ffmpeg 8.x on macOS,
    which this was developed against, has neither `drawtext` nor `subtitles`.
    Previously that meant the watermark was silently skipped on any such
    machine while the run still reported success.

    `overlay`, by contrast, is a core filter present in every ffmpeg build, so
    rendering the text ourselves and compositing the result is portable to any
    environment that can run the rest of the pipeline.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont  # noqa: PLC0415
    except ImportError:
        log.warning("watermark_pillow_missing")
        return None

    try:
        font_path = find_available_font()
        font = (
            ImageFont.truetype(font_path, font_size)
            if font_path
            else ImageFont.load_default()
        )

        measure = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        box = measure.textbbox((0, 0), text, font=font)
        width = (box[2] - box[0]) + padding * 2
        height = (box[3] - box[1]) + padding * 2

        img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        # Rounded translucent plate keeps the text legible over any footage.
        draw.rounded_rectangle(
            [(0, 0), (width - 1, height - 1)], radius=8, fill=(0, 0, 0, 110)
        )
        draw.text(
            (padding - box[0], padding - box[1]),
            text,
            font=font,
            fill=(255, 255, 255, 230),
        )

        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(out_path, "PNG")
        return out_path
    except Exception as exc:  # noqa: BLE001 - watermark must never break a run
        log.warning("watermark_png_render_failed", error=str(exc))
        return None


def _overlay_xy(position: str, margin: int = 24) -> tuple[str, str]:
    """Map a named corner to ffmpeg overlay x/y expressions."""
    return {
        "top-left": (str(margin), str(margin)),
        "top-right": (f"W-w-{margin}", str(margin)),
        "bottom-left": (str(margin), f"H-h-{margin}"),
        "bottom-right": (f"W-w-{margin}", f"H-h-{margin}"),
    }.get(position, (f"W-w-{margin}", f"H-h-{margin}"))


def _try_drawtext(stream, text: str, position: str, fontfile: Optional[str]) -> tuple:
    """
    Try to apply drawtext. Returns (possibly-modified-stream, applied).
    If the filter fails (e.g. libfreetype not compiled, font missing), returns
    the original stream and applied=False.
    """
    if not text:
        return stream, False
    safe_text = text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    x_expr, y_expr = _watermark_position(position)
    kwargs = dict(
        text=safe_text,
        fontsize=20,
        fontcolor="white@0.85",
        x=x_expr, y=y_expr,
        box=1, boxcolor="black@0.4",
        boxborderw=6,
    )
    if fontfile:
        kwargs["fontfile"] = fontfile
    try:
        new_stream = ffmpeg.filter(stream, "drawtext", **kwargs)
        # Force evaluation: run a tiny 0.1s encode to confirm drawtext works
        try:
            args = ffmpeg.compile(
                ffmpeg.output(new_stream, "-", t=0.1, f="null", vcodec="libx264"),
                overwrite_output=True,
            )
            proc = subprocess.run(
                args, capture_output=True, text=True, timeout=15,
            )
            if proc.returncode == 0:
                return new_stream, True
            log.warning("drawtext_test_failed_skipping", stderr=proc.stderr[-300:])
            return stream, False
        except Exception as exc:
            log.warning("drawtext_test_exception_skipping", error=str(exc))
            return stream, False
    except Exception as exc:
        log.warning("drawtext_build_failed", error=str(exc))
        return stream, False


def transform_video(
    input_path: Path,
    output_path: Path,
    intro_path: Optional[Path] = None,
    outro_path: Optional[Path] = None,
    watermark_text: str = "",
    watermark_position: str = "bottom-right",
    max_height: int = 1080,
) -> TransformResult:
    """
    Re-encode, normalize, and brand a video file.

    - H.264 video + AAC audio
    - Strip all metadata
    - Scale to max_height (preserve aspect)
    - Optional intro/outro concat
    - Optional text watermark (skipped gracefully if drawtext unavailable)
    """
    if not input_path.exists():
        return TransformResult(
            success=False, video_id=input_path.stem, error=f"input not found: {input_path}"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    font = find_available_font()
    watermark_applied = False

    try:
        # Build the input list: optional intro + main video + optional outro
        inputs = []
        if intro_path and intro_path.exists():
            inputs.append(ffmpeg.input(str(intro_path)))
        inputs.append(ffmpeg.input(str(input_path)))
        if outro_path and outro_path.exists():
            inputs.append(ffmpeg.input(str(outro_path)))

        # Concat if we have multiple
        if len(inputs) > 1:
            joined = ffmpeg.concat(*inputs, v=1, a=1).node
            v, a = joined[0], joined[1]
        else:
            v, a = inputs[0].video, inputs[0].audio

        # Scale filter
        v = ffmpeg.filter(v, "scale", "trunc(oh*a/2)*2", f"min({max_height},ih)")

        # Watermark. Two strategies, in order of preference:
        #   1. drawtext  - one filter, no temp files, but needs libfreetype
        #   2. overlay   - render the text to a PNG with Pillow and composite;
        #                  works on any ffmpeg build
        # Only if both fail do we continue without a watermark.
        watermark_png: Optional[Path] = None
        if watermark_text:
            v, watermark_applied = _try_drawtext(v, watermark_text, watermark_position, font)

            if not watermark_applied:
                watermark_png = render_watermark_png(
                    watermark_text, output_path.parent / f".wm_{output_path.stem}.png"
                )
                if watermark_png:
                    wm_input = ffmpeg.input(str(watermark_png))
                    x_expr, y_expr = _overlay_xy(watermark_position)
                    v = ffmpeg.filter([v, wm_input], "overlay", x=x_expr, y=y_expr)
                    watermark_applied = True
                    log.info(
                        "watermark_applied_via_overlay",
                        text=watermark_text,
                        position=watermark_position,
                    )

            if not watermark_applied:
                log.warning(
                    "watermark_skipped_continuing_without", text=watermark_text
                )

        out = ffmpeg.output(
            v, a, str(output_path),
            vcodec="libx264",
            acodec="aac",
            crf=23,
            preset="veryfast",
            movflags="+faststart",
            **{"map_metadata": "-1"},  # strip all metadata
        ).overwrite_output()

        ffmpeg.run(out, capture_stdout=True, capture_stderr=True, quiet=True)

        if watermark_png and watermark_png.exists():
            watermark_png.unlink(missing_ok=True)

        elapsed = time.time() - started
        if not output_path.exists():
            return TransformResult(
                success=False, video_id=input_path.stem, error="ffmpeg did not produce output"
            )

        size = output_path.stat().st_size
        duration = _ffprobe_duration(output_path)
        log.info(
            "transform_ok",
            video_id=input_path.stem,
            size=size,
            duration=round(duration, 2),
            elapsed=round(elapsed, 2),
            watermark_applied=watermark_applied,
        )
        return TransformResult(
            success=True, video_id=input_path.stem,
            output_path=output_path, file_size_bytes=size,
            duration_sec=duration, elapsed_sec=elapsed,
            watermark_applied=watermark_applied,
        )

    except ffmpeg.Error as exc:
        stderr = exc.stderr.decode("utf-8", errors="ignore") if exc.stderr else ""
        log.error(
            "transform_failed",
            video_id=input_path.stem,
            error=stderr[-500:] if stderr else str(exc),
        )
        return TransformResult(
            success=False, video_id=input_path.stem, error=stderr[-500:] or str(exc)
        )
    except Exception as exc:
        log.error("transform_unexpected_error", video_id=input_path.stem, error=str(exc))
        return TransformResult(
            success=False, video_id=input_path.stem, error=str(exc)
        )


def _watermark_position(position: str) -> tuple[str, str]:
    """Return drawtext x/y expressions for the given position."""
    positions = {
        "top-left": ("20", "20"),
        "top-right": ("w-tw-20", "20"),
        "bottom-left": ("20", "h-th-20"),
        "bottom-right": ("w-tw-20", "h-th-20"),
    }
    return positions.get(position, positions["bottom-right"])
