"""Sanitised render scripts for farm submission.

Before a Deadline submit, write a .nk that contains only what the submitted
Write actually needs -- the nodes upstream of it -- instead of a full copy of
the artist's script. Unused Reads, dead branches and other Writes no longer
load on every task, so a broken gizmo or missing file in an unrelated branch
can't fail the job.

The artist's working script is never touched: the needed set is found with a
read-only upstream walk, those top-level nodes are written out with
nuke.nodeCopy() (only the selection is changed, and it is restored), and a Root
block built from the live root's knobs is put in front. No nodes are deleted,
no undo is involved, and no second Nuke is launched.

Modes (TOML default "sanitize_render_script"):
    "off"  full copy via scriptSaveToTemp, exactly as before
    "log"  full copy, plus a report of what WOULD be stripped (default while
           this is new -- use it to find references the walk can't see)
    "on"   sanitised copy; the full copy is kept beside it as *_full.nk, and
           any failure falls back to the full copy rather than block a submit

Known blind spot: nodes referenced only from python inside an expression
([python nuke.toNode("X")...]) or by name from a callback are invisible to
the walk and would be stripped. "log" mode exists to surface those.
"""

import os
import tempfile

import nuke

# What a node can depend on: pipes, hidden inputs (e.g. a Viewer-style hidden
# link, roto's source), and expression links ([value Tracker1.translate]).
_DEP_FLAGS = nuke.INPUTS | nuke.HIDDEN_INPUTS | nuke.EXPRESSIONS

# Layers Nuke always has; anything else gets an add_layer line in the output
# so channel references in the copied nodes still resolve on load.
_BUILTIN_LAYERS = {
    "none", "rgb", "rgba", "alpha", "depth", "motion", "forward", "backward",
    "mask", "rotopaint_mask", "other",
}

MODES = ("off", "log", "on")


def _mode():
    """Configured sanitize mode, from the TOML-layered defaults."""
    try:
        import quick_write

        mode = str(
            quick_write.get_quick_write_defaults().get(
                "sanitize_render_script", "log"
            )
        ).strip().lower()
    except Exception:
        mode = "log"
    return mode if mode in MODES else "log"


def _top_level(node):
    """The root-level ancestor of `node` (the node itself if top-level)."""
    top_name = node.fullName().split(".")[0]
    return nuke.toNode("root." + top_name)


def upstream_closure(start):
    """Top-level nodes the render of `start` needs, as {fullName: node}.

    Walks pipes, hidden inputs and expression links recursively, and descends
    into groups/gizmos so an expression inside one that points outside it
    pulls that node in too. Everything is mapped to its top-level ancestor,
    since that is the unit nodeCopy writes.
    """
    keep = {}
    seen = set()
    stack = [start]
    while stack:
        node = stack.pop()
        if node is None:
            continue
        try:
            key = node.fullName()
        except ValueError:  # deleted node
            continue
        if key in seen:
            continue
        seen.add(key)

        top = _top_level(node)
        if top is not None:
            keep.setdefault(top.fullName(), top)
            if top.fullName() not in seen:
                stack.append(top)

        try:
            stack.extend(node.dependencies(_DEP_FLAGS))
        except Exception:
            pass
        if isinstance(node, nuke.Group):
            try:
                stack.extend(node.nodes())
            except Exception:
                pass

    # Clones write as references to their original; copying one without the
    # rest is unreliable, so keep every cloned node if any kept node is one.
    if any(_is_clone(n) for n in keep.values()):
        for n in nuke.allNodes():
            if _is_clone(n):
                keep.setdefault(n.fullName(), n)
    return keep


def _is_clone(node):
    try:
        return node.clones() > 0
    except Exception:
        return False


def nodes_within(tops):
    """Every node inside the given top-level nodes, groups recursed."""
    out = []
    for top in tops:
        out.append(top)
        if isinstance(top, nuke.Group):
            try:
                with top:
                    out.extend(nuke.allNodes(recurseGroups=True))
            except Exception:
                pass
    return out


def stripped_nodes(closure):
    """Top-level nodes the sanitised script would drop (Viewers/Backdrops
    aside -- they never matter to a render)."""
    ignore = {"Viewer", "BackdropNode", "StickyNote"}
    return [
        n for n in nuke.allNodes()
        if n.fullName() not in closure and n.Class() not in ignore
    ]


def _add_layer_lines():
    """add_layer statements for every non-builtin layer in the session."""
    by_layer = {}
    for chan in nuke.channels():
        layer, _, _ = chan.partition(".")
        if layer and layer not in _BUILTIN_LAYERS:
            by_layer.setdefault(layer, []).append(chan)
    return [
        "add_layer {%s %s}" % (layer, " ".join(chans))
        for layer, chans in sorted(by_layer.items())
    ]


def _root_block():
    """Root { ... } built from the live root, keeping its name so
    [value root.name] and project-relative paths resolve as in the session."""
    knobs = nuke.root().writeKnobs(nuke.WRITE_NON_DEFAULT_ONLY | nuke.TO_SCRIPT)
    lines = ["Root {", " inputs 0"]
    lines.extend(" " + l for l in knobs.splitlines() if l.strip())
    lines.append("}")
    return lines


def _copy_nodes(tops):
    """nodeCopy the given top-level nodes; returns the copied text. Restores
    the user's selection and modified flag."""
    was_modified = nuke.root().modified()
    all_top = nuke.allNodes()
    previously = {n.fullName() for n in all_top if n.isSelected()}
    keep = {n.fullName() for n in tops}
    fd, tmp = tempfile.mkstemp(suffix=".nk", prefix="hornet_sanitize_")
    os.close(fd)
    try:
        with nuke.root():
            for n in all_top:
                n.setSelected(n.fullName() in keep)
            nuke.nodeCopy(tmp)
        with open(tmp, encoding="utf-8") as fh:
            return fh.read()
    finally:
        with nuke.root():
            for n in nuke.allNodes():
                try:
                    n.setSelected(n.fullName() in previously)
                except Exception:
                    pass
        if not was_modified:
            nuke.root().setModified(False)
        try:
            os.remove(tmp)
        except OSError:
            pass


def build_sanitised_script(start):
    """Return (script_text, closure) for a script holding only what `start`
    needs. Raises ValueError if the copied text looks incomplete."""
    closure = upstream_closure(start)
    body = _copy_nodes(list(closure.values()))

    version_line = None
    rest = []
    for line in body.splitlines():
        if line.startswith("set cut_paste_input"):
            continue
        if version_line is None and line.startswith("version "):
            version_line = line
            continue
        rest.append(line)
    text_body = "\n".join(rest)

    # A complete upstream closure has no inputs outside the copy, so the
    # paste-format input placeholder must not appear. If it does, the walk
    # missed something -- refuse rather than ship a broken graph.
    if "$cut_paste_input" in text_body:
        raise ValueError("copied graph still references outside inputs")
    target = _top_level(start)
    if target is None or ("name %s\n" % target.name()) not in text_body + "\n":
        raise ValueError("submitted node missing from the copied graph")

    out = ["#! %s -nx" % nuke.EXE_PATH]
    out.append(version_line or "version %s" % nuke.NUKE_VERSION_STRING)
    out.extend(_add_layer_lines())
    out.extend(_root_block())
    out.append(text_body)
    return "\n".join(out) + "\n", closure


def save_render_script(start, out_path, log=None):
    """Write the farm copy of the script for rendering `start` to `out_path`,
    according to the configured mode. Never raises for sanitise problems --
    falls back to a full copy -- so a submit is never blocked by this."""
    say = log or nuke.tprint
    mode = _mode()

    if mode == "off":
        nuke.scriptSaveToTemp(out_path)
        return out_path

    try:
        text, closure = build_sanitised_script(start)
    except Exception as exc:
        say("[sanitize] could not build a sanitised script ({}); submitting "
            "the full script".format(exc))
        nuke.scriptSaveToTemp(out_path)
        return out_path

    dropped = stripped_nodes(closure)
    summary = "[sanitize] {}: keeps {} top-level node(s), strips {}{}".format(
        start.name(), len(closure), len(dropped),
        (": " + ", ".join(sorted(n.name() for n in dropped)[:40])
         + (" ..." if len(dropped) > 40 else "")) if dropped else "",
    )

    if mode == "log":
        say(summary + "  (log mode: full script submitted)")
        nuke.scriptSaveToTemp(out_path)
        return out_path

    # mode == "on": keep the full copy beside it for an instant resubmit.
    root, ext = os.path.splitext(out_path)
    try:
        nuke.scriptSaveToTemp(root + "_full" + ext)
    except Exception as exc:
        say("[sanitize] could not write the full backup copy: {}".format(exc))
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(text)
    say(summary)
    return out_path
