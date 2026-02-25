from __future__ import annotations
import nuke
import pyblish.api


class CollectInstanceData(pyblish.api.InstancePlugin):
    """Collect Nuke instance data

    """

    order = pyblish.api.CollectorOrder - 0.49
    label = "Collect Nuke Instance Data"
    hosts = ["nuke", "nukeassist"]

    settings_category = "nuke"

    # presets
    sync_workfile_version_on_product_base_types: list[str] = []

    def process(self, instance):
        product_base_type = instance.data["productBaseType"]

        # Get format
        root = nuke.root()
        format_ = root['format'].value()
        resolution_width = format_.width()
        resolution_height = format_.height()
        pixel_aspect = format_.pixelAspect()

        # sync workfile version
        if product_base_type in self.sync_workfile_version_on_product_base_types:  # noqa: E501
            self.log.debug(
                "Syncing version with workfile for '{}'".format(
                    product_base_type
                )
            )
            # get version to instance for integration
            instance.data['version'] = instance.context.data['version']

        instance.data.update({
            "step": 1,
            "fps": root['fps'].value(),
            "resolutionWidth": resolution_width,
            "resolutionHeight": resolution_height,
            "pixelAspect": pixel_aspect

        })

        # add creator attributes to instance
        creator_attributes = instance.data["creator_attributes"]
        instance.data.update(creator_attributes)

        # Check if this is a single frame render and override family to 'image'
        # Get frame range directly from write node since frameStart/frameEnd aren't set yet
        if product_type in ["render", "prerender"]:
            group_node = instance.data["transientData"]["node"]

            # Find write node inside the group
            write_node = None
            if group_node.Class() == "Group":
                with group_node:
                    for child_node in nuke.allNodes():
                        if child_node.Class() == "Write":
                            write_node = child_node
                            break

            if write_node:
                first_frame = int(write_node["first"].getValue())
                last_frame = int(write_node["last"].getValue())

                if first_frame == last_frame:
                    self.log.info(
                        f"Single frame detected ({first_frame}) - setting family to 'image', render_target to 'frames', and disabling review"
                    )
                    instance.data["family"] = "image"
                    instance.data["render_target"] = "frames"
                    # Disable review for single frames
                    instance.data["review"] = False
                    if "creator_attributes" in instance.data:
                        instance.data["creator_attributes"]["review"] = False
                    # Add _singleFrame suffix to product name
                    if not instance.data["productName"].endswith("_singleFrame"):
                        instance.data["productName"] = instance.data["productName"] + "_singleFrame"
                        self.log.info(f"Renamed product to: {instance.data['productName']}")

        # add review family if review activated on instance
        if instance.data.get("review"):
            instance.data["families"].append("review")

        # add staging dir information on instance
        staging_dir = instance.data["transientData"].get("stagingDir")
        staging_dir_persistent = instance.data["transientData"].get(
            "stagingDir_persistent", False
        )
        staging_dir_is_custom = instance.data["transientData"].get(
            "stagingDir_is_custom", False
        )
        if staging_dir:
            self.log.debug("Updating StagingDir: {}".format(staging_dir))
            instance.data.update({
                "stagingDir": staging_dir,
                "stagingDir_persistent": staging_dir_persistent,
                "stagingDir_is_custom": staging_dir_is_custom,
            })

        # self.log.debug(f"stagingDir: {instance.data.get('stagingDir')}")

        if "stagingDir" in instance.data.keys():
            self.log.debug(f"StagingDir: {instance.data.get('stagingDir')}")
        else:
            self.log.debug("staging dir not in instance data!")
        
        strng = "\n" + '\n'.join([f"    {k}: {v}" for k, v in instance.data.items()])


        self.log.debug(strng)
