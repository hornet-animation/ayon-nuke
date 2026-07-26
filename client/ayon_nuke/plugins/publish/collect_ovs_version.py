from __future__ import annotations
import os
import re

import pyblish.api


class CollectOvsVersion(pyblish.api.InstancePlugin):
    """Force OVS instances to publish the version their render path points at.

    OVS nodes render straight into a versioned publish folder, and the
    quick-publish pre-flight points the node at the newest unpublished
    render on disk. Core's CollectAnatomyInstanceData (with
    follow_workfile_version enabled) overwrites instance.data["version"]
    with the CURRENT workfile version at CollectorOrder+0.49 -- so after a
    render -> version-up -> publish sequence the publish would target the
    wrong (workfile) version. This plugin runs just after it and re-asserts
    the write-path version for OVS instances, both on the instance and in
    its anatomyData (which is what the publish templates are filled from).
    """

    order = pyblish.api.CollectorOrder + 0.495
    label = "Collect OVS Version"
    hosts = ["nuke"]

    def process(self, instance):
        if not instance.data.get("is_ovs"):
            return

        version = self._version_from_write_path(instance)
        if version is None:
            self.log.warning(
                "OVS instance: could not parse a version from the write "
                "path; publish will use the workfile version"
            )
            return

        old = instance.data.get("version")
        instance.data["version"] = version
        anatomy_data = instance.data.get("anatomyData")
        if isinstance(anatomy_data, dict):
            anatomy_data["version"] = version
        self.log.info(
            "OVS: publishing version v{:03d} taken from the write path "
            "(collector had set {})".format(version, old)
        )

    def _version_from_write_path(self, instance):
        """Parse the version from the OVS node's output path.

        The publish template puts the version as its own folder
        (.../product/vNNN/file.####.ext), so the parent folder name is
        authoritative; a version token anywhere in the path is the fallback.
        Returns int or None.
        """
        try:
            node = instance.data["transientData"]["node"]
            knob = node.knob("File output") or node.knob("file")
            path = (knob.value() or "").replace("\\", "/")
            if not path:
                return None
            m = re.match(r"^v(\d+)$", os.path.basename(os.path.dirname(path)))
            if m:
                return int(m.group(1))
            tokens = re.findall(r"[/\\_.]v(\d+)", path)
            return int(tokens[-1]) if tokens else None
        except Exception:
            return None
