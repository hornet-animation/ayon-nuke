from typing import Literal
from ayon_server.settings import BaseSettingsModel, SettingsField
from ayon_server.settings.enum import addon_all_app_host_names_enum
from ayon_server.types import ColorRGBA_float


def codec_enum():
    return [
        {"value": "prores_ks", "label": "Apple ProRes (prores_ks)"},
        {"value": "libx264", "label": "h.264 (libx264)"},
        {"value": "libx265", "label": "h.265 / HEVC (libx265)"},
        {"value": "mjpeg", "label": "Motion JPEG"},
        {"value": "dnxhd", "label": "Avid DNxHD/DNxHR"},
        {"value": "libsvtav1", "label": "AV1 (libsvtav1)"},
    ]


def pix_fmt_enum():
    return [
        {"value": "yuv420p", "label": "4:2:0 8-bit"},
        {"value": "yuv420p10le", "label": "4:2:0 10-bit"},
        {"value": "yuv422p", "label": "4:2:2 8-bit"},
        {"value": "yuv422p10le", "label": "4:2:2 10-bit"},
        {"value": "yuv444p", "label": "4:4:4 8-bit"},
        {"value": "yuv444p10le", "label": "4:4:4 10-bit"},
        {"value": "yuva444p10le", "label": "4:4:4 10-bit + alpha"},
        {"value": "rgb24", "label": "RGB 8-bit"},
        {"value": "rgb48le", "label": "RGB 16-bit"},
    ]


def profile_enum():
    return [
        {"value": "0", "label": "ProRes Proxy"},
        {"value": "1", "label": "ProRes LT"},
        {"value": "2", "label": "ProRes 422"},
        {"value": "3", "label": "ProRes 422 HQ"},
        {"value": "4", "label": "ProRes 4444"},
        {"value": "5", "label": "ProRes 4444 XQ"},
        {"value": "baseline", "label": "h264 Baseline"},
        {"value": "main", "label": "h264 Main"},
        {"value": "high", "label": "h264 High"},
        {"value": "high10", "label": "h264 High 10"},
        {"value": "high422", "label": "h264 High 4:2:2"},
        {"value": "high444", "label": "h264 High 4:4:4"},
    ]


def text_element_name_enum():
    return [
        {"value": "shot", "label": "Shot"},
        {"value": "name", "label": "Product Name"},
        {"value": "version", "label": "Version"},
        {"value": "project", "label": "Project"},
        {"value": "artist", "label": "Artist"},
        {"value": "comment", "label": "Comment"},
        {"value": "discipline", "label": "Discipline"},
        {"value": "datetime", "label": "Date / Time"},
        {"value": "framecounter", "label": "Frame Counter"},
    ]


class FFmpegCodecModel(BaseSettingsModel):
    _layout = "expanded"

    name: str = SettingsField("", title="Codec name (appended to filename)")
    codec: str = SettingsField(
        "prores_ks",
        title="Codec",
        enum_resolver=codec_enum,
    )
    movie_ext: Literal["mov", "mp4", "mxf", "mkv", "webm"] = SettingsField(
        "mov",
        title="Container",
    )
    width: int = SettingsField(0, title="Width (px, 0 = preserve)", ge=0)
    height: int = SettingsField(0, title="Height (px, 0 = preserve)", ge=0)
    fit: bool = SettingsField(True, title="Letterbox / pad to fit")
    pix_fmt: str = SettingsField(
        "yuv422p10le",
        title="Pixel Format",
        enum_resolver=pix_fmt_enum,
    )
    profile: str = SettingsField(
        "3",
        title="Profile / Flavor",
        enum_resolver=profile_enum,
    )
    crf: int = SettingsField(
        23,
        title="CRF (h264/x265 quality)",
        ge=0,
        le=51,
    )
    bitrate: str = SettingsField(
        "",
        title="Bitrate",
        placeholder="e.g. 10M",
    )
    extra_args: str = SettingsField(
        "",
        title="Extra ffmpeg args",
        widget="textarea",
        placeholder="-tune film -g 48 -bf 2 -vendor ap10",
    )


class FFmpegColorspaceModel(BaseSettingsModel):
    _layout = "compact"
    output: str = SettingsField("", title="Output OCIO colorspace")


class FFmpegCropmaskModel(BaseSettingsModel):
    _layout = "compact"
    enable: bool = SettingsField(False, title="Enable")
    aspect: float = SettingsField(2.39, title="Aspect ratio", gt=0)
    opacity: float = SettingsField(0.5, title="Bar opacity", ge=0, le=1)


class FFmpegBoxModel(BaseSettingsModel):
    _layout = "compact"
    x1: float = SettingsField(0.0, ge=0, le=1)
    y1: float = SettingsField(0.0, ge=0, le=1)
    x2: float = SettingsField(1.0, ge=0, le=1)
    y2: float = SettingsField(1.0, ge=0, le=1)


class FFmpegTextElementModel(BaseSettingsModel):
    _layout = "expanded"
    name: str = SettingsField(
        "shot",
        title="Element",
        enum_resolver=text_element_name_enum,
    )
    box: FFmpegBoxModel = SettingsField(
        default_factory=FFmpegBoxModel, title="Box (0–1, BL origin)"
    )
    font: str = SettingsField("", title="Font (override)")
    font_size: float = SettingsField(
        0.0, title="Size (override, 0 = inherit)", ge=0, le=1
    )
    font_color: ColorRGBA_float = SettingsField(
        (1.0, 1.0, 1.0, 1.0),
        title="Color (override)",
    )
    justify: Literal["left", "center", "right"] = SettingsField(
        "left",
        title="Justify",
    )
    prefix: str = SettingsField("", title="Prefix")
    datetime_format: str = SettingsField(
        "",
        title="strftime format (datetime element only)",
        placeholder="%Y-%m-%dT%H:%M:%S",
    )


class FFmpegBurninModel(BaseSettingsModel):
    _layout = "expanded"
    enabled: bool = SettingsField(False, title="Enable burn-ins")
    font: str = SettingsField("", title="Default font path")
    font_size: float = SettingsField(
        0.02, title="Default size (fraction of width)", gt=0, le=1
    )
    font_color: ColorRGBA_float = SettingsField(
        (0.8, 0.8, 0.8, 1.0),
        title="Default color",
    )
    cropmask: FFmpegCropmaskModel = SettingsField(default_factory=FFmpegCropmaskModel)
    text_elements: list[FFmpegTextElementModel] = SettingsField(
        default_factory=list,
        title="Text elements",
    )


class FFmpegProfileModel(BaseSettingsModel):
    _isGroup = True
    product_types: list[str] = SettingsField(
        default_factory=list, title="Product types"
    )
    hosts: list[str] = SettingsField(
        default_factory=list,
        title="Hosts",
        enum_resolver=addon_all_app_host_names_enum,
    )
    task_types: list[str] = SettingsField(default_factory=list, title="Task types")
    task_names: list[str] = SettingsField(default_factory=list, title="Task names")
    product_names: list[str] = SettingsField(
        default_factory=list, title="Product names"
    )

    codec: FFmpegCodecModel = SettingsField(default_factory=FFmpegCodecModel)
    colorspace: FFmpegColorspaceModel = SettingsField(
        default_factory=FFmpegColorspaceModel
    )
    burnin: FFmpegBurninModel = SettingsField(default_factory=FFmpegBurninModel)


class IntegrateFFmpegReviewModel(BaseSettingsModel):
    _layout = "expanded"
    enabled: bool = SettingsField(False, title="Enable plugin")
    product_types: list[str] = SettingsField(
        default_factory=list, title="Product types"
    )
    hosts: list[str] = SettingsField(
        default_factory=list,
        title="Hosts",
        enum_resolver=addon_all_app_host_names_enum,
    )
    task_types: list[str] = SettingsField(default_factory=list, title="Task types")
    task_names: list[str] = SettingsField(default_factory=list, title="Task names")
    product_names: list[str] = SettingsField(
        default_factory=list, title="Product names"
    )
    profiles: list[FFmpegProfileModel] = SettingsField(
        default_factory=list,
        title="Profiles (deliverables)",
    )


DEFAULT_FFMPEG_REVIEW_SETTINGS = {
    "enabled": False,
    "product_types": [],
    "hosts": [],
    "task_types": [],
    "task_names": [],
    "product_names": [],
    "profiles": [],
}
