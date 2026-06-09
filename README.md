# Nuke addon
Nuke integration for AYON - Forked for Hornet.
Key Features:

- quick_write — `client/ayon_nuke/startup/quick_write.py`
- quick_publish — `client/ayon_nuke/startup/hornet_publish_utils.py`
- one-click-submit to deadline — `client/ayon_nuke/startup/hornet_deadline_utils.py`
- custom write node — `client/ayon_nuke/startup/custom_write_node.py`
- custom ffmpeg review media — `client/ayon_nuke/plugins/publish/extract_ffmpeg_review.py`, `client/ayon_nuke/startup/ffmpegbuilder.py`
- views write — `client/ayon_nuke/startup/views_write.py`
- oversized write nodes — `client/ayon_nuke/plugins/publish/increment_ovs_publish.py` (uses `get_ovs_pathing` from `client/ayon_nuke/api/lib.py`)


# Setup
to use this fork in an ayon dev bundle, the vendored ffmpeg binaries must be included so that review media can reference them.

to automatically set them up, simply run `python setup_ffmpeg.py` before adding this repo as your development bundle target. More information: 


# Vendored ffmpeg

ffmpeg binaries aren't committed in, as they are large, complete binaries — the release workflow pulls them from
[`hornet-animation/Academy-FFmpeg-Build`](https://github.com/hornet-animation/Academy-FFmpeg-Build)
at package time.

The build system is critical for maintaining a build that includes FFMPEG 8.1+ build flag `--enable-libopencolorio` which bakes native OCIO support, including transformations, into the ffmpeg binary. the command line arguments implemented by this build flag are essential for publish step `extract_ffmpeg_review`

`setup_ffmpeg.py` fetches the latest release of that repo, grabs
`ffmpeg-windows-x86_64.zip` and `ffmpeg-linux-x86_64.zip`, and extracts them
into `client/ayon_nuke/vendor/ffmpeg/{windows,linux}/`. 

In `.github/workflows/hornet_package_release.yml` it runs as the `Bootstrap
vendored ffmpeg` step, right before `create_package.py` — so the binaries are
in place when the release zip is built.

running create_package.py to build a new addon release will not result in working review generation unless the vendored folders are setup correctly

