"""GOG Galaxy content system (generation 2): install, update, rollback and repair.

Builds are described by a zlib-compressed JSON manifest listing *depots*; each
depot manifest lists files made of zlib-compressed chunks addressed by MD5.
Chunks are fetched in parallel through time-limited "secure links", verified
(compressed + decompressed MD5) and written in place, so installs can be
paused, resumed, verified and updated incrementally (unchanged chunks of a
modified file are reused from disk).
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import shutil
import stat
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

import requests

from nebula import paths
from nebula.core.jobs import Canceled, JobContext
from nebula.core.utils import case_insensitive_path
from nebula.integrations.gog.api import CDN, GogApi, galaxy_path

log = logging.getLogger(__name__)

RESUME_FILE = ".nebula-resume"
MARKER_FILE = ".nebula"


def branch_label(branch: str | None) -> str:
    """Human name of a GOG build channel: None/"" is the default "Stable" channel."""
    if not branch:
        return "Stable"
    s = str(branch).replace("_", " ").strip()
    return s[:1].upper() + s[1:]


def nice_date(iso: str | None) -> str:
    """"2023-06-13T09:19:10+0000" or "2023-06-13" -> "13 Jun 2023" ("" when missing or invalid)."""
    from datetime import datetime

    if not iso:
        return ""
    try:
        d = datetime.strptime(str(iso)[:10], "%Y-%m-%d")
    except ValueError:
        return ""
    return f"{d.day} {d:%b %Y}"


def _branch(build: dict) -> str | None:
    """Channel of a build: None for the default (Stable) channel."""
    return build.get("branch") or None


LANGUAGE_NAMES = {
    "english": "en-US", "german": "de-DE", "french": "fr-FR", "polish": "pl-PL", "russian": "ru-RU",
    "spanish": "es-ES", "italian": "it-IT", "portuguese": "pt-PT", "brazilian": "pt-BR",
    "chinese": "zh-Hans", "japanese": "ja-JP", "korean": "ko-KR", "czech": "cs-CZ", "hungarian": "hu-HU",
    "turkish": "tr-TR", "dutch": "nl-NL", "swedish": "sv-SE", "danish": "da-DK", "finnish": "fi-FI",
    "norwegian": "nb-NO", "ukrainian": "uk-UA", "romanian": "ro-RO", "arabic": "ar",
}

LANGUAGE_LABELS = {
    "en": "English", "de": "Deutsch", "fr": "Français", "pl": "Polski", "ru": "Русский",
    "es": "Español", "it": "Italiano", "pt": "Português", "pt-br": "Português (Brasil)",
    "zh": "中文", "ja": "日本語", "ko": "한국어", "cs": "Čeština",
    "hu": "Magyar", "tr": "Türkçe", "nl": "Nederlands", "sv": "Svenska", "da": "Dansk", "fi": "Suomi",
    "nb": "Norsk", "uk": "Українська", "ro": "Română",
}


def normalize_lang(code: str) -> str:
    c = (code or "").strip()
    return LANGUAGE_NAMES.get(c.lower(), c)


def lang_label(code: str) -> str:
    c = normalize_lang(code).lower()
    return LANGUAGE_LABELS.get(c) or LANGUAGE_LABELS.get(c.split("-")[0]) or code


def lang_matches(depot_langs: list[str], target: str) -> bool:
    t = normalize_lang(target).lower()
    for raw in depot_langs or ["*"]:
        lang = normalize_lang(raw).lower()
        if lang == "*" or lang == t:
            return True
        if lang.split("-")[0] == t.split("-")[0] and not (lang.startswith("pt") or lang.startswith("zh")):
            return True
    return False


# --------------------------------------------------------------- models
@dataclass
class DepotFile:
    path: str
    chunks: list = field(default_factory=list)
    md5: str = ""
    sha256: str = ""
    flags: list = field(default_factory=list)
    product_id: str = ""

    @classmethod
    def from_item(cls, item: dict, product_id: str) -> "DepotFile":
        return cls(
            path=item["path"].replace("\\", "/").lstrip("/"),
            chunks=[{"md5": c["md5"], "size": int(c["size"]), "compressedMd5": c["compressedMd5"],
                     "compressedSize": int(c.get("compressedSize", 0))} for c in item.get("chunks", [])],
            md5=item.get("md5") or "",
            sha256=item.get("sha256") or "",
            flags=list(item.get("flags") or []),
            product_id=str(product_id),
        )

    @property
    def size(self) -> int:
        return sum(c["size"] for c in self.chunks)

    @property
    def compressed_size(self) -> int:
        return sum(c.get("compressedSize", 0) for c in self.chunks)

    @property
    def final_md5(self) -> str:
        if self.md5:
            return self.md5
        if len(self.chunks) == 1:
            return self.chunks[0]["md5"]
        return ""

    @property
    def support(self) -> bool:
        return "support" in self.flags

    @property
    def executable(self) -> bool:
        return "executable" in self.flags

    def same_content(self, other: "DepotFile") -> bool:
        a, b = self.final_md5, other.final_md5
        if a and b:
            return a == b
        if self.sha256 and other.sha256:
            return self.sha256 == other.sha256
        return [c["md5"] for c in self.chunks] == [c["md5"] for c in other.chunks]


@dataclass
class FileSet:
    files: list = field(default_factory=list)  # list[DepotFile]
    dirs: list = field(default_factory=list)  # list[str]
    links: list = field(default_factory=list)  # list[(path, target)]


class BuildInfo:
    """A parsed generation-2 build manifest."""

    def __init__(self, build: dict, meta: dict):
        self.build = build
        self.meta = meta
        self.product_id = str(meta.get("baseProductId") or build.get("product_id"))
        self.build_id = str(build.get("build_id") or meta.get("buildId") or "")
        self.version = build.get("version_name") or ""
        self.date = build.get("date_published") or ""
        self.branch = build.get("branch")
        self.install_directory = meta.get("installDirectory") or ""
        self.client_id = meta.get("clientId") or ""
        self.client_secret = meta.get("clientSecret") or ""
        self.dependencies = list(meta.get("dependencies") or [])
        self.products = [{"id": str(p.get("productId")), "name": p.get("name", "")} for p in meta.get("products", [])]
        self.depots = list(meta.get("depots") or [])

    def languages(self) -> list[str]:
        out: list[str] = []
        for d in self.depots:
            for lang in d.get("languages", []):
                code = normalize_lang(lang)
                if code != "*" and code not in out:
                    out.append(code)
        return out

    def dlc_products(self) -> list[dict]:
        return [p for p in self.products if p["id"] != self.product_id]

    def selected_depots(self, language: str, dlc_ids: list[str], include_base: bool = True) -> list[dict]:
        wanted = set(dlc_ids)
        if include_base:
            wanted.add(self.product_id)
        langs = self.languages()
        if langs and not any(lang_matches([lang], language) for lang in langs):
            language = "en-US"  # fallback when the requested language is unavailable
        return [d for d in self.depots
                if str(d.get("productId")) in wanted and lang_matches(d.get("languages", ["*"]), language)]

    def sizes(self, language: str, dlc_ids: list[str]) -> tuple[int, int]:
        depots = self.selected_depots(language, dlc_ids)
        return (sum(int(d.get("compressedSize", 0) or 0) for d in depots),
                sum(int(d.get("size", 0) or 0) for d in depots))

    def dlc_size(self, dlc_id: str, language: str) -> int:
        return sum(int(d.get("size", 0) or 0) for d in self.depots
                   if str(d.get("productId")) == dlc_id and lang_matches(d.get("languages", ["*"]), language))


class Gen2:
    def __init__(self, api: GogApi, product_id: str):
        self.api = api
        self.product_id = str(product_id)

    def builds(self, os_name: str = "windows") -> list[dict]:
        items = [b for b in self.api.builds(self.product_id, os_name, 2) if int(b.get("generation", 2)) == 2]
        items = [b for b in items if b.get("public", True)]
        items.sort(key=lambda b: b.get("date_published", ""), reverse=True)
        return items

    @staticmethod
    def find(builds: list[dict], build_id: str) -> dict | None:
        """The build with this id; older installs may carry the legacy (pre-Galaxy-2) id instead."""
        build_id = str(build_id or "")
        if not build_id:
            return None
        return (next((b for b in builds if str(b.get("build_id")) == build_id), None)
                or next((b for b in builds if str(b.get("legacy_build_id") or "") == build_id), None))

    @staticmethod
    def latest(builds: list[dict], branch: str | None) -> dict | None:
        """Newest build on exactly this channel (None/"" = Stable); no fallback to other channels."""
        branch = branch or None
        return next((b for b in builds if _branch(b) == branch), None)

    @staticmethod
    def branches(builds: list[dict]) -> list[str | None]:
        """Channels that have builds: Stable (None) first, then the others by their newest build."""
        newest: dict[str | None, str] = {}
        for b in builds:
            br, date = _branch(b), b.get("date_published") or ""
            if br not in newest or date > newest[br]:
                newest[br] = date
        others = sorted((br for br in newest if br is not None), key=lambda br: newest[br], reverse=True)
        return ([None] if None in newest else []) + others

    def pick(self, builds: list[dict], build_id: str = "", branch: str | None = None) -> dict | None:
        """A specific build, or the newest one on ``branch`` (falling back to Stable, never to another beta)."""
        if build_id:
            return self.find(builds, build_id)
        return self.latest(builds, branch) or self.latest(builds, None)

    def build_info(self, build: dict) -> BuildInfo:
        meta, _ = self.api.zjson(build["link"], auth=False)
        if int(meta.get("version", 2)) != 2:
            raise ValueError("Not a generation 2 manifest")
        return BuildInfo(build, meta)

    def file_set(self, info: BuildInfo, language: str, dlc_ids: list[str], include_base: bool = True,
                 ctx: JobContext | None = None) -> FileSet:
        out = FileSet()
        seen: set[str] = set()
        for depot in info.selected_depots(language, dlc_ids, include_base):
            if ctx:
                ctx.check()
            data, _ = self.api.zjson(f"{CDN}/content-system/v2/meta/{galaxy_path(depot['manifest'])}", auth=False)
            for item in data.get("depot", {}).get("items", []):
                kind = item.get("type")
                if kind == "DepotFile":
                    f = DepotFile.from_item(item, depot["productId"])
                    k = f.path.lower()
                    if k in seen:
                        continue
                    seen.add(k)
                    out.files.append(f)
                elif kind == "DepotDirectory":
                    out.dirs.append(item["path"].replace("\\", "/").strip("/"))
                elif kind == "DepotLink":
                    out.links.append((item["path"].replace("\\", "/"), item["target"].replace("\\", "/")))
        return out


# ------------------------------------------------------- chunk fetching
class Unauthorized(Exception):
    pass


class ChunkFetcher:
    def __init__(self, ctx: JobContext, session: requests.Session,
                 link_provider: Callable[[str], list[dict]]):
        self.ctx = ctx
        self.session = session
        self._provider = link_provider
        self._links: dict[str, list[dict]] = {}
        self._lock = threading.Lock()

    def endpoints(self, product_id: str, refresh: bool = False) -> list[dict]:
        with self._lock:
            if refresh or product_id not in self._links:
                links = self._provider(product_id)
                if not links:
                    raise IOError(f"No download endpoints for product {product_id}")
                self._links[product_id] = links
            return self._links[product_id]

    @staticmethod
    def url(endpoint: dict, compressed_md5: str) -> str:
        if endpoint.get("url_format"):
            params = dict(endpoint.get("parameters", {}))
            params["path"] = str(params.get("path", "")).rstrip("/") + "/" + galaxy_path(compressed_md5)
            url = endpoint["url_format"]
            for k, v in params.items():
                url = url.replace("{" + k + "}", str(v))
            return url
        return endpoint["url"].rstrip("/") + "/" + galaxy_path(compressed_md5)

    def fetch(self, product_id: str, chunk: dict) -> bytes:
        last: Exception | None = None
        for attempt in range(8):
            self.ctx.check()
            eps = self.endpoints(product_id, refresh=isinstance(last, Unauthorized))
            ep = eps[attempt % len(eps)]
            try:
                r = self.session.get(self.url(ep, chunk["compressedMd5"]), timeout=(10, 60))
                if r.status_code in (401, 403):
                    last = Unauthorized()
                    continue
                if r.status_code != 200:
                    raise IOError(f"HTTP {r.status_code}")
                raw = r.content
                self.ctx.add_network(len(raw))
                if hashlib.md5(raw).hexdigest() != chunk["compressedMd5"]:
                    raise IOError("compressed checksum mismatch")
                data = zlib.decompress(raw)
                if hashlib.md5(data).hexdigest() != chunk["md5"]:
                    raise IOError("checksum mismatch")
                return data
            except Canceled:
                raise
            except (requests.RequestException, OSError, zlib.error) as exc:
                last = exc
                time.sleep(min(8.0, 0.5 * (2 ** attempt)))
        raise IOError(f"Failed to download chunk {chunk['compressedMd5']}: {last}")


# ----------------------------------------------------------- executing
@dataclass
class _FileState:
    file: DepotFile
    target: Path
    tmp: Path
    remaining: int
    reuse: dict  # md5 -> (offset, size) inside the current on-disk file
    lock: threading.Lock = field(default_factory=threading.Lock)


class Executor:
    def __init__(self, ctx: JobContext, install_dir: Path, support_dir: Path, fetcher: ChunkFetcher,
                 resume_file: Path | None = None):
        self.ctx = ctx
        self.install_dir = Path(install_dir)
        self.support_dir = Path(support_dir)
        self.fetcher = fetcher
        self.resume_file = resume_file
        self._error: BaseException | None = None
        self._resume_lock = threading.Lock()

    def target(self, f: DepotFile) -> Path:
        if f.support:
            return self.support_dir / f.product_id / f.path
        return self.install_dir / f.path

    def resolved_target(self, f: DepotFile) -> Path:
        return case_insensitive_path(self.target(f))

    def apply(self, files: list[DepotFile], old: dict[str, DepotFile] | None = None, dirs: list[str] = (),
              links: list = (), deletions: list[DepotFile] = ()) -> None:
        ctx = self.ctx
        total = sum(f.size for f in files)
        ctx.set_total(total)
        ctx.set_done(0)
        free = shutil.disk_usage(self._existing_parent(self.install_dir)).free
        if total > free:
            raise IOError(f"Not enough disk space: {total // 2**20} MiB needed, {free // 2**20} MiB free")
        self.install_dir.mkdir(parents=True, exist_ok=True)
        workers = ctx.workers
        sem = threading.Semaphore(workers * 3)
        pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="gog-dl")
        try:
            for f in files:
                ctx.check()
                if self._error:
                    raise self._error
                target = self.resolved_target(f)
                target.parent.mkdir(parents=True, exist_ok=True)
                if not f.chunks:
                    target.write_bytes(b"")
                    self._finalize_perms(target, f)
                    self._mark_done(f)
                    continue
                reuse = {}
                prev = (old or {}).get(f.path.lower())
                if prev is not None and target.is_file():
                    off = 0
                    for c in prev.chunks:
                        reuse.setdefault(c["md5"], (off, c["size"]))
                        off += c["size"]
                tmp = target.with_name(target.name + ".nbpart")
                with open(tmp, "wb") as fh:
                    fh.truncate(f.size)
                state = _FileState(f, target, tmp, len(f.chunks), reuse)
                offset = 0
                for chunk in f.chunks:
                    while not sem.acquire(timeout=0.5):
                        ctx.check()
                        if self._error:
                            raise self._error
                    fut = pool.submit(self._chunk_task, state, chunk, offset)
                    fut.add_done_callback(lambda _f: sem.release())
                    offset += chunk["size"]
            pool.shutdown(wait=True)
            if self._error:
                raise self._error
        except BaseException:
            self._error = self._error or Canceled()
            pool.shutdown(wait=True, cancel_futures=True)
            raise
        for f in deletions:
            p = self.resolved_target(f)
            try:
                if p.is_file() or p.is_symlink():
                    p.unlink()
            except OSError:
                pass
        for path, tgt in links:
            p = self.install_dir / path
            try:
                if p.is_symlink() or p.exists():
                    p.unlink()
                p.parent.mkdir(parents=True, exist_ok=True)
                p.symlink_to(tgt)
            except OSError as exc:
                log.warning("Could not create link %s: %s", p, exc)
        if deletions:
            self._remove_empty_dirs()
        # created last so that directories the game expects to exist (even empty) survive cleanup
        for d in dirs:
            case_insensitive_path(self.install_dir / d).mkdir(parents=True, exist_ok=True)

    def _existing_parent(self, p: Path) -> Path:
        while not p.exists() and p != p.parent:
            p = p.parent
        return p

    def _chunk_task(self, state: _FileState, chunk: dict, offset: int) -> None:
        if self._error:
            return
        try:
            self.ctx.check()
            data = None
            loc = state.reuse.get(chunk["md5"])
            if loc is not None:
                try:
                    with open(state.target, "rb") as fh:
                        fh.seek(loc[0])
                        candidate = fh.read(loc[1])
                    if hashlib.md5(candidate).hexdigest() == chunk["md5"]:
                        data = candidate
                except OSError:
                    data = None
            if data is None:
                data = self.fetcher.fetch(state.file.product_id, chunk)
            fd = os.open(state.tmp, os.O_WRONLY)
            try:
                os.pwrite(fd, data, offset)
            finally:
                os.close(fd)
            self.ctx.add(len(data), network=False)
            with state.lock:
                state.remaining -= 1
                last = state.remaining == 0
            if last:
                os.replace(state.tmp, state.target)
                self._finalize_perms(state.target, state.file)
                self._mark_done(state.file)
        except BaseException as exc:  # noqa: BLE001
            if self._error is None:
                self._error = exc

    def _finalize_perms(self, target: Path, f: DepotFile) -> None:
        if f.executable:
            try:
                target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            except OSError:
                pass

    def _mark_done(self, f: DepotFile) -> None:
        if not self.resume_file:
            return
        with self._resume_lock:
            with open(self.resume_file, "a") as fh:
                fh.write(f"{f.final_md5 or f.size}\t{f.path}\n")

    def _remove_empty_dirs(self) -> None:
        for root, dirs, files in os.walk(self.install_dir, topdown=False):
            if root == str(self.install_dir):
                continue
            if not dirs and not files:
                try:
                    os.rmdir(root)
                except OSError:
                    pass


def read_resume(path: Path) -> dict[str, str]:
    out = {}
    try:
        for line in path.read_text().splitlines():
            if "\t" in line:
                digest, p = line.split("\t", 1)
                out[p.lower()] = digest
    except OSError:
        pass
    return out


def verify_files(ctx: JobContext, files: list[DepotFile], executor: Executor) -> list[DepotFile]:
    """Return the files whose on-disk content does not match the manifest."""
    total = sum(f.size for f in files)
    ctx.set_total(total)
    ctx.set_done(0)
    broken: list[DepotFile] = []
    lock = threading.Lock()

    def check(f: DepotFile):
        ctx.check()
        p = executor.resolved_target(f)
        ok = p.is_file() and p.stat().st_size == f.size
        if ok:
            with open(p, "rb") as fh:
                for c in f.chunks:
                    ctx.check()
                    data = fh.read(c["size"])
                    ctx.add(len(data), network=False)
                    if hashlib.md5(data).hexdigest() != c["md5"]:
                        ok = False
                        break
        if not ok:
            with lock:
                broken.append(f)

    with ThreadPoolExecutor(max_workers=max(2, min(8, ctx.workers))) as pool:
        for fut in [pool.submit(check, f) for f in files]:
            fut.result()
    return broken


# --------------------------------------------------- stored manifests
def manifest_path(product_id: str) -> Path:
    return paths.manifests_dir("gog") / f"{product_id}.json.gz"


def save_manifest(product_id: str, data: dict) -> None:
    p = manifest_path(product_id)
    tmp = p.with_suffix(".tmp")
    with gzip.open(tmp, "wt") as fh:
        json.dump(data, fh)
    os.replace(tmp, p)


def load_manifest(product_id: str) -> dict | None:
    p = manifest_path(product_id)
    try:
        with gzip.open(p, "rt") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def delete_manifest(product_id: str) -> None:
    manifest_path(product_id).unlink(missing_ok=True)


def manifest_files(data: dict) -> list[DepotFile]:
    return [DepotFile(**f) for f in data.get("files", [])]


def build_manifest_record(info: BuildInfo, fs: FileSet, os_name: str, language: str, dlcs: list[str],
                          install_dir: str, redists: list[dict]) -> dict:
    return {
        "generation": 2,
        "product_id": info.product_id,
        "build_id": info.build_id,
        "version": info.version,
        "branch": info.branch,
        "os": os_name,
        "language": language,
        "dlcs": dlcs,
        "install_dir": install_dir,
        "client_id": info.client_id,
        "client_secret": info.client_secret,
        "dependencies": info.dependencies,
        "redists": redists,
        "files": [asdict(f) for f in fs.files],
        "dirs": fs.dirs,
        "links": fs.links,
        "installed_at": time.time(),
    }


# ------------------------------------------------------ redistributables
def install_dependencies(api: GogApi, ctx: JobContext, session: requests.Session, dep_ids: list[str],
                         install_dir: Path) -> list[dict]:
    """Download the redistributables a build depends on.

    Global redistributables (``__redist/...``: MSVC, DirectX, .NET...) are shared
    between games in Super Nova's redist folder and installed into each Proton prefix
    on first launch. Game-local dependencies are written into the game folder.
    """
    if not dep_ids:
        return []
    ctx.stage("Fetching dependencies")
    repo = api.dependencies_repository()
    depots = [d for d in repo.get("depots", []) if d.get("dependencyId") in dep_ids]
    redist_root = paths.redist_dir()
    global_files: list[DepotFile] = []
    local_files: list[DepotFile] = []
    redists = []
    for d in depots:
        exe = d.get("executable", {}) or {}
        data, _ = api.zjson(f"{CDN}/content-system/v2/dependencies/meta/{galaxy_path(d['manifest'])}", auth=False)
        files = [DepotFile.from_item(i, "redist") for i in data.get("depot", {}).get("items", [])
                 if i.get("type") == "DepotFile"]
        if str(exe.get("path", "")).startswith("__redist"):
            for f in files:
                p = redist_root / f.path
                if not (p.is_file() and p.stat().st_size == f.size):
                    global_files.append(f)
            redists.append({
                "id": d["dependencyId"],
                "name": d.get("readableName") or d["dependencyId"],
                "exe": str(redist_root / exe["path"].replace("\\", "/")),
                "args": exe.get("arguments", ""),
            })
        else:
            local_files += files
    if not global_files and not local_files:
        return redists
    fetcher = ChunkFetcher(ctx, session, lambda _pid: api.dependency_link())
    if global_files:
        ctx.stage("Downloading redistributables")
        Executor(ctx, redist_root, redist_root / "__support", fetcher).apply(global_files)
    if local_files:
        ctx.stage("Downloading game dependencies")
        Executor(ctx, install_dir, install_dir / ".support", fetcher).apply(local_files)
    return redists
