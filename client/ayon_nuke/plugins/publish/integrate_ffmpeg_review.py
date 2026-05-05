import os
import re
import copy
import subprocess
import threading
import time

import nuke
import pyblish.api
from ayon_core.pipeline.publish import OptionalPyblishPluginMixin

from ayon_nuke.startup.ffmpegbuilder import FFMpegBuilder

import ayon_nuke
_ADDON_ROOT = os.path.dirname(ayon_nuke.__file__)
DEFAULT_FONT = os.path.join(_ADDON_ROOT, "resources", "fonts", "Inter-Variable.ttf")


class IntegrateFFmpegReview(pyblish.api.InstancePlugin, OptionalPyblishPluginMixin):
    """Generate review movies from published image sequences via ffmpeg.

    Each profile is a self-contained deliverable (codec + output colorspace
    + burn-ins). Every profile that passes both the plugin-level and
    per-profile filters runs as its own ffmpeg invocation.
    """

    label = "Integrate FFmpeg Review"
    order = pyblish.api.IntegratorOrder + 4.2
    families = ["render", "prerender"]
    hosts = ["nuke"]
    settings_category = "nuke"
    optional = True

    def process(self, instance):
        project_settings = instance.context.data["project_settings"]
        plugin_settings = (
            project_settings.get("nuke", {})
            .get("publish", {})
            .get("IntegrateFFmpegReview", {})
        )

        if not plugin_settings.get("enabled", False):
            return

        criteria = {
            "product_types": instance.data.get("productType", ""),
            "hosts":         instance.context.data.get("hostName", ""),
            "task_types":    instance.data.get("taskType", ""),
            "task_names":    instance.data.get("task", ""),
            "product_names": instance.data.get("productName", ""),
        }
        if not _filters_match(plugin_settings, criteria):
            self.log.info("Instance does not match plugin-level filters, skipping")
            return

        publish_dir = instance.data.get("publishDir")
        if not publish_dir:
            self.log.warning("publishDir not found on instance, skipping")
            return

        profiles = plugin_settings.get("profiles", [])
        if not profiles:
            self.log.info("No profiles configured, skipping")
            return

        input_pattern, start_frame = self._resolve_input_pattern(instance)
        if not input_pattern:
            return

        anatomy_data = instance.data.get("anatomyData", {})
        text_values = {
            "shot":    anatomy_data.get("folder", {}).get("name"),
            "name":    instance.data.get("name"),
            "version": anatomy_data.get("version"),
            "project": (instance.data.get("project") or {}).get("name"),
        }

        fps              = instance.data.get("fps") or instance.context.data.get("fps", 24)
        input_colorspace = instance.data.get("colorspace")

        for profile in profiles:
            if not _filters_match(profile, criteria):
                self.log.debug(
                    "Profile codec=%r skipped by per-profile filters",
                    (profile.get("codec") or {}).get("name"),
                )
                continue

            self._run_profile(
                instance,
                codec=profile["codec"],
                colorspace=profile.get("colorspace") or {},
                burnin=profile["burnin"],
                input_pattern=input_pattern,
                start_frame=start_frame,
                publish_dir=publish_dir,
                fps=fps,
                input_colorspace=input_colorspace,
                text_values=text_values,
            )

    def _run_profile(
        self,
        instance,
        *,
        codec,
        colorspace,
        burnin,
        input_pattern,
        start_frame,
        publish_dir,
        fps,
        input_colorspace,
        text_values,
    ):
        output_colorspace = colorspace.get("output") or None

        # Codec name is appended so multi-profile deliverables don't collide.
        seq_basename = f"{_derive_seq_basename(input_pattern)}_{codec['name']}"
        output_path  = os.path.join(
            publish_dir, f"{seq_basename}.{codec['movie_ext']}"
        )

        globals_config = {
            "width":     codec["width"],
            "height":    codec["height"],
            "fit":       codec["fit"],
            "framerate": fps,
        }

        codec_config = {
            "codec":      codec["codec"],
            "name":       codec["name"],
            "movie_ext":  codec["movie_ext"],
            "pix_fmt":    codec["pix_fmt"]    or None,
            "profile":    codec["profile"]    or None,
            "crf":        codec["crf"]        or None,
            "bitrate":    codec["bitrate"]    or None,
            "extra_args": codec.get("extra_args") or None,
        }

        profile_config = _burnin_to_dict(burnin) if burnin["enabled"] else {}

        self.log.info(
            f"Running ffmpeg review | codec={codec['name']} "
            f"burnin={burnin['enabled']} "
            f"colorspace={input_colorspace}->{output_colorspace} "
            f"input={input_pattern} start_frame={start_frame}"
        )

        builder = (
            FFMpegBuilder(globals_config, codec_config, profile_config)
            .input(input_pattern, start_number=start_frame)
            .output(output_path)
            .color(
                input_colorspace=input_colorspace,
                output_colorspace=output_colorspace,
                ocio_config=None,
            )
            .text(**text_values)
        )

        try:
            if nuke.env.get("gui"):
                self._run_with_dialog(
                    builder.build(), builder.get_env(), codec["name"],
                )
            else:
                builder.run(check=True)
        except subprocess.CalledProcessError as exc:
            self.log.warning(
                f"FFMpegBuilder failed for codec={codec['name']} "
                f"(returncode={exc.returncode})"
            )
            return

        instance.data["representations"].append({
            "name":       codec["name"],
            "ext":        codec["movie_ext"],
            "files":      os.path.basename(builder.output_path),
            "stagingDir": os.path.dirname(builder.output_path),
            "tags":       ["review"],
            "frameStart": instance.data.get("frameStart"),
            "frameEnd":   instance.data.get("frameEnd"),
        })
        self.log.info(f"Added review representation: {builder.output_path}")

    def _run_with_dialog(self, cmd, env, codec_name):
        """Run ffmpeg under a cancellable Nuke ProgressTask."""
        # -progress emits newline-delimited key=value lines; -nostats kills
        # the \r-overwriting line that would block Python's line iteration.
        cmd = cmd[:-1] + ["-progress", "pipe:2", "-nostats", cmd[-1]]

        task = nuke.ProgressTask(f"FFmpeg Review — {codec_name}")
        task.setMessage("Starting ffmpeg…")

        proc = subprocess.Popen(
            cmd, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )

        output_lines = []

        def _reader():
            for raw in proc.stdout:
                line = raw.rstrip()
                if not line:
                    continue
                output_lines.append(line)
                task.setMessage(line[-80:])
                if task.isCancelled():
                    break
            proc.stdout.close()

        reader_thread = threading.Thread(target=_reader, daemon=True)
        reader_thread.start()

        while reader_thread.is_alive():
            if task.isCancelled():
                proc.terminate()
                time.sleep(0.5)
                if proc.poll() is None:
                    proc.kill()
                break
            time.sleep(0.1)

        reader_thread.join()
        returncode = proc.wait()
        del task

        if returncode != 0:
            self.log.error(
                f"FFMpegBuilder failed for codec={codec_name} "
                f"(returncode={returncode})\n"
                + "\n".join(output_lines)
            )
            raise subprocess.CalledProcessError(returncode, cmd)

    def _resolve_input_pattern(self, instance):
        """Return ``(pattern_path, start_frame)`` resolved from anatomy."""
        repz = instance.data.get("representations", [])
        ext  = instance.data.get("ext")

        for rep in repz:
            if rep.get("ext") != ext:
                continue

            anatomy       = instance.context.data["anatomy"]
            template_data = copy.deepcopy(instance.data["anatomyData"])
            template_data["representation"] = rep["name"]
            template_data["ext"]            = rep["ext"]

            publish_template = anatomy.get_template_item("publish", "render")
            path_template    = publish_template["path"]

            frame_start = instance.data["frameStart"]
            template_data["frame"] = frame_start
            first_frame_path = str(path_template.format_strict(template_data))

            pattern = _pattern_from_first_frame(first_frame_path, frame_start)
            self.log.debug(f"Resolved input pattern: {pattern}")
            return pattern, frame_start

        self.log.warning("Could not resolve input path for ffmpeg review, skipping")
        return None, None


def _filters_match(settings, criteria):
    """Empty filter list = wildcard; otherwise the criterion must be in it."""
    for key, value in criteria.items():
        filt = settings.get(key) or []
        if filt and value not in filt:
            return False
    return True


def _pattern_from_first_frame(path, frame):
    """``render_v001.0001.exr`` (frame=1) → ``render_v001.%04d.exr``."""
    dirname = os.path.dirname(path)
    fname   = os.path.basename(path)
    for m in reversed(list(re.finditer(r"\d+", fname))):
        digits = m.group()
        if int(digits) == int(frame):
            new_fname = f"{fname[:m.start()]}%0{len(digits)}d{fname[m.end():]}"
            return os.path.join(dirname, new_fname)
    return path


def _derive_seq_basename(pattern_path):
    """``/a/b/render_v001.%04d.exr`` → ``render_v001``."""
    fname = os.path.splitext(os.path.basename(pattern_path))[0]
    return re.sub(r"[._-]?%\d*d$", "", fname)


def _resolve_font(font):
    if font and os.path.isfile(font):
        return font
    return DEFAULT_FONT


def _burnin_to_dict(burnin):
    """AYON burnin settings → ``FFMpegBuilder`` profile_config."""
    def box(m):
        return [m["x1"], m["y1"], m["x2"], m["y2"]]

    def color(c):
        return list(c) if c is not None else [0.8, 0.8, 0.8, 1.0]

    return {
        "font":       _resolve_font(burnin.get("font", "")),
        "font_size":  burnin.get("font_size", 0.02),
        "font_color": color(burnin.get("font_color")),
        "cropmask":   burnin.get("cropmask", {"enable": False}),
        "text_elements": {
            el["name"]: {
                **{k: v for k, v in el.items()
                   if k not in ("name", "box", "font_color")},
                "box":        box(el["box"]),
                "font_color": color(el.get("font_color")),
                "font":       _resolve_font(el.get("font", "")),
            }
            for el in burnin.get("text_elements", [])
        },
    }
