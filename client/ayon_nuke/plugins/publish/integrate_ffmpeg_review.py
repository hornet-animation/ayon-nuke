import os
import re
import copy
import subprocess

import nuke
import pyblish.api
from qtpy import QtCore, QtWidgets
from ayon_core.pipeline.publish import OptionalPyblishPluginMixin

from ayon_nuke.startup.ffmpegbuilder import FFMpegBuilder

import ayon_nuke

_ADDON_ROOT = os.path.dirname(ayon_nuke.__file__)
DEFAULT_FONT = os.path.join(
    _ADDON_ROOT, "resources", "fonts", "Inter-Variable.ttf"
)


class _FFmpegDialog(QtWidgets.QDialog):
    """Non-modal dialog showing ffmpeg output in a scrollable ~5-line viewport."""

    line_received = QtCore.Signal(str)
    job_started = QtCore.Signal(int, str)
    all_done = QtCore.Signal()
    cancelled = QtCore.Signal()

    def __init__(self, total_jobs, parent=None):
        super().__init__(parent)
        self.setWindowTitle("FFmpeg Review")
        self.setModal(False)
        self.resize(700, 240)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        self.status_label = QtWidgets.QLabel(
            "Encoding deliverable 0/{}".format(total_jobs)
        )
        layout.addWidget(self.status_label)

        self.text = QtWidgets.QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(10000)
        self.text.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        fm = self.text.fontMetrics()
        self.text.setMinimumHeight(
            fm.lineSpacing() * 5 + 2 * self.text.frameWidth() + 4
        )
        layout.addWidget(self.text)

        self.cancel_btn = QtWidgets.QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self._on_cancel)
        layout.addWidget(self.cancel_btn)

        self._cancelled = False
        self._total_jobs = total_jobs

        self.line_received.connect(self._on_line)
        self.job_started.connect(self._on_job_started)
        self.all_done.connect(self.close)

    def _on_cancel(self):
        if self._cancelled:
            return
        self._cancelled = True
        self.cancel_btn.setEnabled(False)
        self.status_label.setText("Cancelling…")
        self.cancelled.emit()

    def closeEvent(self, event):
        # pass X button close to cancel handler to avoid memory leak
        if not self._cancelled:
            self._on_cancel()
        super().closeEvent(event)

    def _on_line(self, line):
        self.text.appendPlainText(line)

    def _on_job_started(self, idx, name):
        self.status_label.setText(
            "Encoding deliverable {}/{} — {}".format(
                idx, self._total_jobs, name
            )
        )

    def append_line(self, line):
        self.line_received.emit(line)

    def start_job(self, idx, name):
        self.job_started.emit(idx, name)

    def finish(self):
        self.all_done.emit()

    def is_cancelled(self):
        return self._cancelled


class IntegrateFFmpegReview(
    pyblish.api.InstancePlugin, OptionalPyblishPluginMixin
):
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
        self.log.info("Processing instance: %s", instance.data.get("name"))
        project_settings = instance.context.data["project_settings"]
        plugin_settings = (
            project_settings.get("nuke", {})
            .get("publish", {})
            .get("IntegrateFFmpegReview", {})
        )

        if not plugin_settings.get("enabled", False):
            self.log.info("Plugin is disabled, skipping")
            return

        criteria = {
            "product_types": instance.data.get("productType", ""),
            "hosts": instance.context.data.get("hostName", ""),
            "task_types": instance.data.get("taskType", ""),
            "task_names": instance.data.get("task", ""),
        }
        self.log.debug("Filter criteria: %s", criteria)
        if not _filters_match(plugin_settings, criteria):
            self.log.info(
                "Instance does not match plugin-level filters, skipping"
            )
            return

        publish_dir = instance.data.get("publishDir")
        if not publish_dir:
            self.log.warning("publishDir not found on instance, skipping")
            return
        self.log.debug("publishDir: %s", publish_dir)

        profiles = plugin_settings.get("profiles", [])
        if not profiles:
            self.log.info("No profiles configured, skipping")
            return
        self.log.info("Found %d profile(s) to process", len(profiles))

        input_pattern, start_frame = self._resolve_input_pattern(instance)
        if not input_pattern:
            return

        anatomy_data = instance.data.get("anatomyData", {})
        text_values = {
            "shot": anatomy_data.get("folder", {}).get("name"),
            "name": instance.data.get("name"),
            "version": anatomy_data.get("version"),
            "project": (instance.data.get("project") or {}).get("name"),
        }

        fps = instance.data.get("fps") or instance.context.data.get("fps", 24)
        input_colorspace = instance.data.get("colorspace")
        create_read_node = (
            plugin_settings.get("create_read_node", False)
            and nuke.env.get("gui")
        )

        # Count matching profiles for dialog
        matching_profiles = [
            p for p in profiles if _filters_match(p, criteria)
        ]
        total_jobs = len(matching_profiles)

        dialog = None
        if nuke.env.get("gui") and total_jobs > 0:
            app = QtWidgets.QApplication.instance()
            parent = app.activeWindow() if app else None
            dialog = _FFmpegDialog(total_jobs, parent=parent)
            dialog.show()

        try:
            for idx, profile in enumerate(matching_profiles, start=1):
                profile_name = profile.get("name") or "unnamed"
                self.log.info("Running profile: %s", profile_name)

                if dialog is not None:
                    dialog.start_job(idx, profile_name)
                    if dialog.is_cancelled():
                        raise RuntimeError("FFmpeg review cancelled by user")

                self._run_profile(
                    instance,
                    profile_name=profile_name,
                    codec=profile["codec"],
                    colorspace=profile.get("colorspace") or {},
                    burnin=profile["burnin"],
                    input_pattern=input_pattern,
                    start_frame=start_frame,
                    publish_dir=publish_dir,
                    fps=fps,
                    input_colorspace=input_colorspace,
                    text_values=text_values,
                    dialog=dialog,
                    create_read_node=create_read_node,
                )
        finally:
            if dialog is not None:
                if dialog.is_cancelled():
                    dialog.close()
                else:
                    dialog.finish()

    def _run_profile(
        self,
        instance,
        *,
        profile_name,
        codec,
        colorspace,
        burnin,
        input_pattern,
        start_frame,
        publish_dir,
        fps,
        input_colorspace,
        text_values,
        dialog=None,
        create_read_node=False,
    ):
        output_colorspace = colorspace.get("output") or None
        delivery = colorspace.get("delivery") or None

        # Profile name is appended so multi-profile deliverables don't collide.
        seq_basename = f"{_derive_seq_basename(input_pattern)}_{profile_name}"
        output_path = os.path.join(
            publish_dir, f"{seq_basename}.{codec['movie_ext']}"
        )

        globals_config = {
            "width": codec["width"],
            "height": codec["height"],
            "fit": codec["fit"],
            "framerate": fps,
        }

        codec_config = {
            "codec": codec["codec"],
            "name": profile_name,
            "movie_ext": codec["movie_ext"],
            "pix_fmt": codec["pix_fmt"] or None,
            "profile": codec["profile"] or None,
            "extra_args": codec.get("extra_args") or None,
        }

        profile_config = _burnin_to_dict(burnin)

        self.log.info(
            f"Running ffmpeg review | profile={profile_name} "
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
                delivery=delivery,
            )
            .text(**text_values)
        )

        try:
            if nuke.env.get("gui"):
                self.log.debug("Running with Nuke GUI dialog")
                self._run_with_dialog(
                    builder.build(),
                    builder.get_env(),
                    profile_name,
                    dialog,
                )
            else:
                self.log.debug("Running ffmpeg headless")
                builder.run(check=True)
        except subprocess.CalledProcessError as exc:
            self.log.error(
                f"FFMpegBuilder failed for profile={profile_name} "
                f"(returncode={exc.returncode})"
            )
            raise Exception(f"OCIO config not set or found $OCIO set to {os.environ.get('OCIO')}")

        instance.data["representations"].append(
            {
                "name": profile_name,
                "ext": codec["movie_ext"],
                "files": os.path.basename(builder.output_path),
                "stagingDir": os.path.dirname(builder.output_path),
                "tags": ["review"],
                "frameStart": instance.data.get("frameStart"),
                "frameEnd": instance.data.get("frameEnd"),
            }
        )
        self.log.info(f"Added review representation: {builder.output_path}")

        if create_read_node:
            self._create_read_node(
                builder.output_path, profile_name, output_colorspace
            )

    def _create_read_node(self, output_path, profile_name, colorspace):
        try:
            read = nuke.nodes.Read(
                file=output_path.replace("\\", "/"),
                name=f"Review_{profile_name}",
            )
            if colorspace:
                read["colorspace"].setValue(colorspace)
            self.log.info(
                f"Created Read node {read.name()} -> {output_path}"
            )
        except Exception as exc:
            self.log.warning(
                f"Could not create Read node for profile={profile_name}: {exc}"
            )

    def _run_with_dialog(self, cmd, env, profile_name, dialog):
        """Run ffmpeg under a cancellable Qt dialog using QProcess.

        Output is streamed via ``readyReadStandardOutput``; a nested
        ``QEventLoop`` blocks the caller while still pumping events, so the
        Cancel button and window-close stay live without threads.
        """
        # -progress emits newline-delimited key=value lines on stderr;
        # -nostats kills the \r-overwriting status line.
        cmd = cmd[:-1] + ["-progress", "pipe:2", "-nostats", cmd[-1]]
        program, args = cmd[0], cmd[1:]

        qenv = QtCore.QProcessEnvironment()
        for k, v in env.items():
            qenv.insert(k, v)

        proc = QtCore.QProcess()
        proc.setProcessEnvironment(qenv)
        proc.setProcessChannelMode(QtCore.QProcess.MergedChannels)

        output_lines = []

        def _emit(text):
            text = text.rstrip()
            if not text:
                return
            output_lines.append(text)
            if dialog is not None:
                dialog.append_line(text)

        def _on_lines():
            while proc.canReadLine():
                _emit(bytes(proc.readLine()).decode("utf-8", errors="replace"))

        loop = QtCore.QEventLoop()
        proc.readyReadStandardOutput.connect(_on_lines)
        proc.finished.connect(lambda *_: loop.quit())
        proc.errorOccurred.connect(lambda *_: loop.quit())
        if dialog is not None:
            dialog.cancelled.connect(proc.kill)

        try:
            proc.start(program, args)
            if not proc.waitForStarted(5000):
                raise subprocess.CalledProcessError(
                    1, cmd, output=str(proc.errorString())
                )

            loop.exec_()
            _emit(bytes(proc.readAll()).decode("utf-8", errors="replace"))

            if dialog is not None and dialog.is_cancelled():
                raise RuntimeError("FFmpeg review cancelled by user")

            rc = proc.exitCode()
            if (
                proc.exitStatus() != QtCore.QProcess.NormalExit
                or rc != 0
            ):
                self.log.error(
                    f"FFMpegBuilder failed for profile={profile_name} "
                    f"(returncode={rc})\n" + "\n".join(output_lines)
                )
                raise subprocess.CalledProcessError(rc, cmd)
        finally:
            if dialog is not None:
                try:
                    dialog.cancelled.disconnect(proc.kill)
                except (TypeError, RuntimeError):
                    pass

    def _resolve_input_pattern(self, instance):
        """Return ``(pattern_path, start_frame)`` resolved from anatomy."""
        repz = instance.data.get("representations", [])
        ext = instance.data.get("ext")
        self.log.debug(
            "Resolving input pattern for ext=%s, representations=%d",
            ext,
            len(repz),
        )

        for rep in repz:
            if rep.get("ext") != ext:
                self.log.debug(
                    "Skipping representation ext=%s", rep.get("ext")
                )
                continue

            anatomy = instance.context.data["anatomy"]
            template_data = copy.deepcopy(instance.data["anatomyData"])
            template_data["representation"] = rep["name"]
            template_data["ext"] = rep["ext"]

            publish_template = anatomy.get_template_item("publish", "render")
            path_template = publish_template["path"]

            frame_start = instance.data["frameStart"]
            template_data["frame"] = frame_start
            first_frame_path = str(path_template.format_strict(template_data))
            self.log.debug("First frame path: %s", first_frame_path)

            pattern = _pattern_from_first_frame(first_frame_path, frame_start)
            self.log.info(
                "Resolved input pattern: %s (start_frame=%s)",
                pattern,
                frame_start,
            )
            return pattern, frame_start

        self.log.warning(
            "Could not resolve input path for ffmpeg review, skipping"
        )
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
    fname = os.path.basename(path)
    for m in reversed(list(re.finditer(r"\d+", fname))):
        digits = m.group()
        if int(digits) == int(frame):
            new_fname = (
                f"{fname[: m.start()]}%0{len(digits)}d{fname[m.end() :]}"
            )
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
        return list(c) if c is not None else [1.0, 1.0, 1.0, 1.0]

    return {
        "cropmask": burnin.get("cropmask", {"enable": False}),
        "text_elements": {
            el["name"]: {
                **{
                    k: v
                    for k, v in el.items()
                    if k not in ("name", "box", "font_color")
                },
                "box": box(el["box"]),
                "font_color": color(el.get("font_color")),
                "font": _resolve_font(el.get("font", "")),
            }
            for el in burnin.get("text_elements", [])
        },
    }
