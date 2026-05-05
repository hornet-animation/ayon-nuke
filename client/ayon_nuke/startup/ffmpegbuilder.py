import datetime
import os
import shlex
import subprocess


FFMPEG_EXE = os.path.normpath(
    os.path.join(
        os.path.dirname(__file__), "..", "vendor", "ffmpeg", "bin", "ffmpeg.exe",
    )
)


def _which_ffmpeg():
    if os.path.isfile(FFMPEG_EXE):
        return FFMPEG_EXE
    return "ffmpeg"


def _escape_drawtext(txt):
    """Escape ``'`` for ffmpeg ``drawtext text='...'`` (single-quote → ``''``)."""
    return txt.replace("'", "''")


class FFMpegBuilder:
    """Build an ffmpeg command for review-media encoding.

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
        self._ocio_config = None
        self._ffmpeg = _which_ffmpeg()

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

    def color(self, input_colorspace=None, output_colorspace=None, ocio_config=None):
        self._input_colorspace = input_colorspace
        self._output_colorspace = output_colorspace
        self._ocio_config = ocio_config
        return self

    def _resolve_font(self, font_path):
        """Return a font path safe to embed in an ffmpeg filter string.

        Windows drive-letter colons (``D:/...``) collide with ffmpeg's
        ``key:value`` filter syntax, so paths are converted to relative form
        when possible.
        """
        if not font_path:
            return None
        if os.path.isfile(font_path):
            return self._make_filter_safe_path(font_path)
        pkg_dir = os.path.dirname(__file__)
        abs_path = os.path.join(pkg_dir, font_path)
        if os.path.isfile(abs_path):
            return self._make_filter_safe_path(abs_path)
        return None

    @staticmethod
    def _make_filter_safe_path(abs_path):
        abs_path = os.path.normpath(os.path.abspath(abs_path))
        cwd = os.path.normpath(os.getcwd())
        try:
            rel = os.path.relpath(abs_path, cwd)
            return rel.replace(os.sep, "/")
        except ValueError:
            return abs_path.replace(os.sep, "/")

    @staticmethod
    def _frames_to_timecode(frame, framerate):
        """Frame N at fps F → ``HH:MM:SS:FF`` (non-drop-frame)."""
        fps = max(1, int(round(float(framerate))))
        f = int(frame) % fps
        s = (int(frame) // fps) % 60
        m = (int(frame) // (fps * 60)) % 60
        h = int(frame) // (fps * 3600)
        return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"

    @staticmethod
    def _resolve_crop(value, ref_px):
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
            for name, element in text_elements.items():
                if not element or not element.get("enable", True):
                    continue
                dt = self._build_drawtext(name, element, out_w, out_h)
                if dt:
                    filters.append(dt)

        return ",".join(filters) if filters else None

    def _build_drawtext(self, name, element, width, height):
        font = self._resolve_font(element.get("font"))
        font_size = element.get("font_size", 0.015)
        font_color = element.get("font_color", [0.8, 0.8, 0.8, 1.0])
        box = element.get("box", [0.0, 0.0, 1.0, 1.0])
        justify = element.get("justify", "left")
        prefix = element.get("prefix", "") or ""

        if name == "framecounter":
            # ffmpeg's frame_num — zero-padding via %{eif} breaks filter parsing.
            raw_text = "%{frame_num}"
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
                raw_text = prefix + raw_text
            raw_text = str(raw_text)

        x = int(box[0] * width)
        y = int(height - box[3] * height)
        fontsize = max(1, int(font_size * width))

        r, g, b, a = font_color
        color_hex = f"0x{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"

        parts = []
        if font:
            parts.append(f"fontfile='{font.replace(os.sep, '/')}'")
        parts.append(f"text='{_escape_drawtext(raw_text)}'")
        parts.append(f"fontsize={fontsize}")
        parts.append(f"fontcolor={color_hex}")
        parts.append(f"alpha={a}")
        parts.append(f"x={x}")
        parts.append(f"y={y}")
        parts.append("y_align=font")

        if justify == "center":
            parts.append("text_align=C")
        elif justify == "right":
            parts.append("text_align=R")
        else:
            parts.append("text_align=L")

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
        if enc.get("codec"):
            cmd.extend(["-c:v", enc["codec"]])
        if enc.get("profile"):
            cmd.extend(["-profile:v", str(enc["profile"])])
        if enc.get("qscale"):
            cmd.extend(["-qscale:v", str(enc["qscale"])])
        if enc.get("preset"):
            cmd.extend(["-preset", enc["preset"]])
        if enc.get("keyint"):
            cmd.extend(["-g", str(enc["keyint"])])
        if enc.get("bframes") is not None:
            cmd.extend(["-bf", str(enc["bframes"])])
        if enc.get("tune"):
            cmd.extend(["-tune", enc["tune"]])
        if enc.get("crf") is not None:
            cmd.extend(["-crf", str(enc["crf"])])
        if enc.get("pix_fmt"):
            cmd.extend(["-pix_fmt", enc["pix_fmt"]])
        if enc.get("vendor"):
            cmd.extend(["-vendor", enc["vendor"]])
        if enc.get("metadata_s"):
            cmd.extend(["-metadata:s", enc["metadata_s"]])
        if enc.get("bitrate"):
            cmd.extend(["-b:v", enc["bitrate"]])
        if enc.get("quality"):
            cmd.extend(["-q:v", str(enc["quality"])])

        if framerate:
            cmd.extend(["-r", str(framerate)])

        cmd.extend(["-timecode", self._frames_to_timecode(self._start_number, framerate)])

        # extra_args last so they override anything above.
        extra = enc.get("extra_args")
        if extra:
            cmd.extend(shlex.split(str(extra)))

        cmd.append(self._output_path)
        return cmd

    def build_string(self):
        return " ".join(shlex.quote(str(arg)) for arg in self.build())

    def get_env(self):
        env = os.environ.copy()
        if self._ocio_config:
            env["OCIO"] = self._ocio_config
        return env

    def run(self, check=False):
        cmd = self.build()
        env = self.get_env()
        print("Running:")
        for part in cmd:
            print(f"  {part}")
        print()
        result = subprocess.run(cmd, env=env)
        if check and result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, cmd)
        return result
