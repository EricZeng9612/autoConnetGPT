#!/usr/bin/env python3
"""Explicit installation only; importing this module never changes launchd."""
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time

LABEL = "com.eric.autoConnetGPT"


def launchagent(program_dir, python):
    return {
        "Label": LABEL,
        "ProgramArguments": [str(python), str(program_dir / "autoConnetGPT.py"), "--daemon"],
        "RunAtLoad": True, "KeepAlive": True, "ProcessType": "Background",
        "ThrottleInterval": 30,
        # Application logs are rotated by the program. Avoid unbounded launchd logs.
        "StandardOutPath": "/dev/null", "StandardErrorPath": "/dev/null",
        "EnvironmentVariables": {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LC_ALL": "C"},
    }


def command(*args, check=True):
    return subprocess.run(["/bin/launchctl", *map(str, args)], capture_output=True,
                          text=True, timeout=30, check=check)


def atomic_bytes(path, data, mode=0o600):
    fd, name = tempfile.mkstemp(prefix=".install-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def manage(action):
    if sys.platform != "darwin" or sys.version_info < (3, 9):
        raise RuntimeError("Requires macOS and Python 3.9+ (install Python first).")
    source = Path(__file__).resolve().parent
    target = Path.home() / "Library/Application Support/autoConnetGPT"
    agents = Path.home() / "Library/LaunchAgents"
    agent = agents / (LABEL + ".plist")
    domain = "gui/" + str(os.getuid())
    service = domain + "/" + LABEL
    if action == "install":
        # Validate all inputs before stopping an existing installation.
        from autoConnetGPT import config_load
        config_load(target / "config.json")
        files = ["autoConnetGPT.py", "autoConnetGPT.zsh", "manage.py", "install.zsh",
                 "uninstall.zsh", "README.md", "SOP.md", "SECURITY.md", "LICENSE", "config.example.json"]
        payloads = {name: (source / name).read_bytes() for name in files}
        compile(payloads["autoConnetGPT.py"], "autoConnetGPT.py", "exec")
    loaded = command("print", service, check=False).returncode == 0
    if loaded:
        command("bootout", service)
        for _ in range(20):
            if command("print", service, check=False).returncode != 0:
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("Service did not stop; no files changed.")
    if action == "uninstall":
        # Keep config, history and logs recoverable; do not recursively delete.
        backup = Path(tempfile.mkdtemp(prefix="autoConnetGPT-uninstalled-", dir=str(target.parent)))
        if agent.exists():
            shutil.move(str(agent), str(backup / agent.name))
        if target.exists():
            shutil.move(str(target), str(backup / "installation"))
        print("Stopped service. Recoverable archive:", backup)
        print("Legacy logs in ~/Library/Logs/autoConnetGPT were not changed.")
        return
    # Preserve an existing version for manual rollback. Do not erase its configuration.
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    if (target / "autoConnetGPT.zsh").exists() or (target / "autoConnetGPT.py").exists():
        backup = Path(tempfile.mkdtemp(prefix="autoConnetGPT-backup-", dir=str(target.parent)))
        shutil.copytree(str(target), str(backup / "installation"))
        if agent.exists():
            shutil.copy2(str(agent), str(backup / agent.name))
        print("Previous version backed up to:", backup)
    os.chmod(target, 0o700)
    agents.mkdir(parents=True, exist_ok=True)
    for name, data in payloads.items():
        atomic_bytes(target / name, data, 0o700 if name.endswith((".py", ".zsh")) else 0o600)
    docs = target / "docs"
    docs.mkdir(exist_ok=True, mode=0o700)
    for doc in (source / "docs").glob("*.md"):
        atomic_bytes(docs / doc.name, doc.read_bytes())
    if not (target / "config.json").exists():
        atomic_bytes(target / "config.json", payloads["config.example.json"])
    python = str(Path(sys.executable).resolve())
    atomic_bytes(target / "python-path", (python + "\n").encode())
    plist = plistlib.dumps(launchagent(target, python))
    plistlib.loads(plist)
    atomic_bytes(agent, plist)
    command("enable", service)
    command("bootstrap", domain, agent)  # RunAtLoad already starts it; no duplicate kickstart.
    time.sleep(1)
    command("print", service)
    print("Installed autoConnetGPT 2.0.0. It performs an immediate check.")
    print("Configuration:", target / "config.json")
    print("Status:", str(target / "autoConnetGPT.zsh") + " --status")


if __name__ == "__main__":
    os.umask(0o077)
    if len(sys.argv) != 2 or sys.argv[1] not in ("install", "uninstall"):
        sys.exit("Usage: manage.py install|uninstall")
    try:
        manage(sys.argv[1])
    except Exception as exc:
        # Do not print raw subprocess/config contents, which may include secrets.
        sys.exit("Installation safely stopped (%s). Check Python, files and launchd permissions; "
                 "an existing service may be stopped. Backups, if created, are preserved." % type(exc).__name__)
