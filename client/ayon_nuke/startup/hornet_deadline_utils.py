import json
import os
import getpass
import nuke
import sys
import subprocess
import requests
from pathlib import Path
from datetime import datetime

try:
    from ayon_core.settings import get_current_project_settings  # type: ignore
    from ayon_api import get_bundle_settings  # type: ignore
except ImportError:
    print("oh well")


## copied from submit_nuke_to_deadline.py
def GetDeadlineCommand():
    # type: () -> str
    deadlineBin = ""  # type: str
    try:
        deadlineBin = os.environ["DEADLINE_PATH"]
    except KeyError:
        # if the error is a key error it means that DEADLINE_PATH is not set. however Deadline command may be in the PATH or on OSX it could be in the file /Users/Shared/Thinkbox/DEADLINE_PATH
        pass

    # On OSX, we look for the DEADLINE_PATH file if the environment variable does not exist.
    if deadlineBin == "" and os.path.exists(
        "/Users/Shared/Thinkbox/DEADLINE_PATH"
    ):
        with open("/Users/Shared/Thinkbox/DEADLINE_PATH") as f:
            deadlineBin = f.read().strip()

    deadlineCommand = os.path.join(deadlineBin, "deadlinecommand")  # type: str

    return deadlineCommand


def CallDeadlineCommand(arguments, hideWindow=True):
    deadlineCommand = GetDeadlineCommand()  # type: str

    startupinfo = None  # type: ignore # this is only a windows option
    if hideWindow and os.name == "nt":
        # Python 2.6 has subprocess.STARTF_USESHOWWINDOW, and Python 2.7 has subprocess._subprocess.STARTF_USESHOWWINDOW, so check for both.
        try:
            if hasattr(subprocess, "_subprocess") and hasattr(
                subprocess._subprocess,
                "STARTF_USESHOWWINDOW",  # type: ignore
            ):
                startupinfo = subprocess.STARTUPINFO()  # type: ignore # this is only a windows option
                startupinfo.dwFlags |= (
                    subprocess._subprocess.STARTF_USESHOWWINDOW
                )  # type: ignore # this is only a windows option
            elif hasattr(subprocess, "STARTF_USESHOWWINDOW"):
                startupinfo = subprocess.STARTUPINFO()  # type: ignore # this is only a windows option
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW  # type: ignore # this is only a windows option
        except AttributeError:
            # Handle cases where subprocess attributes don't exist
            pass

    environment = {}
    for key in os.environ.keys():
        environment[key] = str(os.environ[key])

    if os.name == "nt":
        deadlineCommandDir = os.path.dirname(deadlineCommand)
        if not deadlineCommandDir == "":
            environment["PATH"] = (
                deadlineCommandDir + os.pathsep + os.environ["PATH"]
            )

    arguments.insert(0, deadlineCommand)
    output = ""

    # Specifying PIPE for all handles to workaround a Python bug on Windows. The unused handles are then closed immediatley afterwards.
    proc = subprocess.Popen(
        arguments,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        startupinfo=startupinfo,
        env=environment,
    )
    output, errors = proc.communicate()

    if sys.version_info[0] > 2 and type(output) is bytes:
        output = output.decode()

    return output  # type: ignore


## END copied from submit_nuke_to_deadline.py


def getSubmitterInfo():
    try:
        return json.loads(
            CallDeadlineCommand(
                [
                    "-prettyJSON",
                    "-GetSubmissionInfo",
                    "Pools",
                    "Groups",
                    "MaxPriority",
                    "UserHomeDir",
                    "RepoDir:submission/Nuke/Main",
                    "RepoDir:submission/Integration/Main",
                ]
            )
        )  # type: Dict

    except Exception as e:
        print("Failed to get submitter info: {}".format(e))


def get_frame_range_for_deadline(knobValues):
    """Generate frame range string with interval for Deadline submission"""
    if 'framelist' in knobValues and knobValues.get('framelist'):
        return knobValues['framelist']
    try:
        start = int(knobValues.get("first", nuke.root().firstFrame()))
        end = int(knobValues.get("last", nuke.root().lastFrame()))
        interval = int(knobValues.get("renderInterval", 1))

        if interval <= 1:
            return f"{start}-{end}"
        else:
            return f"{start}-{end}x{interval}"
    except Exception as e:
        print(f"Error generating frame range: {e}")
        return f"{int(nuke.root().firstFrame())}-{int(nuke.root().lastFrame())}"


def getNodeSubmissionInfo(node):
    print("getNodeSubmissionInfo")
    # node = nuke.thisNode()
    if node is None:
        raise Exception("Node provided to getNodeSubmissionInfo None")
    if node.Class() != "Group":
        raise Exception(
            "Node provided to getNodeSubmissionInfo is not a Group"
        )
    # print(f"render node: {node.Class()}")
    # inside_name = node.parent().fullName() + "." + node.name() + ".inside_" + node.name()
    inside_name = node.fullName() + ".inside_" + node.name()
    inside_write = nuke.toNode(inside_name)

    if inside_write is None:
        raise Exception(
            f"Node provided to getNodeSubmissionInfo has no inside write node\nAttempted to get inside write node: {inside_name}"
        )

    relevant_knobs = [
        "File output",
        "deadlinePool",
        "deadlineGroup",
        "deadlinePriority",
        "deadlineChunkSize",
        "concurrentTasks",
        "renderInterval",
        "framelist"
    ]

    # relevant_inside_knobs = ["first", "last"]
    all_knobs = node.allKnobs()
    knob_values = {
        knb.name(): knb.value()
        for knb in all_knobs
        if knb.name() in relevant_knobs
    }

    try:
        first_knob = inside_write.knob("first")
        last_knob = inside_write.knob("last")
        if first_knob is not None:
            knob_values["first"] = first_knob.value()
        if last_knob is not None:
            knob_values["last"] = last_knob.value()
    except Exception as e:
        print(f"Failed to get first/last knobs from inside write node: {e}")

    return knob_values


def deadlineNetworkSubmit(*, dev=False, batch=None, silent=False, node=None):
    # TODO I added miliseconds to the timestamp which forms the file name to allow for batch submissions, otherwise it fails beacuse it tries to
    # overwrite the same file each time.
    # Would be better to save once per batch which requires refactor

    print("deadlineNetworkSubmit dev mode v4")

    if node is None:
        node = nuke.thisNode()

    if node is None:
        raise Exception("Node provided to submitter None")

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")
    temp_script_path = "{path}/submission/{name}_{time}.nk".format(
        path=os.environ["AYON_WORKDIR"],
        name=os.path.splitext(os.path.basename(nuke.root().name()))[0],
        time=timestamp,
    )

    body = build_request(getNodeSubmissionInfo(node), temp_script_path, node)

    if batch is not None:
        body["JobInfo"]["BatchName"] = batch

    if dev:
        nuke.tprint(body)
        print(body)
        return

    file_path = body["PluginInfo"]["OutputFilePath"]
    nuke.tprint(f"File path: {file_path}")

    # save a copy of the script with the render as an artist discoverable backup
    save_script_with_render(
        Path(file_path)
    )  # ticket HPIPE-702 back up script with render

    # Create nuke script that render node will access
    if not os.path.exists(
        os.path.join(os.environ["AYON_WORKDIR"], "submission")
    ):
        os.mkdir(os.path.join(os.environ["AYON_WORKDIR"], "submission"))
    print(f"temp_script_path: {temp_script_path}")
    nuke.scriptSaveToTemp(temp_script_path)

    deadline_url = get_deadline_url()

    try:
        import requests
    except ImportError:
        raise Exception("failed to import requests")

    response = requests.post(deadline_url, json=body, timeout=10)

    if not response.ok:
        nuke.alert("Failed to submit to Deadline: {}".format(response.text))
        raise Exception(response.text)
    else:
        if not silent:
            nuke.alert("Submitted to Deadline Sucessfully")
        return True


def _rez_extra_info_pairs():
    """Compute ExtraInfoKeyValue entries that bypass Deadline's Rez
    event plugin AND drive its worker-side rez-env wrapper.

    The worker activates the rez context only when it can match a tool
    name from DEADLINE_REZ_TOOLS against the render plugin's executable
    list. For Nuke jobs ``nuke`` must be in that list (added on the
    Deadline side under Tools > Configure Plugins > Nuke).

    Pre-setting both keys at submission triggers the event plugin's
    OnJobSubmitted early-return so it doesn't warn about missing
    REZ_USED_RESOLVE (which it can't see on REST submissions because it
    reads the Deadline web service's env, not the submitter's).
    """
    resolve = os.environ.get("REZ_USED_RESOLVE")
    if not resolve:
        return []
    # ResolveSep on this farm is "-", which is the form REZ_USED_RESOLVE
    # already uses, so pass through verbatim.
    pairs = [("DEADLINE_REZ_REQUEST_PACKAGES", resolve)]
    try:
        out = subprocess.check_output(
            ["rez-context", "--tools"]
        ).decode("utf-8").splitlines()
        tools = [line.split()[0] for line in out[2:] if line.split()]
    except (OSError, subprocess.CalledProcessError):
        tools = []
    # ``nuke`` must be present for the worker to wrap the Nuke plugin in
    # rez-env; rez-context may not be on PATH from the submitter env, so
    # we guarantee it rather than relying on the subprocess succeeding.
    for required in ("nuke", "rez"):
        if required not in tools:
            tools.append(required)
    pairs.append(("DEADLINE_REZ_TOOLS", " ".join(tools)))
    return pairs


def build_request(knobValues, temp_script_path, node):
    # Include critical environment variables with submission
    print("build_request")
    submissionEnvVars = [
        "AYON_SERVER_URL",
        "AYON_API_KEY",
        "AYON_APP_NAME",
        "AYON_PROJECT_NAME",
        "AYON_TASK_NAME",
        "AYON_FOLDER_PATH",
        "AYON_HOST_NAME",
        "AYON_USE_DEV",
        "AYON_USE_STAGING",
        "HORNET_ROOT",
        "NUKE_PATH",
        "OCIO",
        "OPTICAL_FLARES_PATH",
        "peregrinel_LICENSE",
        "OFX_PLUGIN_PATH",
        "RVL_SERVER",
        "neatlab_LICENSE",
        "REZ_CONFIG_FILE",
        "REZ_USED_RESOLVE",
    ]
    environment = dict(
        {k: os.environ[k] for k in submissionEnvVars if k in os.environ.keys()}
    )
    # environment["HARDING"] = "picard"
    body = {
        "JobInfo": {
            # Job name, as seen in Monitor
            "Name": os.environ["AYON_PROJECT_NAME"].split("_")[0]
            + "_"
            + os.environ["AYON_FOLDER_PATH"]
            + "_"
            # + nuke.thisNode().fullName(),
            + node.fullName(),
            # pass submitter user
            "UserName": getpass.getuser(),
            "Priority": int(knobValues.get("deadlinePriority")) or 95,
            "Pool": knobValues.get("deadlinePool") or "local",
            "SecondaryPool": "",
            "Group": knobValues.get("deadlineGroup") or "nuke",
            "Plugin": "Nuke",
            "Frames": get_frame_range_for_deadline(knobValues),
            # Optional, enable double-click to preview rendered
            # frames from Deadline Monitor
            # "OutputFilename0": str(output_filename_0).replace("\\", "/"),
            # limiting groups
            "ChunkSize": int(knobValues.get("deadlineChunkSize", 1)) or 1,
            "LimitGroups": "nuke-limit",
            "ConcurrentTasks": int(knobValues.get("concurrentTasks", 1)),
        },
        "PluginInfo": {
            # Input
            "SceneFile": temp_script_path.replace("\\", "/"),
            # Output directory and filename
            "OutputFilePath": knobValues["File output"].replace("\\", "/"),
            # "OutputFilePrefix": render_variables["filename_prefix"],
            # Mandatory for Deadline
            "Version": str(nuke.NUKE_VERSION_MAJOR)
            + "."
            + str(nuke.NUKE_VERSION_MINOR),
            # Resolve relative references
            "ProjectPath": nuke.script_directory().replace("\\", "/"),
            # using GPU by default
            # Only the specific write node is rendered.
            # "WriteNode": nuke.thisNode().fullName(),
            "WriteNode": node.fullName(),
        },
        # Mandatory for Deadline, may be empty
        "AuxFiles": [],
    }
    body["JobInfo"].update(
        {
            "EnvironmentKeyValue%d" % index: "{key}={value}".format(
                key=key, value=str(environment[key])
            )
            for index, key in enumerate(environment)
        }
    )

    rez_pairs = _rez_extra_info_pairs()
    if rez_pairs:
        n = sum(
            1 for k in body["JobInfo"] if k.startswith("ExtraInfoKeyValue")
        )
        for i, (key, value) in enumerate(rez_pairs):
            body["JobInfo"]["ExtraInfoKeyValue%d" % (n + i)] = (
                "%s=%s" % (key, value)
            )

    print(body)
    return body


def save_script_with_render(write_node_file_path, is_ovs=False):
    """
    Back up the script next to the render files

    Args:
        write_node_file_path (Path): the value of the "File" knob of the write node
        is_ovs (bool): whether the write node is an OVS write node
    """
    if is_ovs:
        nuke.tprint("Render is OVS: Skipping script save with local render.")
        return

    if not isinstance(write_node_file_path, Path):
        write_node_file_path = Path(write_node_file_path)

    scripts_subfolder = "scripts"

    nuke.tprint("write_node_file_path", str(write_node_file_path))

    # write_node_file_path.parent.mkdir(parents=True, exist_ok=True)
    script_name = Path(nuke.root().name()).stem
    render_dir = Path(write_node_file_path).parent
    render_name = str(Path(write_node_file_path.name)).split(".")[0]
    save_name = script_name + "__" + render_name + ".nk"
    save_path = render_dir / Path(scripts_subfolder) / save_name
    save_path.parent.mkdir(parents=True, exist_ok=True)
    nuke.tprint("save_path", str(save_path))

    if Path(save_path).exists():
        os.remove(save_path)

    # Save a copy next to render
    nuke.scriptSaveToTemp(str(save_path))
    if Path(save_path).exists():
        nuke.tprint("Saved script to {}".format(save_path))
    else:
        nuke.tprint("Failed to save script to {}".format(save_path))


def get_deadline_server():
    project_settings = get_current_project_settings()
    deadline_settings = project_settings["deadline"]

    deadline_server = deadline_settings["deadline_urls"][0]["value"]
    if not deadline_server or deadline_server in [
        "http://127.0.0.1:8082",
        "http://localhost:8082",
    ]:
        # If it is, fetch the settings from the production bundle
        bundle_settings = get_bundle_settings(variant="production")["addons"]
        deadline_settings = next(
            (
                addon
                for addon in bundle_settings
                if addon.get("name") == "deadline"
            ),
            None,
        )

        if deadline_settings:
            deadline_server = deadline_settings["settings"]["deadline_urls"][
                0
            ]["value"]
    print(f"Current Deadline Webserver URL: {deadline_server}")

    return deadline_server


def get_deadline_url():
    deadline_server = get_deadline_server()
    deadline_url = "{}/api/jobs".format(deadline_server)
    return deadline_url







def vanilla_submit(write_node, frame_range=None, *, dev=False, batch=None, silent=False):
    """
    Submit a vanilla write node to Deadline
    
    Args:
        write_node: The Nuke write node to submit
        frame_range: Frame range string (e.g., "1-100" or "1-100x2"). If None, uses write node's frame range
        dev: Development mode - prints submission info without actually submitting
        batch: Batch name for grouping submissions
        silent: If True, doesn't show success alert
    """
    print("vanilla_submit - submitting vanilla write node to Deadline")
    
    if write_node is None:
        raise Exception("Write node provided to vanilla_submit is None")
    
    if write_node.Class() != "Write":
        raise Exception("Node provided to vanilla_submit is not a Write node")
    
    # Get frame range from write node if not provided
    if frame_range is None:
        first_frame = write_node.knob("first").value() if write_node.knob("first") else nuke.root().firstFrame()
        last_frame = write_node.knob("last").value() if write_node.knob("last") else nuke.root().lastFrame()
        frame_range = f"{int(first_frame)}-{int(last_frame)}"
    
    # Create timestamp for unique temp script
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")
    temp_script_path = "{path}/submission/{name}_{time}.nk".format(
        path=os.environ["AYON_WORKDIR"],
        name=os.path.splitext(os.path.basename(nuke.root().name()))[0],
        time=timestamp,
    )
    
    # Build submission request
    body = build_vanilla_request(write_node, frame_range, temp_script_path)
    
    if batch is not None:
        body["JobInfo"]["BatchName"] = batch
    
    if dev:
        nuke.tprint(body)
        print(body)
        return
    
    file_path = body["PluginInfo"]["OutputFilePath"]
    nuke.tprint(f"File path: {file_path}")
    
    # Save a copy of the script with the render as an artist discoverable backup
    save_script_with_render(Path(file_path))
    
    # Create nuke script that render node will access
    if not os.path.exists(os.path.join(os.environ["AYON_WORKDIR"], "submission")):
        os.mkdir(os.path.join(os.environ["AYON_WORKDIR"], "submission"))
    
    print(f"temp_script_path: {temp_script_path}")
    nuke.scriptSaveToTemp(temp_script_path)
    
    deadline_url = get_deadline_url()
    
    try:
        import requests
    except ImportError:
        raise Exception("failed to import requests")
    
    response = requests.post(deadline_url, json=body, timeout=10)
    
    if not response.ok:
        nuke.alert("Failed to submit to Deadline: {}".format(response.text))
        raise Exception(response.text)
    else:
        if not silent:
            nuke.alert("Submitted to Deadline Successfully")
        return True


def build_vanilla_request(write_node, frame_range, temp_script_path):
    """Build Deadline submission request for vanilla write nodes"""
    print("build_vanilla_request")
    
    # Include critical environment variables with submission
    submissionEnvVars = [
        "HORNET_ROOT",
        "NUKE_PATH",
        "OCIO",
        "OPTICAL_FLARES_PATH",
        "peregrinel_LICENSE",
        "OFX_PLUGIN_PATH",
        "RVL_SERVER",
        "neatlab_LICENSE",
    ]
    environment = dict(
        {k: os.environ[k] for k in submissionEnvVars if k in os.environ.keys()}
    )
    
    # Get output file path from write node
    output_file_path = write_node.knob("file").value()
    if not output_file_path:
        raise Exception("Write node has no output file path specified")
    
    body = {
        "JobInfo": {
            # Job name, as seen in Monitor
            "Name": os.environ["AYON_PROJECT_NAME"].split("_")[0]
            + "_"
            + os.environ["AYON_FOLDER_PATH"]
            + "_"
            + write_node.fullName(),
            # pass submitter user
            "UserName": getpass.getuser(),
            "Priority": 95,  # Default priority
            "Pool": "local",  # Default pool
            "SecondaryPool": "",
            "Group": "nuke",  # Default group
            "Plugin": "Nuke",
            "Frames": frame_range,
            "ChunkSize": 1,  # Default chunk size
            "LimitGroups": "nuke-limit",
            "ConcurrentTasks": 1,  # Default concurrent tasks
        },
        "PluginInfo": {
            # Input
            "SceneFile": temp_script_path.replace("\\", "/"),
            # Output directory and filename
            "OutputFilePath": output_file_path.replace("\\", "/"),
            # Mandatory for Deadline
            "Version": str(nuke.NUKE_VERSION_MAJOR)
            + "."
            + str(nuke.NUKE_VERSION_MINOR),
            # Resolve relative references
            "ProjectPath": nuke.script_directory().replace("\\", "/"),
            # Only the specific write node is rendered
            "WriteNode": write_node.fullName(),
        },
        # Mandatory for Deadline, may be empty
        "AuxFiles": [],
    }
    
    # Add environment variables
    body["JobInfo"].update(
        {
            "EnvironmentKeyValue%d" % index: "{key}={value}".format(
                key=key, value=str(environment[key])
            )
            for index, key in enumerate(environment)
        }
    )
    
    print(body)
    return body



def get_deadline_pools():
    """Get list of available pools from Deadline"""
    deadline_server = get_deadline_server()
    pools_url = "{}/api/pools?NamesOnly=true".format(deadline_server)

    
    try:
        response = requests.get(pools_url, timeout=10)
        
        if response.ok:
            pools = response.json()
            print(f"Available pools: {pools}")
            return pools
        else:
            print(f"Couldn't get pools from deadline: {response.text}")
            return []
            
    except Exception as e:
        print(f"Error getting pools from Deadline: {e}")
        return []


def get_deadline_groups():
    """Get list of available groups from Deadline"""
    deadline_server = get_deadline_server()
    groups_url = "{}/api/groups?NamesOnly=true".format(deadline_server)
    
    try:
        response = requests.get(groups_url, timeout=10)
        
        if response.ok:
            groups = response.json()    
            print(f"Available groups: {groups}")
            return groups
        else:
            print(f"Couldn't get groups from deadline: {response.text}")
            return []
            
    except Exception as e:
        print(f"Error getting groups from Deadline: {e}")
        return []
