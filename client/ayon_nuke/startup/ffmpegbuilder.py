import datetime
import os
import shlex
import subprocess

import ayon_nuke


_ADDON_ROOT = os.path.dirname(ayon_nuke.__file__)
FFMPEG_EXE = os.path.normpath(
    os.path.join(
        _ADDON_ROOT, "vendor", "ffmpeg", "bin", "ffmpeg.exe",
    )
)


# (color_primaries, color_trc, colorspace) per delivery target.
# Describes the bytes in the encoded file, independent of which OCIO config
# produced them — names will not drift across configs.
DELIVERY_TAGS = {
    "rec709":      ("bt709",   "bt709",        "bt709"),
    "srgb":        ("bt709",   "iec61966-2-1", "bt709"),
    "rec2020_sdr": ("bt2020",  "bt2020-10",    "bt2020nc"),
    "rec2020_pq":  ("bt2020",  "smpte2084",    "bt2020nc"),
    "rec2020_hlg": ("bt2020",  "arib-std-b67", "bt2020nc"),
    "p3_d65":      ("smpte432", "bt709",       "bt709"),
    "linear":      ("bt709",   "linear",       "bt709"),
}

# Codec + delivery combinations that use full-range YUV. ProRes / DNxHD are
# always tv-range; libx264 etc. can encode full-range, which sRGB / linear
# deliveries want so blacks aren't crushed into 16-235.
_FLEX_RANGE_CODECS = {"libx264", "libx265", "libsvtav1", "mjpeg"}
_PC_RANGE_DELIVERIES = {"srgb", "linear"}


# (codec_config key, ffmpeg flag, allow_zero).
# allow_zero=True keeps 0 as a valid value (crf=0 is lossless;
# bframes=0 means "no B-frames"); the rest treat 0/"" as "not set".
_ENCODER_OPTS = [
    ("codec",      "-c:v",        False),
    ("profile",    "-profile:v",  False),
    ("qscale",     "-qscale:v",   False),
    ("preset",     "-preset",     False),
    ("keyint",     "-g",          False),
    ("bframes",    "-bf",         True),
    ("tune",       "-tune",       False),
    ("crf",        "-crf",        True),
    ("pix_fmt",    "-pix_fmt",    False),
    ("vendor",     "-vendor",     False),
    ("metadata_s", "-metadata:s", False),
    ("bitrate",    "-b:v",        False),
    ("quality",    "-q:v",        False),
]


class FFMpegBuilder:
    """Build ffmpeg command-line arguments for review-media encoding.

    Assembles a single ffmpeg invocation whose ``-vf`` chain handles colour
    conversion (``ocio``), scaling (``scale``/``pad``), crop-masks
    (``drawbox``) and text burn-ins (``drawtext``).
    """

    def __init__(self, globals_config, codec_config, profile_config=None):
        self.g = globals_config or {}
        self.c = codec_config or {}
        self.p = profile_config or {}

        self._input_path = None
        self._output_path = None
        self._start_number = 1
        self._text = {}
        self._input_colorspace = None
        self._output_colorspace = None
        self._delivery = None
        self._ffmpeg = FFMPEG_EXE if os.path.isfile(FFMPEG_EXE) else "ffmpeg"

    def input(self, path, start_number=1):
        self._input_path = path
        self._start_number = start_number
        return self

    def output(self, path):
        self._output_path = path
        return self

    @property
    def output_path(self):
        return self._output_path

    def text(self, **kwargs):
        self._text.update(kwargs)
        return self

    def color(self, input_colorspace=None, output_colorspace=None, delivery=None):
        """``delivery`` is independent of ``output_colorspace`` — it names
        the target spec (``rec709``, ``srgb``, …) so the encoded file can
        be tagged with the right primaries/trc/matrix/range regardless of
        what the specific name in the OCIO file is.
        """
        self._input_colorspace = input_colorspace
        self._output_colorspace = output_colorspace
        self._delivery = delivery
        return self

    @staticmethod
    def _resolve_crop(value, ref_px):
        """Convert a crop value (int, str percent, or None) to pixels."""
        if not value:
            return 0
        if isinstance(value, str) and "%" in value:
            pct = float(value.split("%")[0]) / 100.0
            return int(pct * ref_px)
        return int(value)

    def _build_vf_chain(self):
        filters = []

        width = self.g.get("width")
        height = self.g.get("height")
        fit = self.g.get("fit", True)
        cropwidth = self.g.get("cropwidth")
        cropheight = self.g.get("cropheight")

        out_w = width or 1920
        out_h = height or 1080

        if cropwidth or cropheight:
            cw = self._resolve_crop(cropwidth, out_w)
            ch = self._resolve_crop(cropheight, out_h)
            x = int(cw / 2) if cw else 0
            y = int(ch / 2) if ch else 0
            crop_w = f"iw-{cw}" if cw else "iw"
            crop_h = f"ih-{ch}" if ch else "ih"
            filters.append(f"crop={crop_w}:{crop_h}:{x}:{y}")

        if width or height:
            if fit:
                filters.append(
                    f"scale={width or -1}:{height or -1}:force_original_aspect_ratio=decrease"
                )
                filters.append(f"pad={width or -1}:{height or -1}:(ow-iw)/2:(oh-ih)/2")
            else:
                filters.append(f"scale={width or -1}:{height or -1}")

        if self._input_colorspace and self._output_colorspace:
            filters.append(
                f"ocio=input='{self._input_colorspace}':output='{self._output_colorspace}'"
            )

        cropmask = self.p.get("cropmask") if self.p else None
        if cropmask and cropmask.get("enable"):
            ar = cropmask.get("aspect")
            opacity = cropmask.get("opacity", 0.5)
            if ar:
                mask_h = int(round(out_w / ar))
                bar_h = int((out_h - mask_h) / 2)
                if bar_h > 0:
                    color = f"black@{opacity}"
                    filters.append(
                        f"drawbox=x=0:y=0:w=iw:h={bar_h}:color={color}:t=fill"
                    )
                    filters.append(
                        f"drawbox=x=0:y=ih-{bar_h}:w=iw:h={bar_h}:color={color}:t=fill"
                    )

        text_elements = self.p.get("text_elements") if self.p else None
        if text_elements:
            # drawtext's box-compositing renders the box as magenta with
            # hollow green-edged text on the float planar RGB that ocio
            # outputs from EXR sources. gbrp16le keeps planar-RGB layout
            # and float-equivalent precision, and lands drawtext on a
            # working code path.
            filters.append("format=gbrp16le")
            for name, element in text_elements.items():
                if not element or not element.get("enable", True):
                    continue
                dt = self._build_drawtext(name, element, out_w, out_h)
                if dt:
                    filters.append(dt)

        return ",".join(filters) if filters else None

    def _build_drawtext(self, name, element, width, height):
        # Windows drive-letter colons collide with ffmpeg's key:value filter
        # syntax, so the resolved font path is converted to relative form
        # against cwd when possible.
        font = None
        font_path = element.get("font")
        if font_path:
            pkg_dir = os.path.dirname(__file__)
            for cand in (font_path, os.path.join(pkg_dir, font_path)):
                if not os.path.isfile(cand):
                    continue
                abs_path = os.path.normpath(os.path.abspath(cand))
                try:
                    font = os.path.relpath(abs_path, os.getcwd())
                except ValueError:
                    # Different Windows drives — relpath impossible.
                    font = abs_path
                font = font.replace(os.sep, "/")
                break

        font_size = element.get("font_size", 0.015)
        font_color = element.get("font_color", [0.8, 0.8, 0.8, 1.0])
        box = element.get("box", [0.0, 0.0, 1.0, 1.0])
        justify = element.get("justify", "left")
        prefix = element.get("prefix", "") or ""

        if name == "framecounter":
            # n is the filter's input frame index (0-based); offset by the
            # source sequence's start frame so the burnin reads as the actual
            # frame number on the timeline. Colons inside %{...} are escaped
            # so ffmpeg's filter parser doesn't mistake them for option
            # separators (single quotes don't shield them here).
            raw_text = f"%{{eif\\:n+{self._start_number}\\:d}}"
        else:
            raw_text = self._text.get(name)
            if raw_text is None and name == "datetime":
                fmt = element.get("datetime_format")
                raw_text = (
                    datetime.datetime.now().strftime(fmt) if fmt
                    else datetime.datetime.now().replace(microsecond=0).isoformat()
                )
            if raw_text is None:
                return None
            if prefix:
                raw_text = f"{prefix}{raw_text}"

        # text_align only controls multi-line wrap alignment in drawtext, so
        # for single-line burnins we have to anchor x ourselves using the
        # ``tw`` (text-width) expression — otherwise right/center burnins
        # render off the right edge of the frame.
        left_px = int(box[0] * width)
        right_px = int(box[2] * width)
        if justify == "right":
            x_expr = f"{right_px}-tw"
        elif justify == "center":
            x_expr = f"{(left_px + right_px) // 2}-tw/2"
        else:
            x_expr = str(left_px)
        y = int(height - box[3] * height)
        fontsize = max(1, int(font_size * width))

        r, g, b, a = font_color
        color_hex = f"0x{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"

        # Single quotes inside text='...' must be doubled so ffmpeg's filter
        # parser doesn't terminate the value early.
        escaped = raw_text.replace("'", "''")

        parts = []
        if font:
            parts.append(f"fontfile='{font}'")
        parts.append(f"text='{escaped}'")
        parts.append(f"fontsize={fontsize}")
        parts.append(f"fontcolor={color_hex}")
        parts.append(f"alpha={a}")
        parts.append(f"x={x_expr}")
        parts.append(f"y={y}")
        parts.append("y_align=font")
        parts.append("box=1")
        parts.append("boxcolor=black@0.5")
        parts.append("boxborderw=8")

        return "drawtext=" + ":".join(parts)

    def build(self):
        """Return the assembled ffmpeg command as a list."""
        if not self._input_path:
            raise RuntimeError("input() must be called before build()")
        if not self._output_path:
            raise RuntimeError("output() must be called before build()")

        out_dir = os.path.dirname(self._output_path)
        if out_dir and not os.path.exists(out_dir):
            os.makedirs(out_dir)

        cmd = [self._ffmpeg, "-y", "-hide_banner", "-loglevel", "info"]

        cmd.extend(["-start_number", str(self._start_number)])
        framerate = self.g.get("framerate", 24)
        cmd.extend(["-framerate", str(framerate)])
        cmd.extend(["-i", self._input_path])

        vf = self._build_vf_chain()
        if vf:
            cmd.extend(["-vf", vf])

        enc = self.c
        for key, flag, allow_zero in _ENCODER_OPTS:
            val = enc.get(key)
            if val is None or (not allow_zero and not val):
                continue
            cmd.extend([flag, str(val)])

        # Delivery container tags (range/primaries/trc/matrix). Emitted before
        # extra_args so a profile can still override any of them explicitly.
        # Range is derived from codec + delivery: ProRes / DNxHD stay tv-range,
        # h264/h265/AV1 use full range for sRGB / linear deliveries so blacks
        # aren't crushed.
        delivery = self._delivery
        tags = DELIVERY_TAGS.get(delivery) if delivery and delivery != "none" else None
        if tags:
            primaries, trc, matrix = tags
            use_pc = (
                enc.get("codec") in _FLEX_RANGE_CODECS
                and delivery in _PC_RANGE_DELIVERIES
            )
            cmd.extend([
                "-color_range", "pc" if use_pc else "tv",
                "-color_primaries", primaries,
                "-color_trc", trc,
                "-colorspace", matrix,
            ])

        if framerate:
            cmd.extend(["-r", str(framerate)])

        # Non-drop-frame HH:MM:SS:FF for the source start frame.
        fps = max(1, int(round(float(framerate))))
        n = int(self._start_number)
        tc = f"{n // (fps * 3600):02d}:{n // (fps * 60) % 60:02d}:{n // fps % 60:02d}:{n % fps:02d}"
        cmd.extend(["-timecode", tc])

        # extra_args last so they override anything above. ``comments=True``
        # enables ``#`` end-of-line comments in the textarea setting.
        extra = enc.get("extra_args")
        if extra:
            cmd.extend(shlex.split(str(extra), comments=True))

        cmd.append(self._output_path)
        return cmd

    def build_string(self):
        return " ".join(shlex.quote(str(arg)) for arg in self.build())

    def run(self, check=False):
        cmd = self.build()
        print("Running:")
        for part in cmd:
            print(f"  {part}")
        print()
        result = subprocess.run(cmd)
        if check and result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, cmd)
        return result
