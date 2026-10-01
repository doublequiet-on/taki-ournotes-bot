# L3
# Input: 命令行模式、仓库 Git 状态、候选提交与 CI 结果、版本状态、本地环境及运行进程。
# Output: 按模式产生检查记录、隔离版本环境、部署／事务状态或启动与恢复后的实例。
# Pos: L2.md 的 Windows 更新与受管运行入口；部署状态协议的写入方。
# Effects/Dependencies: Git/GitHub、安装与文件写入、Windows 任务／进程控制及工作树快进；隔离验证清空 QQ/AI 凭据，受管运行会连接 QQ。

"""Windows single-instance updater. No QQ messages are sent by validation."""
from __future__ import annotations

import argparse
import base64
import contextlib
import datetime as dt
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
import uuid

REPOSITORY = "doublequiet-on/taki-ournotes-bot"
SHA = re.compile(r"[0-9a-f]{40}\Z")
HIDDEN = 0x08000000 if os.name == "nt" else 0


class Paused(RuntimeError):
    """A safe, non-secret diagnostic suitable for the operator."""


def run(args, *, cwd=None, env=None, timeout=120):
    result = subprocess.run(args, cwd=cwd, env=env, capture_output=True,
                            timeout=timeout, creationflags=HIDDEN)
    if result.returncode:
        # Command output can contain credentials, proxy URLs and private paths.
        raise Paused("外部命令失败；未输出原始响应，请检查网络或本地环境")
    return result.stdout.decode("utf-8-sig", errors="replace").strip()


def atomic_json(path, value):
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, OSError):
        raise Paused("运行状态文件无法读取；已暂停，请人工检查，不会自动重置") from None


@contextlib.contextmanager
def exclusive(path):
    with path.open("a+b") as handle:
        handle.seek(0)
        if path.stat().st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise Paused("已有检查或更新正在进行，本次跳过") from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def validation_env():
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC",
               "SYSTEMDRIVE", "PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA",
               "APPDATA", "USERPROFILE", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
               "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"}
    result = {k: v for k, v in os.environ.items() if k.upper() in allowed}
    result.update(QQ_APP_ID="", QQ_APP_SECRET="", AI_API_KEY="", PYTHONUTF8="1",
                  PYTHONDONTWRITEBYTECODE="1", PIP_DISABLE_PIP_VERSION_CHECK="1")
    return result


def api(path):
    request = urllib.request.Request("https://api.github.com/repos/" + REPOSITORY + path,
                                     headers={"User-Agent": "Taki-Updater", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def ci_passed(commit, get=api):
    runs = get("/actions/workflows/checks.yml/runs?event=push&branch=main&head_sha=" + commit + "&per_page=100")["workflow_runs"]
    runs = [r for r in runs if r.get("head_sha") == commit and r.get("head_branch") == "main"
            and r.get("event") == "push"]
    if not runs:
        return False
    latest = max(runs, key=lambda r: (r["id"], r.get("run_attempt", 1)))
    if latest.get("status") != "completed" or latest.get("conclusion") != "success":
        return False
    jobs = get(f"/actions/runs/{latest['id']}/attempts/{latest.get('run_attempt', 1)}/jobs?per_page=100")["jobs"]
    successful = {j["name"] for j in jobs if j.get("conclusion") == "success"}
    return {"test-and-package (3.10)", "test-and-package (3.12)"} <= successful


def extract_snapshot(blob, destination):
    with tarfile.open(fileobj=io.BytesIO(blob)) as archive:
        for member in archive.getmembers():
            path = PurePosixPath(member.name)
            if (path.is_absolute() or ".." in path.parts or "\\" in member.name
                    or ":" in member.name or not (member.isfile() or member.isdir())):
                raise Paused("版本快照包含不支持的路径或链接，已暂停")
            leaf = path.name
            if (leaf == ".env" or (leaf.startswith(".env.") and leaf != ".env.example")
                    or ".venv" in path.parts or (path.parts[0] == "data" and leaf not in {"data", ".gitkeep"})):
                raise Paused("版本快照包含运行配置或数据，已暂停")
        # All entries validated; avoid version-dependent tarfile filter APIs.
        for member in archive.getmembers():
            target = destination.joinpath(*PurePosixPath(member.name).parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.extractfile(member).read())


def windows_processes():
    if os.name != "nt":
        raise Paused("自动部署目前仅支持 Windows；其他平台仅运行离线测试")
    raw = run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
               "@(Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" | "
               "Select-Object ProcessId,ParentProcessId,ExecutablePath,CommandLine,CreationDate) | ConvertTo-Json -Compress"], timeout=30)
    value = json.loads(raw or "[]")
    return value if isinstance(value, list) else [value]


def argv_windows(command):
    import ctypes
    from ctypes import wintypes
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    shell.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    shell.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    count = ctypes.c_int()
    ptr = shell.CommandLineToArgvW(command, ctypes.byref(count))
    if not ptr:
        return []
    try:
        return [ptr[i] for i in range(count.value)]
    finally:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree(ptr)


def processes(root):
    """Select only this checkout's runtime; python venv parent/child is one unit."""
    found = []
    rows = windows_processes()
    legacy = []
    for row in rows:
        args = argv_windows(row.get("CommandLine") or "")
        managed = False
        if "--run-bot" in args and "--project-root" in args:
            i = args.index("--project-root")
            managed = i + 1 < len(args) and Path(args[i + 1]).resolve() == root
        exe = Path(row.get("ExecutablePath") or "_").resolve()
        old = (exe == (root / ".venv/Scripts/python.exe").resolve()
               and any(args[i:i+3] == ["-m", "ournotes_bot.main", "bot"] for i in range(len(args))))
        if managed or old:
            found.append(row)
        if old:
            legacy.append(row["ProcessId"])
    for row in rows:
        if row["ParentProcessId"] in legacy and row not in found:
            args = argv_windows(row.get("CommandLine") or "")
            if any(args[i:i+3] == ["-m", "ournotes_bot.main", "bot"] for i in range(len(args))):
                found.append(row)
    ids = {p["ProcessId"] for p in found}
    for row in rows:
        args = argv_windows(row.get("CommandLine") or "")
        is_query = any(args[i:i+3] == ["-m", "ournotes_bot.main", "bot"] for i in range(len(args)))
        if is_query and row["ProcessId"] not in ids:
            raise Paused("存在无法确认归属的查询进程，已暂停，避免重复登录")
    if sum(p["ParentProcessId"] not in ids for p in found) > 1:
        raise Paused("检测到多个 Taki 实例，已暂停；请先人工检查")
    return found


def stop(root):
    selected = processes(root)
    # Kill children before the venv launcher, checking creation time to avoid PID reuse.
    ids = {p["ProcessId"] for p in selected}
    for row in sorted(selected, key=lambda p: p["ParentProcessId"] not in ids):
        now = {p["ProcessId"]: p for p in windows_processes()}
        if row["ProcessId"] not in now:
            continue
        if now[row["ProcessId"]]["CreationDate"] != row["CreationDate"]:
            raise Paused("进程发生变化，已暂停停止操作")
        run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
             f"Stop-Process -Id {int(row['ProcessId'])} -ErrorAction Stop"], timeout=30)
    if processes(root):
        raise Paused("旧进程未完全退出，未启动第二个实例")


def launch_runtime(root, release, token):
    """A separate task prevents the updater task's job from owning the long-lived bot."""
    pythonw = Path(release["python"]).with_name("pythonw.exe")
    if not pythonw.is_file():
        raise Paused("运行环境缺少 pythonw.exe")
    arguments = subprocess.list2cmdline(["-B", "-u", "-X", "utf8", str(Path(__file__).resolve()),
                                       "--project-root", str(root), "--run-bot", release["source"], "--token", token])
    def quote(value):
        return "'" + str(value).replace("'", "''") + "'"
    script = f"""
$ErrorActionPreference = 'Stop'
$name = 'Taki-OurNotes-Runtime'
$existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
if ($existing -and -not $existing.Actions[0].Arguments.Contains({quote(root)})) {{ throw 'Runtime task belongs to another checkout' }}
if ($existing -and $existing.State -eq 'Running') {{
    Stop-ScheduledTask -TaskName $name
    Start-Sleep -Seconds 1
}}
$action = New-ScheduledTaskAction -Execute {quote(pythonw)} -Argument {quote(arguments)} -WorkingDirectory {quote(root)}
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $name -Action $action -Principal $principal -Settings $settings -Description 'Taki runtime managed by the updater; no independent trigger.' -Force | Out-Null
Start-ScheduledTask -TaskName $name
"""
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded], timeout=60)


class Updater:
    def __init__(self, root):
        self.root = root.resolve()
        self.folder = self.root / "data/updater"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.state_file = self.folder / "state.json"
        self.journal = self.folder / "transaction.json"

    def event(self, message):
        # Callers pass static messages or validated SHA only, never raw exceptions.
        stamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        atomic_json(self.folder / "status.json", {"time_utc": stamp, "message": message})
        log = self.folder / "updater.log"
        if log.exists() and log.stat().st_size > 1_000_000:
            os.replace(log, self.folder / "updater.previous.log")
        with log.open("a", encoding="utf-8") as handle:
            handle.write(stamp + " " + message + "\n")
        print(message, flush=True)

    def git(self, *args):
        env = os.environ.copy()
        env.update(GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="Never")
        return run([self.git_path(), "-c", "http.sslBackend=openssl", "-c", "credential.helper=", *args],
                   cwd=self.root, env=env, timeout=180)

    def git_path(self):
        settings = self.folder / "settings.json"
        configured = read_json(settings).get("git_executable") if settings.exists() else None
        executable = configured or shutil.which("git")
        if not executable or not Path(executable).is_file():
            raise Paused("找不到 Git；请重新运行计划任务安装脚本，记录可用的 Git 位置")
        return str(executable)

    def clean(self):
        if self.git("branch", "--show-current") != "main":
            raise Paused("当前不是 main 分支，已暂停更新")
        if self.git("status", "--porcelain", "--untracked-files=all"):
            raise Paused("本地有未提交修改或新文件，已暂停更新，不会覆盖")
        if self.git("remote", "get-url", "origin") not in {
            f"https://github.com/{REPOSITORY}.git", f"git@github.com:{REPOSITORY}.git"}:
            raise Paused("origin 与预期仓库不一致，已暂停")
        commit = self.git("rev-parse", "HEAD")
        if not SHA.fullmatch(commit):
            raise Paused("无法识别版本编号")
        return commit

    def snapshot(self, commit):
        if not SHA.fullmatch(commit):
            raise Paused("无效版本编号")
        directory = self.folder / "releases" / (commit[:12] + "-" + uuid.uuid4().hex[:8])
        source = directory / "source"
        source.mkdir(parents=True)
        result = subprocess.run([self.git_path(), "archive", "--format=tar", commit], cwd=self.root,
                                capture_output=True, creationflags=HIDDEN, timeout=60)
        if result.returncode:
            raise Paused("无法生成版本快照")
        extract_snapshot(result.stdout, source)
        return {"sha": commit, "source": str(source), "python": str(self.root / ".venv/Scripts/python.exe")}

    def prepare(self, commit):
        release = self.snapshot(commit)
        source = Path(release["source"])
        venv = source.parent / "venv"
        self.event("正在隔离目录安装和验证新版本；旧机器人继续运行")
        env = validation_env()
        env["PATH"] = str(Path(self.git_path()).parent) + os.pathsep + env.get("PATH", "")
        run([sys.executable, "-m", "venv", str(venv)], env=env, timeout=180)
        python = venv / "Scripts/python.exe"
        self.event("隔离环境已创建，正在安装依赖")
        run([str(python), "-m", "pip", "install", str(source)], cwd=source, env=env, timeout=600)
        self.event("依赖已安装，正在运行离线测试与帮助检查")
        env["PYTHONPATH"] = str(source / "src")
        run([str(python), "-B", "-m", "unittest", "discover", "-s", "tests", "-q"], cwd=source, env=env, timeout=300)
        run([str(python), "-B", "-m", "ournotes_bot.main", "--help"], cwd=source, env=env)
        release["python"] = str(python)
        return release

    def validate_release(self, release):
        if not SHA.fullmatch(release.get("sha", "")):
            raise Paused("版本状态损坏，已暂停")
        source = Path(release["source"]).resolve()
        python = Path(release["python"]).resolve()
        if (not source.is_relative_to((self.folder / "releases").resolve())
                or not source.is_dir() or not python.is_file()
                or not (python.is_relative_to(self.folder.resolve())
                        or python == (self.root / ".venv/Scripts/python.exe").resolve())):
            raise Paused("版本运行路径无效，已暂停")

    def start(self, release):
        self.validate_release(release)
        if processes(self.root):
            raise Paused("机器人仍在运行，拒绝启动第二个实例")
        token = uuid.uuid4().hex
        ready = self.folder / ("ready-" + token)
        launch_runtime(self.root, release, token)
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if ready.exists():
                time.sleep(5)
                if processes(self.root):
                    return
            time.sleep(1)
        raise Paused("新实例未在 90 秒内连接 QQ，准备恢复上一版")

    def restore(self, transaction):
        previous = transaction["previous"]
        self.validate_release(previous)
        stop(self.root)
        self.start(previous)
        state = transaction.get("old_state", {})
        state.update(active=previous, rejected=transaction["candidate"]["sha"])
        atomic_json(self.state_file, state)
        self.journal.unlink(missing_ok=True)
        self.event("已恢复上一运行版本；问题版本暂停自动重试，缓存和额度未回滚")

    def deploy(self, candidate, previous, state, head):
        if self.clean() != head:
            raise Paused("验证期间本地版本已变化，已暂停切换")
        transaction = {"previous": previous, "candidate": candidate, "old_state": state}
        atomic_json(self.journal, transaction)
        try:
            stop(self.root)
            self.start(candidate)
            # Only fast-forward, never reset/stash or overwrite ignored local files.
            if self.clean() != head:
                raise Paused("切换期间本地版本发生变化，恢复上一运行版本")
            self.git("merge", "--ff-only", "--no-overwrite-ignore", candidate["sha"])
            atomic_json(self.state_file, {"active": candidate, "previous": previous, "rejected": None})
            self.journal.unlink()
        except Exception:
            self.restore(transaction)
            raise Paused("更新未完成；上一运行版本已恢复") from None
        self.event("更新成功：" + candidate["sha"][:12] + "；已确认 QQ 网关上线，未发送测试消息")

    def once(self, *, initialize=False, check=False, start_only=False, retry=False):
        head = self.clean()
        if self.journal.exists():
            if check:
                raise Paused("存在未完成更新，等待恢复；本次仅检查")
            self.restore(read_json(self.journal))
            return
        state = read_json(self.state_file) if self.state_file.exists() else {}
        if state:
            self.validate_release(state["active"])
            if not check and not processes(self.root):
                self.start(state["active"])
                self.event("已启动保存的运行版本")
        elif not initialize:
            raise Paused("尚未初始化自动更新，请先运行安装步骤")
        if start_only:
            self.event("已检查运行实例；未启动重复实例")
            return
        self.git("fetch", "--no-tags", "origin", "refs/heads/main:refs/remotes/origin/main")
        target = self.git("rev-parse", "refs/remotes/origin/main")
        if not SHA.fullmatch(target):
            raise Paused("远端版本编号无效")
        self.git("merge-base", "--is-ancestor", head, target)
        if state and target == state["active"]["sha"]:
            self.event("运行中；main 暂无新版本")
            return
        if state.get("rejected") == target and not retry:
            raise Paused("此版本之前更新失败，暂停重试；等待新提交或手动 --retry")
        if not ci_passed(target):
            raise Paused("目标版本的 GitHub Checks 尚未全部通过，保持当前版本")
        if check:
            self.event("发现可验证的新版本：" + target[:12] + "；尚未部署")
            return
        previous = state.get("active") or self.snapshot(head)
        try:
            candidate = self.prepare(target)
        except Exception:
            # A network outage may be transient; do not retry an expensive build every 5 min.
            if state:
                state["rejected"] = target
                atomic_json(self.state_file, state)
            raise Paused("隔离安装或测试失败；原实例未停止，请检查后用 --retry 重试") from None
        self.deploy(candidate, previous, state, head)


def run_bot(root, source, token):
    if not re.fullmatch(r"[0-9a-f]{32}", token):
        raise Paused("无效启动标识")
    import logging
    import runpy
    # pythonw has no stdout/stderr; keep diagnostics local to the ignored data folder.
    logfile = (root / "data/updater" / ("runtime-" + token + ".log")).open("a", encoding="utf-8", buffering=1)
    sys.stdout = logfile
    sys.stderr = logfile
    sys.path.insert(0, str(Path(source) / "src"))
    from ournotes_bot import config
    config.CONFIG_ROOT = root
    config.SOURCE_ROOT = root
    class Ready(logging.Handler):
        def emit(self, record):
            if record.name == "ournotes_bot.qq" and "已上线" in record.getMessage():
                (root / "data/updater" / ("ready-" + token)).touch()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("ournotes_bot.qq").addHandler(Ready())
    sys.argv = ["ournotes-bot", "bot"]
    runpy.run_module("ournotes_bot.main", run_name="__main__")


def main():
    parser = argparse.ArgumentParser(description="Taki Windows 自动更新：只部署 main 上通过 Checks 的版本")
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    group = parser.add_mutually_exclusive_group()
    for name in ("once", "initialize", "check", "start", "status"):
        group.add_argument("--" + name, action="store_true")
    parser.add_argument("--retry", action="store_true")
    parser.add_argument("--run-bot", help=argparse.SUPPRESS)
    parser.add_argument("--token", help=argparse.SUPPRESS)
    args = parser.parse_args()
    updater = Updater(args.project_root)
    if args.run_bot:
        run_bot(updater.root, args.run_bot, args.token or "")
        return
    if args.status:
        status = updater.folder / "status.json"
        print(json.dumps(read_json(status), ensure_ascii=False, indent=2) if status.exists() else "尚无检查记录")
        return
    try:
        with exclusive(updater.folder / "update.lock"):
            updater.once(initialize=args.initialize, check=args.check, start_only=args.start, retry=args.retry)
    except Paused as error:
        updater.event(str(error))
        raise SystemExit(1)
    except Exception:
        updater.event("检查或更新异常，已停止本次操作；如有未完成切换，下次检查尝试恢复")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
