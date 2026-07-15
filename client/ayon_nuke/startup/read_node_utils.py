import re
import os
import glob
import nuke
import json
import platform
import pathlib
from ayon_core.lib import Logger, StringTemplate
from file_sequence import SequenceFactory
from ayon_nuke.api.lib import (
    get_server_pub_version,
    is_version_file_linked,
    get_version_from_path,
    INSTANCE_DATA_KNOB,
)
# Safe (non-circular): quick_write only imports this module lazily inside
# functions. Resolves the group's file-output knob old or new name.
from quick_write import get_file_output_knob

log = Logger.get_logger(__name__)

SINGLE_FILE_FORMATS = [
    "avi",
    "mp4",
    "mxf",
    "mov",
    "mpg",
    "mpeg",
    "wmv",
    "m4v",
    "m2v",
]


def evaluate_filepath_new(
    k_value, k_eval, project_dir, first_frame, allow_relative
):
    # get combined relative path
    combined_relative_path = None
    if k_eval is not None and project_dir is not None:
        combined_relative_path = os.path.abspath(
            os.path.join(project_dir, k_eval)
        )
        combined_relative_path = combined_relative_path.replace("\\", "/")
        filetype = combined_relative_path.split(".")[-1]
        frame_number = re.findall(r"\d+", combined_relative_path)[-1]
        basename = combined_relative_path[
            : combined_relative_path.rfind(frame_number)
        ]
        filepath_glob = basename + "*" + filetype
        glob_search_results = glob.glob(filepath_glob)
        if len(glob_search_results) <= 0:
            combined_relative_path = None

    try:
        # k_value = k_value % first_frame
        if os.path.isdir(os.path.basename(k_value)):
            # doesn't check for file, only parent dir
            filepath = k_value
        elif os.path.exists(k_eval):
            filepath = k_eval
        elif not isinstance(project_dir, type(None)) and not isinstance(
            combined_relative_path, type(None)
        ):
            filepath = combined_relative_path

        filepath = os.path.abspath(filepath)
    except Exception as E:
        log.error(
            "Cannot create Read node. Perhaps it needs to be \nrendered first :) Error: `{}`".format(
                E
            )
        )
        return None

    filepath = filepath.replace("\\", "/")
    # assumes last number is a sequence counter
    current_frame = re.findall(r"\d+", filepath)[-1]
    padding = len(current_frame)
    basename = filepath[: filepath.rfind(current_frame)]
    filetype = filepath.split(".")[-1]

    # sequence or not?
    if filetype in SINGLE_FILE_FORMATS:
        pass
    else:
        # Image sequence needs hashes
        # to do still with no number not handled
        filepath = basename + "#" * padding + "." + filetype

    # relative path? make it relative again
    if allow_relative:
        if (not isinstance(project_dir, type(None))) and project_dir != "":
            filepath = filepath.replace(project_dir, ".")

    # get first and last frame from disk
    frames = []
    firstframe = 0
    lastframe = 0
    filepath_glob = basename + "*" + filetype
    glob_search_results = glob.glob(filepath_glob)
    for f in glob_search_results:
        frame = re.findall(r"\d+", f)[-1]
        frames.append(frame)
    frames = sorted(frames)
    firstframe = frames[0]
    lastframe = frames[len(frames) - 1]

    if int(lastframe) < 0:
        lastframe = firstframe

    return filepath, firstframe, lastframe


def create_read_node(ndata, comp_start):
    nuke.tprint(ndata["filepath"])
    print(ndata["filepath"])

    read = nuke.createNode("Read", 'file "' + ndata["filepath"] + '"')
    read.knob("colorspace").setValue(int(ndata["colorspace"]))
    read.knob("raw").setValue(ndata["rawdata"])
    read.knob("first").setValue(int(ndata["firstframe"]))
    read.knob("last").setValue(int(ndata["lastframe"]))
    read.knob("origfirst").setValue(int(ndata["firstframe"]))
    read.knob("origlast").setValue(int(ndata["lastframe"]))
    if comp_start == int(ndata["firstframe"]):
        read.knob("frame_mode").setValue("1")
        read.knob("frame").setValue(str(comp_start))
    else:
        read.knob("frame_mode").setValue("0")
    read.knob("xpos").setValue(ndata["new_xpos"])
    read.knob("ypos").setValue(ndata["new_ypos"])
    nuke.inputs(read, 0)
    return read


def write_to_read(write_group_node, allow_relative=False, context=None, xypos=None):

    current_context = nuke.thisNode()

    comp_start = nuke.Root().knob("first_frame").value()
    project_dir = nuke.Root().knob("project_directory").getValue()
    if not os.path.exists(project_dir):
        project_dir = nuke.Root().knob("project_directory").evaluate()

    group_read_nodes = []
    with write_group_node:
        height = (
            write_group_node.screenHeight()
        )  # get group height and position
        new_xpos = int(write_group_node.knob("xpos").value())
        new_ypos = int(write_group_node.knob("ypos").value()) + height + 20
        group_writes = [n for n in nuke.allNodes() if n.Class() == "Write"]
        if group_writes != []:
            # there can be only 1 write node, taking first
            n = group_writes[0]

            if n.knob("file") is not None:
                result = evaluate_filepath_new(
                    n.knob("file").getValue(),
                    n.knob("file").evaluate(),
                    project_dir,
                    comp_start,
                    allow_relative,
                )

                if result is not None:
                    myfile, firstFrame, lastFrame = result
                else:
                    nuke.message("No render found")
                    return

                # myfile, firstFrame, lastFrame = evaluate_filepath_new(
                #     n.knob("file").getValue(),
                #     n.knob("file").evaluate(),
                #     project_dir,
                #     comp_start,
                #     allow_relative,
                # )
                # if not myfile:
                #     return

                # get node data
                ndata = {
                    "filepath": myfile,
                    "firstframe": int(firstFrame),
                    "lastframe": int(lastFrame),
                    "new_xpos": new_xpos,
                    "new_ypos": new_ypos,
                    "colorspace": n.knob("colorspace").getValue(),
                    "rawdata": n.knob("raw").value(),
                    "write_frame_mode": str(n.knob("frame_mode").value()),
                    "write_frame": n.knob("frame").value(),
                }
                group_read_nodes.append(ndata)

    if context is not None:
        with context:
 
            for oneread in group_read_nodes:
                read = create_read_node(oneread, comp_start)
    else:
        for oneread in group_read_nodes:
            read = create_read_node(oneread, comp_start)
    
    if xypos is not None:
        read.setXYpos(xypos[0], xypos[1])

def slice_path(path, start, end):
    # return a re-joined path from a slice of path parts

    path = pathlib.Path(path)
    return pathlib.Path(path.parts[start]).joinpath(
        *path.parts[start + 1 : end]
    )


def get_publish_instance_data(write_node):
    # parse the json data from the write node

    return json.loads(
        write_node["publish_instance"].value().replace("JSON:::", "")
    )


def assemble_publish_path(ayon_write_node):
    from ayon_core.pipeline import registered_host, Anatomy

    host = registered_host()
    context = host.get_current_context()
    instance_data = get_publish_instance_data(ayon_write_node)
    anatomy = Anatomy()
    directory_template = anatomy.templates["publish"]["render"]["directory"]
    file_template = anatomy.templates["publish"]["render"]["file"]

    root = anatomy.roots["work"].value.rstrip("/")
    project_name = context["project_name"]
    hierarchy = pathlib.Path(context["folder_path"].lstrip("/")).parent
    shot = pathlib.Path(context["folder_path"]).name
    product = instance_data["productType"]
    name = instance_data["productName"]

    if INSTANCE_DATA_KNOB in ayon_write_node.knobs():
        data = json.loads(
            ayon_write_node[INSTANCE_DATA_KNOB].value().replace("JSON:::", "", 1)
        )
        is_ovs = data["is_ovs"]
    else:
        is_ovs = False

    # Check if last publish was single frame and adjust product name
    is_single_frame = False
    if ayon_write_node.knob("_last_publish_single_frame"):
        is_single_frame = ayon_write_node.knob("_last_publish_single_frame").value()

    if is_single_frame:
        # Add _singleFrame suffix to product name to match publish folder
        if not name.endswith("_singleFrame"):
            name = name + "_singleFrame"

    # If the write node path is linked to a version file, get the version from there
    server_version = get_server_pub_version(project_name, name, context["folder_path"])
    if is_version_file_linked() and is_ovs:
        file_version_num = get_version_from_path(
            get_file_output_knob(ayon_write_node).value()
        )
        if int(file_version_num) < server_version[0]:
            log.warning(
                f"File version {file_version_num} is lower than server version {server_version[0]}. Using server version."
            )
            versions = server_version
        
        else:
            versions = (file_version_num, "v" + file_version_num)

    else:
        versions = server_version






    publish_path = pathlib.Path(
        directory_template.format_map(
            {
                "root": {"work": root},
                "project": {"name": project_name},
                "hierarchy": hierarchy,
                "folder": {"name": shot},
                "product": {"type": product, "name": name},
                "version": versions[0],
            }
        )
    )

    # try:
    #     newest_version = Path(
    #         sorted([d.name for d in publish_path.iterdir() if d.is_dir()])[-1]
    #     )
    # except IndexError:
    #     print("No versions, has it been published?")
    #     log.error("No versions, has it been published?")
    #     return
    # except Exception as e:
    #     print(f"Unexpected error: {e}")
    #     log.error(f"Unexpected error: {e}")
    #     return

    extension = ayon_write_node["file_type"].value()

    # first_frame = int(ayon_write_node["Render Start"].getValue())
    # last_frame = int(ayon_write_node["Render End"].getValue())
    # pad_count = len(str(last_frame))

    file_data = {
        "folder": {"name": shot},
        "product": {"name": name},
        "frame": "%04d",
        "version": versions[0],
        "ext": extension,
    }

    # first_frame = instance_data.get("frameStart")
    # last_frame = instance_data.get("frameEnd")

    file_string = StringTemplate(file_template).format_strict(file_data)
    # file_string = f"{file_string} {first_frame}-{last_frame}"

    # """
    # Ideally we would query the database for products to get the latest version,
    # and parse "file_template" to get the file name.

    # For now I am finding the latest version from the file system, and
    # using FileSequence lib to extract a valid image sequence from that location
    # """
    # RESOLVED

    # seqs = FileSequence.match_components_in_path(
    #     Components(
    #         extension=extension,
    #     ),
    #     publish_path,
    # )

    # if not seqs:
    #     print("No sequence found")
    #     return

    # sequence = seqs[0]
    # string = sequence.sequence_string(sequence.StringVariant.NUKE)
    
    # if not publish_path.exists():
    #     print(f"publish_path does not exist: {publish_path}")
    #     return None

    result = publish_path / file_string

    if not publish_path.exists():
        print(f"publish_path does not exist: {publish_path}")
        return None

    if is_single_frame:
        # For single frames, drop the frame token from the render-template name.
        result = pathlib.Path(str(result).replace(".%04d", ""))
        target = result.name

        # The integrator publishes with a template that prepends the project
        # code (e.g. "rndAlex3_"), so the on-disk file ends with -- but is not
        # equal to -- our render-template name. Resolve against what's actually
        # in the version folder: prefer a suffix match (the prepended real name),
        # then the exact render-template file, then the sole file present.
        candidates = sorted(
            p for p in publish_path.glob("*.{}".format(extension))
            if p.is_file()
        )
        suffix_matches = [p for p in candidates if p.name.endswith(target)]
        if len(suffix_matches) == 1:
            result = suffix_matches[0]
        elif result.exists():
            pass  # exact render-template name is on disk; keep it
        elif len(candidates) == 1:
            result = candidates[0]
        else:
            msg = (
                "No published single frame matching '{}' in {}. "
                "Files found: {}".format(
                    target,
                    publish_path,
                    [p.name for p in candidates] or "none",
                )
            )
            print(msg)
            log.error(msg)
            return None
    else:
        # For sequences, add frame range
        fs = SequenceFactory.from_sequence_string_absolute(
            str(result), min_frames=1
        )
        if fs is None:
            # The integrator may have written file names with a different
            # publish template than "render" (e.g. "default" prefixes the
            # project code), so fall back to the sequence with a matching
            # extension in the version folder.
            candidates = [
                s
                for s in SequenceFactory.from_directory(
                    publish_path, min_frames=1
                )
                if s.extension == extension
            ]
            if len(candidates) == 1:
                fs = candidates[0]
            else:
                msg = (
                    f"No published sequence matching '{result.name}' in "
                    f"{publish_path}. Sequences found there: "
                    f"{[s.sequence_string() for s in candidates] or 'none'}"
                )
                print(msg)
                log.error(msg)
                return None
        result = pathlib.Path(
            f"{fs.absolute_file_name} {fs.first_frame}-{fs.last_frame}"
        )

    print(result)
    log.debug(f"Assembled publish path:{result}")

    return result


def find_review_media(publish_dir):
    """Return published review movie files in a version folder.

    Looks for single-file movie formats (mov/mp4/...) directly in the version
    directory, where ExtractFFmpegReview's deliverables are integrated. Returns
    an empty list if the directory is missing or holds no movies.
    """
    if publish_dir is None or not publish_dir.exists():
        return []

    movies = []
    for ext in SINGLE_FILE_FORMATS:
        movies.extend(publish_dir.glob("*.{}".format(ext)))

    return sorted(p.as_posix() for p in movies if p.is_file())


def read_from_publish(ayon_write_node, context = None, xypos = None):
    if (ayon_write_node) is None:
        log.error("ayon_write_node is None")
        nuke.tprint("ayon_write_node is None")

    with nuke.toNode("root"):
        ppath = assemble_publish_path(ayon_write_node)
        if not ppath:
            return

        if not ppath.exists():
            print("absolutely no publishes were discovered.")

        publish_path = ppath.as_posix()

        if (publish_path) is None:
            return

        read_node = nuke.nodes.Read()
        read_node["file"].fromUserText(publish_path)

        if xypos is not None:
            read_node.setXYpos(xypos[0], xypos[1])

        else:
            read_node.setXYpos(
                int(ayon_write_node["xpos"].getValue()),
                int(ayon_write_node["ypos"].getValue()) + 60,
            )

        # Also pull in any published review media that sits in the same version
        # folder. fromUserText lets Nuke discover the movie's frame range, so we
        # don't set first/last ourselves.
        review_xpos = read_node.xpos()
        review_ypos = read_node.ypos()
        for offset, movie_path in enumerate(
            find_review_media(ppath.parent), start=1
        ):
            review_read = nuke.nodes.Read()
            review_read["file"].fromUserText(movie_path)
            review_read.setXYpos(review_xpos + offset * 100, review_ypos)
            log.info(f"Read review media from publish: {movie_path}")

        return read_node


def get_publish_instance_data(write_node):
    # parse the json data from the write node

    return json.loads(
        write_node["publish_instance"].value().replace("JSON:::", "")
    )


def navigate_to_render(write_node):
    """Open explorer at the location of the render file

    Args:
        Ayon write node

    """

    file_path = pathlib.Path(
        get_file_output_knob(write_node).evaluate()
    ).parent
    if not file_path.exists():
        return

    if platform.system() == "Windows":
        os.startfile(file_path)
    elif platform.system() == "Darwin":  # macOS
        subprocess.run(["open", file_path])
    else:  # Linux
        subprocess.run(["xdg-open", file_path])


def navigate_to_publish(write_node):
    """Open explorer at the location of the publish file

    Args:
        Ayon write node

    """

    path = assemble_publish_path(write_node)
    if not path:
        return

    # assemble_publish_path resolves a file inside the latest version folder;
    # open that version folder itself -- not its parent, which holds every
    # version subfolder.
    path = path if path.is_dir() else path.parent

    if not path.exists():
        print("absolutely no publishes were discovered.")
        return

    print(f"Publish path: {path}")

    if platform.system() == "Windows":
        os.startfile(path)
    elif platform.system() == "Darwin":  # macOS
        subprocess.run(["open", path])
    else:  # Linux
        subprocess.run(["xdg-open", path])


def filter_write_nodes(nodes):
    filtered_nodes = [
        n
        for n in nodes
        if n.Class() == "Group"
        and "publish_instance" in n.knobs()
        and "submit" in n.knobs()
    ]

    return filtered_nodes
