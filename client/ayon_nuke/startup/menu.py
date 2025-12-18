
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
    handle_farm_publish_logic,
    quick_publish_wrapper,
    show_quick_publish_info,
    quick_write_node,
    ovs_write_node,
    quick_publish_wrapper,
    quick_write_node,
    ovs_write_node,
    quick_publish_wrapper,
    update_ovs_write_version,
    _quick_write_node,
)
from hornet_deadline_utils import save_script_with_render, deadlineNetworkSubmit
from hornet_publish_utils import quick_publish
from ayon_core.pipeline import install_host
from ayon_nuke.api import NukeHost
from ayon_core.lib import Logger
from ayon_nuke.api.lib import WorkfileSettings


import hornet_publish_review_media
import hornet_deadline_utils
import file_sequence
import views_write

host = NukeHost()
install_host(host)

log = Logger.get_logger(__name__)

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
    """Callback synchronizing version of publishable write nodes"""
    try:
        print("Hornet- syncing version to write nodes")
        # rootVersion = pype.get_version_from_path(nuke.root().name())
        pattern = re.compile(r"[\._]v([0-9]+)", re.IGNORECASE)
        rootVersion = pattern.findall(nuke.root().name())[0]
        padding = len(rootVersion)
        new_version = "v" + str("{" + ":0>{}".format(padding) + "}").format(
            int(rootVersion)
        )
        print("new_version: {}".format(new_version))
    except Exception as e:
        print(e)
        return
    groupnodes = [
        node.nodes() for node in nuke.allNodes() if node.Class() == "Group"
    ]
    allnodes = [
        node for group in groupnodes for node in group
    ] + nuke.allNodes()
    for each in allnodes:
        if each.Class() == "Write":
            # check if the node is avalon tracked
            if each.name().startswith("inside_"):
                avalonNode = nuke.toNode(each.name().replace("inside_", ""))
                if avalonNode is None:
                    print(f"Avalon node not found for {each.name()}")
                    continue
            else:
                avalonNode = each
            if "AvalonTab" not in avalonNode.knobs():
                print("tab failure")
                continue

            avalon_knob_data = avalon.nuke.get_avalon_knob_data(
                avalonNode, ["avalon:", "ak:"]
            )
            try:
                if avalon_knob_data["families"] not in ["render", "write"]:
                    print("families fail")
                    log.debug(avalon_knob_data["families"])
                    continue

                node_file = each["file"].value()

                # node_version = "v" + pype.get_version_from_path(node_file)
                node_version = "v" + pattern.findall(node_file)[0]

                log.debug("node_version: {}".format(node_version))

                node_new_file = node_file.replace(node_version, new_version)
                each["file"].setValue(node_new_file)
                # H: don't need empty folders if work file isn't rendered later
                # if not os.path.isdir(os.path.dirname(node_new_file)):
                #    log.warning("Path does not exist! I am creating it.")
                #    os.makedirs(os.path.dirname(node_new_file), 0o766)
            except Exception as e:
                print(e)
                log.warning(
                    "Write node: `{}` has no version in path: {}".format(
                        each.name(), e
                    )
                )

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

            if group.knob("generate_review_media_on_farm"):
                saved_states["generate_review_media_on_farm"] = group.knob("generate_review_media_on_farm").value()
                group.knob("generate_review_media_on_farm").setValue(False)
                group.knob("generate_review_media_on_farm").setEnabled(False)

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

            if group.knob("generate_review_media_on_farm"):
                group.knob("generate_review_media_on_farm").setEnabled(True)
                if "generate_review_media_on_farm" in saved_states:
                    group.knob("generate_review_media_on_farm").setValue(saved_states["generate_review_media_on_farm"])

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

    from ayon_core.tools.utils import host_tools

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
nuke.addKnobChanged(handle_farm_publish_logic, nodeClass="Group")
nuke.addKnobChanged(warnSingleFrame, nodeClass="Write")
nuke.addKnobChanged(enable_disable_frame_range, nodeClass="Write")
#nuke.addOnScriptSave(set_hwrite_version)
nuke.addOnScriptSave(writes_ver_sync)
nuke.addOnScriptLoad(WorkfileSettings().set_colorspace)
nuke.addOnCreate(WorkfileSettings().set_colorspace, nodeClass="Root")


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
