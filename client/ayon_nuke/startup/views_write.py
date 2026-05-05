import pathlib

# from turtle import color
import nuke

import ast
import hornet_deadline_utils

from qtpy import QtWidgets, QtCore  # type: ignore
from ayon_core.pipeline import registered_host, Anatomy
from ayon_nuke.api.lib import get_version_from_path

# COLORSPACE_LIST = [
#     "Output - Rec.709",
#     "Output - sRGB",
#     "scene_linear",
#     "Utility - Raw",
# ]

RENDER_DEFAULT_COLORSPACE = "Output - Rec.709"
RENDER_DEFAULT_FILE_FORMAT = "dpx"
DEFAULT_ASPECT = ""
# DEFAULT_ASPECT = "16x9"

DEFAULT_DIALOG_WIDTH = 1200
DEFAULT_DIALOG_HEIGHT = 1000

APPROVAL_FRAME_DEFAULT_COLORSPACE = "Output - sRGB"
APPROVAL_FRAME_DEFAULT_FILE_FORMAT = "png"


class Render_submission_dialog(QtWidgets.QDialog):
    def __init__(self, parent=None, saved_data=None, kroger_node=None):
        super().__init__(parent)
        self.saved_data = saved_data or {}
        self.kroger_node = kroger_node
        self.submission_type = (
            None  # Track which type of submission was performed
        )
        self.setup_ui()
        self.populate_view_table()
        self.load_saved_data()

    def setup_ui(self):
        self.setWindowTitle("Render Submission")
        self.setMinimumSize(DEFAULT_DIALOG_WIDTH, DEFAULT_DIALOG_HEIGHT)
        self.resize(700, 500)

        # Main layout
        layout = QtWidgets.QVBoxLayout(self)

        # Global settings section
        global_group = QtWidgets.QGroupBox("Global Settings")
        global_layout = QtWidgets.QFormLayout(global_group)

        # Global frame range - use script globals
        first_frame = int(nuke.root().firstFrame())
        last_frame = int(nuke.root().lastFrame())
        default_range = f"{first_frame}-{last_frame}"

        self.global_range_edit = QtWidgets.QLineEdit(default_range)

        range_layout = QtWidgets.QHBoxLayout()
        range_layout.addWidget(self.global_range_edit)

        self.apply_global_btn = QtWidgets.QPushButton("Apply to All")
        self.apply_global_btn.clicked.connect(self.apply_global_range)
        range_layout.addWidget(self.apply_global_btn)

        global_layout.addRow("Global Range:", range_layout)

        # Global Priority with Apply button
        self.global_priority_spin = QtWidgets.QSpinBox()
        self.global_priority_spin.setRange(1, 100)
        self.global_priority_spin.setValue(95)
        priority_layout = QtWidgets.QHBoxLayout()
        priority_layout.addWidget(self.global_priority_spin)

        self.apply_priority_btn = QtWidgets.QPushButton("Apply to All")
        self.apply_priority_btn.clicked.connect(self.apply_global_priority)
        priority_layout.addWidget(self.apply_priority_btn)

        global_layout.addRow("Global Priority:", priority_layout)

        # Chunk Size
        self.chunk_size_spin = QtWidgets.QSpinBox()
        self.chunk_size_spin.setRange(1, 1000)
        self.chunk_size_spin.setValue(1)
        global_layout.addRow("Chunk Size:", self.chunk_size_spin)

        # Concurrent Tasks
        self.concurrent_tasks_spin = QtWidgets.QSpinBox()
        self.concurrent_tasks_spin.setRange(1, 100)
        self.concurrent_tasks_spin.setValue(2)
        global_layout.addRow("Concurrent Tasks:", self.concurrent_tasks_spin)

        # Pool
        self.pool_edit = QtWidgets.QLineEdit("local")
        global_layout.addRow("Pool:", self.pool_edit)

        # Group
        self.group_edit = QtWidgets.QLineEdit("nuke")
        global_layout.addRow("Group:", self.group_edit)

        # File Format
        self.file_format_combo = QtWidgets.QComboBox()
        self.file_format_combo.addItems(self.get_file_format_options())
        self.file_format_combo.setCurrentText(RENDER_DEFAULT_FILE_FORMAT)
        global_layout.addRow("File Format:", self.file_format_combo)

        # Colorspace
        self.colorspace_combo = QtWidgets.QComboBox()
        self.colorspace_combo.addItems(self.get_colorspace_options())
        self.colorspace_combo.setCurrentText(RENDER_DEFAULT_COLORSPACE)
        global_layout.addRow("Colorspace:", self.colorspace_combo)
        # self.colorspace_combo.currentIndexChanged.connect(self.on_colorspace_changed)

        layout.addWidget(global_group)

        # Views table section
        table_group = QtWidgets.QGroupBox("View Selection")
        table_layout = QtWidgets.QVBoxLayout(table_group)

        # Selection buttons
        selection_buttons = QtWidgets.QHBoxLayout()

        self.select_all_btn = QtWidgets.QPushButton("Select All")
        self.select_all_btn.clicked.connect(self.select_all_views)
        selection_buttons.addWidget(self.select_all_btn)

        self.select_none_btn = QtWidgets.QPushButton("Select None")
        self.select_none_btn.clicked.connect(self.select_none_views)
        selection_buttons.addWidget(self.select_none_btn)

        selection_buttons.addStretch()
        table_layout.addLayout(selection_buttons)

        # Create the table
        self.view_table = QtWidgets.QTableWidget()
        self.view_table.setColumnCount(4)
        self.view_table.setHorizontalHeaderLabels(
            ["View Name", "Frame Range", "Priority", "Render"]
        )

        # Table settings
        self.view_table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectRows
        )
        self.view_table.setAlternatingRowColors(True)
        self.view_table.verticalHeader().setVisible(False)

        # Resize columns
        header = self.view_table.horizontalHeader()
        header.setSectionResizeMode(
            0, QtWidgets.QHeaderView.Fixed
        )  # View Name - fixed width
        header.setSectionResizeMode(
            1, QtWidgets.QHeaderView.Stretch
        )  # Frame Range
        header.setSectionResizeMode(
            2, QtWidgets.QHeaderView.Fixed
        )  # Priority - fixed width
        header.setSectionResizeMode(
            3, QtWidgets.QHeaderView.ResizeToContents
        )  # Render checkbox

        # Set specific column widths
        self.view_table.setColumnWidth(0, 120)  # View Name - wider
        self.view_table.setColumnWidth(2, 70)  # Priority - narrower

        table_layout.addWidget(self.view_table)

        layout.addWidget(table_group)

        # Test button for applying settings
        test_layout = QtWidgets.QHBoxLayout()
        self.apply_settings_btn = QtWidgets.QPushButton(
            "Apply Settings to nodes"
        )
        self.apply_settings_btn.clicked.connect(
            lambda: self.apply_settings(debug=True)
        )
        test_layout.addWidget(self.apply_settings_btn)
        test_layout.addStretch()
        layout.addLayout(test_layout)

        # Dialog buttons - Submit Local, Submit to Farm, and Cancel
        button_layout = QtWidgets.QHBoxLayout()

        self.submit_local_btn = QtWidgets.QPushButton("Submit Local")

        self.submit_local_btn.clicked.connect(self.submit_locally)
        button_layout.addWidget(self.submit_local_btn)

        self.submit_farm_btn = QtWidgets.QPushButton("Submit to Farm")

        self.submit_farm_btn.clicked.connect(self.submit_to_farm)
        button_layout.addWidget(self.submit_farm_btn)

        button_layout.addStretch()

        self.cancel_btn = QtWidgets.QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_btn)

        layout.addLayout(button_layout)

    def populate_view_table(self):
        """Populate the table with current script views"""

        views = nuke.views()
        self.view_table.setRowCount(len(views))

        # Get default frame range
        first_frame = int(nuke.root().firstFrame())
        last_frame = int(nuke.root().lastFrame())
        default_range = f"{first_frame}-{last_frame}"

        for row, view_name in enumerate(views):
            # Column 0: View Name (read-only)
            name_item = QtWidgets.QTableWidgetItem(view_name)
            name_item.setFlags(name_item.flags() & ~QtCore.Qt.ItemIsEditable)
            # Remove the gray background - keep default
            self.view_table.setItem(row, 0, name_item)

            # Column 1: Frame Range (editable)
            range_item = QtWidgets.QTableWidgetItem(default_range)
            self.view_table.setItem(row, 1, range_item)

            # Column 2: Priority (spin box)
            priority_spin = QtWidgets.QSpinBox()
            priority_spin.setRange(1, 100)
            priority_spin.setValue(95)
            priority_spin.setAlignment(QtCore.Qt.AlignCenter)
            self.view_table.setCellWidget(row, 2, priority_spin)

            # Column 3: Render checkbox
            checkbox = QtWidgets.QCheckBox()
            # Default "main" views to unchecked, others to checked
            default_checked = view_name.lower() != "main"
            checkbox.setChecked(default_checked)

            # Center the checkbox in the cell
            checkbox_widget = QtWidgets.QWidget()
            checkbox_layout = QtWidgets.QHBoxLayout(checkbox_widget)
            checkbox_layout.addWidget(checkbox)
            checkbox_layout.setAlignment(QtCore.Qt.AlignCenter)
            checkbox_layout.setContentsMargins(0, 0, 0, 0)

            self.view_table.setCellWidget(row, 3, checkbox_widget)

        # Auto-resize rows to content
        self.view_table.resizeRowsToContents()

    def load_saved_data(self):
        """Load previously saved data into the dialog"""
        if not self.saved_data:
            return

        # Load global frame range
        if "global_frame_range" in self.saved_data:
            self.global_range_edit.setText(
                self.saved_data["global_frame_range"]
            )

        # Load global render settings
        if "global_priority" in self.saved_data:
            self.global_priority_spin.setValue(
                self.saved_data["global_priority"]
            )

        if "chunk_size" in self.saved_data:
            self.chunk_size_spin.setValue(self.saved_data["chunk_size"])

        if "concurrent_tasks" in self.saved_data:
            self.concurrent_tasks_spin.setValue(
                self.saved_data["concurrent_tasks"]
            )

        if "pool" in self.saved_data:
            self.pool_edit.setText(self.saved_data["pool"])

        if "group" in self.saved_data:
            self.group_edit.setText(self.saved_data["group"])

        if "file_format" in self.saved_data:
            self.file_format_combo.setCurrentText(
                self.saved_data["file_format"]
            )

        if "colorspace" in self.saved_data:
            self.colorspace_combo.setCurrentText(self.saved_data["colorspace"])

        # Load view-specific data
        if "view_data" in self.saved_data:
            view_data = self.saved_data["view_data"]
            selected_views = self.saved_data.get("selected_views", [])

            for row in range(self.view_table.rowCount()):
                view_name = self.view_table.item(row, 0).text()

                if view_name in view_data:
                    # Set frame range
                    frame_range = view_data[view_name].get("frame_range", "")
                    if frame_range:
                        range_item = self.view_table.item(row, 1)
                        if range_item:
                            range_item.setText(frame_range)

                    # Set priority
                    priority = view_data[view_name].get("priority", 95)
                    priority_widget = self.view_table.cellWidget(row, 2)
                    if priority_widget:
                        priority_widget.setValue(priority)

                # Set checkbox state
                checkbox = self.get_checkbox_from_row(row)
                if checkbox:
                    checkbox.setChecked(view_name in selected_views)

    def get_file_format_options(self):
        """
        Get available file format options from the interior write nodes.
        """
        interior_groups = [
            node
            for node in self.kroger_node.nodes()
            if node.Class() == "Group"
        ]
        if interior_groups:
            interior_writes = [
                node
                for node in interior_groups[0].nodes()
                if node.Class() == "Write"
            ]
            if interior_writes:
                return self.get_combo_box_options(
                    interior_writes[0], "file_type"
                )
        # if no groups yet exist create a sacrifical write node and return the options
        temp_write = nuke.createNode("Write")
        options = self.get_combo_box_options(temp_write, "file_type")
        nuke.delete(temp_write)
        return options
        # return []

    def get_colorspace_options(self):
        """
        Get available colorspace options from the interior write nodes.
        """

        color_paths = []

        interior_groups = [
            node
            for node in self.kroger_node.nodes()
            if node.Class() == "Group"
        ]
        if interior_groups:
            interior_writes = [
                node
                for node in interior_groups[0].nodes()
                if node.Class() == "Write"
            ]
            if interior_writes:
                color_paths = self.get_combo_box_options(
                    interior_writes[0], "out_colorspace"
                )
                # if color_paths:
                #     names = []
                #     for color in color_paths:
                #         names.append(color.split("/")[-1])
                #     return names
                # else:
                #     return []

        if not color_paths:
            temp_write = nuke.createNode("Write")
            color_paths = self.get_combo_box_options(
                temp_write, "out_colorspace"
            )
            nuke.delete(temp_write)

        if color_paths:
            names = []
            for color in color_paths:
                names.append(color.split("/")[-1])
            return names

        return []

    def get_combo_box_options(self, node, knob_name):
        """
        Return all options from a combo box (Enumeration_Knob) on a Nuke node.
        """
        knob = node.knobs().get(knob_name)
        if knob and knob.Class() == "Enumeration_Knob":
            return [knob.enumName(i) for i in range(knob.numValues())]
        return []

    def get_checkbox_from_row(self, row):
        """Get the checkbox widget from a specific row"""
        checkbox_widget = self.view_table.cellWidget(row, 3)
        if checkbox_widget:
            return checkbox_widget.layout().itemAt(0).widget()
        return None

    def get_priority_from_row(self, row):
        """Get the priority spinbox value from a specific row"""
        priority_widget = self.view_table.cellWidget(row, 2)
        if priority_widget:
            return priority_widget.value()
        return 95

    def select_all_views(self):
        """Check all render checkboxes"""
        for row in range(self.view_table.rowCount()):
            checkbox = self.get_checkbox_from_row(row)
            if checkbox:
                checkbox.setChecked(True)

    def select_none_views(self):
        """Uncheck all render checkboxes"""
        for row in range(self.view_table.rowCount()):
            checkbox = self.get_checkbox_from_row(row)
            if checkbox:
                checkbox.setChecked(False)

    def apply_global_range(self):
        """Apply global frame range to all view rows"""
        global_range = self.global_range_edit.text().strip()

        for row in range(self.view_table.rowCount()):
            range_item = self.view_table.item(row, 1)
            if range_item:
                range_item.setText(global_range)

    def apply_global_priority(self):
        """Apply global priority to all view rows"""
        global_priority = self.global_priority_spin.value()

        for row in range(self.view_table.rowCount()):
            priority_widget = self.view_table.cellWidget(row, 2)
            if priority_widget:
                priority_widget.setValue(global_priority)

    def get_selected_data(self):
        """Extract user selections from the dialog"""

        # Get job name from script
        script_name = (
            nuke.root().name().split("/")[-1].replace(".nk", "")
            or "UntitledScript"
        )
        global_range = self.global_range_edit.text().strip()

        # Get global render settings
        global_priority = self.global_priority_spin.value()
        chunk_size = self.chunk_size_spin.value()
        concurrent_tasks = self.concurrent_tasks_spin.value()
        pool = self.pool_edit.text().strip()
        group = self.group_edit.text().strip()
        file_format = self.file_format_combo.currentText()
        colorspace = self.colorspace_combo.currentText()

        selected_views = []
        view_data = {}

        for row in range(self.view_table.rowCount()):
            view_name = self.view_table.item(row, 0).text()
            frame_range = self.view_table.item(row, 1).text().strip()
            priority = self.get_priority_from_row(row)

            # Get checkbox state
            checkbox = self.get_checkbox_from_row(row)
            is_checked = checkbox.isChecked() if checkbox else False

            if is_checked:
                selected_views.append(view_name)

            # Store all view data regardless of checkbox state
            view_data[view_name] = {
                "frame_range": frame_range,
                "priority": priority,
            }

        return {
            "job_name": script_name,
            "global_frame_range": global_range,
            "global_priority": global_priority,
            "chunk_size": chunk_size,
            "concurrent_tasks": concurrent_tasks,
            "pool": pool,
            "group": group,
            "file_format": file_format,
            "colorspace": colorspace,
            "selected_views": selected_views,
            "view_data": view_data,
        }

    def validate_data(self):
        """Validate user input"""

        data = self.get_selected_data()

        # Job name is automatically determined from script, so no need to check

        # Check at least one view selected
        if not data["selected_views"]:
            QtWidgets.QMessageBox.warning(
                self,
                "Validation Error",
                "Please select at least one view to render.",
            )
            return False

        # Validate frame ranges for selected views only
        for view_name in data["selected_views"]:
            view_info = data["view_data"][view_name]
            frame_range = view_info["frame_range"]

            if not frame_range:
                QtWidgets.QMessageBox.warning(
                    self,
                    "Validation Error",
                    f"Please enter a frame range for view '{view_name}'.",
                )
                return False

            # Basic frame range validation
            if "-" not in frame_range and not frame_range.isdigit():
                QtWidgets.QMessageBox.warning(
                    self,
                    "Validation Error",
                    f"Invalid frame range for '{view_name}': {frame_range}\n"
                    "Use format: 1001-1100 or single frame: 1001",
                )
                return False

        return True

    def apply_settings(self, debug=False):
        """Test applying settings to nodes without submitting"""
        if not self.validate_data():
            return

        data = self.get_selected_data()

        if debug:
            if not data.get("selected_views"):
                QtWidgets.QMessageBox.warning(
                    self, "Test Apply", "No views selected!"
                )
                return

            if not self.kroger_node:
                QtWidgets.QMessageBox.warning(
                    self, "Test Apply", "No views write node reference!"
                )
                return

        return apply_settings_to_nodes(data, self.kroger_node, debug)

    # Removed old accept() method - now using specific submit_locally() and submit_to_farm() methods

    def submit_locally(self):
        """Handle submit local button click"""
        if self.validate_data():
            data = self.get_selected_data()
            num_renders = len(data["selected_views"])

            # Show confirmation dialog
            reply = QtWidgets.QMessageBox.question(
                self,
                "Confirm Local Render",
                f"About to render {num_renders} render{'s' if num_renders != 1 else ''} locally, continue?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No,
            )

            if reply == QtWidgets.QMessageBox.Yes:
                self.submission_type = "local"
                self.apply_settings()

                # Close the dialog first so users can see Nuke's progress bars
                self.accept()

                # Run the renders after dialog is closed
                try:
                    submit_renders_local(data, self.kroger_node)
                    print("Local render data:", data)
                except Exception as e:
                    # Show error message after renders fail
                    QtWidgets.QMessageBox.critical(
                        None,  # No parent since dialog is closed
                        "Local Render Error",
                        f"An error occurred during local rendering:\n{str(e)}",
                    )

    def submit_to_farm(self):
        """Handle submit to farm button click"""
        if self.validate_data():
            data = self.get_selected_data()
            num_renders = len(data["selected_views"])

            # Show confirmation dialog
            reply = QtWidgets.QMessageBox.question(
                self,
                "Confirm Farm Submission",
                f"About to submit {num_renders} render{'s' if num_renders != 1 else ''} to farm, continue?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No,
            )

            if reply == QtWidgets.QMessageBox.Yes:
                self.submission_type = "farm"
                self.apply_settings()
                import quick_write

                sub_write_node_generator = quick_write._quick_write_node
                submit_renders(
                    data, self.kroger_node, sub_write_node_generator
                )
                print("Farm submission data:", data)
                self.accept()


class Generate_review_frame_dialog(QtWidgets.QDialog):
    def __init__(self, parent=None, kroger_node=None):
        super().__init__(parent)
        self.kroger_node = kroger_node
        self.setup_ui()
        self.populate_view_table()

    def setup_ui(self):
        self.setWindowTitle("Generate Review Frame")
        self.setMinimumSize(600, 400)
        self.resize(700, 500)

        # Main layout
        layout = QtWidgets.QVBoxLayout(self)

        # Global settings section
        global_group = QtWidgets.QGroupBox("Render Settings")
        global_layout = QtWidgets.QFormLayout(global_group)

        # Single frame number input
        current_frame = int(nuke.frame())
        self.frame_number_spin = QtWidgets.QSpinBox()
        self.frame_number_spin.setRange(1, 999999)
        self.frame_number_spin.setValue(current_frame)
        global_layout.addRow("Frame Number:", self.frame_number_spin)

        # Priority
        self.global_priority_spin = QtWidgets.QSpinBox()
        self.global_priority_spin.setRange(1, 100)
        self.global_priority_spin.setValue(95)
        global_layout.addRow("Priority:", self.global_priority_spin)

        # Pool
        self.pool_edit = QtWidgets.QLineEdit("local")
        global_layout.addRow("Pool:", self.pool_edit)

        # Group
        self.group_edit = QtWidgets.QLineEdit("nuke")
        global_layout.addRow("Group:", self.group_edit)

        # File Format
        self.file_format_combo = QtWidgets.QComboBox()
        self.file_format_combo.addItems(self.get_file_format_options())
        self.file_format_combo.setCurrentText(
            APPROVAL_FRAME_DEFAULT_FILE_FORMAT
        )
        global_layout.addRow("File Format:", self.file_format_combo)

        # Colorspace
        self.colorspace_combo = QtWidgets.QComboBox()
        self.colorspace_combo.addItems(self.get_colorspace_options())
        self.colorspace_combo.setCurrentText(APPROVAL_FRAME_DEFAULT_COLORSPACE)
        global_layout.addRow("Colorspace:", self.colorspace_combo)

        layout.addWidget(global_group)

        # Views table section
        table_group = QtWidgets.QGroupBox("View Selection")
        table_layout = QtWidgets.QVBoxLayout(table_group)

        # Selection buttons
        selection_buttons = QtWidgets.QHBoxLayout()

        self.select_all_btn = QtWidgets.QPushButton("Select All")
        self.select_all_btn.clicked.connect(self.select_all_views)
        selection_buttons.addWidget(self.select_all_btn)

        self.select_none_btn = QtWidgets.QPushButton("Select None")
        self.select_none_btn.clicked.connect(self.select_none_views)
        selection_buttons.addWidget(self.select_none_btn)

        selection_buttons.addStretch()
        table_layout.addLayout(selection_buttons)

        # Create the table
        self.view_table = QtWidgets.QTableWidget()
        self.view_table.setColumnCount(2)
        self.view_table.setHorizontalHeaderLabels(["View Name", "Render"])

        # Table settings
        self.view_table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectRows
        )
        self.view_table.setAlternatingRowColors(True)
        self.view_table.verticalHeader().setVisible(False)

        # Resize columns
        header = self.view_table.horizontalHeader()
        header.setSectionResizeMode(
            0, QtWidgets.QHeaderView.Stretch
        )  # View Name - stretch to fill
        header.setSectionResizeMode(
            1, QtWidgets.QHeaderView.ResizeToContents
        )  # Render checkbox

        table_layout.addWidget(self.view_table)

        layout.addWidget(table_group)

        # Dialog buttons - Render Local, Render on Farm, and Cancel
        button_layout = QtWidgets.QHBoxLayout()

        self.render_local_btn = QtWidgets.QPushButton("Render Local")
        self.render_local_btn.clicked.connect(self.render_locally)
        button_layout.addWidget(self.render_local_btn)

        self.render_farm_btn = QtWidgets.QPushButton("Render on Farm")
        self.render_farm_btn.clicked.connect(self.render_farmly)
        button_layout.addWidget(self.render_farm_btn)

        button_layout.addStretch()

        self.cancel_btn = QtWidgets.QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_btn)

        layout.addLayout(button_layout)

    def populate_view_table(self):
        """Populate the table with current script views"""
        views = nuke.views()
        self.view_table.setRowCount(len(views))

        for row, view_name in enumerate(views):
            # Column 0: View Name (read-only)
            name_item = QtWidgets.QTableWidgetItem(view_name)
            name_item.setFlags(name_item.flags() & ~QtCore.Qt.ItemIsEditable)
            self.view_table.setItem(row, 0, name_item)

            # Column 1: Render checkbox
            checkbox = QtWidgets.QCheckBox()
            # Default "main" views to unchecked, others to checked
            default_checked = view_name.lower() != "main"
            checkbox.setChecked(default_checked)

            # Center the checkbox in the cell
            checkbox_widget = QtWidgets.QWidget()
            checkbox_layout = QtWidgets.QHBoxLayout(checkbox_widget)
            checkbox_layout.addWidget(checkbox)
            checkbox_layout.setAlignment(QtCore.Qt.AlignCenter)
            checkbox_layout.setContentsMargins(0, 0, 0, 0)

            self.view_table.setCellWidget(row, 1, checkbox_widget)

        # Auto-resize rows to content
        self.view_table.resizeRowsToContents()

    def get_file_format_options(self):
        """
        Get available file format options from the interior write nodes.
        """
        if not self.kroger_node:
            return []
        interior_groups = [
            node
            for node in self.kroger_node.nodes()
            if node.Class() == "Group"
        ]
        if interior_groups:
            interior_writes = [
                node
                for node in interior_groups[0].nodes()
                if node.Class() == "Write"
            ]
            if interior_writes:
                return self.get_combo_box_options(
                    interior_writes[0], "file_type"
                )
        # if no groups yet exist create a sacrificial write node and return the options
        temp_write = nuke.createNode("Write")
        options = self.get_combo_box_options(temp_write, "file_type")
        nuke.delete(temp_write)
        return options

    def get_colorspace_options(self):
        """
        Get available colorspace options from the interior write nodes.
        """
        color_paths = []

        if not self.kroger_node:
            return []
        interior_groups = [
            node
            for node in self.kroger_node.nodes()
            if node.Class() == "Group"
        ]
        if interior_groups:
            interior_writes = [
                node
                for node in interior_groups[0].nodes()
                if node.Class() == "Write"
            ]
            if interior_writes:
                color_paths = self.get_combo_box_options(
                    interior_writes[0], "out_colorspace"
                )

        if not color_paths:
            temp_write = nuke.createNode("Write")
            color_paths = self.get_combo_box_options(
                temp_write, "out_colorspace"
            )
            nuke.delete(temp_write)

        if color_paths:
            names = []
            for color in color_paths:
                names.append(color.split("/")[-1])
            return names

        return []

    def get_combo_box_options(self, node, knob_name):
        """
        Return all options from a combo box (Enumeration_Knob) on a Nuke node.
        """
        knob = node.knobs().get(knob_name)
        if knob and knob.Class() == "Enumeration_Knob":
            return [knob.enumName(i) for i in range(knob.numValues())]
        return []

    def get_checkbox_from_row(self, row):
        """Get the checkbox widget from a specific row"""
        checkbox_widget = self.view_table.cellWidget(row, 1)
        if checkbox_widget:
            return checkbox_widget.layout().itemAt(0).widget()
        return None

    def select_all_views(self):
        """Check all render checkboxes"""
        for row in range(self.view_table.rowCount()):
            checkbox = self.get_checkbox_from_row(row)
            if checkbox:
                checkbox.setChecked(True)

    def select_none_views(self):
        """Uncheck all render checkboxes"""
        for row in range(self.view_table.rowCount()):
            checkbox = self.get_checkbox_from_row(row)
            if checkbox:
                checkbox.setChecked(False)

    def get_selected_data(self):
        """Extract user selections from the dialog"""
        # Get job name from script
        script_name = (
            nuke.root().name().split("/")[-1].replace(".nk", "")
            or "UntitledScript"
        )
        frame_number = self.frame_number_spin.value()

        # Get render settings
        priority = self.global_priority_spin.value()
        pool = self.pool_edit.text().strip()
        group = self.group_edit.text().strip()
        file_format = self.file_format_combo.currentText()
        colorspace = self.colorspace_combo.currentText()

        selected_views = []

        for row in range(self.view_table.rowCount()):
            view_name = self.view_table.item(row, 0).text()

            # Get checkbox state
            checkbox = self.get_checkbox_from_row(row)
            is_checked = checkbox.isChecked() if checkbox else False

            if is_checked:
                selected_views.append(view_name)

        return {
            "job_name": script_name,
            "frame_number": frame_number,
            "priority": priority,
            "pool": pool,
            "group": group,
            "file_format": file_format,
            "colorspace": colorspace,
            "selected_views": selected_views,
        }

    def validate_data(self):
        """Validate user input"""
        data = self.get_selected_data()

        # Check at least one view selected
        if not data["selected_views"]:
            QtWidgets.QMessageBox.warning(
                self,
                "Validation Error",
                "Please select at least one view to render.",
            )
            return False

        return True

    def render_locally(self):
        """Handle render local button click"""
        if self.validate_data():
            data = self.get_selected_data()
            num_renders = len(data["selected_views"])

            # Show confirmation dialog
            reply = QtWidgets.QMessageBox.question(
                self,
                "Confirm Local Render",
                f"About to render {num_renders} review frame{'s' if num_renders != 1 else ''} locally at frame {data['frame_number']}, continue?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No,
            )

            if reply == QtWidgets.QMessageBox.Yes:
                render_approval_frames(
                    True,
                    data["selected_views"],
                    data["frame_number"],
                    data["file_format"],
                    data["colorspace"],
                    self.kroger_node,
                )
                print("Local render data:", data)
                self.accept()

    def render_farmly(self):
        """Handle render on farm button click"""
        if self.validate_data():
            data = self.get_selected_data()
            num_renders = len(data["selected_views"])

            # Show confirmation dialog
            reply = QtWidgets.QMessageBox.question(
                self,
                "Confirm Farm Render",
                f"About to submit {num_renders} review frame{'s' if num_renders != 1 else ''} to farm at frame {data['frame_number']}, continue?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No,
            )

            if reply == QtWidgets.QMessageBox.Yes:
                render_approval_frames(
                    False,
                    data["selected_views"],
                    data["frame_number"],
                    data["file_format"],
                    data["colorspace"],
                    self.kroger_node,
                )
                print("Farm render data:", data)
                self.accept()


class Batch_publish_dialog(QtWidgets.QDialog):
    def __init__(self, parent=None, kroger_node=None):
        super().__init__(parent)
        self.kroger_node = kroger_node
        self.setup_ui()
        self.populate_view_table()

    def setup_ui(self):
        self.setWindowTitle("Batch Publish")
        self.setMinimumSize(400, 300)
        self.resize(500, 400)

        layout = QtWidgets.QVBoxLayout(self)

        table_group = QtWidgets.QGroupBox("Select Views to Publish")
        table_layout = QtWidgets.QVBoxLayout(table_group)

        selection_buttons = QtWidgets.QHBoxLayout()

        self.select_all_btn = QtWidgets.QPushButton("Select All")
        self.select_all_btn.clicked.connect(self.select_all_views)
        selection_buttons.addWidget(self.select_all_btn)

        self.select_none_btn = QtWidgets.QPushButton("Select None")
        self.select_none_btn.clicked.connect(self.select_none_views)
        selection_buttons.addWidget(self.select_none_btn)

        selection_buttons.addStretch()
        table_layout.addLayout(selection_buttons)

        self.view_table = QtWidgets.QTableWidget()
        self.view_table.setColumnCount(2)
        self.view_table.setHorizontalHeaderLabels(["View Name", "Publish"])

        self.view_table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectRows
        )
        self.view_table.setAlternatingRowColors(True)
        self.view_table.verticalHeader().setVisible(False)

        header = self.view_table.horizontalHeader()
        header.setSectionResizeMode(
            0, QtWidgets.QHeaderView.Stretch
        )  # View Name
        header.setSectionResizeMode(
            1, QtWidgets.QHeaderView.ResizeToContents
        )  # Publish checkbox

        table_layout.addWidget(self.view_table)
        layout.addWidget(table_group)

        # Settings section
        settings_group = QtWidgets.QGroupBox("Publish Settings")
        settings_layout = QtWidgets.QFormLayout(settings_group)

        # Pool dropdown
        self.pool_combo = QtWidgets.QComboBox()
        self.populate_pool_dropdown()
        settings_layout.addRow("Pool:", self.pool_combo)

        # Group dropdown
        self.group_combo = QtWidgets.QComboBox()
        self.populate_group_dropdown()
        settings_layout.addRow("Group:", self.group_combo)

        layout.addWidget(settings_group)

        warning_label = QtWidgets.QLabel(
            "Warning: Submitting a large number of publishes can take several minutes"
        )
        warning_label.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(warning_label)

        warning_label2 = QtWidgets.QLabel(
            "If you get failures check the external nuke shell window for errors.\nIf you try to publish a version that already exists, it will fail."
        )
        warning_label2.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(warning_label2)

        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )

        publish_button = button_box.button(QtWidgets.QDialogButtonBox.Ok)
        publish_button.setText("Publish")

        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def populate_view_table(self):
        """Populate the table with available views from views write nodes"""
        if not self.kroger_node:
            return

        views = []
        try:
            # Get views from the generated nodes inside the views write group
            with self.kroger_node:
                all_nodes = nuke.allNodes()
                generated_nodes = [
                    node
                    for node in all_nodes
                    if node.Class() != "Input" and node.knob("view_name_knob")
                ]

                for node in generated_nodes:
                    view_name_knob = node.knob("view_name_knob")
                    if view_name_knob:
                        view_name = view_name_knob.getValue()
                        if view_name not in views:
                            views.append(view_name)
        except Exception as e:
            print(f"Error getting views: {e}")
            return

        self.view_table.setRowCount(len(views))

        for row, view_name in enumerate(views):
            # Column 0: View Name (read-only)
            name_item = QtWidgets.QTableWidgetItem(view_name)
            name_item.setFlags(name_item.flags() & ~QtCore.Qt.ItemIsEditable)
            self.view_table.setItem(row, 0, name_item)

            # Column 1: Publish checkbox
            checkbox = QtWidgets.QCheckBox()
            # Default all views to checked
            checkbox.setChecked(True)

            # Center the checkbox in the cell
            checkbox_widget = QtWidgets.QWidget()
            checkbox_layout = QtWidgets.QHBoxLayout(checkbox_widget)
            checkbox_layout.addWidget(checkbox)
            checkbox_layout.setAlignment(QtCore.Qt.AlignCenter)
            checkbox_layout.setContentsMargins(0, 0, 0, 0)

            self.view_table.setCellWidget(row, 1, checkbox_widget)

        self.view_table.resizeRowsToContents()

    def get_checkbox_from_row(self, row):
        """Get the checkbox widget from a specific row"""
        checkbox_widget = self.view_table.cellWidget(row, 1)
        if checkbox_widget:
            return checkbox_widget.layout().itemAt(0).widget()
        return None

    def select_all_views(self):
        """Check all publish checkboxes"""
        for row in range(self.view_table.rowCount()):
            checkbox = self.get_checkbox_from_row(row)
            if checkbox:
                checkbox.setChecked(True)

    def select_none_views(self):
        """Uncheck all publish checkboxes"""
        for row in range(self.view_table.rowCount()):
            checkbox = self.get_checkbox_from_row(row)
            if checkbox:
                checkbox.setChecked(False)

    def get_selected_views(self):
        """Get list of selected view names"""
        selected_views = []
        for row in range(self.view_table.rowCount()):
            view_name = self.view_table.item(row, 0).text()
            checkbox = self.get_checkbox_from_row(row)
            if checkbox and checkbox.isChecked():
                selected_views.append(view_name)
        return selected_views

    def populate_pool_dropdown(self):
        """Populate the pool dropdown with available pools from Deadline"""
        try:
            import hornet_deadline_utils

            pools = hornet_deadline_utils.get_deadline_pools()

            # Clear existing items
            self.pool_combo.clear()

            if pools:
                self.pool_combo.addItems(pools)
                # Set default to first pool or "local" if available
                if "local" in pools:
                    self.pool_combo.setCurrentText("local")
                else:
                    self.pool_combo.setCurrentIndex(0)
            else:
                # Fallback if no pools are available
                self.pool_combo.addItem("local")
                print(
                    "Warning: Could not retrieve pools from Deadline, using 'local' as default"
                )

        except Exception as e:
            print(f"Error populating pool dropdown: {e}")
            # Fallback
            self.pool_combo.clear()
            self.pool_combo.addItem("local")

    def populate_group_dropdown(self):
        """Populate the group dropdown with available groups from Deadline"""
        try:
            import hornet_deadline_utils

            groups = hornet_deadline_utils.get_deadline_groups()

            # Clear existing items
            self.group_combo.clear()

            if groups:
                self.group_combo.addItems(groups)
                # Set default to first group or "nuke" if available
                if "nuke" in groups:
                    self.group_combo.setCurrentText("nuke")
                else:
                    self.group_combo.setCurrentIndex(0)
            else:
                # Fallback if no groups are available
                self.group_combo.addItem("nuke")
                print(
                    "Warning: Could not retrieve groups from Deadline, using 'nuke' as default"
                )

        except Exception as e:
            print(f"Error populating group dropdown: {e}")
            # Fallback
            self.group_combo.clear()
            self.group_combo.addItem("nuke")

    def get_selected_pool(self):
        """Get the selected pool value"""
        return self.pool_combo.currentText()

    def get_selected_group(self):
        """Get the selected group value"""
        return self.group_combo.currentText()

    def validate_data(self):
        """Validate that at least one view is selected"""
        selected_views = self.get_selected_views()
        if not selected_views:
            QtWidgets.QMessageBox.warning(
                self,
                "Validation Error",
                "Please select at least one view to publish.",
            )
            return False
        return True

    def accept(self):
        """Override accept to validate and confirm before closing"""
        if self.validate_data():
            selected_views = self.get_selected_views()
            num_views = len(selected_views)

            # Show confirmation dialog
            reply = QtWidgets.QMessageBox.question(
                self,
                "Confirm Batch Publish",
                f"About to batch publish {num_views} view{'s' if num_views != 1 else ''}, continue?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No,  # Default to No for safety
            )

            if reply == QtWidgets.QMessageBox.Yes:
                super().accept()


def apply_settings_to_nodes(data, views_write_node, debug=False):
    """Apply settings to nodes - standalone function that can be called from anywhere"""
    if not data or not data.get("selected_views"):
        if debug:
            print("No views selected for settings application")
        return False

    selected_views = data["selected_views"]
    view_data = data["view_data"]

    try:
        # Find the generated nodes inside the views write group
        with views_write_node:
            all_nodes = nuke.allNodes()
            generated_nodes = [
                node for node in all_nodes if node.Class() != "Input"
            ]
            print(f"found {len(generated_nodes)} generated nodes")

            # Create a mapping of view names to nodes
            view_to_node = {}
            for node in generated_nodes:
                view_name_knob = node.knob("view_name_knob")
                if view_name_knob:
                    view_name = view_name_knob.getValue()
                    view_to_node[view_name] = node
                    print(f"  mapped view '{view_name}' to node {node.name()}")
                else:
                    print(f"  node {node.name()} has no view_name_knob")

            print(f"final view_to_node mapping: {list(view_to_node.keys())}")

        results = []
        print(f"applying settings to {len(selected_views)} selected views...")

        # Apply settings to each selected view
        for view_name in selected_views:
            if view_name not in view_to_node:
                results.append(f"❌ {view_name}: Node not found")
                continue

            node = view_to_node[view_name]
            node_results = []

            try:
                # Get frame range for this specific view
                frame_range = view_data[view_name]["frame_range"]
                view_priority = view_data[view_name]["priority"]

                # Parse frame range
                if "-" in frame_range:
                    start_frame, end_frame = frame_range.split("-", 1)
                    start_frame = start_frame.strip()
                    end_frame = end_frame.strip()
                else:
                    # Single frame
                    start_frame = end_frame = frame_range.strip()

                # Check if view_name_knob still exists before setting values
                print(
                    f"before setting values: view_name_knob exists = {node.knob('view_name_knob') is not None}"
                )

                # Test applying each setting and report results
                knob_tests = [
                    ("file_type", data["file_format"]),
                    ("first", int(start_frame)),
                    ("last", int(end_frame)),
                    ("deadlinePriority", view_priority),
                    ("concurrentTasks", data["concurrent_tasks"]),
                    ("deadlineChunkSize", data["chunk_size"]),
                    ("deadlinePool", data["pool"]),
                    ("deadlineGroup", data["group"]),
                    ("colorspace", data["colorspace"]),
                ]

                for knob_name, value in knob_tests:
                    if node.knob(knob_name):
                        try:
                            node[knob_name].setValue(value)
                            node_results.append(f"✅ {knob_name}: {value}")
                            print(f"  ✅ {view_name}.{knob_name}: {value}")
                        except Exception as e:
                            node_results.append(
                                f"❌ {knob_name}: Failed to set ({e})"
                            )
                            print(
                                f"  ❌ {view_name}.{knob_name}: Failed to set ({e})"
                            )
                    else:
                        node_results.append(f"⚠️ {knob_name}: Knob not found")
                        print(f"  ⚠️ {view_name}.{knob_name}: Knob not found")

                # Check if view_name_knob still exists after setting values
                knob_exists_after = node.knob("view_name_knob") is not None
                print(
                    f"after setting values: view_name_knob exists = {knob_exists_after}"
                )

                # Re-add the view_name_knob if it was removed
                if not knob_exists_after:
                    print(f"re-adding view_name_knob for {view_name}")
                    view_name_knob = nuke.String_Knob(
                        "view_name_knob", "Render View"
                    )
                    view_name_knob.setValue(view_name)
                    node.addKnob(view_name_knob)
                    print(
                        f"re-added view_name_knob: {node.knob('view_name_knob') is not None}"
                    )

                results.append(f"📁 {view_name}:")
                results.extend([f"   {result}" for result in node_results])

            except Exception as e:
                results.append(f"❌ {view_name}: Error - {str(e)}")

        # Print results to console
        if debug and results:
            print("\n=== Apply Settings Results ===")
            for result_line in results:
                print(result_line)
            print("=== Settings Application Complete ===\n")

        return True

    except Exception as e:
        if debug:
            print(f"Error applying settings: {str(e)}")
        return False


def load_saved_data_from_node(kroger_node=None):
    """Load saved data from the node's hidden knob"""
    try:
        viewsWrite = kroger_node if kroger_node else nuke.thisNode()
        if viewsWrite.knob("dialog_data"):
            data_str = viewsWrite["dialog_data"].getValue()
            if data_str:
                # Safely evaluate the string as a Python dictionary
                return ast.literal_eval(data_str)
    except (ValueError, SyntaxError) as e:
        print(f"Error loading saved data: {e}")
    return {}


def save_data_to_node(data, kroger_node=None):
    """Save data to the node's hidden knob"""
    try:
        viewsWrite = kroger_node if kroger_node else nuke.thisNode()
        if viewsWrite.knob("dialog_data"):
            viewsWrite["dialog_data"].setValue(str(data))
    except Exception as e:
        print(f"Error saving data: {e}")


def submit_button_callback():
    """Callback function for the button"""

    import quick_write

    sub_write_node_generator = quick_write._quick_write_node

    views_write_node = nuke.thisNode()

    update_views_list(views_write_node)

    saved_data = load_saved_data_from_node(views_write_node)

    dialog = Render_submission_dialog(
        saved_data=saved_data, kroger_node=views_write_node
    )

    if dialog.exec_() == QtWidgets.QDialog.Accepted:
        data = dialog.get_selected_data()
        print("Dialog accepted with data:")
        print(data)

        save_data_to_node(data, views_write_node)

        # Check submission type to avoid double submission
        if dialog.submission_type == "local":
            print("Local submission already handled by dialog")
        elif dialog.submission_type == "farm":
            print("Farm submission already handled by dialog")
        else:
            # Fallback for any other case (shouldn't happen)
            print("No submission type set, this shouldn't happen")

    else:
        print("Dialog cancelled")

        data = dialog.get_selected_data()
        save_data_to_node(data, views_write_node)


def batch_publish_button_callback():
    """Callback function for the batch publish button"""

    kroger_node = nuke.thisNode()
    # print(kroger_node.name())

    dialog = Batch_publish_dialog(kroger_node=kroger_node)

    if dialog.exec_() == QtWidgets.QDialog.Accepted:
        selected_views = dialog.get_selected_views()
        selected_pool = dialog.get_selected_pool()
        selected_group = dialog.get_selected_group()

        print(f"Batch publishing selected views: {selected_views}")
        print(f"Using pool: {selected_pool}, group: {selected_group}")
        print(kroger_node.name())

        batch_publish(
            selected_views=selected_views,
            kroger_node=kroger_node,
            pool=selected_pool,
            group=selected_group,
        )
    else:
        print("Batch publish dialog cancelled")


def generate_review_frame_button_callback():
    """Callback function for the generate review frame button"""

    kroger_node = nuke.thisNode()

    dialog = Generate_review_frame_dialog(kroger_node=kroger_node)

    if dialog.exec_() == QtWidgets.QDialog.Accepted:
        data = dialog.get_selected_data()
        print("Generate review frame dialog accepted with data:")
        print(data)
        # TODO: Implement actual review frame generation logic here
    else:
        print("Generate review frame dialog cancelled")


def views_write_to_reads_button_callback():
    """Callback function for the views write to reads button"""
    node = nuke.thisNode()
    with nuke.thisNode().parent():
        views_write_to_reads(node)


def views_write_read_from_publish_button_callback():
    """Callback function for the views write read from publish button"""
    views_write_read_from_publish(nuke.thisNode())


def pregenerate_writes_button_callback():
    """Callback function for the pregenerate writes button"""
    views_write_node = nuke.thisNode()

    # Get current views from the script
    current_views = nuke.views()
    update_views_list(views_write_node)

    if not current_views:
        nuke.message("No views found in the current script.")
        return

    # Get aspect from the views write node
    aspect = views_write_node["aspect"].getValue()

    # Import the sub_write_node_generator
    import quick_write

    sub_write_node_generator = quick_write._quick_write_node

    try:
        # Call the sync function to pregenerate writes for all current views
        _sync_sub_writes_with_views_list(
            current_views, aspect, sub_write_node_generator, views_write_node
        )

        # Show success message
        nuke.tprint(
            f"Successfully pregenerated write nodes for {len(current_views)} view(s):\n"
            + "\n".join(f"- {view}" for view in current_views)
        )

    except Exception as e:
        nuke.message(f"Error pregenerating writes: {str(e)}")
        print(f"Error in pregenerate_writes_button_callback: {str(e)}")


def test_generate_review_frame_dialog():
    """Test function to launch the Generate Review Frame dialog"""
    dialog = Generate_review_frame_dialog()
    dialog.exec_()


def submit_renders(data, viewsWrite, sub_write_node_generator):
    print("submitting renders with data:")
    """Submit the selected renders to the farm"""
    import os
    from datetime import datetime

    try:
        import hornet_deadline_utils
    except ImportError:
        nuke.message("Error: hornet_deadline_utils module not found!")
        return

    print(f"submitting renders with data: {data}")

    print("viewsWrite node found:", viewsWrite.name())
    selected_views = data["selected_views"]

    if not selected_views:
        nuke.message("No views selected for rendering!")
        return

    succeeded = 0
    failed = 0
    script = os.path.basename(nuke.toNode("root").name()).split(".")[0]
    now = datetime.now().strftime("%H-%M-%S")
    batch_name = f"{script}_{now}"

    aspect = viewsWrite["aspect"].getValue()

    with viewsWrite:
        _sync_sub_writes_with_views_list(
            selected_views, aspect, sub_write_node_generator, viewsWrite
        )
        sub_writes = _get_subwrite_nodes(viewsWrite)
        print(f"sub writes: {sub_writes}")

        view_to_node = {}
        for node in sub_writes:
            print(node.name())
            view_name_knob = node.knob("view_name_knob")
            if view_name_knob:
                view_name = view_name_knob.getValue()
                view_to_node[view_name] = node
            else:
                print(f"node {node.name()} has no view_name_knob")

        print("View to node mapping:")
        print(view_to_node)

    # Apply settings before submitting
    print("Applying settings before submission...")
    apply_settings_to_nodes(data, viewsWrite, debug=False)

    # Now submit the renders
    for view_name in selected_views:
        if view_name not in view_to_node:
            print(
                f"Warning: No node found for view '{view_name}', skipping..."
            )
            failed += 1
            continue

        node = view_to_node[view_name]
        print(node.fullName())
        try:
            with node.begin():
                hornet_deadline_utils.deadlineNetworkSubmit(
                    # deadlineNetworkSubmit(
                    batch=batch_name,
                    silent=True,
                    node=node,
                )

            succeeded += 1
            print(f"Successfully submitted: {view_name}")

        except Exception as e:
            print(f"Failed to submit view '{view_name}': {str(e)}")
            failed += 1

    # Show results
    if succeeded > 0:
        if failed > 0:
            nuke.message(
                f"Submitted {succeeded} render{'s' if succeeded != 1 else ''} to farm.\n{failed} submission{'s' if failed != 1 else ''} failed."
            )
        else:
            nuke.message(
                f"Successfully submitted {succeeded} render{'s' if succeeded != 1 else ''} to farm!"
            )
    else:
        nuke.message("No renders were submitted successfully.")

    print(f"Submission complete: {succeeded} succeeded, {failed} failed")


def submit_renders_local(data, viewsWrite, sub_write_node_generator=None):
    """Submit the selected renders locally"""
    print("submitting renders locally with data:")

    if sub_write_node_generator is None:
        import quick_write

        sub_write_node_generator = quick_write._quick_write_node

    print(f"submitting renders locally with data: {data}")

    print("viewsWrite node found:", viewsWrite.name())
    selected_views = data["selected_views"]

    if not selected_views:
        nuke.message("No views selected for rendering!")
        return

    succeeded = 0
    failed = 0
    aspect = viewsWrite["aspect"].getValue()

    with viewsWrite:
        _sync_sub_writes_with_views_list(
            selected_views, aspect, sub_write_node_generator, viewsWrite
        )
        sub_writes = _get_subwrite_nodes(viewsWrite)
        print(f"sub writes: {sub_writes}")

        view_to_node = {}
        for node in sub_writes:
            print(node.name())
            view_name_knob = node.knob("view_name_knob")
            if view_name_knob:
                view_name = view_name_knob.getValue()
                view_to_node[view_name] = node
            else:
                print(f"node {node.name()} has no view_name_knob")

        print("View to node mapping:")
        print(view_to_node)

    # Apply settings before rendering
    print("Applying settings before local rendering...")
    apply_settings_to_nodes(data, viewsWrite, debug=False)

    # Now render locally
    total_views = len(selected_views)
    for i, view_name in enumerate(selected_views, 1):
        if view_name not in view_to_node:
            print(
                f"Warning: No node found for view '{view_name}', skipping..."
            )
            failed += 1
            continue

        node = view_to_node[view_name]
        print(f"Rendering {i}/{total_views}: {node.fullName()}")

        try:
            # Get frame range for this view
            view_data = data["view_data"][view_name]
            frame_range = view_data["frame_range"]

            # Parse frame range
            if "-" in frame_range:
                start_frame, end_frame = frame_range.split("-", 1)
                start_frame = int(start_frame.strip())
                end_frame = int(end_frame.strip())
            else:
                # Single frame
                start_frame = end_frame = int(frame_range.strip())

            print(
                f"  Rendering frames {start_frame}-{end_frame} for view '{view_name}'..."
            )

            # Execute the render locally
            nuke.execute(node, start_frame, end_frame)
            succeeded += 1
            print(f"  ✓ Successfully rendered locally: {view_name}")

        except Exception as e:
            print(f"  ✗ Failed to render view '{view_name}' locally: {str(e)}")
            failed += 1

    # Show results
    if succeeded > 0:
        if failed > 0:
            nuke.message(
                f"Rendered {succeeded} render{'s' if succeeded != 1 else ''} locally.\n{failed} render{'s' if failed != 1 else ''} failed."
            )
        else:
            nuke.message(
                f"Successfully rendered {succeeded} render{'s' if succeeded != 1 else ''} locally!"
            )
    else:
        nuke.message("No renders were completed successfully.")

    print(f"Local rendering complete: {succeeded} succeeded, {failed} failed")


def update_views_list(viewsWrite=None):
    """Update the views list in the views write node"""
    if viewsWrite is None:
        viewsWrite = nuke.thisNode()

    if viewsWrite.Class() != "Group":
        return

    viewsWrite["views_list"].setValue("\n".join(nuke.views()))


def _delete_all_sub_nodes(viewsWrite=None):
    """Delete all sub nodes in the views write node"""

    if viewsWrite is None:
        viewsWrite = nuke.thisNode()

    if viewsWrite.Class() != "Group":
        return

    with viewsWrite:
        all_nodes = nuke.allNodes()
        generated_nodes = [
            node for node in all_nodes if node.Class() != "Input"
        ]
        print(f"deleting {len(generated_nodes)} existing nodes...")

        for generated_node in generated_nodes:
            nuke.delete(generated_node)


def _sync_sub_writes_with_views_list(
    views, aspect, sub_write_node_generator, viewsWrite=None
):
    """Sync the sub writes with the views"""

    print(f"syncing sub writes with views list: {views} with aspect: {aspect}")

    if viewsWrite is None:
        viewsWrite = nuke.thisNode()

    if viewsWrite.Class() != "Group":
        raise ValueError(
            f"viewsWrite is not a Group node: {viewsWrite.Class()}"
        )

    print(f"viewsWrite: {viewsWrite.name()}")

    existing_subwrite_node_views = _get_subwrite_node_views(viewsWrite)
    print(f"existing subwrite node views: {existing_subwrite_node_views}")
    # print(f"existing subwrite node views: {existing_subwrite_node_views}")

    for subwrite_view in existing_subwrite_node_views:
        if subwrite_view not in views:
            for wn in _get_subwrite_by_view(subwrite_view, viewsWrite):
                _delete_subwrite_node(wn, viewsWrite)

    for view in views:
        if view not in _get_subwrite_node_views(viewsWrite):
            _add_subwrite_node(
                view, sub_write_node_generator, aspect, viewsWrite
            )


def _get_subwrite_by_view(view_name, viewsWrite=None):
    """Get a subwrite node by view name"""
    if viewsWrite is None:
        viewsWrite = nuke.thisNode()

    if viewsWrite.Class() != "Group":
        raise ValueError(
            f"viewsWrite is not a Group node: {viewsWrite.Class()}"
        )

    found = []
    for wn in _get_subwrite_nodes(viewsWrite):
        if wn.knob("view_name_knob").getValue() == view_name:
            found.append(wn)

    return found


def _get_subwrite_node_views(viewsWrite=None):
    """Get a list of the exisiting subwrite nodes as a list of view names"""
    if viewsWrite is None:
        viewsWrite = nuke.thisNode()

    if viewsWrite.Class() != "Group":
        raise ValueError(
            f"viewsWrite is not a Group node: {viewsWrite.Class()}"
        )

    return [
        node.knob("view_name_knob").getValue()
        for node in _get_subwrite_nodes(viewsWrite)
    ] or []


def _get_subwrite_nodes(viewsWrite=None):
    """Get a list of all the subwrite nodes"""
    if viewsWrite is None:
        viewsWrite = nuke.thisNode()

    if viewsWrite.Class() != "Group":
        raise ValueError(
            f"viewsWrite is not a Group node: {viewsWrite.Class()}"
        )

    print(f"getting all subwrite nodes for viewsWrite: {viewsWrite.name()}")

    with viewsWrite:
        return [
            node
            for node in nuke.allNodes()
            if node.Class() != "Input" and "view_name_knob" in node.knobs()
        ]


def _add_subwrite_node(
    view_name, sub_write_node_generator, aspect=None, viewsWrite=None
):
    """Add a subwrite node to the views write node"""
    if viewsWrite is None:
        viewsWrite = nuke.thisNode()

    if viewsWrite.Class() != "Group":
        raise ValueError(
            f"viewsWrite is not a Group node: {viewsWrite.Class()}"
        )

    if view_name not in nuke.views():
        raise ValueError(f"view_name is not in the views: {view_name}")

    if aspect:
        variant_name = f"{view_name}_{aspect}"
    else:
        variant_name = view_name

    with viewsWrite:
        input = nuke.allNodes("Input")[0]

        # oneview = nuke.createNode("OneView", inpanel=False)
        # oneview["view"].setValue(view_name)
        # oneview.setInput(0, input)
        # oneview.hideControlPanel()

        new_node = sub_write_node_generator(variant_name, inpanel=False)
        new_node.setInput(0, input)

        if new_node.knob("views"):
            new_node["views"].setValue(view_name)
            print(f"  set quick_write views to '{view_name}'")

        new_node.hideControlPanel()

        view_name_knob = nuke.String_Knob("view_name_knob", "Render View")
        view_name_knob.setValue(view_name)
        new_node.addKnob(view_name_knob)

        if new_node["views"].setValue(view_name):
            return new_node

        else:
            raise ValueError(
                f"Failed to set views for new_node: {new_node.name()}"
            )


def _delete_subwrite_node(subwrite_node, viewsWrite=None):
    """Delete a subwrite node from the views write node"""
    if viewsWrite is None:
        viewsWrite = nuke.thisNode()

    if viewsWrite.Class() != "Group":
        raise ValueError(
            f"viewsWrite is not a Group node: {viewsWrite.Class()}"
        )

    if isinstance(subwrite_node, str):
        raise ValueError(
            f"subwrite_node is not a string for some reason: {subwrite_node}"
        )

    with viewsWrite:
        oneview = subwrite_node.input(0)
        print(oneview)
        if oneview.Class() == "OneView":
            nuke.delete(oneview)

        nuke.delete(subwrite_node)


# def _get_input_node(viewsWrite=None):
#     if viewsWrite is None:
#         viewsWrite = nuke.thisNode()

#     if viewsWrite.Class() != "Group":
#         raise ValueError(f"viewsWrite is not a Group node: {viewsWrite.Class()}")

#     with viewsWrite:
#         return nuke.allNodes("Input")[0]

# def create_write_nodes_for_views(viewsWrite, sub_write_node_generator):
#     """Create generated nodes for all current views"""
#     with viewsWrite:
#         input_nodes = [
#             node for node in nuke.allNodes() if node.Class() == "Input"
#         ]
#         if not input_nodes:
#             print("Warning: No Input node found")
#             return

#         source_node = input_nodes[0]
#         init_position = [
#             int(source_node["xpos"].getValue()),
#             int(source_node["ypos"].getValue()),
#         ]
#         xOffset = 100
#         yOffset = 100
#         init_position[1] += yOffset

#         aspect_value = ""
#         if viewsWrite.knob("aspect"):
#             aspect_value = viewsWrite["aspect"].getValue().strip()

#             if aspect_value and not aspect_value.isspace():
#                 aspect_value = aspect_value.replace(" ", "_")
#             else:
#                 aspect_value = ""

#         for view in nuke.views():
#             if aspect_value:
#                 variant_name = f"{view}_{aspect_value}"
#                 print(
#                     f"creating node for view '{view}' with aspect '{aspect_value}' -> variant: '{variant_name}'"
#                 )
#             else:
#                 variant_name = view
#                 print(f"creating node for view '{view}' (no aspect)")

#             oneview = nuke.createNode("OneView", inpanel=False)
#             oneview["view"].setValue(view)
#             oneview.setInput(0, source_node)
#             oneview.setXYpos(init_position[0], init_position[1])
#             oneview.hideControlPanel()

#             init_position[1] += yOffset

#             generated_node = sub_write_node_generator(
#                 variant_name, inpanel=False
#             )

#             if generated_node.knob("views"):
#                 generated_node["views"].setValue(view)
#                 print(f"  set quick_write views to '{view}'")

#             view_name_knob = nuke.String_Knob("view_name_knob", "Render View")
#             view_name_knob.setValue(view)
#             generated_node.addKnob(view_name_knob)
#             generated_node.setInput(
#                 0, oneview
#             )  # Connect to OneView instead of source_node
#             generated_node.setXYpos(init_position[0], init_position[1])
#             init_position[1] += yOffset  # Move position for next pair of nodes
#             generated_node.hideControlPanel()


def views_write_node(sub_write_node_generator=None):
    """Create a views write node"""

    # if sub_write_node_generator is None:
    #     import quick_write

    #     sub_write_node_generator = quick_write._quick_write_node

    viewsWrite = nuke.createNode("Group")
    viewsWrite["tile_color"].setValue(16728063)

    # Use Nuke's built-in unique naming - this automatically appends numbers if name exists
    viewsWrite.setName("views_write")

    # Add aspect ratio text input at the top
    aspect_knob = nuke.String_Knob("aspect", "Aspect")
    aspect_knob.setValue(DEFAULT_ASPECT)  # Default aspect ratio
    viewsWrite.addKnob(aspect_knob)

    divider1 = nuke.Text_Knob("divider1", "")
    viewsWrite.addKnob(divider1)

    regenerate_knob = nuke.PyScript_Knob("refresh_list", "Refresh Views")
    regenerate_knob.setValue("views_write.update_views_list()")
    viewsWrite.addKnob(regenerate_knob)

    pregenerate_knob = nuke.PyScript_Knob(
        "pregenerate_writes", "Pregenerate Writes"
    )
    pregenerate_knob.setValue(
        "views_write.pregenerate_writes_button_callback()"
    )
    viewsWrite.addKnob(pregenerate_knob)

    views_list_knob = nuke.Multiline_Eval_String_Knob("views_list", "Views")
    views_list_knob.setFlag(nuke.READ_ONLY)
    viewsWrite.addKnob(views_list_knob)

    divider_submit = nuke.Text_Knob("divider_submit", "")
    viewsWrite.addKnob(divider_submit)

    button_knob = nuke.PyScript_Knob("render_dialog_button", "Submit")
    button_knob.setValue("views_write.submit_button_callback()")
    viewsWrite.addKnob(button_knob)

    batch_publish_knob = nuke.PyScript_Knob(
        "batch_publish_button", "Batch Publish"
    )
    batch_publish_knob.setValue("views_write.batch_publish_button_callback()")
    viewsWrite.addKnob(batch_publish_knob)

    generate_review_frame_knob = nuke.PyScript_Knob(
        "generate_review_frame_button", "Generate Review Frame"
    )
    generate_review_frame_knob.setValue(
        "views_write.generate_review_frame_button_callback()"
    )
    viewsWrite.addKnob(generate_review_frame_knob)

    divider2 = nuke.Text_Knob("divider2", "")
    viewsWrite.addKnob(divider2)

    # Add buttons for views_write_to_reads and views_write_read_from_publish
    views_write_to_reads_knob = nuke.PyScript_Knob(
        "views_write_to_reads_button", "Render to Read"
    )
    views_write_to_reads_knob.setValue(
        "views_write.views_write_to_reads_button_callback()"
    )
    viewsWrite.addKnob(views_write_to_reads_knob)

    views_write_read_from_publish_knob = nuke.PyScript_Knob(
        "views_write_read_from_publish_button", "Publish to Read"
    )
    views_write_read_from_publish_knob.setValue(
        "views_write.views_write_read_from_publish_button_callback()"
    )
    viewsWrite.addKnob(views_write_read_from_publish_knob)

    divider3 = nuke.Text_Knob("divider3", "")
    viewsWrite.addKnob(divider3)

    data_knob = nuke.String_Knob("dialog_data", "Dialog Data")
    data_knob.setVisible(False)
    viewsWrite.addKnob(data_knob)

    with viewsWrite:
        source_node = nuke.createNode("Input", inpanel=False)

    # Create write nodes for current views
    # create_write_nodes_for_views(viewsWrite, sub_write_node_generator)

    # Auto-populate the write nodes list on creation
    update_views_list(viewsWrite)

    viewsWrite.showControlPanel()

    return viewsWrite


"""
        Args:
        write_nodes (list): List of nuke write group nodes to publish
        delay (float): Seconds to wait between publishes
        review (bool): Whether to generate review media at all
        review_farm (bool): Whether to generate review media on farm (True) or locally (False)
        integrate_farm (bool): Whether to use farm integration ("frames_farm") or local ("frames")
        burnin (bool): Whether to include burnins in review media
        silent (bool): Suppress individual success/failure popup messages (batch summary still shown)
"""


def batch_publish(
    selected_views=None,
    review=True,
    review_farm=True,
    integrate_farm=True,
    burnin=True,
    silent=True,
    kroger_node=None,
    pool="local",
    group="nuke",
):
    try:
        import hornet_publish_utils  # noqa: F401

        print("[OK] Successfully imported hornet_publish_utils")
    except ImportError as e:
        error_msg = f"[ERROR] Error importing hornet_publish_utils: {e}"
        print(error_msg)
        nuke.message(error_msg)
        return

    try:
        # Get the current node
        viewsWrite = kroger_node if kroger_node else nuke.thisNode()
        print(f"Using kroger node: {viewsWrite.name()}")

        publis_nodes = []
        print(f"Looking for publish nodes in {viewsWrite.name()}...")
        print(f"Selected views filter: {selected_views}")

        with viewsWrite.begin():
            nodes = nuke.allNodes("Group")
            print(f"Found {len(nodes)} Group nodes inside views write")

            for node in nodes:
                node_name = node.name()
                print(f"  Checking node: {node_name}")

                # Check for required knobs
                has_publish_instance = (
                    "publish_instance" in node.knobs().keys()
                )
                has_quick_publish = "quick_publish" in node.knobs().keys()
                print(
                    f"    publish_instance: {has_publish_instance}, quick_publish: {has_quick_publish}"
                )

                if not (has_publish_instance and has_quick_publish):
                    print(
                        f"    [SKIP] Skipping {node_name} - missing required knobs"
                    )
                    continue

                # Check view filter
                if selected_views:
                    view_name_knob = node.knob("view_name_knob")
                    if view_name_knob:
                        view_name = view_name_knob.getValue()
                        print(f"    Node view: '{view_name}'")
                        if view_name not in selected_views:
                            print(
                                f"    [SKIP] Skipping {node_name} - view '{view_name}' not in selected views"
                            )
                            continue
                    else:
                        print(
                            f"    [WARN] Node {node_name} has no view_name_knob"
                        )

                publis_nodes.append(node)
                print(f"    [OK] Added {node_name} to publish list")

        print(f"Final publish list: {len(publis_nodes)} nodes")
        for i, node in enumerate(publis_nodes):
            view_knob = node.knob("view_name_knob")
            view_name = view_knob.getValue() if view_knob else "unknown"
            print(f"  {i + 1}. {node.name()} (view: {view_name})")

        if not publis_nodes:
            error_msg = "[ERROR] No publish nodes found matching criteria"
            print(error_msg)
            nuke.message(error_msg)
            return

        print("Starting batch publish with hornet_publish_utils...")
        print(
            f"   Parameters: review={review}, review_farm={review_farm}, integrate_farm={integrate_farm}"
        )
        print(f"   Parameters: burnin={burnin}, silent={silent}")
        print(f"   Parameters: pool={pool}, group={group}")

        # Apply pool and group settings to all publish nodes before publishing
        for node in publis_nodes:
            try:
                # Apply pool setting if the node has a deadlinePool knob
                pool_knob = node.knob("deadlinePool")
                if pool_knob:
                    pool_knob.setValue(pool)
                    print(f"  Set pool to '{pool}' for node: {node.name()}")

                # Apply group setting if the node has a deadlineGroup knob
                group_knob = node.knob("deadlineGroup")
                if group_knob:
                    group_knob.setValue(group)
                    print(f"  Set group to '{group}' for node: {node.name()}")

            except Exception as e:
                print(
                    f"  Warning: Could not set pool/group for node {node.name()}: {e}"
                )

        hornet_publish_utils.batch_publish_write_nodes(
            publis_nodes,
            delay_between_publishes=0.25,
            review=review,
            review_farm=review_farm,
            integrate_farm=integrate_farm,
            burnin=burnin,
            silent=silent,
            pool=pool,
            group=group,
        )

        print("[OK] Batch publish completed successfully")

    except Exception as e:
        error_msg = f"[ERROR] Batch publish error: {str(e)}"
        print(error_msg)
        print(f"   Error type: {type(e).__name__}")
        import traceback

        print("   Full traceback:")
        traceback.print_exc()
        nuke.message(error_msg)
        return


def render_approval_frames(
    local, views, frame, format, colorspace, viewsWrite=None
):
    """Render approval frames for specified views and frame number.
    
    This function generates and executes write nodes to render "approval frames"
    which are a single specified frame for every "view".

    They are rquired to render directly to the publish directory bypassing pyblish pipeline,
    therefore vanilla write nodes are used and the publish directory is resolved from the
    Ayon API.

    
    Args:
        local (bool): If True, renders locally using nuke.execute(). 
                     If False, submits to farm using Deadline.
        views (list): List of view names to render (e.g., ['left', 'right']).
        frame (int): Frame number to render.
        format (str): Output file format (e.g., 'png', 'dpx', 'exr').
        colorspace (str): Colorspace to use for rendering (e.g., 'Output - sRGB').
        viewsWrite (nuke.Group, optional): The views write node group to use.
                                          If None, uses the current node.
    
    Raises:
        ValueError: If viewsWrite is not a Group node.
    
    Note:
        - Creates temporary write nodes for each view
        - Renders to approval_frames directory in publish root
        - File naming pattern: {view}_v{version}.{frame}.{format}
        - Automatically cleans up temporary write nodes after rendering
        - Uses batch name: {script_name}__approval_frames for farm submissions
    """
    if viewsWrite is None:
        views_write = nuke.thisNode()
    writes = generate_review_frame_write_nodes(
        views, frame, format, colorspace, viewsWrite
    )

    deadline_batch = (
        f"{pathlib.Path(nuke.root().name()).stem}__approval_frames"
    )

    for write in writes:
        if local:
            nuke.execute(write, frame, frame)
        else:
            hornet_deadline_utils.vanilla_submit(
                write, f"{frame}", batch=deadline_batch, silent=True
            )

    for write in writes:
        nuke.delete(write)


def generate_review_frame_write_nodes(
    views, frame, format, colorspace, viewsWrite=None
):
    if viewsWrite is None:
        views_write = nuke.thisNode()

    if viewsWrite.Class() != "Group":
        raise ValueError(
            f"viewsWrite is not a Group node: {viewsWrite.Class()}"
        )

    # input_node = _get_input_node(viewsWrite)
    publish_root = get_publish_root()

    with viewsWrite:
        input_node = nuke.allNodes("Input")[0]

        writes = []
        for view in views:
            writes.append(
                generate_review_frame_write_node(
                    view, frame, format, colorspace, publish_root
                )
            )

        for write in writes:
            print(
                f"Setting input node for {write.name()} to {input_node.name()}"
            )
            write.setInput(0, input_node)

    return writes


def generate_review_frame_write_node(view, frame, format, colorspace, root):
    version = get_version_from_path(nuke.root().name())
    file_path = (
        pathlib.Path(root)
        / "approval_frames"
        / f"v{version}"
        / f"{view}_v{version}.{frame}.{format}"
    )

    # write = nuke.createNode("Write")
    write = nuke.nodes.Write()
    write["file"].setValue(file_path.as_posix())
    write["file_type"].setValue(format)
    write["colorspace"].setValue(colorspace)
    write["views"].setValue(view)
    write["first"].setValue(frame)
    write["last"].setValue(frame)
    write["create_directories"].setValue(True)
    return write


def get_publish_root():
    """Get the base publish root directory"""

    # Get current context
    host = registered_host()
    context = host.get_current_context()

    # Initialize anatomy
    anatomy = Anatomy()

    # Get available root keys
    available_roots = anatomy.root_names()

    # Try to get publish root, fallback to work root
    if available_roots and "publish" in available_roots:
        publish_root = anatomy.roots["publish"].value.rstrip("/")
    else:
        # Use work root as base and build publish path
        work_root = anatomy.roots["work"].value.rstrip("/")
        project_name = context["project_name"]
        folder_path = context["folder_path"]
        publish_root = f"{work_root}/{project_name}/{folder_path}/publish"

    if pathlib.Path(publish_root).exists():
        return publish_root
    else:
        raise ValueError(
            f"Failed to resolve valid publish root: {publish_root}"
        )


import read_node_utils


def views_write_to_reads(node):
    context = nuke.thisNode()
    xypos = [node.xpos(), node.ypos() + 100]

    with node:
        nodes = [n for n in nuke.allNodes() if "readfrom" in n.knobs().keys()]
        if len(nodes) == 0:
            print(
                "no internal write nodes have been created yet. have you rendered?"
            )
            return
        for write_group in nodes:
            read_node_utils.write_to_read(
                write_group, context=context, xypos=xypos
            )
            xypos[0] += 100


def views_write_read_from_publish(node):
    xypos = [node.xpos(), node.ypos() + 100]
    with node:
        nodes = [n for n in nuke.allNodes() if "readfrom" in n.knobs().keys()]
        if len(nodes) == 0:
            print(
                "no internal write nodes have been created yet. have you rendered?"
            )
            return
        for write_group in nodes:
            read_node_utils.read_from_publish(write_group, xypos=xypos)
            xypos[0] += 100


def sanitize_aspect():
    node = nuke.thisNode()
    knob = nuke.thisKnob()
    try:
        if knob.name() == "aspect":
            aspect = knob.value()
            knob.setValue(aspect.replace(" ", "_").replace(":", "x"))
        else:
            return None
    except Exception as e:
        print(f"Error sanitizing aspect: {e}")
        return None
    