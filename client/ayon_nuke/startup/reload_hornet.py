import importlib
import sys

import nuke

# List of all startup modules to reload. Deliberately excludes this module
# (reload_hornet) itself -- reloading it would reset the _registered registry
# below and break duplicate-free callback re-registration.
HORNET_MODULES = [
    "quick_write",
    "read_node_utils",
    "hornet_deadline_utils",
    "hornet_publish_utils",
    "file_sequence",
    "file_sequence.file_sequence",
    "views_write",
    "custom_write_node",
    "clear_rendered",
    "frame_setting_for_read_nodes",
]

# Quick Write callbacks whose registrations we manage, so a reload can rebind
# them to freshly reloaded code. Each entry is
# (add_fn, remove_fn, quick_write attribute name, kwargs).
_QW_CALLBACK_SPECS = [
    (nuke.addKnobChanged, nuke.removeKnobChanged, "embedOptions", {"nodeClass": "Write"}),
    (nuke.addKnobChanged, nuke.removeKnobChanged, "refresh_deadline_callback", {"nodeClass": "Group"}),
    (nuke.addKnobChanged, nuke.removeKnobChanged, "on_priority_clamp", {"nodeClass": "Group"}),
    (nuke.addKnobChanged, nuke.removeKnobChanged, "auto_match_publish_range", {"nodeClass": "Group"}),
    (nuke.addKnobChanged, nuke.removeKnobChanged, "on_variant_field_changed", {"nodeClass": "Group"}),
    (nuke.addOnCreate, nuke.removeOnCreate, "restore_file_output_height", {"nodeClass": "Group"}),
    # Paste dedup: Root onCreate flags script-load windows (loads must never
    # trigger the dedup); Group onCreate dedups pasted duplicate variants.
    (nuke.addOnCreate, nuke.removeOnCreate, "flag_script_loading", {"nodeClass": "Root"}),
    (nuke.addOnCreate, nuke.removeOnCreate, "dedup_variant_on_create", {"nodeClass": "Group"}),
    (nuke.addOnScriptLoad, nuke.removeOnScriptLoad, "locate_obsolete_on_load", {}),
]

# Currently-registered (remove_fn, func, kwargs) so we can unregister on reload.
_registered = []


def register_quick_write_callbacks():
    """(Re)register the Quick Write callbacks against the current quick_write.

    Removes any previous registrations first, so calling this after a reload
    rebinds to the reloaded functions instead of stacking duplicate callbacks.
    Call once at menu load, and again after reloading modules.
    """
    import quick_write

    for remove_fn, func, kwargs in _registered:
        try:
            remove_fn(func, **kwargs)
        except Exception:
            pass
    _registered.clear()

    for add_fn, remove_fn, attr, kwargs in _QW_CALLBACK_SPECS:
        func = getattr(quick_write, attr, None)
        if func is None:
            continue
        add_fn(func, **kwargs)
        _registered.append((remove_fn, func, kwargs))


def reload_hornet_modules(verbose=True):
    """Reload all hornet/startup modules, then rebind Quick Write callbacks."""
    reloaded = []
    failed = []

    for mod_name in HORNET_MODULES:
        if mod_name in sys.modules:
            try:
                importlib.reload(sys.modules[mod_name])
                reloaded.append(mod_name)
            except Exception as e:
                failed.append(f"{mod_name}: {e}")
        else:
            failed.append(f"{mod_name}: not loaded")

    # Rebind knobChanged/onCreate/onScriptLoad callbacks to the reloaded funcs
    # so panel-building code (embedOptions) etc. takes effect without a restart.
    try:
        register_quick_write_callbacks()
        callbacks_msg = "quick_write callbacks re-registered"
    except Exception as e:
        callbacks_msg = f"callback re-registration FAILED: {e}"

    if verbose:
        print(f"Reloaded {len(reloaded)} modules")
        for mod in reloaded:
            print(f"  reloaded {mod}")
        if failed:
            print(f"\nFailed to reload {len(failed)} modules:")
            for msg in failed:
                print(f"  failed {msg}")
        print(callbacks_msg)

    return reloaded, failed


# Backwards compatibility
def reload_hornet_deadline_utils():
    """Reload only hornet_deadline_utils (legacy function)."""
    mod_name = "hornet_deadline_utils"
    if mod_name in sys.modules:
        importlib.reload(sys.modules[mod_name])
        print(f"Reloaded {mod_name}")
    else:
        print(f"Module {mod_name} not found")


if __name__ == "__main__":
    reload_hornet_modules()
