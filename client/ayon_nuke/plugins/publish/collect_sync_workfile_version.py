from __future__ import annotations
import os
import re

import pyblish.api


class CollectSyncWorkfileVersion(pyblish.api.InstancePlugin):
    """Collect sync workfile version to instance data
    after scene version is collected by CollectSceneVersion.
    """

    order = pyblish.api.CollectorOrder + 0.001
    label = "Collect Sync Workfile Version"
    hosts = ["nuke", "nukeassist"]

    settings_category = "nuke"

    # presets
    sync_workfile_version_on_product_base_types: list[str] = []

    def process(self, instance: pyblish.api.Instance):
        product_base_type: str = instance.data["productBaseType"]
        # sync workfile version
        if product_base_type in self.sync_workfile_version_on_product_base_types:  # noqa: E501
            # OVS nodes render straight into a versioned publish path, so
            # that path -- not the workfile -- is authoritative for the
            # publish version. After render -> version-up -> publish, the
            # workfile version has no frames; the node's path (re-pointed by
            # the quick-publish OVS pre-flight) says which version really
            # holds the render.
            if instance.data.get("is_ovs"):
                version = self._version_from_write_path(instance)
                if version is not None:
                    self.log.debug(
                        f"OVS instance: using version v{version:03d} from "
                        f"the write path instead of the workfile version"
                    )
                    instance.data["version"] = version
                    return
                self.log.warning(
                    "OVS instance: could not parse a version from the write "
                    "path; falling back to the workfile version"
                )
            self.log.debug(
                f"Syncing version with workfile for '{product_base_type}'"
            )
            # get version to instance for integration
            instance.data['version'] = instance.context.data['version']

    def _version_from_write_path(self, instance):
        """Parse the version from the OVS node's output path.

        The publish template puts the version as its own folder
        (.../product/vNNN/file.####.ext), so the parent folder name is
        authoritative; a version token anywhere in the path is the fallback.
        Returns int or None.
        """
        try:
            node = instance.data["transientData"]["node"]
            knob = node.knob("file") or node.knob("File output")
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
