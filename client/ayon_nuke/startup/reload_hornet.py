import importlib
import sys

# List of all startup modules to reload
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
    "reload_hornet",
]


def reload_hornet_modules(verbose=True):
    """Reload all hornet/startup modules."""
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
    
    if verbose:
        print(f"Reloaded {len(reloaded)} modules")
        for mod in reloaded:
            print(f"  ✓ {mod}")
        if failed:
            print(f"\nFailed to reload {len(failed)} modules:")
            for msg in failed:
                print(f"  ✗ {msg}")
    
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