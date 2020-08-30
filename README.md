# Warehouse Filebrowser

POC for [Feature request: "view source" tool for inspecting package contents](https://github.com/pypa/warehouse/issues/5118) and [Expose the METADATA file of wheels in the simple API](https://github.com/pypa/warehouse/issues/8254).

This implements a simple web server that downloads, extracts, and displays
“interesting” files from distributions available on PyPI.

Usage:

1. Run the server.
2. Navigate to `/<project-name>` to get a list of files.
3. Click on the file name of interest to view.

Any file name may return 404 if it does not actually exist in the
distribution archive.
