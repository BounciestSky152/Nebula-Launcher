"""Self-updater: keeps Nebula in sync with its GitHub repository.

Only files the app actually needs at runtime are considered: the ``nebula/``
package and ``pyproject.toml`` (dependencies). Commits that only touch the
README, docs, screenshots, tests or scripts never trigger an update.

Detection compares git blob hashes of the installed files with the tree of the
latest commit on GitHub, so it works even when the installed commit is unknown
(e.g. installed from a tarball). Four install modes are supported:

* ``git``    running from a git checkout of the repository -> ``git merge --ff-only``
* ``pip``    installed into a virtualenv (scripts/install.sh) -> ``pip install`` of the new source
* ``files``  running from a plain, writable source folder -> changed files are copied in place
* ``manual`` read-only install -> the user is told an update exists

This module only uses the standard library plus ``requests`` so it can run while
the rest of the package is being replaced.
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import logging
import os
import pkgutil
import shutil
import subprocess
import sys
import tarfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import requests

log = logging.getLogger(__name__)

DEFAULT_REPO = "BounciestSky152/Nebula-Launcher"
DEFAULT_BRANCH = "main"
API_BASE = "https://api.github.com"
CODELOAD_BASE = "https://codeload.github.com"
GIT_BASE = "https://github.com"

PACKAGE = "nebula"
DEPENDENCY_FILE = "pyproject.toml"


class UpdateError(Exception):
    pass


def is_runtime_path(path: str) -> bool:
    """True for repository paths the running app depends on."""
    if path == DEPENDENCY_FILE:
        return True
    if not path.startswith(PACKAGE + "/"):
        return False
    parts = path.split("/")
    return "__pycache__" not in parts and not path.endswith((".pyc", ".pyo"))


def git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


@dataclass
class UpdateInfo:
    remote_sha: str
    mode: str
    added: list = field(default_factory=list)
    changed: list = field(default_factory=list)
    removed: list = field(default_factory=list)
    commits: list = field(default_factory=list)  # list of (sha, first line of message)
    dependencies_changed: bool = False

    @property
    def files(self) -> list[str]:
        return sorted(self.added + self.changed + self.removed)

    def summary(self) -> str:
        parts = []
        if self.changed:
            parts.append(f"{len(self.changed)} changed")
        if self.added:
            parts.append(f"{len(self.added)} new")
        if self.removed:
            parts.append(f"{len(self.removed)} removed")
        return ", ".join(parts) + " file(s)" if parts else "no file changes"


class Updater:
    def __init__(self, repo: str = DEFAULT_REPO, branch: str = DEFAULT_BRANCH, state_path: Path | None = None,
                 package_dir: Path | None = None, session: requests.Session | None = None,
                 api_base: str = API_BASE, codeload_base: str = CODELOAD_BASE, git_base: str = GIT_BASE):
        from nebula import paths

        self.repo = repo.strip().strip("/")
        self.branch = branch.strip() or DEFAULT_BRANCH
        self.package_dir = Path(package_dir or Path(__file__).resolve().parent)
        self.state_path = Path(state_path or paths.data_dir() / "update-state.json")
        self.staging_root = self.state_path.parent / "updates"
        self.api_base, self.codeload_base, self.git_base = api_base, codeload_base, git_base
        if session is None:
            from nebula.core.http import new_session

            session = new_session()
        self.session = session
        self._mode: str | None = None
        self.state = self._load_state()

    # ------------------------------------------------------------- state
    def _load_state(self) -> dict:
        try:
            return json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            return {}

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=2))
        os.replace(tmp, self.state_path)

    @property
    def installed_sha(self) -> str:
        if self.mode == "git":
            return self._git("rev-parse", "HEAD").strip()
        return self.state.get("installed_sha", "")

    # -------------------------------------------------------------- mode
    @property
    def project_root(self) -> Path:
        return self.package_dir.parent

    @property
    def mode(self) -> str:
        if self._mode is None:
            self._mode = self._detect_mode()
        return self._mode

    def _detect_mode(self) -> str:
        root = self.project_root
        if (root / ".git").exists() and shutil.which("git"):
            try:
                url = self._git("remote", "get-url", "origin").strip().lower()
            except UpdateError:
                url = ""
            if self.repo.lower() in url.removesuffix(".git") or self.repo.lower() + ".git" in url:
                return "git"
        site_dirs = {p for p in sys.path if p.endswith("site-packages")}
        in_site = any(str(self.package_dir).startswith(s) for s in site_dirs)
        if in_site and sys.prefix != getattr(sys, "base_prefix", sys.prefix) \
                and importlib.util.find_spec("pip") is not None and os.access(self.package_dir, os.W_OK):
            return "pip"
        if not in_site and os.access(self.package_dir, os.W_OK):
            return "files"
        return "manual"

    def _git(self, *args: str, timeout: float = 120) -> str:
        proc = subprocess.run(["git", "-C", str(self.project_root), *args], capture_output=True, text=True,
                              timeout=timeout)
        if proc.returncode != 0:
            raise UpdateError(f"git {' '.join(args)} failed: {proc.stderr.strip()[:300]}")
        return proc.stdout

    # ------------------------------------------------------------ GitHub
    def remote_head(self) -> str:
        """SHA of the branch tip (conditional request: unchanged answers don't use API quota)."""
        url = f"{self.api_base}/repos/{self.repo}/commits/{self.branch}"
        headers = {"Accept": "application/vnd.github.sha"}
        etag = self.state.get("head_etag")
        if etag and self.state.get("head_sha"):
            headers["If-None-Match"] = etag
        try:
            r = self.session.get(url, headers=headers, timeout=20)
            if r.status_code == 304:
                return self.state["head_sha"]
            if r.ok and len(r.text.strip()) == 40:
                self.state["head_sha"] = r.text.strip()
                self.state["head_etag"] = r.headers.get("ETag", "")
                return self.state["head_sha"]
            log.info("GitHub API answered %s, falling back to git protocol", r.status_code)
        except requests.RequestException as exc:
            log.info("GitHub API unreachable (%s), falling back to git protocol", exc)
        return self._head_via_git_protocol()

    def _head_via_git_protocol(self) -> str:
        r = self.session.get(f"{self.git_base}/{self.repo}.git/info/refs", params={"service": "git-upload-pack"},
                             timeout=20)
        if not r.ok:
            raise UpdateError(f"Could not reach {self.repo} (HTTP {r.status_code})")
        want = f"refs/heads/{self.branch}".encode()
        for line in r.content.split(b"\n"):
            line = line[4:] if len(line) > 4 else line
            parts = line.split(b"\0")[0].split(b" ")
            if len(parts) >= 2 and parts[1].strip() == want and len(parts[0][-40:]) == 40:
                return parts[0][-40:].decode()
        raise UpdateError(f"Branch {self.branch} not found in {self.repo}")

    def remote_tree(self, sha: str) -> dict[str, str]:
        r = self.session.get(f"{self.api_base}/repos/{self.repo}/git/trees/{sha}", params={"recursive": "1"},
                             timeout=30)
        if not r.ok:
            raise UpdateError(f"Could not read the file list of {sha[:7]} (HTTP {r.status_code})")
        data = r.json()
        if data.get("truncated"):
            log.warning("GitHub truncated the tree listing; some files may be missed")
        return {e["path"]: e["sha"] for e in data.get("tree", []) if e.get("type") == "blob"}

    def commit_messages(self, base: str, head: str) -> list[tuple[str, str]]:
        try:
            if base:
                r = self.session.get(f"{self.api_base}/repos/{self.repo}/compare/{base}...{head}", timeout=20)
                if r.ok:
                    return [(c["sha"], c["commit"]["message"].splitlines()[0]) for c in r.json().get("commits", [])][-20:]
            r = self.session.get(f"{self.api_base}/repos/{self.repo}/commits/{head}", timeout=20)
            if r.ok:
                return [(head, r.json()["commit"]["message"].splitlines()[0])]
        except (requests.RequestException, ValueError, KeyError):
            pass
        return []

    # ------------------------------------------------------------- local
    def local_runtime_files(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for dirpath, dirnames, filenames in os.walk(self.package_dir):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for name in filenames:
                full = Path(dirpath) / name
                rel = f"{PACKAGE}/{full.relative_to(self.package_dir).as_posix()}"
                if is_runtime_path(rel):
                    try:
                        out[rel] = git_blob_sha(full.read_bytes())
                    except OSError:
                        continue
        dep = self.project_root / DEPENDENCY_FILE
        if self.mode in ("git", "files") and dep.is_file():
            out[DEPENDENCY_FILE] = git_blob_sha(dep.read_bytes())
        elif self.state.get("dependency_blob"):
            out[DEPENDENCY_FILE] = self.state["dependency_blob"]
        return out

    # ------------------------------------------------------------- check
    def check(self) -> UpdateInfo | None:
        """Return what would change, or None when the app is up to date."""
        mode = self.mode
        self.state["last_check"] = time.time()
        if mode == "git":
            return self._check_git()
        head = self.remote_head()
        if head == self.state.get("installed_sha") and self.state.get("verified_sha") == head:
            self._save_state()
            return None
        remote = {p: s for p, s in self.remote_tree(head).items() if is_runtime_path(p)}
        local = self.local_runtime_files()
        info = UpdateInfo(remote_sha=head, mode=mode)
        for path, sha in remote.items():
            if path not in local:
                if path == DEPENDENCY_FILE:
                    # dependency list unknown locally: only reinstall deps alongside real code changes
                    continue
                info.added.append(path)
            elif local[path] != sha:
                info.changed.append(path)
        info.removed = sorted(p for p in local if p not in remote and p != DEPENDENCY_FILE)
        info.dependencies_changed = DEPENDENCY_FILE in info.changed
        if not info.files:
            # nothing the app needs changed (e.g. README or screenshots): remember and move on
            self.state.update(installed_sha=head, verified_sha=head)
            self._save_state()
            return None
        info.commits = self.commit_messages(self.state.get("installed_sha", ""), head)
        self.state["available"] = asdict(info)
        self._save_state()
        return info

    def _check_git(self) -> UpdateInfo | None:
        self._git("fetch", "--quiet", "origin", self.branch)
        head = self._git("rev-parse", f"origin/{self.branch}").strip()
        local = self._git("rev-parse", "HEAD").strip()
        if head == local:
            self._save_state()
            return None
        try:
            self._git("merge-base", "--is-ancestor", "HEAD", f"origin/{self.branch}", timeout=30)
        except UpdateError:
            raise UpdateError("Your checkout has diverged from GitHub; update it manually") from None
        names = self._git("diff", "--name-status", "--no-renames", "HEAD", f"origin/{self.branch}")
        info = UpdateInfo(remote_sha=head, mode="git")
        for line in names.splitlines():
            status, _, path = line.partition("\t")
            if not is_runtime_path(path):
                continue
            {"A": info.added, "D": info.removed}.get(status[:1], info.changed).append(path)
        info.dependencies_changed = DEPENDENCY_FILE in info.changed
        log_lines = self._git("log", "--format=%H %s", f"HEAD..origin/{self.branch}").splitlines()
        info.commits = [(ln[:40], ln[41:]) for ln in log_lines][:20]
        if not info.files:
            # only docs/tests changed: fast-forward quietly, no restart needed
            self._fast_forward()
            self._save_state()
            return None
        self._save_state()
        return info

    # ------------------------------------------------------------ apply
    def download(self, info: UpdateInfo, progress=None) -> Path:
        """Download and verify the source of ``info.remote_sha``. Returns the extracted project folder."""
        target = self.staging_root / info.remote_sha
        marker = target / ".verified"
        if marker.exists():
            return next(p for p in target.iterdir() if p.is_dir())
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
        archive = target / "source.tar.gz"
        with self.session.get(f"{self.codeload_base}/{self.repo}/tar.gz/{info.remote_sha}", stream=True,
                              timeout=(15, 120)) as r:
            if not r.ok:
                raise UpdateError(f"Download failed (HTTP {r.status_code})")
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            with open(archive, "wb") as fh:
                for block in r.iter_content(256 * 1024):
                    fh.write(block)
                    done += len(block)
                    if progress:
                        progress(done, total)
        root = target.resolve()
        with tarfile.open(archive) as tar:
            for m in tar.getmembers():
                dest = (target / m.name).resolve()
                if root not in dest.parents and dest != root:
                    raise UpdateError(f"Unsafe path in update archive: {m.name}")
                if m.issym() or m.islnk():
                    raise UpdateError(f"Links are not allowed in update archives: {m.name}")
            try:
                tar.extractall(target, filter="data")
            except TypeError:  # pragma: no cover - Python < 3.12
                tar.extractall(target)
        archive.unlink()
        dirs = [p for p in target.iterdir() if p.is_dir()]
        if len(dirs) != 1 or not (dirs[0] / PACKAGE / "__init__.py").is_file():
            raise UpdateError("The downloaded update does not look like Nebula")
        src = dirs[0]
        # integrity: every runtime file must match the commit's tree
        expected = {p: s for p, s in self.remote_tree(info.remote_sha).items() if is_runtime_path(p)}
        for path, sha in expected.items():
            f = src / path
            if not f.is_file() or git_blob_sha(f.read_bytes()) != sha:
                raise UpdateError(f"Downloaded file failed verification: {path}")
        marker.write_text(info.remote_sha)
        return src

    def apply(self, info: UpdateInfo, source: Path | None = None) -> str:
        """Install the update. Returns a short description of what was done."""
        mode = self.mode
        if mode == "git":
            self._fast_forward()
            result = f"Fast-forwarded to {info.remote_sha[:7]}"
        elif mode == "manual":
            raise UpdateError("This installation is read-only; reinstall Nebula to update")
        else:
            source = source or self.download(info)
            if mode == "pip":
                cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "--quiet", "--disable-pip-version-check"]
                if not info.dependencies_changed:
                    cmd.append("--no-deps")
                proc = subprocess.run(cmd + [str(source)], capture_output=True, text=True, timeout=900)
                if proc.returncode != 0:
                    raise UpdateError("pip failed: " + (proc.stderr.strip() or proc.stdout.strip())[-400:])
                result = f"Reinstalled from {info.remote_sha[:7]}"
            else:
                self._sync_files(info, source)
                result = f"Updated {len(info.files)} file(s)"
            dep = source / DEPENDENCY_FILE
            if dep.is_file():
                self.state["dependency_blob"] = git_blob_sha(dep.read_bytes())
        self.state.update(installed_sha=info.remote_sha, verified_sha=info.remote_sha, applied_at=time.time(),
                          last_update={"sha": info.remote_sha, "files": info.files, "commits": info.commits})
        self.state.pop("available", None)
        self._save_state()
        shutil.rmtree(self.staging_root, ignore_errors=True)
        return result

    def _fast_forward(self) -> None:
        if self._git("status", "--porcelain", "--untracked-files=no").strip():
            raise UpdateError("Your checkout has local changes; commit or stash them to receive updates")
        self._git("merge", "--ff-only", f"origin/{self.branch}")

    def _sync_files(self, info: UpdateInfo, source: Path) -> None:
        root = self.project_root.resolve()
        for path in info.added + info.changed:
            if path == DEPENDENCY_FILE:
                dest = self.project_root / DEPENDENCY_FILE
            else:
                dest = self.project_root / path
            if root not in dest.resolve().parents:
                raise UpdateError(f"Refusing to write outside the app folder: {path}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + ".nbupdate")
            shutil.copyfile(source / path, tmp)
            os.replace(tmp, dest)
        for path in info.removed:
            dest = self.project_root / path
            if root in dest.resolve().parents and dest.is_file():
                dest.unlink()


def preload_modules() -> int:
    """Import every module of the package now, so files replaced by an update are never
    half-loaded into a running process (the new code takes effect after a restart)."""
    import nebula

    count = 0
    for mod in pkgutil.walk_packages(nebula.__path__, "nebula."):
        if mod.name.startswith("nebula.__main__"):
            continue
        try:
            importlib.import_module(mod.name)
            count += 1
        except Exception as exc:  # noqa: BLE001
            log.debug("preload of %s failed: %s", mod.name, exc)
    return count


def restart_command() -> list[str]:
    """Command that starts the (updated) app again with the same arguments."""
    return [sys.executable, "-m", PACKAGE, *[a for a in sys.argv[1:] if a != "--after-update"], "--after-update"]
