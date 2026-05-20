import os
import re
import subprocess
import tempfile

import pyblish.api
from qtpy import QtCore, QtWidgets
from ayon_core import resources
from ayon_core.pipeline.publish import OptionalPyblishPluginMixin

from ayon_nuke.startup.ffmpegbuilder import FFMpegBuilder


# sanitized for ffmpeg's drawtext filter: `\` → `/`, drive-letter `:` → `\:`.
BURNIN_FONT = (
    resources.get_liberation_font_path()
    .replace("\\", "/")
    .replace(":", r"\:")
)


def _is_gui_run():
    # farm-side pyblish is launched with `--targets farm`; anything else
    # is treated as an interactive Nuke session where GUI work is allowed.
    return "farm" not in pyblish.api.registered_targets()


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

    def is_cancelled(self):
        return self._cancelled


class ExtractFFmpegReview(
    pyblish.api.InstancePlugin, OptionalPyblishPluginMixin
):
    """Generate review movies from published image sequences via ffmpeg.

    Each profile is a self-contained deliverable (codec + output colorspace
    + burn-ins). Every profile that passes both the plugin-level and
    per-profile filters runs as its own ffmpeg invocation.
    """

    label = "Extract FFmpeg Review"
    # Runs in extract phase so the appended representation passes through
    # IntegrateAsset (at IntegratorOrder + 0.0) and lands in the AYON DB.
    order = pyblish.api.ExtractorOrder + 0.49
    families = ["render", "prerender"]
    hosts = ["nuke"]
    targets = ["local", "farm"]
    settings_category = "nuke"
    optional = True

    def process(self, instance):
        creator_attrs = instance.data.get("creator_attributes") or {}
        if not creator_attrs.get("review", True):
            self.log.info("Review media disabled on instance, skipping")
            return
        # Defer to the farm-side pyblish run when the user asked for review
        # on farm; that run has "farm" in registered_targets.
        if (
            creator_attrs.get("hornet_review_use_farm")
            and "farm" not in pyblish.api.registered_targets()
        ):
            self.log.info("Deferring review media to farm publish")
            return
        self.log.info("Processing instance: %s", instance.data.get("name"))
        project_settings = instance.context.data["project_settings"]
        plugin_settings = (
            project_settings.get("nuke", {})
            .get("publish", {})
            .get("ExtractFFmpegReview", {})
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
            and _is_gui_run()
        )

        # Count matching profiles for dialog
        matching_profiles = [
            p for p in profiles if _filters_match(p, criteria)
        ]
        total_jobs = len(matching_profiles)

        dialog = None
        if _is_gui_run() and total_jobs > 0:
            app = QtWidgets.QApplication.instance()
            parent = app.activeWindow() if app else None
            dialog = _FFmpegDialog(total_jobs, parent=parent)
            dialog.show()

        try:
            for idx, profile in enumerate(matching_profiles, start=1):
                profile_name = profile.get("name") or "unnamed"
                self.log.info("Running profile: %s", profile_name)

                if dialog is not None:
                    dialog.job_started.emit(idx, profile_name)
                    if dialog.is_cancelled():
                        raise RuntimeError("FFmpeg review cancelled by user")

                self._run_profile(
                    instance,
                    profile_name=profile_name,
                    codec=profile["codec"],
                    colorspace=profile.get("colorspace") or {},
                    burnin_config=profile["burnin"],
                    do_burnin=creator_attrs.get("review_burnin", True),
                    input_pattern=input_pattern,
                    start_frame=start_frame,
                    fps=fps,
                    input_colorspace=input_colorspace,
                    text_values=text_values,
                    dialog=dialog,
                    create_read_node=create_read_node,
                    profile_index=idx,
                )
        finally:
            if dialog is not None:
                if dialog.is_cancelled():
                    dialog.close()
                else:
                    dialog.all_done.emit()

    def _run_profile(
        self,
        instance,
        *,
        profile_name,
        codec,
        colorspace,
        burnin_config,
        do_burnin,
        input_pattern,
        start_frame,
        fps,
        input_colorspace,
        text_values,
        dialog=None,
        create_read_node=False,
        profile_index=1,
    ):
        output_colorspace = colorspace.get("output") or None
        delivery = colorspace.get("delivery") or None

        # Write to a per-profile temp dir so IntegrateAsset transfers the
        # output to its template-driven publish location and registers it.
        staging = tempfile.mkdtemp(prefix=f"review_{profile_name}_")
        fname = os.path.splitext(os.path.basename(input_pattern))[0]
        seq_basename = re.sub(r"[._-]?%\d*d$", "", fname) + f"_{profile_name}"
        output_path = os.path.join(
            staging, f"{seq_basename}.{codec['movie_ext']}"
        )

        globals_config = {
            "width": codec["width"],
            "height": codec["height"],
            "fit": codec["fit"],
            "framerate": fps,
            "source_width": instance.data.get("resolutionWidth"),
            "source_height": instance.data.get("resolutionHeight"),
        }

        codec_config = {
            "codec": codec["codec"],
            "pix_fmt": codec["pix_fmt"] or None,
            "profile": codec["profile"] or None,
            "extra_args": codec.get("extra_args") or None,
        }

        profile_config = {
            "cropmask": burnin_config.get("cropmask", {"enable": False}),
            "text_elements": {} if not do_burnin else {
                el["name"]: {
                    **{
                        k: v
                        for k, v in el.items()
                        if k not in ("name", "box", "font_color")
                    },
                    "box": [
                        el["box"]["x1"], el["box"]["y1"],
                        el["box"]["x2"], el["box"]["y2"],
                    ],
                    "font_color": (
                        list(el["font_color"]) if el.get("font_color") is not None
                        else [1.0, 1.0, 1.0, 1.0]
                    ),
                    "font": BURNIN_FONT,
                }
                for el in burnin_config.get("text_elements", [])
            },
        }

        frame_end = instance.data.get("frameEnd")
        frame_count = (
            max(1, int(frame_end) - int(start_frame) + 1)
            if frame_end is not None else None
        )

        self.log.info(
            f"Running ffmpeg review | profile={profile_name} "
            f"colorspace={input_colorspace}->{output_colorspace} "
            f"input={input_pattern} start_frame={start_frame} "
            f"frame_count={frame_count}"
        )

        builder = (
            FFMpegBuilder(globals_config, codec_config, profile_config)
            .input(input_pattern, start_number=start_frame, frame_count=frame_count)
            .output(output_path)
            .color(
                input_colorspace=input_colorspace,
                output_colorspace=output_colorspace,
                delivery=delivery,
            )
            .text(**text_values)
        )

        self.log.info(
            "ffmpeg cmd | profile=%s\n  text_values=%s\n  cmd=%s",
            profile_name,
            text_values,
            builder.build_string(),
        )

        try:
            if _is_gui_run():
                self.log.debug("Running with Nuke GUI dialog")
                self._run_with_dialog(builder.build(), profile_name, dialog)
            else:
                self.log.debug("Running ffmpeg headless")
                builder.run(check=True)
        except subprocess.CalledProcessError as exc:
            self.log.error(
                f"FFMpegBuilder failed for profile={profile_name} "
                f"(returncode={exc.returncode})"
            )
            raise

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
            # lazy: only reachable in interactive Nuke (gated by _is_gui_run).
            import nuke
            try:
                read = nuke.nodes.Read(
                    file=builder.output_path.replace("\\", "/"),
                    name=f"Review_{profile_name}",
                )
                if output_colorspace:
                    read["colorspace"].setValue(output_colorspace)

                # Place the Read beneath the publish write-group it came from,
                # offset horizontally per profile so multiple deliverables sit
                # in a row instead of stacking on top of one another.
                group = (
                    instance.data.get("transientData", {}).get("node")
                )
                if group is not None:
                    read.setXYpos(
                        group.xpos() + (profile_index - 1) * 100,
                        group.ypos() + 100,
                    )

                self.log.info(
                    f"Created Read node {read.name()} -> {builder.output_path}"
                )
            except Exception as exc:
                self.log.warning(
                    f"Could not create Read node for profile={profile_name}: {exc}"
                )

    def _run_with_dialog(self, cmd, profile_name, dialog):
        """Run ffmpeg under a cancellable Qt dialog using QProcess.

        Output is streamed via ``readyReadStandardOutput``; a nested
        ``QEventLoop`` blocks the caller while still pumping events, so the
        Cancel button and window-close stay live without threads.
        """
        # -progress emits newline-delimited key=value lines on stderr;
        # -nostats kills the \r-overwriting status line.
        cmd = cmd[:-1] + ["-progress", "pipe:2", "-nostats", cmd[-1]]
        program, args = cmd[0], cmd[1:]

        proc = QtCore.QProcess()
        proc.setProcessChannelMode(QtCore.QProcess.MergedChannels)

        output_lines = []

        def _emit(text):
            text = text.rstrip()
            if not text:
                return
            output_lines.append(text)
            if dialog is not None:
                dialog.line_received.emit(text)

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
        """Return ``(pattern_path, start_frame)`` reading the source frames
        from the representation's stagingDir — i.e., the write node's output
        folder, where the renders actually live during extract.
        """
        ext = instance.data.get("ext")
        frame_start = int(instance.data["frameStart"])
        for rep in instance.data.get("representations", []):
            if rep.get("ext") != ext:
                continue
            staging = rep.get("stagingDir")
            files = rep.get("files")
            first = files[0] if isinstance(files, (list, tuple)) else files
            if not staging or not first:
                continue
            # ``render_v001.0001.exr`` (frame=1001) → ``render_v001.%04d.exr``
            pattern = os.path.join(staging, first)
            for m in reversed(list(re.finditer(r"\d+", first))):
                if int(m.group()) == frame_start:
                    pattern = os.path.join(
                        staging,
                        f"{first[:m.start()]}%0{len(m.group())}d{first[m.end():]}",
                    )
                    break
            self.log.info(
                "Resolved input pattern: %s (start_frame=%s)",
                pattern, frame_start,
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
