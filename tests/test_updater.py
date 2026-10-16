"""Self-updater against a fake GitHub (API + codeload) and a local git remote."""
import io
import json
import os
import shutil
import subprocess
import tarfile

import pytest

from nebula import updater as up

REPO = "BounciestSky152/Nebula-Launcher"


def write_tree(root, files: dict):
    for path, data in files.items():
        p = root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


class FakeGitHub:
    def __init__(self, server):
        self.server = server
        self.commits = {}  # sha -> files
        self.messages = {}
        self.head = None
        self.api_calls = 0
        server.handlers[("GET", f"/repos/{REPO}/")] = self.api
        server.handlers[("GET", f"/{REPO}/tar.gz/")] = self.tarball

    def commit(self, sha: str, files: dict, message: str = "change"):
        self.commits[sha] = files
        self.messages[sha] = message
        self.head = sha

    def api(self, req):
        self.api_calls += 1
        path = req.path.split("?")[0][len(f"/repos/{REPO}/"):]
        if path == "commits/main":
            if req.headers.get("If-None-Match") == f'"{self.head}"':
                return 304, {}, b""
            return 200, {"ETag": f'"{self.head}"'}, self.head.encode()
        if path.startswith("git/trees/"):
            sha = path.split("/")[-1]
            tree = [{"path": p, "type": "blob", "sha": up.git_blob_sha(d)} for p, d in self.commits[sha].items()]
            return 200, {}, json.dumps({"sha": sha, "tree": tree, "truncated": False}).encode()
        if path.startswith("compare/"):
            base, head = path[len("compare/"):].split("...")
            shas = list(self.commits)
            commits = shas[shas.index(base) + 1: shas.index(head) + 1] if base in shas else [head]
            return 200, {}, json.dumps({"commits": [{"sha": s, "commit": {"message": self.messages[s]}}
                                                    for s in commits]}).encode()
        if path.startswith("commits/"):
            sha = path.split("/")[-1]
            return 200, {}, json.dumps({"sha": sha, "commit": {"message": self.messages[sha]}}).encode()
        return 404, {}, b""

    def tarball(self, req):
        sha = req.path.rsplit("/", 1)[-1]
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for p, d in self.commits[sha].items():
                ti = tarfile.TarInfo(f"Nebula-Launcher-{sha[:7]}/{p}")
                ti.size = len(d)
                tar.addfile(ti, io.BytesIO(d))
        return 200, {}, buf.getvalue()


V1 = {
    "nebula/__init__.py": b"__version__ = '1'\n",
    "nebula/core/a.py": b"A = 1\n",
    "nebula/core/old.py": b"OLD = 1\n",
    "pyproject.toml": b"[project]\nname='nebula'\n",
    "README.md": b"# readme\n",
    "tests/test_x.py": b"",
}


@pytest.fixture()
def gh(server):
    return FakeGitHub(server)


def make(tmp_path, server, **kw):
    return up.Updater(REPO, "main", state_path=tmp_path / "state.json", package_dir=tmp_path / "app" / "nebula",
                      api_base=server.base, codeload_base=server.base, git_base=server.base, **kw)


def test_runtime_path_filter():
    assert up.is_runtime_path("nebula/ui/theme.py") and up.is_runtime_path("pyproject.toml")
    for p in ("README.md", "docs/screenshots/home.png", "tests/test_ui.py", "scripts/install.sh",
              "nebula/__pycache__/x.cpython-313.pyc", "nebula/x.pyc"):
        assert not up.is_runtime_path(p)


def test_files_mode_detects_only_necessary_changes_and_applies(tmp_path, server, gh):
    write_tree(tmp_path / "app", V1)
    gh.commit("a" * 40, V1, "initial")
    u = make(tmp_path, server)
    assert u.mode == "files"
    assert u.check() is None, "identical runtime files -> up to date"
    assert u.state["installed_sha"] == "a" * 40

    # docs-only commit: no update
    docs = dict(V1, **{"README.md": b"# new readme\n", "docs/x.png": b"png"})
    gh.commit("b" * 40, docs, "docs: refresh screenshots")
    assert u.check() is None
    assert u.state["installed_sha"] == "b" * 40

    # real change: modify + add + remove + dependency change
    v3 = dict(docs)
    v3["nebula/core/a.py"] = b"A = 2\n"
    v3["nebula/core/new.py"] = b"NEW = 1\n"
    del v3["nebula/core/old.py"]
    v3["pyproject.toml"] = b"[project]\nname='nebula'\ndependencies=['requests']\n"
    gh.commit("c" * 40, v3, "feat: new thing")
    info = u.check()
    assert info is not None
    assert set(info.changed) == {"nebula/core/a.py", "pyproject.toml"}
    assert info.added == ["nebula/core/new.py"] and info.removed == ["nebula/core/old.py"]
    assert info.dependencies_changed
    assert ("c" * 40, "feat: new thing") in info.commits
    result = u.apply(info)
    assert "file" in result
    app = tmp_path / "app"
    assert (app / "nebula/core/a.py").read_bytes() == b"A = 2\n"
    assert (app / "nebula/core/new.py").exists() and not (app / "nebula/core/old.py").exists()
    assert (app / "README.md").read_bytes() == b"# readme\n", "non-runtime files are never touched"
    assert u.check() is None and u.state["installed_sha"] == "c" * 40
    # unchanged head uses a conditional request
    assert u.check() is None


def test_tampered_download_is_rejected(tmp_path, server, gh, monkeypatch):
    write_tree(tmp_path / "app", V1)
    gh.commit("a" * 40, V1)
    v2 = dict(V1, **{"nebula/core/a.py": b"A = 3\n"})
    gh.commit("d" * 40, v2)
    u = make(tmp_path, server)
    info = u.check()
    real = gh.commits["d" * 40]
    gh.commits["d" * 40] = dict(real, **{"nebula/core/a.py": b"import os; os.system('evil')\n"})
    # tree still advertises the genuine hash -> download must fail verification
    monkeypatch.setattr(u, "remote_tree", lambda sha: {p: up.git_blob_sha(d) for p, d in real.items()})
    with pytest.raises(up.UpdateError, match="verification"):
        u.apply(info)
    assert (tmp_path / "app/nebula/core/a.py").read_bytes() == b"A = 1\n"


def test_api_failure_falls_back_to_git_protocol(tmp_path, server, gh):
    write_tree(tmp_path / "app", V1)
    gh.commit("e" * 40, V1)
    server.handlers[("GET", f"/repos/{REPO}/commits/main")] = lambda req: (403, {}, b"rate limited")
    pkt = (b"001e# service=git-upload-pack\n0000"
           b"003f" + b"e" * 40 + b" refs/heads/main\n0000")
    server.files[f"/{REPO}.git/info/refs?service=git-upload-pack"] = pkt
    server.handlers[("GET", f"/{REPO}.git/info/refs")] = lambda req: (200, {}, pkt)
    u = make(tmp_path, server)
    assert u.remote_head() == "e" * 40


def test_pip_mode_command(tmp_path, server, gh, monkeypatch):
    write_tree(tmp_path / "app", V1)
    gh.commit("a" * 40, V1)
    gh.commit("f" * 40, dict(V1, **{"nebula/core/a.py": b"A = 9\n"}))
    u = make(tmp_path, server)
    u._mode = "pip"  # noqa: SLF001
    info = u.check()
    calls = []

    class Done:
        returncode = 0
        stdout = stderr = ""

    monkeypatch.setattr(up.subprocess, "run", lambda cmd, **kw: (calls.append(cmd), Done())[1])
    u.apply(info)
    cmd = calls[0]
    assert cmd[1:4] == ["-m", "pip", "install"] and "--no-deps" in cmd
    assert cmd[-1].endswith("Nebula-Launcher-fffffff")
    assert u.state["installed_sha"] == "f" * 40


@pytest.mark.skipif(not shutil.which("git"), reason="git required")
def test_git_mode(tmp_path):
    def git(*args, cwd):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=main", *args],
                       cwd=cwd, check=True, capture_output=True)

    remote = tmp_path / "BounciestSky152" / "Nebula-Launcher.git"
    remote.mkdir(parents=True)
    git("init", "--bare", "-b", "main", cwd=remote)
    work = tmp_path / "work"
    work.mkdir()
    git("init", "-b", "main", cwd=work)
    write_tree(work, V1)
    git("add", "-A", cwd=work)
    git("commit", "-m", "v1", cwd=work)
    git("remote", "add", "origin", str(remote), cwd=work)
    git("push", "origin", "main", cwd=work)
    app = tmp_path / "app"
    git("clone", str(remote), str(app), cwd=tmp_path)
    u = up.Updater(REPO, "main", state_path=tmp_path / "state.json", package_dir=app / "nebula")
    assert u.mode == "git"
    assert u.check() is None
    # docs-only commit: fast-forwarded silently, no update reported
    (work / "README.md").write_text("# docs\n")
    git("commit", "-am", "docs", cwd=work)
    git("push", "origin", "main", cwd=work)
    assert u.check() is None
    assert (app / "README.md").read_text() == "# docs\n"
    # runtime change
    (work / "nebula/core/a.py").write_text("A = 42\n")
    git("commit", "-am", "fix: answer", cwd=work)
    git("push", "origin", "main", cwd=work)
    info = u.check()
    assert info and info.changed == ["nebula/core/a.py"] and info.commits[0][1] == "fix: answer"
    u.apply(info)
    assert (app / "nebula/core/a.py").read_text() == "A = 42\n"
    # local edits block updates instead of being overwritten
    (work / "nebula/core/a.py").write_text("A = 43\n")
    git("commit", "-am", "again", cwd=work)
    git("push", "origin", "main", cwd=work)
    (app / "nebula/core/a.py").write_text("my local hack\n")
    info = u.check()
    with pytest.raises(up.UpdateError, match="local changes"):
        u.apply(info)
    assert (app / "nebula/core/a.py").read_text() == "my local hack\n"


def test_preload_and_restart_command():
    assert up.preload_modules() > 20
    cmd = up.restart_command()
    assert cmd[1:3] == ["-m", "nebula"] and cmd[-1] == "--after-update"
    assert os.path.basename(cmd[0]).startswith("python")
