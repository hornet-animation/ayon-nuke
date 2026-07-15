import nuke
import os
import re
from ayon_nuke import api
import json
import hornet_deadline_utils
from ayon_core.lib import Logger
from ayon_core.settings import get_current_project_settings

import ayon_nuke.api.lib as lib
from ayon_nuke.version import __version__ as ADDON_VERSION
from hornet_deadline_utils import save_script_with_render, deadlineNetworkSubmit

from ayon_nuke.api.lib import (
    create_write_node,
    INSTANCE_DATA_KNOB,
    get_ovs_pathing,
)

try:
    import nukescripts
except ImportError:
    nukescripts = None


log = Logger.get_logger(__name__)

# Hidden knob that stamps the AYON nuke addon (bundle) version that created
# the node. Sourced from ayon_nuke.version.__version__, which create_package.py
# rewrites from package.py on every build -- so a version bump + rebuild updates
# this automatically for newly created nodes, with no edits here.
ADDON_VERSION_KNOB = "hornet_addon_version"

# Group-level knob holding the render output path. Named "file" with the
# label "File Output"; older nodes carry the legacy knob (named and labelled
# "File output") until their panel is rebuilt, so lookups must try both.
FILE_OUTPUT_KNOB = "file"
FILE_OUTPUT_LABEL = "File Output"
LEGACY_FILE_OUTPUT_KNOB = "File output"


def get_file_output_knob(node):
    """Return the group's file-output knob (current or legacy name), or None."""
    return node.knob(FILE_OUTPUT_KNOB) or node.knob(LEGACY_FILE_OUTPUT_KNOB)


# Passive cross-shot warning applied to pasted nodes (see flag_foreign_shot).
FOREIGN_SHOT_LABEL_PREFIX = "FOREIGN SHOT: "
FOREIGN_SHOT_TILE_COLOR = 0xE8822EFF  # same orange as the obsolete backdrops
PRERENDER_TILE_COLOR = 2880113407
ADOPTED_TILE_COLOR = 0xCC0000FF  # red: node was adopted, needs a re-render

# dict mapping extension to list of exposed parameters from write node to top level group node
knobMatrix = {
    "exr": ["autocrop", "datatype", "heroview", "metadata", "interleave"],
    "png": ["datatype"],
    "dpx": ["datatype"],
    "tiff": ["datatype", "compression"],
    "jpeg": [],
}

universalKnobs = ["colorspace", "views", "raw"]

knobMatrix = {key: universalKnobs + value for key, value in knobMatrix.items()}
presets = {
    "exr": [
        ("channels", "rgba"),
        ("datatype", "16 bit half"),
    ],
    "png": [
        ("channels", "rgba"),
        ("datatype", "16 bit"),
    ],
    "dpx": [
        ("channels", "rgb"),
        ("datatype", "10 bit"),
        ("big endian", True),
    ],
    "jpeg": [("channels", "rgb")],
}


def quick_write_node(family="render"):
    # return
    variant = nuke.getInput("Variant for Hornet Write Node", "Main")
    if not variant:
        return
    variant = variant.title()
    _quick_write_node(variant, family, inpanel=True)


def ovs_write_node(family="render"):
    variant = nuke.getInput("Variant for Emergency Write Node", "Main").title()
    _quick_write_node(variant, family, is_ovs=True)

def quick_node_data(family="render", variant="_Main", is_ovs=False):
    folder_path = os.environ["AYON_FOLDER_PATH"]
    if "/" in folder_path:
        ayon_asset_name = folder_path.split("/")[-1]
    else:
        ayon_asset_name = folder_path
    ayon_hierarchy = [p for p in folder_path.split("/")[:-1] if p]
    if len(ayon_hierarchy) > 1:
        ayon_hierarchy = "/".join(folder_path.split("/")[:-1])
        ayon_parents = [p for p in folder_path.split('/')[:-1] if p] # splitting on / when the path starts with / gives us an empty [0]
    else:
        ayon_hierarchy = ayon_hierarchy[0]
        ayon_parents = [ayon_hierarchy]

    return {
        "subset": family + os.environ["AYON_TASK_NAME"] + variant,
        "variant": variant,
        "id": "pyblish.avalon.instance",
        "creator": f"create_write_{family}",
        "creator_identifier": f"create_write_{family}",
        "folderPath": os.environ['AYON_FOLDER_PATH'],
        "task": os.environ["AYON_TASK_NAME"],
        "productBaseType": family,
        "productType": family,
        "productName": family + os.environ["AYON_TASK_NAME"] + variant,
        "hierarchy": ayon_hierarchy,
        "folder": {"name": folder_path.split("/")[-1],
                   'type': 'Shot',
                   'path': os.environ['AYON_FOLDER_PATH'],
                   'parents': ayon_parents},
        "fpath_template": "{work}/renders/nuke/{subset}/{subset}.{frame}.{ext}",
        "is_ovs": is_ovs,
    }
def _quick_write_node(variant, family="render", is_ovs=False, inpanel=True):
    """
    Separated this from the nuke.getInput call to allow calls from other scripts,
    such as a loop in the Kroger versioning script
    """

    if not os.path.exists(nuke.Root().name()):
        nuke.message("You must save script first")
        return

    variant = variant.title()

    nuke.tprint("hornet write node")

    if any(
        var is None or var == ""
        for var in [os.environ["AYON_TASK_NAME"], os.environ["AYON_FOLDER_PATH"]]
    ):
        nuke.alert(
            "missing AYON_TASK_NAME and AYON_FOLDER_PATH, can't make quick write"
        )

    # variant = nuke.getInput('Variant for Quick Write Node','Main').title()
    variant = "_" + variant if variant[0] != "_" else variant

    for existing_variants in [
        parse_publish_instance(node)["variant"]
        for node in get_all_ayon_write_nodes()
    ]:
        if variant == existing_variants:
            nuke.message("Variant already exists")
            return

    if variant == "_" or variant == None or variant == "":
        nuke.message("No Variant Specified, will not create Write Node")
        return
    for nde in nuke.allNodes("Write"):
        if (
            nde.knob("name").value()
            == family + os.environ["AYON_TASK_NAME"] + variant
        ):
            nuke.message("Write Node already exists")
            return
    data = quick_node_data(family, variant, is_ovs)
    qnode = create_write_node(
        data["subset"],
        data,
        prerender=True if family == "prerender" else False,
        inpanel=inpanel,
    )

    qnode = nuke.toNode(family + os.environ["AYON_TASK_NAME"] + variant)
    print(f"Created Write Node: {qnode.name()}")
    data["folderPath"] = os.environ["AYON_FOLDER_PATH"]
    api.set_node_data(qnode, api.INSTANCE_DATA_KNOB, data)
    instance_data = json.loads(qnode.knob(api.INSTANCE_DATA_KNOB).value()[7:])
    instance_data.pop("version", None)
    instance_data["task"] = os.environ["AYON_TASK_NAME"]
    instance_data["creator_attributes"] = {
        "render_target": "frames_farm",
        "review": True,
    }
    instance_data["publish_attributes"] = {
        "CollectFramesFixDef": {"frames_to_fix": "", "rewrite_version": False},
        "ValidateCorrectAssetContext": {"active": True},
        "NukeSubmitDeadline": {
            "priority": 95,
            "chunk": 1,
            "concurrency": 1,
            "use_gpu": True,
            "suspend_publish": False,
            "workfile_dependency": True,
            "use_published_workfile": True,
        },
    }
    qnode.knob(api.INSTANCE_DATA_KNOB).setValue(
        "JSON:::" + json.dumps(instance_data)
    )
    if family == "prerender":
        qnode.knob("tile_color").setValue(PRERENDER_TILE_COLOR)

    # Stamp the bundle version BEFORE the file_type set below: that set triggers
    # embedOptions, which builds the visible version display by reading this
    # knob. The knob is in DONT_DELETE, so it survives embedOptions' purge.
    stamp_addon_version(qnode)

    with qnode.begin():
        inside_write = nuke.toNode(
            "inside_" + family + os.environ["AYON_TASK_NAME"] + variant.title()
        )
        if family == "prerender":
            inside_write.knob("file_type").setValue("exr")
        else:
            inside_write.knob("file_type").setValue("dpx")

    # Show the latest published version on the node in the DAG. The TCL
    # expression evaluates the read-only 'publish_version' knob live.
    qnode.knob("label").setValue("publish version: [value publish_version]")

    return qnode


def stamp_addon_version(node):
    """Add/refresh a hidden knob recording the addon version on `node`.

    The value is the addon version active when the node is created. It is not
    auto-updated on load, so an old node opened after an update still reports
    the version it was made with -- which is the point for regression testing.
    """
    knob = node.knob(ADDON_VERSION_KNOB)
    if knob is None:
        knob = nuke.String_Knob(ADDON_VERSION_KNOB, "AYON Nuke Addon Version")
        knob.setVisible(False)
        node.addKnob(knob)
    knob.setValue(ADDON_VERSION)

    # Refresh the visible label too, in case it was built (by embedOptions)
    # before this stamp existed.
    display = node.knob("addon_version_display")
    if display is not None:
        display.setValue(_format_version_display(ADDON_VERSION))


def _format_version_display(version_string):
    """Grey HTML label text for the visible addon-version knob."""
    return "<font color='#808080'>addon {}</font>".format(
        version_string or "unstamped"
    )


def restore_file_output_height():
    """Fix the 'File output' multiline knob collapsing to one line on reload.

    Nuke does not persist the editor height of a Multiline_Eval_String_Knob that
    was added at runtime (inside embedOptions' knobChanged), so on script load it
    renders as a single line. A freshly added multiline knob gets its normal
    multi-line height, so we rebuild just that knob in place: remove everything
    from 'File output' to the end of the panel and re-add it, recreating only the
    multiline knob fresh while re-adding the rest as-is (values and order kept).

    Registered as an onCreate handler; a no-op on nodes without the panel.
    """
    group = nuke.thisNode()
    if group is None or "publish_instance" not in group.knobs():
        return

    knobs = group.allKnobs()
    names = [k.name() for k in knobs]
    # Old nodes carry the legacy knob name; keep whichever the node has, so
    # embedded button scripts saved on old nodes keep resolving their knob.
    target = next(
        (n for n in (FILE_OUTPUT_KNOB, LEGACY_FILE_OUTPUT_KNOB) if n in names),
        None,
    )
    if target is None:
        # Panel not built yet (e.g. onCreate during initial creation) -- nothing
        # to restore; embedOptions will build it at proper height.
        return

    tail = knobs[names.index(target):]
    for knob in tail:
        group.removeKnob(knob)
    for knob in tail:
        if knob.name() == target:
            if target == FILE_OUTPUT_KNOB:
                fresh = nuke.Multiline_Eval_String_Knob(
                    FILE_OUTPUT_KNOB, FILE_OUTPUT_LABEL
                )
            else:
                fresh = nuke.Multiline_Eval_String_Knob(target)
            fresh.setValue(knob.value())
            fresh.setFlag(nuke.READ_ONLY)
            group.addKnob(fresh)
        else:
            group.addKnob(knob)


DONT_DELETE = [
    api.INSTANCE_DATA_KNOB,
    ADDON_VERSION_KNOB,
]


def embedOptions():
    nde = nuke.thisNode()
    print(f"nde: {nde.name()}")
    knb = nuke.thisKnob()

    # log.info(' knob of type' + str(knb.Class()))
    htab = nuke.Tab_Knob("htab", "Hornet")
    htab.setName("htab")
    if knb == nde.knob("file_type"):
        group = nuke.toNode(
            ".".join(["root"] + nde.fullName().split(".")[:-1])
        )
        ftype = knb.value()
    else:
        return

    # if we don't check for this it attempts to embed the options on the views write node
    # when the approval frames function creates vanilla write nodes within it
    if "publish_instance" not in group.knobs().keys():
        return

    # Don't early-return on formats missing from knobMatrix (mov, mxf, ...):
    # that skipped the purge below, leaving the previous format's Link_Knobs
    # pointing at knobs the new write lacks ("Missing knob"). Instead we always
    # purge and rebuild, defaulting unknown formats to just the universal knobs.
    for knb in group.allKnobs():
        try:
            # never clear or touch the invisible string knob that contains the pipeline JSON data
            # if knb.name() != api.INSTANCE_DATA_KNOB:
            if knb.name() not in DONT_DELETE:
                if knb.name() == api.INSTANCE_DATA_KNOB:
                    print("warning you are deleting the instance data knob!")
                    continue
                group.removeKnob(knb)
        except:
            continue

    # Expose publish knobs based on emergency status
    if INSTANCE_DATA_KNOB in group.knobs():
        data = json.loads(
            group.knobs()[INSTANCE_DATA_KNOB].value().replace("JSON:::", "", 1)
        )

        if "is_ovs" in data.keys():
            is_ovs = data["is_ovs"]
        else:
            is_ovs = False
    else:
        is_ovs = False

    # === Variant field -- sits at the very top of the panel ===
    # Backed by the `variant` key in publish_instance JSON. Edits trigger
    # on_variant_field_changed, which renames the node and interior write and
    # rewrites the render paths. Shown without the leading underscore.
    variant_field = nuke.String_Knob("variant_field", "Variant")
    try:
        _current_variant = parse_publish_instance(group).get("variant", "")
        variant_field.setValue(_current_variant.lstrip("_"))
    except Exception as _e:
        log.warning(f"Could not seed variant_field initial value: {_e}")
    variant_field.setTooltip(
        "Rename this write's variant. Renames the node, the interior write, "
        "and the render output path. Any frames already rendered to the old "
        "path are left in place; you must clean them up manually."
    )
    variant_field.setFlag(nuke.STARTLINE)
    group.addKnob(variant_field)
    group.addKnob(nuke.Text_Knob("variant_divider", "", ""))

    # Section headers are plain Text_Knobs, not collapsible groups: group
    # headers ignore rich text, but Text_Knob values render HTML, so these
    # can be styled.
    output_header = nuke.Text_Knob("output_header", "", "<b>Output</b>")
    output_header.setFlag(nuke.STARTLINE)
    group.addKnob(output_header)

    # Blank spacer row (a " " label/text renders as a gap, "" as a divider).
    output_gap = nuke.Text_Knob("output_gap", " ", " ")
    output_gap.setFlag(nuke.STARTLINE)
    group.addKnob(output_gap)

    if FILE_OUTPUT_KNOB not in group.knobs().keys():
        fle = nuke.Multiline_Eval_String_Knob(
            FILE_OUTPUT_KNOB, FILE_OUTPUT_LABEL
        )
        fle.setText(nde.knob("file").value())
        # Read-only (not disabled): still selectable/copyable, just not
        # editable -- the path is managed by the pipeline.
        fle.setFlag(nuke.READ_ONLY)
        group.addKnob(fle)
        link = nuke.Link_Knob("channels")
        link.makeLink(nde.name(), "channels")
        link.setName("channels")
        group.addKnob(link)
        if "file_type" not in group.knobs().keys():
            link = nuke.Link_Knob("file_type")
            link.makeLink(nde.name(), "file_type")
            link.setName("file_type")
            link.setFlag(0x1000)
            group.addKnob(link)
        for kname in knobMatrix.get(ftype, universalKnobs):
            # Only link knobs the inner write actually exposes for this format,
            # so unsupported formats don't produce "Missing knob" entries.
            if nde.knob(kname) is None:
                continue
            link = nuke.Link_Knob(kname)
            link.makeLink(nde.name(), kname)
            link.setName(kname)
            link.setFlag(0x1000)
            group.addKnob(link)
    log.info("links made")

    renderFirst = nuke.Link_Knob("first")
    renderFirst.makeLink(nde.name(), "first")
    renderFirst.setName("first")
    renderFirst.setLabel("Render Start")

    renderLast = nuke.Link_Knob("last")
    renderLast.makeLink(nde.name(), "last")
    renderLast.setName("last")
    renderLast.setLabel("Render End")
    framelist = nuke.String_Knob("framelist", "Frame List", "")

    publishFirst = nuke.Int_Knob("publishFirst", "Publish Start")
    publishLast = nuke.Int_Knob("publishLast", "Publish End")

    usePublishRange = nuke.Boolean_Knob(
        "usePublishRange", "My Publish Range is different from my render range"
    )

    framelist.setValue(f"{nuke.root().firstFrame()}-{nuke.root().lastFrame()}")
    nde.knob("first").setValue(nuke.root().firstFrame())
    nde.knob("last").setValue(nuke.root().lastFrame())
    publishFirst.setValue(nuke.root().firstFrame())
    publishLast.setValue(nuke.root().lastFrame())
    publishFirst.setEnabled(True)
    publishLast.setEnabled(True)
    #to be removed, set to true by default while framelist is tested
    usePublishRange.setValue(True)


    # Spacer, then divider between the output options and the pipeline.
    output_end_gap = nuke.Text_Knob("output_end_gap", " ", " ")
    output_end_gap.setFlag(nuke.STARTLINE)
    group.addKnob(output_end_gap)

    dynamic_div = nuke.Text_Knob("dynamic_div", "")
    dynamic_div.setFlag(nuke.STARTLINE)
    group.addKnob(dynamic_div)

    # The orange temp-files warning shares the header row -- both live in the
    # same Text_Knob value. (OVS nodes render straight to the publish
    # location, so no warning there.)
    rendering_text = "<b>Rendering</b>"
    if not is_ovs:
        rendering_text += (
            "&nbsp;&nbsp;&nbsp;<font color='orange'>All rendered files are "
            "TEMPORARY and WILL BE OVERWRITTEN unless published</font>"
        )
    rendering_header = nuke.Text_Knob("rendering_header", "", rendering_text)
    rendering_header.setFlag(nuke.STARTLINE)
    group.addKnob(rendering_header)

    rendering_gap = nuke.Text_Knob("rendering_gap", " ", " ")
    rendering_gap.setFlag(nuke.STARTLINE)
    group.addKnob(rendering_gap)

    set_globals_button = nuke.PyScript_Knob(
        "set_globals",
        "Set to Globals",
        "set_ranges_to_globals(nuke.thisNode())",
    )
    set_globals_button.clearFlag(nuke.STARTLINE)
    set_globals_button.setTooltip(
        "Set both the render frame range and the publish range to the "
        "script's global frame range."
    )

    group.addKnob(framelist)
    group.addKnob(set_globals_button)

    renderInterval = nuke.Int_Knob("renderInterval", "Render every")
    renderInterval.setValue(1)
    renderInterval.setTooltip(
        "Render every N frames (1 = every frame, 2 = every other frame, etc.)"
    )
    #group.addKnob(renderInterval)

    renderIntervalLabel = nuke.Text_Knob("renderIntervalLabel", "", "frame(s)")
    renderIntervalLabel.clearFlag(nuke.STARTLINE)
    #group.addKnob(renderIntervalLabel)

    submit_to_deadline = nuke.PyScript_Knob(
        "submit",
        "Submit to Deadline",
        "render_or_submit(nuke.thisNode())",
    )

    skip_popup = nuke.Boolean_Knob("skip_popup", "skip popup")
    skip_popup.setValue(False)
    skip_popup.clearFlag(nuke.STARTLINE)
    skip_popup.setTooltip(
        "Skip the Submission Settings popup and submit straight to Deadline "
        "using the node's current settings."
    )

    clear_temp_outputs_button = nuke.PyScript_Knob(
        "clear",
        "Clear Temp Outputs",
        "import os, quick_write;fpath = os.path.dirname(quick_write.get_file_output_knob(nuke.thisNode()).value());[os.remove(os.path.join(fpath, f)) for f in os.listdir(fpath) if os.path.isfile(os.path.join(fpath, f))]",
    )
    publish_button = nuke.PyScript_Knob(
        "publish",
        "Publish",
        "check_and_show_publisher()",
        # "from ayon_core.tools.utils import host_tools;host_tools.show_publisher(tab='Publish')",
    )
    readfrom_src = "import read_node_utils;read_node_utils.write_to_read(nuke.thisNode(), allow_relative=False)"
    readfrom = nuke.PyScript_Knob(
        "readfrom", "Read From Rendered", readfrom_src
    )

    render_local_button = nuke.PyScript_Knob(
        "renderlocal",
        "Render Local",
        "render_or_submit(nuke.thisNode(), local=True)",
    )

    div = nuke.Text_Knob("div", "", "")
    deadlinePriority = nuke.Int_Knob("deadlinePriority", "Priority")
    deadlinePriority.setRange(0, 90)
    deadlinePriority.setTooltip(
        "Render priority (0-90). For higher priority on a single submission, "
        "use the Submission Settings dialog that appears after Submit to Deadline."
    )
    deadlineChunkSize = nuke.Int_Knob("deadlineChunkSize", "    Chunk Size")
    concurrentTasks = nuke.Int_Knob("concurrentTasks", "    Concurrent Tasks")
    # deadlinePool = nuke.String_Knob("deadlinePool", "Pool")
    deadlinePool = nuke.Enumeration_Knob("deadlinePool", "Pool", hornet_deadline_utils.get_deadline_pools())
    # deadlineGroup = nuke.String_Knob("deadlineGroup", "Group")
    deadlineGroup = nuke.Enumeration_Knob("deadlineGroup", "Group", hornet_deadline_utils.get_deadline_groups())

    read_from_publish_button = nuke.PyScript_Knob(
        "readfrompublish",
        "Read From Publish",
        "read_node_utils.read_from_publish(nuke.thisNode())",
    )

    navigate_to_render_button = nuke.PyScript_Knob(
        "navigate_to_render",
        "Navigate to Render",
        "read_node_utils.navigate_to_render(nuke.thisNode())",
    )

    navigate_to_publish_button = nuke.PyScript_Knob(
        "navigate_to_publish",
        "Navigate to Publish",
        "read_node_utils.navigate_to_publish(nuke.thisNode())",
    )

    ovswarn = nuke.Text_Knob(
        "ovswarn",
        "",
        """- This node is for when pipeline steps needs to be bypassed due to an incredibly long or large render.\n
        - Publishing ovs renders will force increment your workfile.""",
    )

    concurrent_warning = nuke.Text_Knob(
        "concurrent_warning", "", "<-- Set to 1 for heavy scripts"
    )
    quick_publish_button = nuke.PyScript_Knob(
        "quick_publish",
        "Publish",
        "quick_publish_wrapper(nuke.thisNode())",
    )
    deadlineChunkSize.setValue(1)
    concurrentTasks.setValue(2)
    # deadlinePool.setValue("local")
    deadlinePool.setValue(get_deadlin_pool())
    deadlineGroup.setValue("nuke")
    deadlinePriority.setValue(90)

    usePublishRange.setFlag(nuke.STARTLINE)
    submit_to_deadline.setFlag(nuke.STARTLINE)
    publishFirst.setFlag(nuke.STARTLINE)
    publishLast.clearFlag(nuke.STARTLINE)
    render_local_button.setFlag(nuke.STARTLINE)
    deadlinePriority.setFlag(nuke.STARTLINE)
    deadlineChunkSize.clearFlag(nuke.STARTLINE)  # Don't start a new line
    concurrentTasks.clearFlag(nuke.STARTLINE)
    concurrent_warning.clearFlag(nuke.STARTLINE)
    deadlineGroup.clearFlag(nuke.STARTLINE)  # Pool and Group share a line

    group.addKnob(deadlinePriority)
    group.addKnob(deadlineChunkSize)
    group.addKnob(concurrentTasks)
    group.addKnob(concurrent_warning)
    group.addKnob(deadlinePool)
    group.addKnob(deadlineGroup)

    # Invisible filler at the end of the Pool/Group row: the last widget on a
    # line stretches to fill it, so without this the Group dropdown hogs the
    # row. With the stretch absorbed here, both dropdowns size to content.
    deadline_row_filler = nuke.Text_Knob("deadline_row_filler", " ", " ")
    deadline_row_filler.clearFlag(nuke.STARTLINE)
    group.addKnob(deadline_row_filler)

    # Spacer between the Deadline settings and the submit row.
    submit_top_gap = nuke.Text_Knob("submit_top_gap", " ", " ")
    submit_top_gap.setFlag(nuke.STARTLINE)
    group.addKnob(submit_top_gap)

    group.addKnob(submit_to_deadline)
    group.addKnob(skip_popup)

    # Spacer between the Deadline submit row and the utility buttons below.
    submit_gap = nuke.Text_Knob("submit_gap", " ", " ")
    submit_gap.setFlag(nuke.STARTLINE)
    group.addKnob(submit_gap)

    group.addKnob(render_local_button)

    # Read From Rendered works for OVS too: write_to_read reads the write's own
    # file path, which for OVS is the publish location it renders straight to.
    group.addKnob(readfrom)

    if not is_ovs:
        group.addKnob(clear_temp_outputs_button)
        group.addKnob(navigate_to_render_button)


    # OVS caveats sit just above the Publish section (the temp-files warning
    # for normal nodes lives at the top of the Rendering section instead).
    if is_ovs:
        group.addKnob(nuke.Text_Knob("tempwarn_gap", "", ""))
        group.addKnob(ovswarn)

    # --- Quick Publish settings (formerly the separate embed_quick_publish
    # knobChanged callback, now built inline so the version label can sit last).
    is_prerender = False
    try:
        qp_data = json.loads(
            group.knobs()["publish_instance"].value().replace("JSON:::", "", 1)
        )
        is_prerender = qp_data.get("productBaseType", "") == "prerender"
    except (KeyError, TypeError, ValueError):
        is_prerender = False

    publish_on_farm_checkbox = nuke.Boolean_Knob(
        "publish_on_farm", "Publish on Farm"
    )
    publish_on_farm_checkbox.setValue(False)
    publish_on_farm_checkbox.setTooltip(
        "Run the full publish on the farm, including review media "
        "generation. Unchecked runs the publish (and review extraction) "
        "locally."
    )
    publish_on_farm_checkbox.setFlag(nuke.STARTLINE)

    # Spacer, then divider line above the Publish section.
    rendering_end_gap = nuke.Text_Knob("rendering_end_gap", " ", " ")
    rendering_end_gap.setFlag(nuke.STARTLINE)
    group.addKnob(rendering_end_gap)

    publish_div = nuke.Text_Knob("publish_div", "")
    publish_div.setFlag(nuke.STARTLINE)
    group.addKnob(publish_div)

    # The grey "current version" readout in this header is synced from the
    # publish_version int (Info tab) by _update_publish_header_version(),
    # which refresh_latest_publish_display() calls at the end of the build.
    publish_header = nuke.Text_Knob("publish_header", "", "<b>Publish</b>")
    publish_header.setFlag(nuke.STARTLINE)
    group.addKnob(publish_header)

    publish_gap = nuke.Text_Knob("publish_gap", " ", " ")
    publish_gap.setFlag(nuke.STARTLINE)
    group.addKnob(publish_gap)

    # Publish range: start/end fields, a button to copy the render range, and a
    # tickbox to keep them synced automatically.
    match_to_render_button = nuke.PyScript_Knob(
        "match_to_render",
        "Match to Render Range",
        "match_publish_to_render(nuke.thisNode())",
    )
    match_to_render_button.clearFlag(nuke.STARTLINE)
    match_to_render_button.setTooltip(
        "Set the publish range to the render (Frame List) range."
    )
    auto_match_checkbox = nuke.Boolean_Knob("auto_match", "Auto Match")
    auto_match_checkbox.setValue(False)
    auto_match_checkbox.clearFlag(nuke.STARTLINE)
    auto_match_checkbox.setTooltip(
        "Automatically match the publish range to the render range whenever "
        "the Frame List changes."
    )

    group.addKnob(publishFirst)
    group.addKnob(publishLast)
    group.addKnob(match_to_render_button)
    group.addKnob(auto_match_checkbox)

    group.addKnob(publish_on_farm_checkbox)

    # Review options only make sense for non-prerender nodes.
    if not is_prerender:
        generate_review_checkbox = nuke.Boolean_Knob(
            "generate_review_media", "Generate Review Media"
        )
        generate_review_checkbox.setValue(True)
        generate_review_checkbox.setTooltip(
            "Generate review media (mp4/mov) for the rendered sequence. "
            "Unchecked skips ExtractFFmpegReview entirely."
        )
        generate_review_checkbox.setFlag(nuke.STARTLINE)

        burnin_checkbox = nuke.Boolean_Knob("burnin", "Apply burnins to Review")
        burnin_checkbox.setValue(True)
        burnin_checkbox.setTooltip(
            "Add burnin information (timecode, frame numbers, etc.) to "
            "review media. Unchecked disables burnins."
        )
        burnin_checkbox.setFlag(nuke.STARTLINE)

        group.addKnob(generate_review_checkbox)
        group.addKnob(burnin_checkbox)

    # Spacer between the publish options and the action buttons.
    publish_buttons_gap = nuke.Text_Knob("publish_buttons_gap", " ", " ")
    publish_buttons_gap.setFlag(nuke.STARTLINE)
    group.addKnob(publish_buttons_gap)

    # Publish action buttons.
    quick_publish_button.setFlag(nuke.STARTLINE)
    group.addKnob(quick_publish_button)
    group.addKnob(read_from_publish_button)
    group.addKnob(navigate_to_publish_button)

    # Spacer, then divider line under the Publish section, above the
    # addon-version stamp.
    publish_end_gap = nuke.Text_Knob("publish_end_gap", " ", " ")
    publish_end_gap.setFlag(nuke.STARTLINE)
    group.addKnob(publish_end_gap)

    version_div = nuke.Text_Knob("version_div", "")
    version_div.setFlag(nuke.STARTLINE)
    group.addKnob(version_div)

    # Visible, read-only addon-version stamp at the bottom of the main tab.
    # Text_Knob is a label (not editable); the value renders HTML, so we grey
    # it out. Sourced from the hidden ADDON_VERSION_KNOB so it reflects the
    # bundle that created the node, not the currently-running one.
    stamp_knob = group.knob(ADDON_VERSION_KNOB)
    stamped_version = stamp_knob.value().strip() if stamp_knob else ""
    version_display = nuke.Text_Knob("addon_version_display", "")
    version_display.setValue(_format_version_display(stamped_version))
    version_display.setFlag(nuke.STARTLINE)
    group.addKnob(version_display)

    # --- Info tab -----------------------------------------------------------
    # Everything added after this Tab_Knob lands on the Info tab.
    group.addKnob(nuke.Tab_Knob("info_tab", "Info"))

    # Non-editable publish-version int: persists in the script and can be
    # referenced by expressions (the DAG label reads it); refreshed from the
    # server on build / on demand. A manual refresh sits beside it (the
    # server isn't polled on script load).
    publish_version_knob = nuke.Int_Knob("publish_version", "Latest Version")
    publish_version_knob.setEnabled(False)
    publish_version_knob.setTooltip(
        "Latest successful publish version for this product (read-only)."
    )
    publish_version_knob.setFlag(nuke.STARTLINE)
    refresh_latest_button = nuke.PyScript_Knob(
        "refresh_latest_publish",
        "Refresh",
        "refresh_latest_publish_display(nuke.thisNode())",
    )
    refresh_latest_button.clearFlag(nuke.STARTLINE)
    refresh_latest_button.setTooltip("Re-query the server for the latest version")
    group.addKnob(publish_version_knob)
    group.addKnob(refresh_latest_button)
    refresh_latest_publish_display(group)

    show_instance_button = nuke.PyScript_Knob(
        "show_instance_data",
        "Show Instance Data",
        "import quick_write;quick_write.show_instance_data(nuke.thisNode())",
    )
    show_instance_button.setFlag(nuke.STARTLINE)
    show_instance_button.setTooltip(
        "Show this node's publish_instance JSON in a dialog, with an option "
        "to copy it to the clipboard."
    )
    group.addKnob(show_instance_button)

    try:
        group["views"].setValue(nuke.views()[0])
    except Exception as e:
        print(f"Error setting views: {e}")

    # Initial label-color state for the publish range knobs.
    update_publish_range_color(group)

    # Orange warning after "Output" when the render path targets another shot.
    update_output_header_warning(group)

    # The panel focuses whichever tab the last knob was added to -- the Info
    # tab here. There's no API to pick the active tab, but a reopened panel
    # always starts on the first tab, so cycle it (only if it's open).
    if group.shown():
        group.hideControlPanel()
        group.showControlPanel()




def check_existing_files_pattern(node):
    """Check if files matching the write node's output pattern already exist."""
    import glob
    import re

    try:
        out_knob = get_file_output_knob(node)
        if out_knob is not None:
            file_path = out_knob.value()
        else:
            node_name = node["name"].value()
            interior_write = "inside_" + node_name
            wnode = nuke.toNode(interior_write)
            if wnode is not None and "file" in wnode.knobs():
                file_path = wnode["file"].value()
            else:
                return True

        if not file_path:
            return True

        glob_pattern = re.sub(r"#+", "*", file_path)
        glob_pattern = re.sub(r"%\d+d", "*", glob_pattern)

        existing_files = glob.glob(glob_pattern)

        if existing_files:
            file_list = existing_files[:10]
            if len(existing_files) > 10:
                file_list.append(
                    f"... and {len(existing_files) - 10} more files"
                )

            files_display = "\n".join([os.path.basename(f) for f in file_list])
            message = f"WARNING: Files matching the output pattern already exist:\n\n{files_display}\n\nThis render may overwrite existing files.\n\nContinue anyway?"

            return nuke.ask(message)

        return True

    except Exception as e:
        log.error(f"Error checking existing files: {e}")
        return True


def get_frame_range_with_interval(node):
    """Generate frame range string with interval in format start-endxinterval"""
    try:
        # Get frame range from inside write node
        inside_name = f"inside_{node.name()}"
        inside_write = nuke.toNode(inside_name)

        if inside_write:
            start = int(inside_write["first"].value())
            end = int(inside_write["last"].value())
        else:
            start = int(nuke.root().firstFrame())
            end = int(nuke.root().lastFrame())

        # Get interval from group node
        interval = 1
        if node.knob("renderInterval"):
            interval = max(1, int(node["renderInterval"].value()))

        if interval == 1:
            return f"{start}-{end}"
        else:
            return f"{start}-{end}x{interval}"
    except Exception as e:
        log.error(f"Error getting frame range: {e}")
        return (
            f"{int(nuke.root().firstFrame())}-{int(nuke.root().lastFrame())}"
        )


def set_ranges_to_globals(node):
    """Set the render frame range and the publish range to the script globals.

    Drives the linked render first/last (which propagate to the interior write),
    the 'Frame List' string, and the publish start/end from root first/last.
    """
    first = int(nuke.root().firstFrame())
    last = int(nuke.root().lastFrame())

    for knob_name, value in (
        ("first", first),
        ("last", last),
        ("framelist", "{}-{}".format(first, last)),
        ("publishFirst", first),
        ("publishLast", last),
    ):
        knob = node.knob(knob_name)
        if knob is not None:
            knob.setValue(value)


def _parse_framelist_bounds(framelist):
    """Return (first, last) ints from a Deadline-style frame list, or None.

    Handles comma-separated ranges and 'start-endxstep' step syntax, e.g.
    '1001-1100', '1-10,20-30', '1-100x2'.
    """
    frames = []
    for part in str(framelist).split(","):
        part = part.strip().split("x")[0]  # drop any step
        for token in part.split("-"):
            token = token.strip()
            if token.isdigit():
                frames.append(int(token))
    if not frames:
        return None
    return min(frames), max(frames)


def match_publish_to_render(node):
    """Set the publish range (publishFirst/publishLast) to the render range,
    parsed from the node's 'Frame List'."""
    framelist_knob = node.knob("framelist")
    if framelist_knob is None:
        return
    bounds = _parse_framelist_bounds(framelist_knob.value())
    if bounds is None:
        return
    first, last = bounds
    if node.knob("publishFirst") is not None:
        node["publishFirst"].setValue(first)
    if node.knob("publishLast") is not None:
        node["publishLast"].setValue(last)


def update_publish_range_color(node):
    """Color the Publish Start/End labels orange when the publish range
    differs from the render (Frame List) range; plain when they match."""
    pf = node.knob("publishFirst")
    pl = node.knob("publishLast")
    framelist_knob = node.knob("framelist")
    if pf is None or pl is None or framelist_knob is None:
        return
    bounds = _parse_framelist_bounds(framelist_knob.value())
    if bounds is None:
        return
    if (int(pf.value()), int(pl.value())) != bounds:
        pf.setLabel("<font color='orange'>Publish Start</font>")
        pl.setLabel("<font color='orange'>Publish End</font>")
    else:
        pf.setLabel("Publish Start")
        pl.setLabel("Publish End")


def auto_match_publish_range():
    """knobChanged handler: when 'Frame List' changes and 'Auto Match' is on,
    keep the publish range synced to the render range. Also recolors the
    Publish Start/End labels whenever either range changes."""
    node = nuke.thisNode()
    knob = nuke.thisKnob()
    if knob is None:
        return
    if knob.name() == "framelist":
        auto = node.knob("auto_match")
        if auto is not None and auto.value():
            match_publish_to_render(node)
        update_publish_range_color(node)
    elif knob.name() in ("publishFirst", "publishLast"):
        update_publish_range_color(node)


def _confirm_publish_range(node):
    """Warn (OK/Cancel) if the publish range differs from the render range.

    Returns True to proceed, False to abort.
    """
    framelist_knob = node.knob("framelist")
    pf = node.knob("publishFirst")
    pl = node.knob("publishLast")
    if framelist_knob is None or pf is None or pl is None:
        return True
    bounds = _parse_framelist_bounds(framelist_knob.value())
    if bounds is None:
        return True
    r_first, r_last = bounds
    p_first, p_last = int(pf.value()), int(pl.value())
    if (p_first, p_last) == (r_first, r_last):
        return True
    return nuke.ask(
        "Publish range ({}-{}) differs from the render range ({}-{}).\n\n"
        "Publish anyway?".format(p_first, p_last, r_first, r_last)
    )


def read_from_rendered_selected():
    """Run Read From Rendered on the selected Hornet Write node(s).

    Menu/hotkey entry point -- unlike the on-node button it can't rely on
    nuke.thisNode(), so it acts on the current selection.
    """
    import read_node_utils
    nodes = [
        n for n in nuke.selectedNodes()
        if n.Class() == "Group" and "publish_instance" in n.knobs()
    ]
    if not nodes:
        nuke.message("Select a Hornet Write node first.")
        return
    for node in nodes:
        read_node_utils.write_to_read(node, allow_relative=False)


PRIORITY_KNOB_MAX = 90


def on_priority_clamp():
    """knobChanged handler that hard-caps deadlinePriority at PRIORITY_KNOB_MAX.

    setRange only constrains the slider -- typed entries can still exceed the
    range -- so we clamp here for hard enforcement. Higher priorities are
    reachable via the Submission Settings dialog at submit time.
    """
    node = nuke.thisNode()
    knob = nuke.thisKnob()
    if knob is None or knob.name() != "deadlinePriority":
        return
    try:
        v = int(knob.value())
    except Exception:
        return
    if v > PRIORITY_KNOB_MAX:
        knob.setValue(PRIORITY_KNOB_MAX)
        nuke.tprint(
            f"Priority capped at {PRIORITY_KNOB_MAX}. Use the Submission "
            f"Settings dialog at submit time for higher one-off values."
        )


# Fall back to object when nukescripts is unavailable (headless import) so the
# module still loads; the dialog is only ever instantiated in a GUI session.
_PanelBase = nukescripts.PythonPanel if nukescripts else object


class SubmitSettingsDialog(_PanelBase):
    """One-shot dialog shown before farm submission. Values entered here are
    used for this submission only and are NOT written back to the node -- so
    a priority of, say, 99 here doesn't persist past this submit."""

    def __init__(self, node):
        nukescripts.PythonPanel.__init__(self, "Submission Settings")
        self._node = node

        # Frame list -- pre-filled from the node's framelist knob. Edits here
        # are forwarded to the Deadline submission via the overrides dict
        # (get_frame_range_for_deadline reads from knobValues["framelist"]).
        self.framelist = nuke.String_Knob("framelist", "Frame List")
        fl_knob = node.knob("framelist")
        self.framelist.setValue(fl_knob.value() if fl_knob else "")
        self.addKnob(self.framelist)

        self.priority = nuke.Int_Knob("priority", "Priority")
        self.priority.setValue(int(node.knob("deadlinePriority").value()))
        # No range cap here -- that's the whole point of this dialog
        self.addKnob(self.priority)

        self.chunk = nuke.Int_Knob("chunk", "Chunk Size")
        self.chunk.setValue(int(node.knob("deadlineChunkSize").value()))
        self.addKnob(self.chunk)

        self.concurrent = nuke.Int_Knob("concurrent", "Concurrent Tasks")
        self.concurrent.setValue(int(node.knob("concurrentTasks").value()))
        self.addKnob(self.concurrent)

        pool_node_knob = node.knob("deadlinePool")
        self.pool = nuke.Enumeration_Knob(
            "pool", "Pool", list(pool_node_knob.values())
        )
        self.pool.setValue(pool_node_knob.value())
        self.addKnob(self.pool)

        group_node_knob = node.knob("deadlineGroup")
        self.group = nuke.Enumeration_Knob(
            "group", "Group", list(group_node_knob.values())
        )
        self.group.setValue(group_node_knob.value())
        self.addKnob(self.group)

    def overrides(self):
        """Return the dialog's values as a dict keyed by node knob name."""
        return {
            "framelist": self.framelist.value(),
            "deadlinePriority": int(self.priority.value()),
            "deadlineChunkSize": int(self.chunk.value()),
            "concurrentTasks": int(self.concurrent.value()),
            "deadlinePool": self.pool.value(),
            "deadlineGroup": self.group.value(),
        }


def _copy_to_clipboard(text):
    """Put `text` on the system clipboard via Qt. Returns True on success."""
    QtWidgets = None
    try:
        from PySide2 import QtWidgets
    except ImportError:
        try:
            from PySide6 import QtWidgets
        except ImportError:
            pass
    if QtWidgets is not None and QtWidgets.QApplication.instance() is not None:
        QtWidgets.QApplication.clipboard().setText(text)
        return True
    return False


class InstanceDataDialog(_PanelBase):
    """Read-only view of a node's publish_instance JSON with a copy button."""

    def __init__(self, node):
        nukescripts.PythonPanel.__init__(
            self, f"Instance Data -- {node.name()}"
        )
        try:
            self._json_text = json.dumps(parse_publish_instance(node), indent=2)
        except Exception as e:
            self._json_text = f"Could not parse instance data: {e}"

        self.text = nuke.Multiline_Eval_String_Knob("instance_json", " ")
        self.text.setValue(self._json_text)
        self.text.setFlag(nuke.READ_ONLY)
        self.addKnob(self.text)

        self.copy_btn = nuke.PyScript_Knob("copy_json", "Copy to Clipboard")
        self.addKnob(self.copy_btn)

    def knobChanged(self, knob):
        if knob is not None and knob.name() == "copy_json":
            if _copy_to_clipboard(self._json_text):
                nuke.message("Instance data copied to clipboard.")
            else:
                nuke.message("Could not access the clipboard.")


def show_instance_data(node):
    """Button entry point: show the node's instance JSON in a dialog."""
    if INSTANCE_DATA_KNOB not in node.knobs():
        nuke.message("No instance data on this node.")
        return
    InstanceDataDialog(node).showModalDialog()


def render_or_submit(node, local=False):
    """ wrapper for Deadline and Render Local buttons."""
    # Pre-flight: scan whole script for duplicate variants. Cheap enough to
    # do every submit; deliberately not wired to the variant_field knob's
    # knobChanged so we don't re-scan on every keystroke.
    duplicates = find_duplicate_variants_in_script()
    if duplicates:
        lines = []
        for variant, names in duplicates.items():
            lines.append(
                f"  Variant '{variant.lstrip('_')}' is used by: "
                f"{', '.join(names)}"
            )
        nuke.message(
            "Cannot render: duplicate variant names found in this script.\n\n"
            + "\n".join(lines)
            + "\n\nEach Hornet write node must have a unique variant. "
            "Edit the Variant field on the colliding nodes and try again."
        )
        log.warning(
            f"Render aborted on '{node.name()}' -- duplicate variants: "
            f"{list(duplicates.keys())}"
        )
        return
    if not check_shot_context(node):
        print("aborted shot context mismatch")
        return
    if lib.get_node_data(node, "publish_instance").get("is_ovs"):
        update_ovs_write_version(node)
    if node.knob("_cancelled") and node["_cancelled"].value():
        print("Cancelled by user")
        return

    # The render path can still target another shot's work area even after
    # the instance data was adopted (the context fix patches JSON only, it
    # does not repath). Rendering there is allowed, but never silent:
    # proceed / adopt current shot / cancel. Covers Deadline AND local.
    foreign_prefix = _render_path_shot_mismatch(node)
    if foreign_prefix is not None:
        choice = _prompt_foreign_render_path(node, foreign_prefix)
        if choice == "cancel":
            print("Render cancelled: foreign render path")
            return
        if choice == "adopt":
            ok, err = adopt_node_to_current_shot(node)
            if ok:
                nuke.message(
                    f"'{node.name()}' now renders into the current shot.\n\n"
                    f"Check the updated output path, then render again."
                )
            else:
                nuke.message(f"Adopt failed:\n\n{err}")
            # Deliberately no render either way -- the user re-renders after
            # checking the new path.
            return
        log.warning(
            f"Foreign-shot render allowed by user on '{node.name()}' "
            f"(work area: {foreign_prefix})"
        )
    if local:
        # Group-traversal lookup rather than nuke.toNode(f"inside_{name}") so
        # a stale interior name (e.g. from a paste or a failed rename) doesn't
        # crash the render.
        interior = _find_interior_write(node)
        if interior is None:
            nuke.message(
                f"Could not find the interior Write node inside "
                f"'{node.name()}'. The node may be corrupted -- try deleting "
                f"and recreating it."
            )
            return
        interior.knob("Render").execute()
        save_script_with_render(
            get_file_output_knob(node).getValue(),
            lib.get_node_data(node, "publish_instance")["is_ovs"],
        )
    else:
        # Farm path. When "skip popup" is on, submit straight from the node's
        # knobs. Otherwise show the one-shot settings dialog and pass its values
        # through as overrides (not written back to the node), so the priority
        # clamp can't clobber them and nothing persists past this submit.
        skip = node.knob("skip_popup")
        if skip is not None and skip.value():
            deadlineNetworkSubmit(node=node)
            return
        dialog = SubmitSettingsDialog(node)
        if not dialog.showModalDialog():
            print(f"Submission cancelled by user on '{node.name()}'")
            return
        deadlineNetworkSubmit(node=node, overrides=dialog.overrides())


def update_ovs_write_version(node):
    """Set the version of ovs quickwrite filepaths based on latest target version."""

    # Clear any previous cancellation flag
    if node.knob("_cancelled"):
        node["_cancelled"].setValue(False)

    if INSTANCE_DATA_KNOB in node.knobs():
        data = json.loads(
            node.knobs()[INSTANCE_DATA_KNOB].value().replace("JSON:::", "", 1)
        )
        if data.get("is_ovs", False):
            if not check_existing_files_pattern(node):
                log.info("Render/submission cancelled due to existing files.")
                # Set cancellation flag
                if not node.knob("_cancelled"):
                    cancel_knob = nuke.Boolean_Knob("_cancelled", "")
                    cancel_knob.setVisible(False)
                    node.addKnob(cancel_knob)
                node["_cancelled"].setValue(True)
                return

    if INSTANCE_DATA_KNOB in node.knobs():
        data = json.loads(
            node.knobs()[INSTANCE_DATA_KNOB].value().replace("JSON:::", "", 1)
        )
        if "is_ovs" not in data.keys():
            log.warning(
                f"{node.name()} is missing is_ovs key, it is probably an old node"
            )
        else:
            if data["is_ovs"] and not check_existing_files_pattern(node):
                prompt = nuke.ask(
                    "Set render output path to latest new product version?"
                )
                if prompt:
                    try:
                        fpath_new = get_ovs_pathing(data)
                        node_name = node["name"].value()
                        interior_write = "inside_" + node_name
                        wnode = nuke.toNode(interior_write)
                        if wnode is not None:
                            wnode["file"].setValue(fpath_new)
                            out_knob = get_file_output_knob(node)
                            if out_knob is not None:
                                out_knob.setValue(fpath_new)
                            # Re-stamp: records the addon version that last
                            # re-pointed this OVS node, and backfills the knob
                            # on older OVS nodes created before stamping existed.
                            stamp_addon_version(node)
                            log.info(
                                f"Updating ovs write path for {node_name}: {fpath_new}"
                            )
                            nuke.toNode(node_name)
                        else:
                            log.warning(
                                f"Interior write node {interior_write} not found, cannot set file path."
                            )
                    except Exception as e:
                        log.error(f"Error setting ovs write version: {e}")
                        nuke.message(
                            "Error setting ovs write version. Check the console for details."
                        )
                else:
                    log.warning(
                        f"{node.name()} is potentially set to output to an old version, this may overwrite existing files on disk"
                    )
    else:
        log.debug(
            f"{node.name()} is missing instance data knob, cannot set version"
        )


OBSOLETE_BACKDROP_PREFIX = "obsolete_locate_"


def _backdrop_around_node(node, version):
    """Draw a red warning backdrop tightly around a single obsolete node."""
    pad = 40
    header = 24  # room for the label above the node
    bd = nuke.nodes.BackdropNode(
        xpos=node.xpos() - pad,
        ypos=node.ypos() - pad - header,
        bdwidth=node.screenWidth() + pad * 2,
        bdheight=node.screenHeight() + pad * 2 + header,
        tile_color=int(0xE8822EFF),
        note_font_size=28,
        label="OBSOLETE WRITE NODE\n{}".format(version),
    )
    bd.knob("name").setValue(OBSOLETE_BACKDROP_PREFIX + node.name())
    return bd


def locate_obsolete_nodes(notify=True):
    """Highlight the AYON write nodes that are out of date.

    A node is obsolete when its stamped addon version differs from the current
    ADDON_VERSION, or when it carries no stamp at all (created before stamping
    existed). Each match gets an orange warning backdrop so it can be found in
    the DAG. Intended for checking extant nodes after an addon update.

    Args:
        notify (bool): show the summary/all-clear dialog. Pass False for a
            silent run (e.g. on script load) -- backdrops are still drawn.
    """
    obsolete = []
    for node in get_all_ayon_write_nodes():
        knob = node.knob(ADDON_VERSION_KNOB)
        stamped = knob.value().strip() if knob else ""
        if stamped == ADDON_VERSION:
            continue
        obsolete.append((node, stamped or "<unstamped>"))

    # Clear backdrops from a previous run so repeated calls don't stack up.
    for bd in nuke.allNodes("BackdropNode"):
        if bd.name().startswith(OBSOLETE_BACKDROP_PREFIX):
            nuke.delete(bd)

    if not obsolete:
        if notify:
            nuke.message(
                f"All AYON write nodes are up to date (addon {ADDON_VERSION})."
            )
        return []

    for node, ver in obsolete:
        _backdrop_around_node(node, ver)

    if notify:
        lines = "\n".join(
            f"  {node.name()}  —  {ver}"
            for node, ver in sorted(obsolete, key=lambda t: t[0].name())
        )
        nuke.message(
            f"Current addon version: {ADDON_VERSION}\n\n"
            f"{len(obsolete)} obsolete write node(s) found and highlighted:"
            f"\n\n{lines}"
        )
    return [(node.name(), ver) for node, ver in obsolete]


def locate_obsolete_on_load():
    """onScriptLoad handler: silently highlight obsolete nodes (GUI only).

    Draws warning backdrops when out-of-date nodes exist and does nothing
    otherwise. Skipped in terminal/farm sessions, wrapped so a failure can
    never block loading a script, and restores the script's modified state so
    merely opening it to look isn't flagged as an unsaved change.
    """
    if not nuke.GUI:
        return
    try:
        was_modified = nuke.Root().modified()
        found = locate_obsolete_nodes(notify=False)
        if not was_modified:
            nuke.Root().setModified(False)
        if found:
            lines = "\n".join(
                f"  {name}  —  {ver}" for name, ver in sorted(found)
            )
            nuke.message(
                "WARNING: {} obsolete AYON write node(s) found in this script "
                "(current addon {}).\nThey are highlighted with orange "
                "backdrops in the node graph.\n\n{}".format(
                    len(found), ADDON_VERSION, lines
                )
            )
    except Exception as e:
        log.warning(f"locate_obsolete_on_load failed: {e}")


def get_all_ayon_write_nodes():
    ayon_write_nodes = []

    for node in nuke.allNodes():
        if node.Class() == "Group":
            # Check if it has AYON instance data
            if INSTANCE_DATA_KNOB in node.knobs():
                ayon_write_nodes.append(node)

    return ayon_write_nodes


def parse_publish_instance(qnode):
    return json.loads(qnode.knob(api.INSTANCE_DATA_KNOB).value()[7:])


def _publish_product_context(node):
    """Return (project_name, product_name, folder_path) for the node's product."""
    try:
        data = json.loads(
            node.knob(INSTANCE_DATA_KNOB).value().replace("JSON:::", "", 1)
        )
    except (AttributeError, ValueError):
        return None, None, None
    project = os.environ.get("AYON_PROJECT_NAME")
    folder_path = data.get("folderPath") or os.environ.get("AYON_FOLDER_PATH")
    product = data.get("productName")
    return project, product, folder_path


def get_latest_publish_version(node):
    """(version_int, version_name) of the latest published version for the
    node's product, or (0, 'v000') if it has never been published."""
    project, product, folder_path = _publish_product_context(node)
    if not (project and product and folder_path):
        return 0, "v000"
    return lib.get_server_pub_version(project, product, folder_path)


def _update_publish_header_version(node):
    """Sync the grey 'current version' readout in the Publish header text to
    the 'publish_version' int (which lives on the Info tab)."""
    header = node.knob("publish_header")
    version_knob = node.knob("publish_version")
    if header is None or version_knob is None:
        return
    header.setValue(
        "<b>Publish</b>&nbsp;&nbsp;&nbsp;<font color='#808080'>"
        "current publish v{:03d}</font>".format(int(version_knob.value()))
    )


def refresh_latest_publish_display(node):
    """Query the server and update the non-editable 'publish_version' int and
    the Publish-header readout that mirrors it.

    On query error the existing (persisted) int value is left untouched, but
    the header readout is still synced to it.
    """
    knob = node.knob("publish_version")
    if knob is None:
        return
    try:
        latest, _ = get_latest_publish_version(node)
    except Exception as e:
        log.warning(f"Could not query latest publish version: {e}")
        _update_publish_header_version(node)
        return
    knob.setValue(int(latest))
    _update_publish_header_version(node)


def _confirm_publish_version(node):
    """Warn (OK/Cancel) if the version about to be published already exists.

    Only meaningful when workfile/publish versions are linked (Hornet setup):
    the publish then targets the workfile version, so re-publishing an already-
    published workfile version collides and fails cryptically at pyblish time.
    When versions are not linked AYON auto-increments, so there is nothing to
    warn about. Fails open (returns True) on any error.
    """
    try:
        if not lib.is_version_file_linked():
            return True
        latest, _ = get_latest_publish_version(node)
        workfile_version = int(lib.get_version_from_path(nuke.Root().name()))
    except Exception as e:
        log.warning(f"Publish version pre-check skipped: {e}")
        return True

    if workfile_version > latest:
        return True

    return nuke.ask(
        "Version v{:03d} already exists for this product "
        "(latest published is v{:03d}).\n\n"
        "Publishing over an existing version will fail. Continue anyway?"
        .format(workfile_version, latest)
    )


def _confirm_render_exists(node):
    """Abort the publish (with a message) if no rendered frames exist on disk.

    Publishing a write node that was never rendered fails deep in pyblish with
    an opaque error, so catch the obvious 'nothing rendered' case up front.
    Returns True to proceed, False to abort.
    """
    import glob
    import re

    out_knob = get_file_output_knob(node)
    if out_knob is None:
        return True
    path = out_knob.evaluate() or out_knob.value()
    if not path:
        return True

    # Turn the frame-numbered output path into a glob and see if anything exists.
    glob_pattern = re.sub(r"%0?\d*d", "*", path)
    glob_pattern = re.sub(r"#+", "*", glob_pattern)
    if glob.glob(glob_pattern):
        return True

    nuke.message(
        "No rendered frames found for this write node:\n\n{}\n\n"
        "Render before publishing.".format(path)
    )
    return False


def quick_publish_wrapper(node):
    from hornet_publish_utils import quick_publish

    # Same shot-context gate as render/submit: without it a node pasted from
    # another shot publishes into that other shot with no warning at all.
    if not check_shot_context(node):
        print("Publish cancelled: shot context mismatch")
        return

    if not _confirm_render_exists(node):
        print("Publish cancelled: no rendered frames found")
        return

    if not _confirm_publish_range(node):
        print("Publish cancelled: publish range differs from render range")
        return

    if not _confirm_publish_version(node):
        print("Publish cancelled: target version already exists")
        return

    review_knob = node.knobs().get("generate_review_media")
    review = review_knob.value() if review_knob else False

    integrate_farm_knob = node.knobs().get("publish_on_farm")
    integrate_farm = (
        integrate_farm_knob.value() if integrate_farm_knob else False
    )
    # review extraction follows the publish location
    review_farm = integrate_farm

    burnin_knob = node.knobs().get("burnin")
    burnin = burnin_knob.value() if burnin_knob else False

    with nuke.root():
        quick_publish(
            node,
            review=review,
            review_farm=review_farm,
            integrate_farm=integrate_farm,
            burnin=burnin,
        )

    # Reflect the new version in the read-only label (best effort).
    refresh_latest_publish_display(node)


def get_deadlin_pool():
    settings = get_current_project_settings()
    try:
        return settings["deadline"]["publish"]["CollectDeadlinePools"][
            "primary_pool"
        ]
    except KeyError:
        return "local"

def refresh_deadline_pools(quick_write_node):
    pool_knob = quick_write_node.knobs().get("deadlinePool")
    # group_knob = quick_write_node.knobs().get("deadlineGroup")
    current_pool = pool_knob.value()
    # current_group = group_knob.value()
    pool_knob.setValues(hornet_deadline_utils.get_deadline_pools())
    if current_pool in pool_knob.values():
        pool_knob.setValue(current_pool)
    else:
        pool_knob.setValue(pool_knob.values()[0])

def refresh_deadline_groups(quick_write_node):
    group_knob = quick_write_node.knobs().get("deadlineGroup")
    current_group = group_knob.value()
    group_knob.setValues(hornet_deadline_utils.get_deadline_groups())
    if current_group in group_knob.values():
        group_knob.setValue(current_group)
    else:
        group_knob.setValue(group_knob.values()[0])


def refresh_deadline_callback():
    node = nuke.thisNode()
    knob = nuke.thisKnob()
    if knob.name() == "deadlinePool":
        try:
            refresh_deadline_pools(node)
        except Exception as e:
            log.error(f"Error refreshing deadline pools: {e}")
    elif knob.name() == "deadlineGroup":
        try:
            refresh_deadline_groups(node)
        except Exception as e:
            log.error(f"Error refreshing deadline groups: {e}")

## we may have a node copied from another script with the wrong instance data
## this can result in rendering to someone elses shot. Alert the user
def check_shot_context(node):
    if INSTANCE_DATA_KNOB not in node.knobs():
        return True
    data = json.loads(
        node.knobs()[INSTANCE_DATA_KNOB].value().replace("JSON:::", "", 1)
    )
    family = data.get("productBaseType", "render")
    variant = data.get("variant", "_Main")
    is_ovs = data.get("is_ovs", False)
    expected = quick_node_data(family, variant, is_ovs)

    # these fields are the true context
    mismatched = []
    changes_details = []
    for key in ("folderPath", "hierarchy", "folder", "task"):
        if data.get(key) != expected.get(key):
            mismatched.append(key)
            changes_details.append(
                f"  {key}: '{data.get(key)}' -> '{expected.get(key)}'"
            )

    if not mismatched:
        return True

    node_name = node.name()
    changes_str = "\n".join(changes_details)
    msg = (
        f"Node '{node_name}' has node data that doesn't match the shot context.\n\n"
        f"This can happen when a node is copied from another script.\n\n"
        f"The following changes will be made:\n"
        f"{changes_str}\n\n"
        f"Update instance data to match the current context?"
    )
    if nuke.ask(msg):
        for key in ("folderPath", "hierarchy", "folder", "task",
                     "subset", "productName"):
            data[key] = expected[key]
        node.knobs()[INSTANCE_DATA_KNOB].setValue(
            "JSON:::" + json.dumps(data)
        )
        log.info(f"Patched instance data on '{node_name}' to match current context")
        try:
            clear_foreign_shot_flag(node)
        except Exception as e:
            log.warning(f"Could not clear foreign-shot flag: {e}")
        nuke.message(f"Updated '{node_name}' to current shot context.")
        return True

    log.warning(f"Shot context mismatch on '{node_name}', user declined fix — blocking submission")
    return False


# ---------------------------------------------------------------------------
# Variant rename (ported from the nuke_hornet_write_dev rez package)
# ---------------------------------------------------------------------------


def _normalize_variant(raw):
    """Title-case, sanitize and underscore-prefix a variant string.

    Non-alphanumeric characters are dropped (node names can't contain spaces
    or punctuation, so letting them through would make the rename fail).
    Returns None if nothing usable remains.
    """
    v = (raw or "").strip()
    if not v:
        return None
    v = re.sub(r"[^A-Za-z0-9_]", "", v.title()).strip("_")
    if not v:
        return None
    return "_" + v


def find_duplicate_variants_in_script():
    """Scan all Hornet write nodes and return a dict mapping each duplicated
    variant to the list of node names that share it. Empty dict if no
    duplicates.

    Cheap -- just parses each node's publish_instance JSON, no I/O.
    """
    by_variant = {}
    for n in get_all_ayon_write_nodes():
        try:
            v = parse_publish_instance(n).get("variant")
        except Exception:
            continue
        if not v:
            continue
        by_variant.setdefault(v, []).append(n.name())
    return {v: names for v, names in by_variant.items() if len(names) > 1}


def _compute_subset(family, task, variant):
    """Mirror the subset construction used in quick_node_data()."""
    return family + task + variant


def _rewrite_path_with_subset(old_path, old_subset, new_subset):
    """Substitute the subset token in an existing file path.

    Used directly only as a fallback when we can't anchor the rewrite to the
    standard '/renders/nuke/' marker (e.g. OVS or a manually-edited path).
    Both directory and filename components contain the subset, so a simple
    string replace covers both occurrences. Returns None if old_subset is
    not found.
    """
    if not old_path or not old_subset:
        return None
    if old_subset not in old_path:
        return None
    return old_path.replace(old_subset, new_subset)


def _rewrite_path_for_context(old_path, old_subset, new_subset):
    """Rebuild a Hornet render path for a variant rename OR a cross-shot
    context update.

    Two-step rewrite anchored on the '/renders/nuke/' marker:
      1. Replace everything before the marker with the current AYON_WORKDIR.
         For same-shot variant renames this is a no-op; for cross-shot
         pastes it re-anchors the path to the new shot's workspace.
      2. Substitute old_subset -> new_subset in the remainder.

    Returns None if the marker isn't found in old_path (e.g. OVS path which
    targets the publish tree directly, or a path that's been manually
    edited away from the template shape). Caller should fall back to
    subset-only substitution and warn.
    """
    if not old_path:
        return None
    new_work = os.environ.get("AYON_WORKDIR", "").replace("\\", "/")
    if not new_work:
        return None

    marker = "/renders/nuke/"
    idx = old_path.replace("\\", "/").find(marker)
    if idx == -1:
        return None

    remainder = old_path.replace("\\", "/")[idx + len(marker):]
    if old_subset and old_subset in remainder:
        remainder = remainder.replace(old_subset, new_subset)
    return f"{new_work}{marker}{remainder}"


def _find_interior_write(group_node):
    """Find the Write node living inside a Hornet write group, regardless of
    its current name.

    Nuke's paste behaviour doesn't always preserve the `inside_<groupname>`
    naming convention, so name-based lookups (`nuke.toNode(f"inside_X")`) are
    unreliable after a paste. Traversing the group's children is robust.
    """
    try:
        for child in group_node.nodes():
            if child.Class() == "Write":
                return child
    except Exception as e:
        log.warning(f"Could not traverse group '{group_node.name()}': {e}")
    return None


def _rename_group_and_interior(node, old_node_name, new_node_name):
    """Rename the group node and its interior write. Returns (ok, error_msg).

    Critical ordering: we grab a *reference* to the interior write BEFORE
    renaming the group, so we don't depend on the interior following the
    `inside_<groupname>` naming convention. The reference stays valid across
    the group rename.
    """
    interior = _find_interior_write(node)
    try:
        node["name"].setValue(new_node_name)
    except Exception as e:
        return False, f"Could not rename group node: {e}"
    if interior is not None:
        try:
            interior["name"].setValue(f"inside_{new_node_name}")
        except Exception as e:
            log.warning(
                f"Renamed group but could not rename interior write: {e}"
            )
    else:
        log.warning(
            f"No interior Write node found inside group '{new_node_name}'. "
            f"Render/submit may fail until the interior is named "
            f"'inside_{new_node_name}'."
        )
    return True, None


def _update_path_knobs(node, new_node_name, old_subset, new_subset):
    """Rewrite both the interior write's `file` knob and the group's
    file-output display knob.

    Primary strategy: _rewrite_path_for_context, which re-anchors the path
    on '/renders/nuke/' to handle cross-shot pastes (where the work-dir
    prefix itself changes, not just the subset). Falls back to subset-only
    substitution if the marker isn't found (OVS or manually-edited paths).
    """

    def _rewrite(old_path):
        rewritten = _rewrite_path_for_context(old_path, old_subset, new_subset)
        if rewritten is not None:
            return rewritten
        # Fallback for OVS / non-standard paths
        return _rewrite_path_with_subset(old_path, old_subset, new_subset)

    interior = _find_interior_write(node)
    if interior is not None and "file" in interior.knobs():
        new_file = _rewrite(interior["file"].value())
        if new_file is None:
            log.warning(
                f"Could not auto-update interior file path on "
                f"'{new_node_name}'. Manual update may be needed."
            )
        else:
            interior["file"].setValue(new_file)

    out_knob = get_file_output_knob(node)
    if out_knob is not None:
        new_out = _rewrite(out_knob.value())
        if new_out is not None:
            out_knob.setValue(new_out)


def _apply_variant_change(node, new_variant_raw, interactive=True):
    """Apply a variant rename to a Hornet write group node.

    Updates publish_instance JSON, renames group + interior write, and
    rewrites file path knobs. Performs validation (empty, duplicate within
    script, optional server-side product collision warning).

    interactive=False skips the server-collision confirmation dialog (used
    by the paste auto-dedup, which must not pop modals mid-paste).

    Returns (True, None) on success, (False, message) on failure.
    """
    new_variant = _normalize_variant(new_variant_raw)
    if not new_variant:
        return False, "Variant cannot be empty."

    if INSTANCE_DATA_KNOB not in node.knobs():
        return False, "Node has no publish_instance data."

    data = json.loads(
        node.knobs()[INSTANCE_DATA_KNOB].value().replace("JSON:::", "", 1)
    )

    old_variant = data.get("variant", "")
    if old_variant == new_variant:
        return True, None  # no-op

    family = data.get("productBaseType", "render")
    task = data.get("task") or os.environ.get("AYON_TASK_NAME", "")

    old_subset = _compute_subset(family, task, old_variant)
    new_subset = _compute_subset(family, task, new_variant)
    old_node_name = node.name()
    new_node_name = new_subset

    # Within-script collision check
    for other in get_all_ayon_write_nodes():
        if other.fullName() == node.fullName():
            continue
        try:
            other_variant = parse_publish_instance(other).get("variant")
        except Exception:
            continue
        if other_variant == new_variant:
            return False, (
                f"Variant '{new_variant.lstrip('_')}' is already used by node "
                f"'{other.name()}'."
            )

    # Server-side existence check: an interactive rename to a product that
    # already has published versions is refused outright (publishing to it
    # would version-up the existing product). Non-interactive (paste dedup)
    # proceeds with a log -- in-script uniqueness is its job, and the
    # publish-time version pre-check still guards the collision.
    try:
        import ayon_api
        project_name = os.environ.get("AYON_PROJECT_NAME")
        folder_path = data.get("folderPath")
        if project_name and folder_path:
            folder = ayon_api.get_folder_by_path(project_name, folder_path)
            if folder:
                existing = ayon_api.get_product_by_name(
                    project_name, new_subset, folder["id"]
                )
                if existing:
                    if not interactive:
                        log.info(
                            f"Product '{new_subset}' already exists on the "
                            f"server; proceeding (non-interactive rename)."
                        )
                    else:
                        return False, (
                            f"Product '{new_subset}' already exists on the "
                            f"server with published versions.\n\n"
                            f"Choose a different variant name."
                        )
    except Exception as e:
        log.warning(f"Server-side product existence check skipped: {e}")

    # Update JSON in-memory first
    data["variant"] = new_variant
    data["subset"] = new_subset
    data["productName"] = new_subset

    # Rename nodes
    ok, err = _rename_group_and_interior(node, old_node_name, new_node_name)
    if not ok:
        return False, err

    # Update file path knobs via subset substitution
    _update_path_knobs(node, new_node_name, old_subset, new_subset)

    # Persist updated JSON last (stale JSON > inconsistent JSON if anything
    # above failed partially)
    node.knobs()[INSTANCE_DATA_KNOB].setValue("JSON:::" + json.dumps(data))

    # Sync the visible variant_field knob (if present). Skip if we're already
    # inside its knobChanged callback to avoid re-entry -- that path sets the
    # knob itself after we return.
    vf = node.knob("variant_field")
    if vf is not None:
        try:
            current_thiskob = nuke.thisKnob()
        except Exception:
            current_thiskob = None
        if current_thiskob is None or current_thiskob.name() != "variant_field":
            try:
                vf.setValue(new_variant.lstrip("_"))
            except Exception:
                pass

    # The product changed, so re-query the latest-publish readout (Info tab
    # int + Publish header). Best effort.
    try:
        refresh_latest_publish_display(node)
    except Exception as e:
        log.warning(f"Could not refresh publish version after rename: {e}")

    log.info(
        f"Variant change: '{old_variant}' -> '{new_variant}' "
        f"(node '{old_node_name}' -> '{new_node_name}')"
    )
    return True, None


def on_variant_field_changed():
    """knobChanged handler for the variant_field String_Knob on the panel.

    Registered globally on Group nodes; filters to Hornet write nodes and to
    the variant_field knob specifically.
    """
    node = nuke.thisNode()
    knob = nuke.thisKnob()
    if knob is None or knob.name() != "variant_field":
        return
    if INSTANCE_DATA_KNOB not in node.knobs():
        return

    new_raw = knob.value()
    try:
        current = parse_publish_instance(node).get("variant", "")
    except Exception:
        current = ""

    normalized = _normalize_variant(new_raw)
    if normalized is None or normalized == current:
        return

    ok, err = _apply_variant_change(node, new_raw)
    if not ok:
        # Restore the visible field to the unchanged variant
        try:
            knob.setValue(current.lstrip("_"))
        except Exception:
            pass
        nuke.message(f"Variant change rejected:\n\n{err}")
        return

    # Normalize what the user sees (e.g. "main " -> "Main")
    try:
        knob.setValue(normalized.lstrip("_"))
    except Exception:
        pass


# --- Paste dedup -----------------------------------------------------------
# A copy/pasted Hornet write node arrives with the same variant as its
# source, silently duplicating a product. dedup_variant_on_create below
# auto-increments the pasted node's variant to keep variants unique.
#
# Script load must NOT trigger it (opening a saved script must never mutate
# it), but onCreate fires for every node during load too. Loading is
# detected with the Root-onCreate + deferred-clear pair: every load (startup,
# File > New, File > Open) creates a Root first, while a paste never does.
# The deferred clear runs only after the synchronous load finishes.

_SCRIPT_LOADING = {"active": False}


def _defer(func):
    """Run `func` after the current synchronous operation (script load,
    paste) finishes, via a 0-ms Qt timer -- nuke has no executeDeferred
    (that's a Maya API). Falls back to calling immediately when no Qt app
    exists (terminal/farm sessions, where these callbacks aren't registered
    anyway -- menu.py is GUI-only)."""
    QtCore = None
    try:
        from PySide2 import QtCore
    except ImportError:
        try:
            from PySide6 import QtCore
        except ImportError:
            pass
    if QtCore is not None and QtCore.QCoreApplication.instance() is not None:
        QtCore.QTimer.singleShot(0, func)
    else:
        func()


def flag_script_loading():
    """onCreate(Root) handler: marks the start of a script load."""
    _SCRIPT_LOADING["active"] = True
    _defer(_clear_script_loading_flag)


def _clear_script_loading_flag():
    _SCRIPT_LOADING["active"] = False


def increment_name(name):
    """General-purpose: increment a trailing numeric counter on a string.

    Behaviour:
      - If the string ends in digits, increment them while preserving padding
        ("Main_01" -> "Main_02", "Hero_009" -> "Hero_010"). Overflow naturally
        extends the width ("999" -> "1000").
      - If the string ends in non-digit characters, append "_1".
      - Empty input returns "_1".

    The match is purely on trailing digits, so internal numerics are left
    alone ("Layer3_Main" -> "Layer3_Main_1").
    """
    if not name:
        return "_1"
    m = re.search(r"(\d+)$", name)
    if m:
        num_str = m.group(1)
        padding = len(num_str)
        new_num_str = str(int(num_str) + 1).zfill(padding)
        return name[: m.start()] + new_num_str
    return name + "_1"


def _node_matches_context(node):
    """True when the node's stored folderPath/task match the current env."""
    try:
        data = parse_publish_instance(node)
    except Exception:
        return True
    env_folder = os.environ.get("AYON_FOLDER_PATH", "")
    env_task = os.environ.get("AYON_TASK_NAME", "")
    stored_folder = data.get("folderPath") or env_folder
    stored_task = data.get("task") or env_task
    return stored_folder == env_folder and stored_task == env_task


def flag_foreign_shot(node):
    """Mark a node pasted from another shot: orange tile + DAG label prefix.

    Passive warning only -- check_shot_context still gates submit/publish,
    and accepting its fix clears the flag via clear_foreign_shot_flag.
    """
    try:
        stored_folder = parse_publish_instance(node).get("folderPath", "?")
    except Exception:
        stored_folder = "?"
    label = node.knob("label")
    if label is not None and not label.value().startswith(
        FOREIGN_SHOT_LABEL_PREFIX
    ):
        label.setValue(
            f"{FOREIGN_SHOT_LABEL_PREFIX}{stored_folder}\n{label.value()}"
        )
    if node.knob("tile_color") is not None:
        node["tile_color"].setValue(FOREIGN_SHOT_TILE_COLOR)
    nuke.tprint(
        f"'{node.name()}' was pasted from another shot ({stored_folder}) -- "
        f"flagged orange in the DAG. Submitting will offer to adopt it."
    )


def clear_foreign_shot_flag(node):
    """Remove the foreign-shot label prefix and restore the tile colour."""
    label = node.knob("label")
    if label is not None and label.value().startswith(
        FOREIGN_SHOT_LABEL_PREFIX
    ):
        lines = label.value().split("\n")
        label.setValue("\n".join(lines[1:]))
    if node.knob("tile_color") is not None:
        try:
            is_prerender = (
                parse_publish_instance(node).get("productBaseType")
                == "prerender"
            )
        except Exception:
            is_prerender = False
        node["tile_color"].setValue(
            PRERENDER_TILE_COLOR if is_prerender else 0
        )


_WORK_RENDERS_MARKER = "/renders/nuke/"


def _render_path_shot_mismatch(node):
    """Return the foreign work-dir prefix when the node's render path points
    outside the current shot's work directory, else None.

    Only meaningful for standard quick-write paths
    ('{work}/renders/nuke/...'); OVS or manually-edited paths without the
    marker are skipped.
    """
    out_knob = get_file_output_knob(node)
    if out_knob is None:
        return None
    path = (out_knob.value() or "").replace("\\", "/")
    idx = path.find(_WORK_RENDERS_MARKER)
    if idx == -1:
        return None
    work = os.environ.get("AYON_WORKDIR", "").replace("\\", "/").rstrip("/")
    if not work:
        return None
    prefix = path[:idx]
    if os.path.normcase(prefix) == os.path.normcase(work):
        return None
    return prefix


def update_output_header_warning(node):
    """Show/clear a bold orange warning after the Output header when the
    node's render path targets another shot's work area."""
    header = node.knob("output_header")
    if header is None:
        return
    if _render_path_shot_mismatch(node) is not None:
        header.setValue(
            "<b>Output</b>&nbsp;&nbsp;&nbsp;<b><font color='orange'>"
            "RENDERS INTO ANOTHER SHOT</font></b>"
        )
    else:
        header.setValue("<b>Output</b>")


def _prompt_foreign_render_path(node, foreign_prefix):
    """Three-button warning when the render path targets another shot's work
    area. Returns 'proceed', 'adopt' or 'cancel'."""
    QtWidgets = None
    try:
        from PySide2 import QtWidgets
    except ImportError:
        try:
            from PySide6 import QtWidgets
        except ImportError:
            pass
    if QtWidgets is None or QtWidgets.QApplication.instance() is None:
        return "proceed"

    current = os.environ.get("AYON_WORKDIR", "?").replace("\\", "/")
    box = QtWidgets.QMessageBox()
    box.setWindowTitle("Render Path Warning")
    box.setIcon(QtWidgets.QMessageBox.Warning)
    box.setText(
        f"'{node.name()}' will render into another shot's work area."
    )
    box.setInformativeText(
        f"Render path work area:\n    {foreign_prefix}\n\n"
        f"Current shot work area:\n    {current}"
    )
    render_btn = box.addButton(
        "Render Anyway", QtWidgets.QMessageBox.AcceptRole
    )
    adopt_btn = box.addButton(
        "Adopt Current Shot", QtWidgets.QMessageBox.ActionRole
    )
    cancel_btn = box.addButton("Cancel", QtWidgets.QMessageBox.RejectRole)
    box.setDefaultButton(cancel_btn)
    # PySide2 uses exec_(), PySide6 uses exec()
    getattr(box, "exec_", box.exec)()
    clicked = box.clickedButton()
    if clicked is render_btn:
        return "proceed"
    if clicked is adopt_btn:
        return "adopt"
    return "cancel"


def _repath_to_current_work(node, new_subset):
    """Re-anchor the node's render paths to the current AYON_WORKDIR,
    substituting the subset found in the path itself (robust regardless of
    whether the instance JSON was already patched)."""
    work = os.environ.get("AYON_WORKDIR", "").replace("\\", "/").rstrip("/")

    def _rewrite(old_path):
        if not old_path or not work:
            return None
        p = old_path.replace("\\", "/")
        idx = p.find(_WORK_RENDERS_MARKER)
        if idx == -1:
            return None
        remainder = p[idx + len(_WORK_RENDERS_MARKER):]
        old_subset = remainder.split("/")[0]
        if old_subset:
            remainder = remainder.replace(old_subset, new_subset)
        return f"{work}{_WORK_RENDERS_MARKER}{remainder}"

    interior = _find_interior_write(node)
    if interior is not None and "file" in interior.knobs():
        new_file = _rewrite(interior["file"].value())
        if new_file is None:
            log.warning(f"Could not repath interior write on '{node.name()}'")
        else:
            interior["file"].setValue(new_file)
    out_knob = get_file_output_knob(node)
    if out_knob is not None:
        new_out = _rewrite(out_knob.value())
        if new_out is not None:
            out_knob.setValue(new_out)


def adopt_node_to_current_shot(node):
    """Fully adopt a foreign node to the current shot/task: patch instance
    JSON, rename group + interior to the new subset, and repath outputs.

    (check_shot_context's fix only patches the JSON -- this also moves the
    render path, which is what actually decides where frames land.)

    Returns (True, None) on success, (False, message) on failure.
    """
    if INSTANCE_DATA_KNOB not in node.knobs():
        return False, "Node has no publish_instance data."
    data = json.loads(
        node.knobs()[INSTANCE_DATA_KNOB].value().replace("JSON:::", "", 1)
    )
    family = data.get("productBaseType", "render")
    variant = data.get("variant", "_Main")
    is_ovs = data.get("is_ovs", False)
    expected = quick_node_data(family, variant, is_ovs)
    new_subset = expected["subset"]

    for key in ("folderPath", "hierarchy", "folder", "task",
                "subset", "productName"):
        data[key] = expected[key]

    ok, err = _rename_group_and_interior(node, node.name(), new_subset)
    if not ok:
        return False, err

    _repath_to_current_work(node, new_subset)

    node.knobs()[INSTANCE_DATA_KNOB].setValue("JSON:::" + json.dumps(data))

    try:
        clear_foreign_shot_flag(node)
    except Exception as e:
        log.warning(f"Could not clear foreign-shot flag: {e}")

    # Red tile: makes it obvious the node was just adopted and its old
    # renders no longer match -- it needs a fresh render.
    if node.knob("tile_color") is not None:
        node["tile_color"].setValue(ADOPTED_TILE_COLOR)

    # Path now targets the current shot -- clears the orange Output warning.
    try:
        update_output_header_warning(node)
    except Exception as e:
        log.warning(f"Could not update Output header warning: {e}")

    try:
        refresh_latest_publish_display(node)
    except Exception as e:
        log.warning(f"Could not refresh publish version after adopt: {e}")

    log.info(f"Adopted '{node.name()}' to current shot (subset {new_subset})")
    return True, None


def _needs_adoption(node):
    """True when the node's stored context or render path differs from the
    current shot."""
    try:
        return (
            not _node_matches_context(node)
            or _render_path_shot_mismatch(node) is not None
        )
    except Exception:
        return False


def adopt_current_shot_selected():
    """Menu entry point: adopt Hornet write nodes to the current shot/task.

    Works on the selection; with nothing selected, offers to adopt every
    foreign node in the script. One consolidated confirm, then adopts
    without per-node dialogs.
    """
    selected = [
        n for n in nuke.selectedNodes()
        if n.Class() == "Group" and INSTANCE_DATA_KNOB in n.knobs()
    ]
    if selected:
        pool = selected
        scope = "selected"
    else:
        pool = get_all_ayon_write_nodes()
        scope = "in this script"

    needs = [n for n in pool if _needs_adoption(n)]
    if not needs:
        nuke.message(
            f"No Hornet write nodes {scope} need adopting -- all match "
            f"the current shot."
        )
        return

    target = (
        f"{os.environ.get('AYON_FOLDER_PATH', '?')} / "
        f"{os.environ.get('AYON_TASK_NAME', '?')}"
    )
    names = "\n".join(f"  - {n.name()}" for n in needs)
    if not nuke.ask(
        f"Adopt {len(needs)} node(s) to the current shot/task "
        f"({target})?\n\n{names}\n\n"
        f"Each node's metadata, name and render output path will be "
        f"updated. Frames already rendered to the previous location are "
        f"left in place, and the nodes will need a fresh render."
    ):
        return

    ok_names, fail_names = [], []
    for node in needs:
        try:
            ok, err = adopt_node_to_current_shot(node)
        except Exception as e:
            ok, err = False, str(e)
        if ok:
            ok_names.append(node.name())
        else:
            log.error(f"Failed to adopt '{node.name()}': {err}")
            fail_names.append(node.name())

    summary = f"Adopted {len(ok_names)} node(s) to the current shot."
    if fail_names:
        summary += f"\n\nFailed: {', '.join(fail_names)}"
    nuke.message(summary)


def dedup_variant_on_create():
    """onCreate(Group) handler: schedule a variant dedup for pasted nodes.

    The actual work is deferred because at onCreate time a pasted group may
    not be fully deserialized yet (interior nodes still loading), and the
    rename touches the interior write and path knobs.
    """
    if _SCRIPT_LOADING["active"]:
        return
    node = nuke.thisNode()
    if node is None:
        return
    try:
        if INSTANCE_DATA_KNOB not in node.knobs():
            return
    except Exception:
        return
    _defer(lambda n=node: _dedup_variant_now(n))


def _dedup_variant_now(node):
    """Rename `node`'s variant with an incrementing suffix if it collides
    with another Hornet write node's variant. No-op when already unique."""
    try:
        node_full = node.fullName()
        variant = parse_publish_instance(node).get("variant")
    except Exception:
        # Node deleted before the deferred ran (e.g. immediate undo), or
        # unparseable instance data -- nothing to do.
        return

    # Passive cross-shot warning, independent of the variant dedup below.
    try:
        if not _node_matches_context(node):
            flag_foreign_shot(node)
        update_output_header_warning(node)
    except Exception as e:
        log.warning(f"Foreign-shot check failed on paste: {e}")

    if not variant:
        return

    taken = set()
    for other in get_all_ayon_write_nodes():
        if other.fullName() == node_full:
            continue
        try:
            v = parse_publish_instance(other).get("variant")
        except Exception:
            continue
        if v:
            taken.add(v)
    if variant not in taken:
        return

    candidate = variant.lstrip("_")
    for _ in range(999):
        candidate = increment_name(candidate)
        normalized = _normalize_variant(candidate)
        if normalized and normalized not in taken:
            break
    else:
        log.warning(
            f"Could not find a free variant name for pasted node "
            f"'{node.name()}' -- submit will stay blocked until it is "
            f"renamed manually."
        )
        return

    ok, err = _apply_variant_change(node, candidate, interactive=False)
    if ok:
        nuke.tprint(
            f"Pasted node: variant '{variant.lstrip('_')}' was already in "
            f"use -- renamed to '{candidate}' ('{node.name()}')."
        )
    else:
        log.warning(
            f"Auto-dedup failed on pasted node '{node.name()}': {err} -- "
            f"submit will stay blocked until the variant is renamed manually."
        )
