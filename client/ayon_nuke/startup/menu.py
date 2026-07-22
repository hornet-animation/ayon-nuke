
"""
I've begun moving quick_write functions into their own file,
but you have to be careful because function calls embedded as
strings in old nodes will break if they are not available
directly in this scope, and old nuke scripts will break

Ideally we would have more organized namespace and avoid importing
all this in to the global scope. I think we would need to refactor
it gradually with depracation warnings

Alex H
"""
import nuke
import os
import json
import quick_write
import read_node_utils
from view_manager import show as show_view_manager
from quick_write import (
    embedOptions,
    presets,
    embed_quick_publish,
    show_quick_publish_info,
    quick_publish_wrapper,
    quick_write_node,
    ovs_write_node,
    update_ovs_write_version,
    _quick_write_node,
    render_or_submit,
)
from hornet_deadline_utils import deadlineNetworkSubmit
from hornet_publish_utils import quick_publish
from ayon_core.pipeline import (
    install_host,
    get_current_folder_path,
    get_current_task_name,
    get_current_project_name,
)
from ayon_nuke.api import NukeHost
from ayon_core.lib import Logger
from ayon_nuke.api.lib import WorkfileSettings
from ayon_core.tools.utils import host_tools
import ayon_api
import hornet_deadline_utils
import file_sequence
import views_write
from reload_hornet import reload_hornet_modules

from ayon_core.pipeline import registered_host
# Version Up Workfile import
from ayon_core.pipeline.workfile import save_next_version as _save_next_version

host = NukeHost()
install_host(host)

log = Logger.get_logger(__name__)


def set_blank_workfile_frame_range():
    #Set root frame range from Ayon task attributes when opening a blank workfile.
    if nuke.root().name() != "Root":
        return

        # Get current context from environment
    project_name = get_current_project_name()
    folder_path = get_current_folder_path()
    task_name = get_current_task_name()

    if not all([project_name, folder_path, task_name]):
        log.warning("Cannot set frame range: missing context data")
        return

    folder_entity = ayon_api.get_folder_by_path(project_name, folder_path)
    task_entity = ayon_api.get_task_by_name(
        project_name, folder_entity["id"], task_name
    )

    task_attributes = task_entity.get("attrib", {})
    frame_end = task_attributes.get("frameEnd")
    frame_start = task_attributes.get("frameStart")

    nuke.root()["first_frame"].setValue(int(frame_start))
    nuke.root()["last_frame"].setValue(int(frame_end))

    log.info(f"Set blank workfile frame range: {frame_start}-{frame_end}")



def apply_format_presets():
    # print("apply_format_presets")
    node = nuke.thisNode()
    knob = nuke.thisKnob()
    if knob.name() == "file_type":
        if knob.value() in presets.keys():
            for preset in presets[knob.value()]:
                if node.knob(preset[0]):
                    node.knob(preset[0]).setValue(preset[1])


# Hornet- helper to switch file extension to filetype
def writes_ver_sync():
    """onScriptSave: re-point OVS Hornet write nodes at the current version.

    The old Avalon-era implementation only touched nodes carrying an
    'AvalonTab', which AYON/Hornet nodes never have -- so it was a silent
    no-op and OVS render paths never followed a workfile version-up. The real
    work now lives in quick_write.sync_ovs_write_versions() (keyed on the
    publish_instance data); this wrapper keeps the existing onScriptSave
    registration pointed at it.
    """
    try:
        quick_write.sync_ovs_write_versions()
    except Exception as e:
        print(f"writes_ver_sync failed: {e}")

def warnSingleFrame():
    singleFrameWarn = nuke.Text_Knob(
        "singleFrameWarn",
        "",
        "- render start and end are the same, quick publish type will be changed to Image",
    )
    nde = nuke.thisNode()
    knb = nuke.thisKnob()
    group = nde.parent()
    if knb.name() == "first" or knb.name() == "last":
        if not knb.value():
            return
        first = nde.knob("first").value()
        last = nde.knob("last").value()
        if first == last:
            if not group.knob("singleFrameWarn"):
                group.addKnob(singleFrameWarn)

            if not group.knob("_saved_knob_states"):
                saved_knob = nuke.String_Knob("_saved_knob_states", "")
                saved_knob.setVisible(False)
                group.addKnob(saved_knob)

            saved_states = {}
            if group.knob("generate_review_media"):
                saved_states["generate_review_media"] = group.knob("generate_review_media").value()
                group.knob("generate_review_media").setValue(False)
                group.knob("generate_review_media").setEnabled(False)

            if group.knob("publish_on_farm"):
                saved_states["publish_on_farm"] = group.knob("publish_on_farm").value()
                group.knob("publish_on_farm").setValue(False)
                group.knob("publish_on_farm").setEnabled(False)

            group.knob("_saved_knob_states").setValue(json.dumps(saved_states))

        else:
            if group.knob("singleFrameWarn"):
                group.removeKnob(group.knob("singleFrameWarn"))

            saved_states = {}
            if group.knob("_saved_knob_states"):
                try:
                    saved_states = json.loads(group.knob("_saved_knob_states").value())
                except (json.JSONDecodeError, ValueError):
                    saved_states = {}

            if group.knob("generate_review_media"):
                group.knob("generate_review_media").setEnabled(True)
                if "generate_review_media" in saved_states:
                    group.knob("generate_review_media").setValue(saved_states["generate_review_media"])

            if group.knob("publish_on_farm"):
                group.knob("publish_on_farm").setEnabled(True)
                if "publish_on_farm" in saved_states:
                    group.knob("publish_on_farm").setValue(saved_states["publish_on_farm"])

def switchExtension():

    nde = nuke.thisNode()
    knb = nuke.thisKnob()

    if nde is None or knb is None:
        return

    if knb == nde.knob("file_type"):
        filek = nde.knob("file")
        old = filek.value()
        pre, ext = os.path.splitext(old)
        filek.setValue(pre + "." + knb.value())


def check_and_show_publisher():
    # this is supposed to check if there's already a publish to save the user
    # from submitting one that fails, but the assemble_publish_path() function
    # doest not currently take version into account and just returns the latest
    # which causes this to return false positive.

    # Leaving it here because it's on the list to make a btter pubklish bath
    # solver, at which point this function will work.

    # publish_path = read_node_utils.assemble_publish_path(nuke.thisNode())

    # if publish_path:
    #     base = publish_path.name.split(".")[0]
    #     if publish_path.parent.glob(f"{base}.*"):
    #         if not nuke.ask("Files exist in publish location. Conitue?"):
    #             return

    host_tools.show_publisher(tab="Publish")


def enable_disable_frame_range():
    # print("enable_disable_frame_range")
    nde = nuke.thisNode()
    knb = nuke.thisKnob()
    if not nde.knob("use_limit") or not knb.name() == "use_limit":
        return
    group = nuke.toNode(".".join(["root"] + nde.fullName().split(".")[:-1]))
    enable = nde.knob("use_limit").value()
    group.knobs()["first"].setEnabled(enable)
    group.knobs()["last"].setEnabled(enable)


def submit_selected_write():
    for nde in nuke.selectedNodes():
        if nde.Class() == "Write":
            submit_write(nde)


def enable_publish_range():
    # print("enable_publish_range")

    nde = nuke.thisNode()
    kb = nuke.thisKnob()

    if not kb == nde.knob("usePublishRange"):
        return
    print("oh no!")
    if kb.value():
        nde.knob("publishFirst").setEnabled(True)
        nde.knob("publishLast").setEnabled(True)
    else:
        nde.knob("publishFirst").setEnabled(False)
        nde.knob("publishLast").setEnabled(False)


hornet_menu = nuke.menu("Nuke")
m = hornet_menu.addMenu("&Hornet Write")
m.addCommand("&Quick Write Node", "quick_write_node()", "Ctrl+W")
m.addCommand(
    "&Quick PreWrite Node",
    "quick_write_node(family='prerender')",
    "Ctrl+Shift+W",
)
m.addCommand(
    "&Quick Single Frame Write Node",
    "quick_write_node(family='image')"
)
m.addCommand("&Oversized Write Node", "ovs_write_node()")
m.addCommand(
        "Views Write Node",
        "views_write.views_write_node(_quick_write_node)",
        tooltip="Create a views write node that generates write nodes for all views",
)

nuke.addKnobChanged(apply_format_presets, nodeClass="Write")
nuke.addKnobChanged(switchExtension, nodeClass="Write")
nuke.addKnobChanged(embedOptions, nodeClass="Write")
nuke.addKnobChanged(embed_quick_publish, nodeClass="Write")
nuke.addKnobChanged(enable_publish_range, nodeClass="Group")
nuke.addKnobChanged(warnSingleFrame, nodeClass="Write")
nuke.addKnobChanged(enable_disable_frame_range, nodeClass="Write")
#nuke.addOnScriptSave(set_hwrite_version)
nuke.addOnScriptSave(writes_ver_sync)
nuke.addOnScriptLoad(WorkfileSettings().set_colorspace)
nuke.addOnCreate(WorkfileSettings().set_colorspace, nodeClass="Root")
nuke.addOnCreate(set_blank_workfile_frame_range, nodeClass="Root")


nuke.addKnobChanged(quick_write.refresh_deadline_callback, nodeClass="Group")
nuke.addKnobChanged(views_write.sanitize_aspect, nodeClass="Group")

### View Manager

toolbar = nuke.toolbar("Nodes")
toolbar.addCommand("Alex Dev / View Manager", "show_view_manager()")


### Project Gizmos

PROJECT_NAME = os.environ["AYON_PROJECT_NAME"]

from node_manager import NodeLoader

nodes_toolbar = nuke.toolbar("Nodes")
project_toolbar = nodes_toolbar.addMenu(PROJECT_NAME)

node_loader = NodeLoader()

project_toolbar.addCommand(
    name="Add Selected Nodes", command="node_loader.add_selected_nodes()"
)
project_toolbar.addCommand(
    name="Add Toolset", command="node_loader.add_toolset()"
)
project_toolbar.addCommand(name="Reload", command="node_loader.populate()")

nuke.menu("Nuke").addCommand(
    "File/Version Up Workfile",
    _save_next_version,
    "alt+shift+s"
)


# This code gets only called from GUI mode.
# Unlike the non-GUI mode (e.g. farm),
# we do expect a valid host at this time.
nuke_host = registered_host()
if nuke_host is None:
    raise RuntimeError("Cannot find expected registered Nuke host.")

nuke_host.setup_ui_callbacks_and_menu()
