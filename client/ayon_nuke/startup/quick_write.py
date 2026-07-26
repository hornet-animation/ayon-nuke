import nuke
import os
from ayon_nuke import api
import json
import hornet_deadline_utils
from ayon_core.lib import Logger
from ayon_core.settings import get_current_project_settings

import ayon_nuke.api.lib as lib
from hornet_deadline_utils import save_script_with_render, deadlineNetworkSubmit

from ayon_nuke.api.lib import (
    create_write_node,
    INSTANCE_DATA_KNOB,
    get_ovs_pathing,
)
from ayon_nuke.version import __version__ as ADDON_VERSION

try:
    import nukescripts
except ImportError:
    nukescripts = None


log = Logger.get_logger(__name__)

# Bumped whenever OVS/publish behavior changes, so a live (dev-mode) session
# can be verified against the source. Printed at module load and echoed by
# the OVS flows.
QUICK_WRITE_REV = "ovs-publish-revF"

nuke.tprint(
    "[hornet quick_write {}] loaded from: {}".format(
        QUICK_WRITE_REV, os.path.abspath(__file__)
    )
)

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
        ("channels", "all"),
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


# ---------------------------------------------------------------------------
# Quick Write / OVS default settings
#
# Layered config, resolved fresh at node creation:
#     user TOML  overrides  project TOML  overrides  hardcoded fallback
#
# menu.py declares the editable hardcoded fallback + the two TOML locations
# at its top and injects them via configure_defaults() at startup (keeps the
# knobs to tweak in one obvious place, avoids a menu<->quick_write import
# cycle). The values below are only used if that injection hasn't run (e.g. a
# bare module reload). TOML files are re-read on every node create, so editing
# them takes effect without a Nuke restart; editing menu.py needs a restart.
# ---------------------------------------------------------------------------
_QW_DEFAULTS = {
    "deadlinePriority": 90,
    "deadlineChunkSize": 1,
    "concurrentTasks": 1,
    "deadlinePool": "",          # "" -> the project's primary Deadline pool
    "deadlineGroup": "nuke",
    "generate_review_media": True,
    "burnin": True,
    "publish_on_farm": False,
}
_QW_PROJECT_TOML = "{project_root}/assets/nuke/config/quick_write.toml"
_QW_USER_TOML = "~/.nuke/quick_write.toml"


def configure_defaults(defaults=None, project_toml=None, user_toml=None):
    """Inject the editable defaults/paths declared at the top of menu.py."""
    global _QW_DEFAULTS, _QW_PROJECT_TOML, _QW_USER_TOML
    if defaults:
        _QW_DEFAULTS = dict(defaults)
    if project_toml:
        _QW_PROJECT_TOML = project_toml
    if user_toml:
        _QW_USER_TOML = user_toml


def _read_toml(path):
    """Parse a TOML file to a dict; {} if missing/unparseable. Uses stdlib
    tomllib (3.11+), falling back to tomli, then toml -- all present in the
    AYON dependency set."""
    if not path or not os.path.isfile(path):
        return {}
    try:
        try:
            import tomllib as _t
        except ImportError:
            import tomli as _t
        with open(path, "rb") as f:
            return _t.load(f) or {}
    except ImportError:
        try:
            import toml as _t2
            with open(path, "r", encoding="utf-8") as f:
                return _t2.load(f) or {}
        except Exception as e:
            log.warning(f"No TOML parser available; ignoring {path} ({e})")
    except Exception as e:
        log.warning(f"Could not read defaults TOML {path}: {e}")
    return {}


def _resolve_project_root():
    """Absolute project directory (anatomy work root + project name), or ''."""
    try:
        from ayon_core.pipeline import Anatomy
        root = Anatomy().roots["work"].value.replace("\\", "/").rstrip("/")
        return "{}/{}".format(root, os.environ.get("AYON_PROJECT_NAME", ""))
    except Exception as e:
        log.warning(f"Could not resolve project root for defaults: {e}")
        return ""


def _merge_toml_over(merged, path):
    """Layer a TOML file's values (top-level keys and/or a [quick_write]
    table) over `merged`, in place, for keys we recognise."""
    data = _read_toml(path)
    if not isinstance(data, dict):
        return
    candidates = {k: v for k, v in data.items() if not isinstance(v, dict)}
    if isinstance(data.get("quick_write"), dict):
        candidates.update(data["quick_write"])
    applied = False
    for k, v in candidates.items():
        if k in merged:
            merged[k] = v
            applied = True
    if applied:
        nuke.tprint(
            "[hornet quick_write {}] applied defaults from {}".format(
                QUICK_WRITE_REV, path
            )
        )


def get_quick_write_defaults():
    """Resolved defaults dict: hardcoded <- project TOML <- user TOML."""
    merged = dict(_QW_DEFAULTS)
    project_toml = os.path.expanduser(
        _QW_PROJECT_TOML.replace("{project_root}", _resolve_project_root())
    )
    user_toml = os.path.expanduser(_QW_USER_TOML)
    _merge_toml_over(merged, project_toml)   # project first ...
    _merge_toml_over(merged, user_toml)      # ... user overrides
    return merged


def quick_write_node(family="render"):
    # return
    variant = nuke.getInput("Variant for Quick Write Node", "Main")
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

    nuke.tprint("quick write node")

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
        "render_taget": "frames_farm",
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
        qnode.knob("tile_color").setValue(2880113407)

    # Stamp the bundle version BEFORE the file_type set below: that set
    # triggers the panel-building knobChanged callbacks, which read this
    # knob for the visible version display. In DONT_DELETE, so it survives
    # panel rebuilds.
    stamp_addon_version(qnode)

    with qnode.begin():
        inside_write = nuke.toNode(
            "inside_" + family + os.environ["AYON_TASK_NAME"] + variant.title()
        )
        if family == "prerender":
            inside_write.knob("file_type").setValue("exr")
        else:
            inside_write.knob("file_type").setValue("dpx")

    return qnode


# Hidden knob that stamps the AYON nuke addon (bundle) version that created
# (or last re-pointed) the node. Sourced from ayon_nuke.version.__version__,
# which create_package.py rewrites on every build -- so a version bump +
# rebuild updates this automatically for newly created nodes.
ADDON_VERSION_KNOB = "hornet_addon_version"

DONT_DELETE = [
    api.INSTANCE_DATA_KNOB,
    ADDON_VERSION_KNOB,
]


def _format_version_display(version_string):
    """Grey HTML label text for the visible addon-version knob."""
    return "<font color='#808080'>addon {}</font>".format(
        version_string or "unstamped"
    )


def stamp_addon_version(node):
    """Add/refresh a hidden knob recording the addon version on `node`.

    The value is the addon version active when the node is created (or when
    an OVS node is re-pointed). It is not auto-updated on load, so an old
    node opened after an update still reports the version it was made with
    -- which is the point for regression tracking.
    """
    knob = node.knob(ADDON_VERSION_KNOB)
    if knob is None:
        knob = nuke.String_Knob(ADDON_VERSION_KNOB, "AYON Nuke Addon Version")
        knob.setVisible(False)
        node.addKnob(knob)
    knob.setValue(ADDON_VERSION)

    # Refresh the visible label too, in case the panel was built before
    # this stamp existed.
    display = node.knob("addon_version_display")
    if display is not None:
        display.setValue(_format_version_display(ADDON_VERSION))


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
    target = "File output"
    if target not in names:
        # Panel not built yet (e.g. onCreate during initial creation) -- nothing
        # to restore; embedOptions will build it at proper height.
        return

    tail = knobs[names.index(target):]
    for knob in tail:
        group.removeKnob(knob)
    for knob in tail:
        if knob.name() == target:
            fresh = nuke.Multiline_Eval_String_Knob(target)
            fresh.setValue(knob.value())
            group.addKnob(fresh)
        else:
            group.addKnob(knob)


# Knob names whose values must survive a file_type change. embedOptions purges
# and rebuilds the whole panel on every file_type change, re-seeding these from
# defaults/root; we snapshot them before the purge and restore them after, so a
# user's frame range, publish range, Deadline params and publish toggles are
# kept. Format-specific knobs (extension + per-format Link_Knobs) are excluded
# on purpose so they still follow the chosen format.
_PRESERVE_ACROSS_FILETYPE = (
    "first", "last", "framelist",
    "publishFirst", "publishLast", "usePublishRange",
    "deadlinePriority", "deadlineChunkSize", "concurrentTasks",
    "deadlinePool", "deadlineGroup", "skip_popup",
    "publish_on_farm", "generate_review_media", "burnin",
)

# Snapshots taken in embedOptions (before the purge) keyed by group full name,
# so the second rebuild callback (embed_quick_publish, which owns the toggles)
# can restore them after it runs.
_EMBED_PRESERVE = {}


def _snapshot_settings(group):
    """Capture format-independent user settings before a file_type rebuild.

    Returns {knob_name: value} for the _PRESERVE_ACROSS_FILETYPE knobs that
    currently exist on `group` (empty on a node's first build). Lets a
    file_type change keep frame range, publish range, Deadline params and the
    publish toggles instead of resetting them to defaults.
    """
    snap = {}
    for name in _PRESERVE_ACROSS_FILETYPE:
        k = group.knob(name)
        if k is None:
            continue
        try:
            snap[name] = k.value()
        except Exception:
            pass
    return snap


def _restore_settings(group, interior, snap):
    """Re-apply values captured by _snapshot_settings after the rebuild.

    Render range (first/last) is written to the interior write node -- the
    group's first/last are Link_Knobs onto it -- everything else to the group.
    Only names still present are touched, so it is safe to call from either
    rebuild path and harmless to call more than once.
    """
    if not snap:
        return
    for name, val in snap.items():
        try:
            if name in ("first", "last"):
                k = interior.knob(name) if interior is not None else None
            else:
                k = group.knob(name)
            if k is not None:
                k.setValue(val)
        except Exception:
            pass


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

    if ftype not in knobMatrix.keys():
        return

    # Snapshot user settings before the purge; restored at the end of this
    # rebuild and (for the toggles) by embed_quick_publish.
    _preserved = _snapshot_settings(group)
    _EMBED_PRESERVE[group.fullName()] = _preserved

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

    beginGroup = nuke.Tab_Knob("beginoutput", "Output", nuke.TABBEGINGROUP)
    group.addKnob(beginGroup)

    if "file" not in group.knobs().keys():
        fle = nuke.Multiline_Eval_String_Knob("File output")
        fle.setText(nde.knob("file").value())
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
        for kname in knobMatrix[ftype]:
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

    endGroup = nuke.Tab_Knob("endoutput", None, nuke.TABENDGROUP)
    group.addKnob(endGroup)
    beginGroup = nuke.Tab_Knob(
        "beginpipeline", "Rendering and Pipeline", nuke.TABBEGINGROUP
    )
    group.addKnob(beginGroup)
    group.addKnob(framelist)
    group.addKnob(publishFirst)
    group.addKnob(publishLast)

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
        "import os;fpath = os.path.dirname(nuke.thisNode().knob('File output').value());[os.remove(os.path.join(fpath, f)) for f in os.listdir(fpath) if os.path.isfile(os.path.join(fpath, f))]",
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
    deadlinediv = nuke.Text_Knob("deadlinediv", "Deadline", "")
    deadlinePriority = nuke.Int_Knob("deadlinePriority", "Priority")
    deadlineChunkSize = nuke.Int_Knob("deadlineChunkSize", "    Chunk Size")
    concurrentTasks = nuke.Int_Knob("concurrentTasks", "    Concurrent Tasks")
    # deadlinePool = nuke.String_Knob("deadlinePool", "Pool")
    deadlinePool = nuke.Enumeration_Knob("deadlinePool", "Pool", hornet_deadline_utils.get_deadline_pools())
    # deadlineGroup = nuke.String_Knob("deadlineGroup", "Group")
    deadlineGroup = nuke.Enumeration_Knob("deadlineGroup", "Group", hornet_deadline_utils.get_deadline_groups())

    read_from_publish_button = nuke.PyScript_Knob(
        "readfrompublish",
        # "Read Latest" (newest render on disk) is OVS-only behaviour; the
        # regular Hornet Write keeps the original "Read From Publish" label
        # and version-linked lookup.
        "Read Latest" if is_ovs else "Read From Publish",
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

    tempwarn = nuke.Text_Knob(
        "tempwarn",
        "",
        "- all rendered files are TEMPORARY and WILL BE OVERWRITTEN unless published ",
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
        "Quick Publish",
        "quick_publish_wrapper(nuke.thisNode())",
    )
    _d = get_quick_write_defaults()
    deadlineChunkSize.setValue(int(_d["deadlineChunkSize"]))
    concurrentTasks.setValue(int(_d["concurrentTasks"]))
    # Empty pool default -> fall back to the project's primary Deadline pool.
    deadlinePool.setValue(_d["deadlinePool"] or get_deadlin_pool())
    deadlineGroup.setValue(_d["deadlineGroup"])
    deadlinePriority.setValue(int(_d["deadlinePriority"]))

    usePublishRange.setFlag(nuke.STARTLINE)
    submit_to_deadline.setFlag(nuke.STARTLINE)
    publishFirst.clearFlag(nuke.STARTLINE)
    publishLast.clearFlag(nuke.STARTLINE)
    render_local_button.setFlag(nuke.STARTLINE)
    deadlinePriority.setFlag(nuke.STARTLINE)
    deadlineChunkSize.clearFlag(nuke.STARTLINE)  # Don't start a new line
    #    concurrentTasks.clearFlag(nuke.STARTLINE)
    concurrent_warning.clearFlag(nuke.STARTLINE)

    group.addKnob(render_local_button)

    if not is_ovs:
        group.addKnob(readfrom)
        group.addKnob(clear_temp_outputs_button)
        group.addKnob(navigate_to_render_button)

    group.addKnob(deadlinediv)
    group.addKnob(deadlinePriority)
    group.addKnob(deadlineChunkSize)
    group.addKnob(concurrentTasks)
    group.addKnob(concurrent_warning)
    group.addKnob(deadlinePool)
    group.addKnob(deadlineGroup)
    group.addKnob(submit_to_deadline)
    group.addKnob(skip_popup)
    group.addKnob(div)
    group.addKnob(quick_publish_button)
    group.addKnob(read_from_publish_button)
    group.addKnob(navigate_to_publish_button)

    if is_ovs is False:
        group.addKnob(tempwarn)

    else:
        group.addKnob(ovswarn)

    endGroup = nuke.Tab_Knob("endpipeline", None, nuke.TABENDGROUP)

    group.addKnob(endGroup)
    try:
        group["views"].setValue(nuke.views()[0])
    except Exception as e:
        print(f"Error setting views: {e}")

    # Restore the settings preserved above (frame/publish range, Deadline
    # params). The publish toggles are rebuilt by embed_quick_publish, which
    # restores them from _EMBED_PRESERVE after it runs.
    _restore_settings(group, nde, _preserved)


def show_quick_publish_info():
    """
    Show a simple info window with text content.
    """
    # Your info text - customize this as needed
    info_text = """
Quick Publish

Experimental alternative to the ayon ui for submitting publishes.

"Publish on Farm" controls where the entire publish runs:
  - Unchecked: publish (and review media generation) runs locally. Nuke
    will freeze until complete; large transfers can take a while but Nuke
    has probably not crashed.
  - Checked: publish runs remotely on the farm, including review media.

"Generate Review Media" toggles the ExtractFFmpegReview step entirely.
When unchecked, no .mov/.mp4 review media is produced.

"Apply burnins to Review" toggles burn-in overlays (timecode, frame
counter, shot/version metadata) on the review media.
    """

    nuke.message(info_text)


def embed_quick_publish():
    """
    Creates the Quick Publish tab with options for publishing renders and generating review media.
    """
    nde = nuke.thisNode()
    knb = nuke.thisKnob()

    # Only run when file_type knob changes, to avoid multiple calls
    if knb != nde.knob("file_type"):
        return

    # div = nuke.Text_Knob("div", "", "")
    # Get the parent group node
    group = nuke.toNode(".".join(["root"] + nde.fullName().split(".")[:-1]))

    # Check if quick publish tab already exists
    if "quick_publish_tab" in group.knobs():
        return

    # Create the Quick Publish tab
    quick_publish_tab = nuke.Tab_Knob(
        "quick_publish_tab",
        "Quick Publish - Settings",
        nuke.TABBEGINGROUP,
    )

    _d = get_quick_write_defaults()

    # Check if this is a prerender node to skip review options
    is_prerender = False
    try:
        data = json.loads(
            group.knobs()["publish_instance"].value().replace("JSON:::", "", 1)
        )
        product_type = data.get("productBaseType", "")
        is_prerender = product_type == "prerender"
    except (KeyError, TypeError, ValueError):
        is_prerender = False

    # Create checkboxes
    publish_on_farm_checkbox = nuke.Boolean_Knob(
        "publish_on_farm", "Publish on Farm"
    )
    publish_on_farm_checkbox.setValue(bool(_d["publish_on_farm"]))
    publish_on_farm_checkbox.setTooltip(
        "Run the full publish on the farm, including review media "
        "generation. Unchecked runs the publish (and review extraction) "
        "locally."
    )
    publish_on_farm_checkbox.setFlag(nuke.STARTLINE)

    # Only create review related for non prerender nodes
    if not is_prerender:
        generate_review_checkbox = nuke.Boolean_Knob(
            "generate_review_media", "Generate Review Media"
        )
        generate_review_checkbox.setValue(bool(_d["generate_review_media"]))
        generate_review_checkbox.setTooltip(
            "Generate review media (mp4/mov) for the rendered sequence. "
            "Unchecked skips ExtractFFmpegReview entirely."
        )
        generate_review_checkbox.setFlag(nuke.STARTLINE)

        burnin_checkbox = nuke.Boolean_Knob(
            "burnin", "Apply burnins to Review"
        )
        burnin_checkbox.setValue(bool(_d["burnin"]))
        burnin_checkbox.setTooltip(
            "Add burnin information (timecode, frame numbers, etc.) to "
            "review media. Unchecked disables burnins."
        )
        burnin_checkbox.setFlag(nuke.STARTLINE)


    # Add the info button
    show_info_button = nuke.PyScript_Knob(
        "show_info",
        "Show Info",
        "quick_write.show_quick_publish_info()",
    )
    show_info_button.setTooltip("Display information window")

    group.addKnob(quick_publish_tab)
    group.addKnob(publish_on_farm_checkbox)

    # Only add review related knobs for non prerender nodes
    if not is_prerender:
        group.addKnob(generate_review_checkbox)
        group.addKnob(burnin_checkbox)

    group.addKnob(show_info_button)

    # Visible, read-only addon-version stamp at the bottom of the panel.
    # Text_Knob is a label (not editable); the value renders HTML, so we
    # grey it out. Sourced from the hidden ADDON_VERSION_KNOB so it reflects
    # the bundle that created (or last re-pointed) the node, not the
    # currently-running one.
    stamp_knob = group.knob(ADDON_VERSION_KNOB)
    stamped_version = stamp_knob.value().strip() if stamp_knob else ""
    version_display = nuke.Text_Knob("addon_version_display", "")
    version_display.setValue(_format_version_display(stamped_version))
    version_display.setFlag(nuke.STARTLINE)
    group.addKnob(version_display)

    # Restore the publish toggles (and re-apply the rest, harmlessly) that were
    # captured before embedOptions purged the panel on this file_type change.
    _restore_settings(group, nde, _EMBED_PRESERVE.pop(group.fullName(), None))




def check_existing_files_pattern(node):
    """Check if files matching the write node's output pattern already exist."""
    import glob
    import re

    try:
        if "File output" in node.knobs():
            file_path = node["File output"].value()
        else:
            wnode = _find_interior_write(node)
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


# Fall back to object when nukescripts is unavailable (headless import) so the
# module still loads; the dialog is only ever instantiated in a GUI session.
_PanelBase = nukescripts.PythonPanel if nukescripts else object


class SubmitSettingsDialog(_PanelBase):
    """One-shot dialog shown before farm submission. Values entered here are
    used for this submission only and are NOT written back to the node -- so
    a priority of, say, 99 here doesn't persist past this submit."""

    def __init__(self, node):
        """Build the dialog, seeding every field from the node's current
        Deadline knobs (frame list, priority, chunk, concurrency, pool,
        group)."""
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


def render_or_submit(node, local=False):
    """ wrapper for Deadline and Render Local buttons."""
    if not check_shot_context(node):
        print("aborted shot context mismatch")
        return
    if lib.get_node_data(node, "publish_instance").get("is_ovs"):
        update_ovs_write_version(node)
    if node.knob("_cancelled") and node["_cancelled"].value():
        print("Cancelled by user")
        return
    if local:
        nuke.toNode(f"inside_{node.name()}").knob("Render").execute()
        save_script_with_render(
            node["File output"].getValue(),
            lib.get_node_data(node, "publish_instance")["is_ovs"],
        )
    else:
        # Farm path. When "skip popup" is on, submit straight from the node's
        # knobs. Otherwise show the one-shot settings dialog and pass its
        # values through as overrides (not written back to the node), so
        # nothing persists past this submit.
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
                        fpath_new = get_ovs_pathing(
                            _ovs_data_with_current_ext(node, data)
                        )
                        node_name = node["name"].value()
                        wnode = _find_interior_write(node)
                        if wnode is not None:
                            wnode["file"].setValue(fpath_new)
                            node["File output"].setValue(fpath_new)
                            # Re-stamp: records the addon version that last
                            # re-pointed this OVS node, and backfills the
                            # knob on OVS nodes created before stamping.
                            stamp_addon_version(node)
                            log.info(
                                f"Updating ovs write path for {node_name}: {fpath_new}"
                            )
                            nuke.toNode(node_name)
                        else:
                            log.warning(
                                f"No interior write node found in {node_name}, cannot set file path."
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


def _find_interior_write(group_node):
    """Find the Write node inside a Hornet write group by traversal.

    Never look the interior up by name ("inside_<group>"): bare-name
    nuke.toNode() resolves against the CURRENT group context, so it fails
    from root-level callbacks (onScriptSave) and after pastes that renamed
    the interior. Walking the group's children is context-free and robust.
    """
    try:
        for child in group_node.nodes():
            if child.Class() == "Write":
                return child
    except Exception as e:
        log.warning(f"Could not traverse group '{group_node.name()}': {e}")
    return None


def _ovs_data_with_current_ext(node, data):
    """Return a copy of the instance data with `ext` corrected to what the
    node actually renders.

    The JSON stores the extension from node-creation time (imageio default,
    usually exr), but _quick_write_node flips file_type afterwards (dpx for
    renders) without updating the JSON -- so get_ovs_pathing(data) would
    rebuild paths with the wrong extension and lose track of real renders.
    """
    ext = None
    interior = _find_interior_write(node)
    if interior is not None:
        try:
            ext = (interior["file_type"].value() or "").strip() or None
        except Exception:
            ext = None
        if not ext:
            try:
                ext = os.path.splitext(
                    interior["file"].value()
                )[1].lstrip(".") or None
            except Exception:
                ext = None
    if ext and ext != data.get("ext"):
        data = dict(data)
        data["ext"] = ext
    return data


def sync_ovs_write_versions():
    """onScriptSave hook: point OVS write nodes at the current publish version.

    OVS nodes render straight into the versioned publish tree, so when the
    workfile version increments their output path must follow -- otherwise the
    node keeps writing to the old version folder. Non-OVS writes render to an
    unversioned temp path, so they are left alone.

    Only runs when workfile and publish versions are linked (the Hornet
    setup): in that mode get_ovs_pathing() resolves to the workfile-matched
    version, so re-deriving on every save is idempotent. When versions are NOT
    linked, get_ovs_pathing() increments each call, which would bump the
    version on every save -- so we skip and leave versioning to render/publish
    time.
    """
    # Stand down while a publish is running: quick_publish saves the script
    # mid-run, and re-syncing then would yank a deliberately re-pointed OVS
    # node back to the (empty) workfile-version path.
    if _OVS_SYNC_SUSPENDED["active"]:
        nuke.tprint(
            "[hornet quick_write {}] OVS save-sync suspended "
            "(publish in progress)".format(QUICK_WRITE_REV)
        )
        return

    # Collect OVS nodes first -- this is cheap and local (just parses the
    # publish_instance JSON). is_version_file_linked() below makes a server
    # round-trip, and this runs on EVERY save, so bail before that when the
    # script has no OVS nodes (the common case).
    ovs_nodes = []
    for node in get_all_ayon_write_nodes():
        try:
            data = parse_publish_instance(node)
        except Exception:
            continue
        if data.get("is_ovs"):
            ovs_nodes.append((node, data))
    if not ovs_nodes:
        return

    try:
        if not lib.is_version_file_linked():
            return
    except Exception as e:
        log.warning(f"OVS version sync skipped (link check failed): {e}")
        return

    for node, data in ovs_nodes:
        try:
            fpath_new = get_ovs_pathing(_ovs_data_with_current_ext(node, data))
        except Exception as e:
            log.warning(
                f"Could not resolve OVS path for '{node.name()}': {e}"
            )
            continue
        interior = _find_interior_write(node)
        if interior is not None and "file" in interior.knobs():
            interior["file"].setValue(fpath_new)
        else:
            log.warning(
                f"No interior write found in '{node.name()}'; only the "
                f"group's File output was updated"
            )
        out_knob = node.knob("File output")
        if out_knob is not None:
            out_knob.setValue(fpath_new)
        nuke.tprint(
            "[hornet quick_write {}] OVS save-sync re-pointed '{}' -> "
            "{}".format(QUICK_WRITE_REV, node.name(), fpath_new)
        )


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


# While a publish runs, the onScriptSave OVS version-sync must stand down:
# quick_publish() saves the script mid-run, and the sync would re-point a
# deliberately re-pointed OVS node back at the (empty) workfile version.
_OVS_SYNC_SUSPENDED = {"active": False}


def _frames_matching_pattern(path_pattern):
    """Return files on disk matching a frame-numbered output pattern
    (%04d / #### tokens are globbed)."""
    import glob as _glob
    import re as _re
    pat = _re.sub(r"%0?\d*d", "*", path_pattern)
    pat = _re.sub(r"#+", "*", pat)
    return _glob.glob(pat)


def _ovs_version_is_published(data, version_num):
    """True when the node's product already has `version_num` on the AYON
    server. Raises on server/lookup errors -- the caller decides the
    fallback policy."""
    import ayon_api
    project = os.environ.get("AYON_PROJECT_NAME")
    folder_path = data.get("folderPath")
    product_name = data.get("productName")
    if not (project and folder_path and product_name):
        return False
    folder = ayon_api.get_folder_by_path(project, folder_path)
    if not folder:
        return False
    product = ayon_api.get_product_by_name(
        project, product_name, folder["id"]
    )
    if not product:
        return False
    return ayon_api.get_version_by_name(
        project, version_num, product["id"]
    ) is not None


def _find_publishable_ovs_version(path, data):
    """Search the OVS product directory backwards (newest version first) for
    a render that is both on disk and not yet published.

    `path` is the node's current output path
    (.../product/vNNN/name_vNNN.%04d.ext); candidate paths are derived by
    swapping the version folder/filename tokens. A candidate must have files
    matching the frame pattern, parse as a coherent sequence (file_sequence
    check, run only on globbed candidates and soft-failing to the glob
    result), and not already exist as a version on the AYON server.

    Returns (version, path_at_version, skipped, already_published). `skipped`
    is a list of (version, reason) pairs for reporting. `already_published` is
    the version number that stopped the search because the most recent render
    on disk is already published (else None) -- we stop there rather than
    walking back to an older, unpublished render. (None, None, skipped, None)
    when no render exists on disk at all.
    """
    import re as _re
    version_dir = os.path.dirname(path)
    product_dir = os.path.dirname(version_dir)
    vname_old = os.path.basename(version_dir)
    base_old = os.path.basename(path)
    skipped = []
    if not os.path.isdir(product_dir):
        return None, None, skipped, None

    candidates = []
    for entry in os.listdir(product_dir):
        m = _re.match(r"^v(\d+)$", entry)
        if m and os.path.isdir(os.path.join(product_dir, entry)):
            candidates.append((int(m.group(1)), entry))

    stem_old, ext_old = os.path.splitext(base_old)

    for num, vname in sorted(candidates, reverse=True):
        stem_cand = stem_old.replace(vname_old, vname)
        cand_path = os.path.join(
            product_dir, vname, stem_cand + ext_old
        ).replace("\\", "/")
        frames = _frames_matching_pattern(cand_path)
        if not frames:
            # Extension drift fallback: the node's path extension can be
            # stale (the instance JSON keeps the creation-time ext, usually
            # exr, while the node actually renders e.g. dpx). Accept frames
            # with any extension and adopt the one found on disk.
            loose = _frames_matching_pattern(
                os.path.join(product_dir, vname, stem_cand + ".*")
                .replace("\\", "/")
            )
            if loose:
                ext_found = os.path.splitext(loose[0])[1]
                frames = [
                    f for f in loose
                    if os.path.splitext(f)[1].lower() == ext_found.lower()
                ]
                cand_path = os.path.join(
                    product_dir, vname, stem_cand + ext_found
                ).replace("\\", "/")
                log.warning(
                    f"OVS search: {vname} frames found with extension "
                    f"'{ext_found}' (node path says '{ext_old}'); using "
                    f"what's on disk"
                )
        if not frames:
            skipped.append((num, "no frames on disk"))
            continue
        if len(frames) > 1:
            try:
                from file_sequence import SequenceFactory
                if not SequenceFactory.from_filenames(
                    [os.path.basename(f) for f in frames]
                ):
                    skipped.append((num, "files do not parse as a sequence"))
                    continue
            except Exception:
                pass  # validation is best-effort; trust the glob
        try:
            if _ovs_version_is_published(data, num):
                # This is the most recent version that actually holds a
                # render on disk, and it is already published. Stop here
                # rather than walking further back to an older, unpublished
                # render -- publishing that would resurrect a superseded
                # version.
                skipped.append((num, "already published"))
                return None, None, skipped, num
        except Exception as e:
            log.warning(
                f"Could not check the server for v{num:03d} ({e}); "
                f"treating it as unpublished"
            )
        return num, cand_path, skipped, None
    return None, None, skipped, None


def _confirm_ovs_publish_version(node):
    """OVS publish pre-flight: ensure we publish a version that has frames.

    OVS nodes render straight into the versioned publish tree, and the
    publish pipeline targets the CURRENT workfile version. After a
    render -> version-up -> publish sequence, both the node path and the
    pipeline point at a version folder with no frames and the publish
    fails. This resolves the newest version folder that actually holds a
    valid render, re-points the node at it, and asks before publishing
    when that version differs from the script version.

    Returns True to proceed, False to abort.
    """
    try:
        data = parse_publish_instance(node)
    except Exception:
        return True
    if not data.get("is_ovs"):
        return True

    nuke.tprint(
        "[hornet quick_write {}] OVS publish pre-flight running on "
        "'{}'".format(QUICK_WRITE_REV, node.name())
    )

    out_knob = node.knob("File output")
    if out_knob is None:
        return True
    path = (out_knob.value() or "").replace("\\", "/")
    if not path:
        return True

    try:
        script_version = int(lib.get_version_from_path(nuke.Root().name()))
    except Exception:
        script_version = None

    chosen, chosen_path, skipped, already_pub = _find_publishable_ovs_version(
        path, data
    )
    nuke.tprint(
        "[hornet quick_write {}] OVS search: chose {} | skipped: {}".format(
            QUICK_WRITE_REV,
            "v{:03d}".format(chosen) if chosen is not None else "nothing",
            ", ".join(
                "v{:03d} ({})".format(n, w) for n, w in skipped
            ) or "none",
        )
    )

    if chosen is None:
        if already_pub is not None:
            nuke.message(
                "The most recent OVS render (v{:03d}) is already "
                "published.\n\nNothing to publish -- render a new version "
                "first.".format(already_pub)
            )
            return False
        lines = "\n".join(
            "  v{:03d}: {}".format(num, why) for num, why in skipped
        ) or "  (no version folders found)"
        nuke.message(
            "Nothing publishable for this OVS node.\n\n"
            "Searched {}:\n{}\n\n"
            "Render a new version before publishing.".format(
                os.path.dirname(os.path.dirname(path)), lines
            )
        )
        return False

    try:
        path_version = int(lib.get_version_from_path(path))
    except Exception:
        path_version = None

    # Node target, newest unpublished render and script version all agree:
    # publish silently, exactly like a normal same-version flow.
    if chosen == path_version and (
        script_version is None or chosen == script_version
    ):
        return True

    prompt = "About to publish OVS render v{:03d}".format(chosen)
    if script_version is not None:
        prompt += " -- the script is at v{:03d}".format(script_version)
    prompt += ".\n"
    if skipped:
        prompt += "\nSkipped: " + ", ".join(
            "v{:03d} ({})".format(num, why) for num, why in skipped
        ) + "\n"
    prompt += "\nProceed?"
    if not nuke.ask(prompt):
        return False

    # Re-point the node so collection reads -- and integration publishes --
    # the version that actually holds the chosen render.
    if chosen != path_version:
        interior = _find_interior_write(node)
        if interior is not None and "file" in interior.knobs():
            interior["file"].setValue(chosen_path)
        out_knob.setValue(chosen_path)
        log.info(f"OVS publish re-pointed '{node.name()}' to {chosen_path}")
    return True


def quick_publish_wrapper(node):
    from hornet_publish_utils import quick_publish

    # OVS: make sure the publish targets a version that actually has frames
    # (otherwise the pipeline publishes the current script version, which
    # fails after render -> version-up -> publish).
    if not _confirm_ovs_publish_version(node):
        print("Publish cancelled: OVS version check")
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

    _OVS_SYNC_SUSPENDED["active"] = True
    try:
        with nuke.root():
            quick_publish(
                node,
                review=review,
                review_farm=review_farm,
                integrate_farm=integrate_farm,
                burnin=burnin,
            )
    finally:
        _OVS_SYNC_SUSPENDED["active"] = False


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
        nuke.message(f"Updated '{node_name}' to current shot context.")
        return True

    log.warning(f"Shot context mismatch on '{node_name}', user declined fix — blocking submission")
    return False
