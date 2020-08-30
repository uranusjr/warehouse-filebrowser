import contextlib
import dataclasses
import io
import os
import pathlib
import tarfile
import tempfile
import zipfile

import async_lru
import httpx
import mousebender.simple

from starlette.applications import Starlette
from starlette.responses import RedirectResponse, Response
from starlette.templating import Jinja2Templates


templates = Jinja2Templates(directory="templates")

app = Starlette(debug=bool(os.environ.get("DEBUG")))


@async_lru.alru_cache(maxsize=128)
async def _get_file(url):
    async with httpx.AsyncClient() as client:
        r = await client.get(url)
        return r.content


def _find_metadata_in_wheel(zf, wheel_filename):
    # TODO: Implement better heuristic to detect non-standard dist-info names.
    return "-".join(wheel_filename.split("-", 2)[:2]) + ".dist-info/METADATA"


def _find_file_in_tgz(tf, sdist_stem, arcname):
    with contextlib.suppress(KeyError):
        return tf.getmember(arcname)
    with contextlib.suppress(KeyError):
        return tf.getmember("{}/{}".format(sdist_stem, arcname))
    # TODO: Implement heuristic to guess the top-level directory name.
    raise FileNotFoundError


@dataclasses.dataclass()
class _Entry:
    project: str
    link: mousebender.simple.ArchiveLink

    @property
    def filename(self):
        return self.link.filename

    @property
    def stem(self):
        return self.filename.rsplit(".", 1)[0]

    def url_for_content(self, arcname):
        return app.url_path_for(
            "dist_file",
            project=self.project,
            dist=self.filename,
            arcname=arcname,
        )


class Wheel(_Entry):
    useful_filenames = ["METADATA"]

    async def read_file(self, filename):
        if filename != "METADATA":
            raise FileNotFoundError
        content = await _get_file(self.link.url)
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            name = _find_metadata_in_wheel(zf, self.filename)
            with zf.open(name) as f:
                return f.read()


class TarGzSdist(_Entry):
    useful_filenames = ["pyproject.toml", "setup.cfg", "setup.py"]

    @property
    def stem(self):
        return self.filename.rsplit(".", 2)[0]

    async def read_file(self, filename):
        if filename not in self.useful_filenames:
            raise FileNotFoundError
        content = await _get_file(self.link.url)
        with tempfile.TemporaryDirectory() as td:
            with tarfile.open("r:gz", fileobj=io.BytesIO(content)) as tf:
                member = _find_file_in_tgz(tf, self.stem, filename)
                tf.extract(member, path=td)
            path = next(p for p in pathlib.Path(td).rglob("*") if p.is_file())
            return path.read_bytes()


class ZipSdist(_Entry):
    useful_filenames = ["pyproject.toml", "setup.cfg", "setup.py"]

    async def read_file(self, filename):
        if filename not in self.useful_filenames:
            raise FileNotFoundError
        content = await _get_file(self.link.url)
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            try:
                info = zf.getinfo(filename)
            except KeyError:
                raise FileNotFoundError
            with zf.open(info) as f:
                return f.read()


class UnsupportedEntry(_Entry):
    useful_filenames = []

    async def read_file(self, filename):
        raise FileNotFoundError


async def _iter_simple_entries(project):
    simple_url = f"https://pypi.org/simple/{project}/"
    async with httpx.AsyncClient() as client:
        r = await client.get(simple_url)
        r.raise_for_status()
    links = mousebender.simple.parse_archive_links(r.text)

    entries = []
    for link in reversed(links):  # Show later versions first.
        if link.filename.endswith(".whl"):
            entry_cls = Wheel
        elif link.filename.endswith(".tar.gz"):
            entry_cls = TarGzSdist
        elif link.filename.endswith(".zip"):
            entry_cls = ZipSdist
        else:
            entry_cls = UnsupportedEntry
        entries.append(entry_cls(project=project, link=link))

    return entries


@app.route("/")
async def index(request):
    if "project" in request.query_params:
        project = request.query_params["project"]
        url = request.url_for("project", project=project)
        return RedirectResponse(url=url)
    return templates.TemplateResponse("index.jinja2", {"request": request})


@app.route("/{project}/")
async def project(request):
    try:
        entries = await _iter_simple_entries(request.path_params["project"])
    except httpx.HTTPStatusError as e:
        return Response(str(e), 400)
    context = {"request": request, "entries": entries}
    return templates.TemplateResponse("project.jinja2", context)


@app.route("/{project}/{dist}/{arcname}", name="dist_file")
async def dist_file(request):
    try:
        entries = await _iter_simple_entries(request.path_params["project"])
    except httpx.HTTPStatusError as e:
        return Response(str(e), 400)
    try:
        entry = next(
            e for e in entries
            if e.filename == request.path_params["dist"]
        )
    except StopIteration:
        return Response("dist file not found", 404)
    try:
        content = await entry.read_file(request.path_params["arcname"])
    except FileNotFoundError:
        return Response("dist content not found", 404)
    return Response(content)
