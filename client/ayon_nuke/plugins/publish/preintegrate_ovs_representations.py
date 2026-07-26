from __future__ import annotations

import pyblish.api


class PreIntegrateOvsRepresentations(pyblish.api.InstancePlugin):
    """Keep OVS publishes copy-free: drop the frame representation(s) so
    IntegrateAsset only transfers/registers lightweight review media.

    OVS nodes render straight into the versioned publish folder to avoid
    copying huge sequences -- but IntegrateAsset would then copy every frame
    within the same folder (the render uses the short render-template
    filename, integration fills the full publish template with the
    project-code prefix), doubling disk use for exactly the renders that
    are too big to copy.

    The frames stay where they were rendered; only non-frame
    representations (the ffmpeg review movie, thumbnails, ...) are
    integrated and registered with the AYON version. The version row is
    still created, so version accounting (and the OVS already-published
    check) keeps working.
    """

    order = pyblish.api.IntegratorOrder - 0.05
    label = "Pre-Integrate OVS Representations"
    hosts = ["nuke"]

    def process(self, instance):
        if not instance.data.get("is_ovs"):
            return

        repres = instance.data.get("representations") or []
        ext = (instance.data.get("ext") or "").lstrip(".").lower()

        kept, dropped = [], []
        for repre in repres:
            rext = (repre.get("ext") or "").lstrip(".").lower()
            is_frames = (
                rext == ext and "review" not in (repre.get("tags") or [])
            )
            (dropped if is_frames else kept).append(repre)

        if not dropped:
            return

        if not kept:
            # Integrating with zero representations is untested territory
            # (and would register a version with nothing attached), so if
            # review media is disabled we fall back to the old copying
            # behavior rather than risk a broken publish.
            self.log.warning(
                "OVS: no non-frame representations (review media disabled?) "
                "-- keeping the frame representation, frames WILL be copied "
                "by the integrator"
            )
            return

        instance.data["representations"] = kept
        self.log.info(
            "OVS: frames stay at the render location -- dropped "
            "representation(s) {}, integrating only: {}".format(
                [r.get("name") for r in dropped],
                [r.get("name") for r in kept],
            )
        )
