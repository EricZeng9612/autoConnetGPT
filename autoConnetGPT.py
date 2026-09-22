#!/usr/bin/env python3
"""Bounded macOS connectivity watchdog. Python 3.9+, standard library only."""
import argparse
import contextlib
import fcntl
import http.client
import json
import logging
from logging.handlers import RotatingFileHandler
import math
import os
from pathlib import Path
import re
import shlex
import signal
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote, urlencode, urlsplit

VERSION = "2.0.0"
DEFAULT_DIR = Path.home() / "Library/Application Support/autoConnetGPT"
DEFAULTS = {
    "interval_seconds": 3600, "proxy_url": "http://127.0.0.1:7897",
    "basic_urls": ["https://www.baidu.com/", "https://www.qq.com/"],
    "general_url": "https://www.gstatic.com/generate_204",
    "chatgpt_url": "https://chatgpt.com/",
    "controller_socket": "/tmp/verge/verge-mihomo.sock",
    "controller_url": "http://127.0.0.1:9090", "controller_secret_file": "",
    "selector_group": "", "singapore_nodes": [],
    "allow_singapore_name_match": False, "max_candidates": 20,
    "latency_samples": 3, "max_switches": 3,
    "remote_evidence_file": "", "task_evidence_file": "", "notifications": True,
}


class GuardError(Exception):
    pass


class Halt(Exception):
    def __init__(self, result, detail):
        self.result, self.detail = result, detail


def read_json(path):
    with Path(path).open(encoding="utf-8") as stream:
        raw = stream.read(2_000_001)
    if len(raw) > 2_000_000:
        raise GuardError("JSON too large")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise GuardError("Expected JSON object")
    return value


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(prefix=".auto-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def config_load(path):
    cfg = dict(DEFAULTS)
    if Path(path).exists():
        supplied = read_json(path)
        if set(supplied) - set(cfg):
            raise GuardError("Unknown configuration keys")
        cfg.update(supplied)
    for key, limits in {"interval_seconds": (60, 86400), "max_candidates": (1, 50),
                        "latency_samples": (1, 5), "max_switches": (1, 3)}.items():
        if type(cfg[key]) is not int or not limits[0] <= cfg[key] <= limits[1]:
            raise GuardError("Invalid " + key)
    for key in ("notifications", "allow_singapore_name_match"):
        if type(cfg[key]) is not bool:
            raise GuardError("Invalid " + key)
    for key in ("basic_urls", "singapore_nodes"):
        if not isinstance(cfg[key], list) or not all(isinstance(x, str) and x for x in cfg[key]):
            raise GuardError("Invalid " + key)
    if not 1 <= len(cfg["basic_urls"]) <= 3:
        raise GuardError("Use one to three basic_urls")
    for key in set(cfg) - {"basic_urls", "singapore_nodes", "notifications",
                           "allow_singapore_name_match", "interval_seconds", "max_candidates",
                           "latency_samples", "max_switches"}:
        if not isinstance(cfg[key], str):
            raise GuardError("Invalid " + key)
    for url in cfg["basic_urls"] + [cfg["general_url"], cfg["chatgpt_url"]]:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise GuardError("Probes require credential-free HTTPS URLs")
    for key in ("proxy_url", "controller_url"):
        parsed = urlsplit(cfg[key])
        if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "::1", "localhost")
                or not parsed.port or parsed.username or parsed.password
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            raise GuardError(key + " must be a loopback HTTP endpoint")
    for key in ("controller_socket", "controller_secret_file", "remote_evidence_file", "task_evidence_file"):
        if cfg[key] and not Path(cfg[key]).is_absolute():
            raise GuardError(key + " must be an absolute path")
    return cfg


@contextlib.contextmanager
def file_lock(path):
    # Never unlink flock files: an open inode remains the lock identity after crashes.
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
        else:
            yield True
    finally:
        os.close(fd)


class UnixHTTP(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost", timeout=8)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


class Mihomo:
    def __init__(self, cfg):
        self.cfg = cfg

    def request(self, method, path, body=None):
        cfg = self.cfg
        if cfg["controller_socket"] and Path(cfg["controller_socket"]).exists():
            conn = UnixHTTP(cfg["controller_socket"])
        else:
            url = urlsplit(cfg["controller_url"])
            conn = http.client.HTTPConnection(url.hostname, url.port, timeout=8)
        headers = {"Content-Type": "application/json"}
        if cfg["controller_secret_file"]:
            secret = Path(cfg["controller_secret_file"])
            info = secret.stat()
            if info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise GuardError("Controller secret must be owned by you with mode 600")
            token = secret.read_text().strip()
            if not token or len(token) > 4096 or "\n" in token or "\r" in token:
                raise GuardError("Invalid controller secret file")
            headers["Authorization"] = "Bearer " + token
        try:
            conn.request(method, path, json.dumps(body) if body is not None else None, headers)
            res = conn.getresponse()
            raw = res.read(2_000_001)
            if res.status not in (200, 204) or len(raw) > 2_000_000:
                raise GuardError("Controller request rejected")
            return json.loads(raw) if raw else {}
        finally:
            conn.close()

    def proxies(self):
        return self.request("GET", "/proxies")["proxies"]

    def group(self, name):
        return self.request("GET", "/proxies/" + quote(name, safe=""))

    def select(self, group, node):
        self.request("PUT", "/proxies/" + quote(group, safe=""), {"name": node})
        if self.group(group).get("now") != node:
            raise GuardError("Selection did not take effect")

    def delay(self, node, url):
        query = urlencode({"url": url, "timeout": 5000, "expected": "200-299"})
        result = self.request("GET", "/proxies/" + quote(node, safe="") + "/delay?" + query)
        delay = result.get("delay")
        if not number(delay) or delay <= 0 or delay > 5000:
            raise GuardError("Node unavailable")
        return delay


class Mac:
    @staticmethod
    def run(argv, timeout=20):
        env = dict(os.environ, LC_ALL="C", LANG="C")
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env)

    def http(self, url, proxy=None):
        args = ["/usr/bin/curl", "--disable", "--silent", "--output", "/dev/null",
                "--write-out", "%{http_code}", "--connect-timeout", "5", "--max-time", "12",
                "--proto", "=https", "--proxy", proxy or "", "--noproxy", "" if proxy else "*", url]
        try:
            res = self.run(args, 15)
            if res.returncode:
                return "failed"
            code = int(res.stdout)
            return "ok" if 200 <= code < 400 else "unknown"
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return "failed"

    def basic(self, cfg):
        probes = [self.http(url) for url in cfg["basic_urls"]]
        if "ok" in probes:
            return "ok"
        return "failed" if all(x == "failed" for x in probes) else "unknown"

    def link(self, cfg):
        probes = [self.http(cfg[key], cfg["proxy_url"]) for key in ("general_url", "chatgpt_url")]
        if "unknown" in probes:
            return "unknown"  # 403/429/5xx cannot establish a VPN transport fault.
        return "ok" if all(x == "ok" for x in probes) else "failed"

    def processes(self):
        res = self.run(["/bin/ps", "-axo", "pid=,ppid=,lstart=,args="])
        if res.returncode:
            raise GuardError("Cannot inspect processes")
        return self.parse_processes(res.stdout)

    @staticmethod
    def parse_processes(raw):
        rows = []
        for line in raw.splitlines():
            parts = line.strip().split(None, 7)
            if len(parts) != 8:
                continue
            try:
                started = time.mktime(time.strptime(" ".join(parts[2:7]), "%a %b %d %H:%M:%S %Y"))
                rows.append({"pid": int(parts[0]), "ppid": int(parts[1]),
                             "started": started, "command": parts[7]})
            except ValueError:
                continue
        return rows

    def app(self):
        rows = self.processes()
        mains = [p for p in rows if p["command"].split(" -", 1)[0] == "/Applications/ChatGPT.app/Contents/MacOS/ChatGPT"]
        if len(mains) != 1:
            return None, False
        main = mains[0]
        server = any(p["ppid"] == main["pid"] and self.is_app_server(p["command"]) for p in rows)
        return main, server

    @staticmethod
    def is_app_server(command):
        executable = "/Applications/ChatGPT.app/Contents/Resources/codex"
        if not command.startswith(executable + " "):
            return False
        try:
            # Current builds place global -c flags BEFORE the app-server subcommand.
            tokens = shlex.split(command[len(executable):])
            index = 0
            while index < len(tokens):
                token = tokens[index]
                if token in ("-c", "--config", "--enable", "--disable"):
                    index += 2
                elif token.startswith(("--config=", "--enable=", "--disable=")):
                    index += 1
                else:
                    return token == "app-server"
            return False
        except ValueError:
            return False

    def core(self, cfg):
        running = any(p["command"].startswith("/Applications/Clash Verge.app/")
                      and "/verge-mihomo" in p["command"] for p in self.processes())
        if not running:
            return False
        url = urlsplit(cfg["proxy_url"])
        try:
            with socket.create_connection((url.hostname, url.port), timeout=3):
                return True
        except OSError:
            return False

    def start(self, app):
        return self.run(["/usr/bin/open", "-g", "-j", "-a", app], 15).returncode == 0

    def restart(self, app):
        # Quit the app, NOT the account. Never force-kill a process.
        result = self.run(["/usr/bin/osascript", "-e", 'tell application "' + app + '" to quit'], 15)
        if result.returncode:
            return False
        prefix = "/Applications/" + app + ".app/Contents/MacOS/"
        for _ in range(10):
            if not any(p["command"].startswith(prefix) for p in self.processes()):
                return self.start(app)
            time.sleep(1)
        return False

    def sleep_protected(self):
        raw = self.run(["/usr/bin/pmset", "-g", "assertions"]).stdout
        return bool(re.search(r"PreventUserIdleSystemSleep\s+1", raw)
                    or re.search(r"PreventSystemSleep\s+1", raw))

    def notify(self, message):
        script = 'on run argv\ndisplay notification (item 1 of argv) with title "Auto Connect GPT"\nend run'
        try:
            self.run(["/usr/bin/osascript", "-e", script, message], 5)
        except (OSError, subprocess.TimeoutExpired):
            pass


def fresh_evidence(path, proc, now, ttl):
    if not path or not proc:
        return None
    try:
        info = Path(path).stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            return None
        value = read_json(path)
        observed, started = value.get("observed_at"), value.get("process_started_at")
        if (type(value.get("pid")) is not int or value["pid"] != proc["pid"]
                or not number(observed) or not number(started)
                or abs(started - proc["started"]) > 1
                or not proc["started"] <= observed <= now or now - observed > ttl):
            return None
        return value
    except (OSError, ValueError, GuardError):
        return None


def singapore_candidates(proxies, group, cfg):
    allowed = set(cfg["singapore_nodes"])
    pattern = re.compile(r"新加坡|狮城|Singapore|🇸🇬|(?:^|[^A-Za-z])SG(?:[^A-Za-z]|$)", re.I)
    result = []
    for name in group.get("all", []):
        info = proxies.get(name, {})
        if (name == group.get("now") or name.upper() in ("DIRECT", "REJECT", "PASS", "COMPATIBLE")
                or not info or "all" in info or info.get("type", "").lower() in
                ("direct", "reject", "rejectdrop", "pass", "compatible")):
            continue
        if name in allowed or (cfg["allow_singapore_name_match"] and pattern.search(name)):
            if name not in result:
                result.append(name)
    # Refuse silent truncation: selecting a minimum from a subset would mislead users.
    if len(result) > cfg["max_candidates"]:
        raise Halt("VPN_CONFIG_REQUIRED", "新加坡候选过多；请缩小白名单后重试。")
    return result


class Watchdog:
    def __init__(self, cfg, state_dir, mac=None, api=None, clock=time.time, pause=time.sleep, dry=False):
        self.cfg, self.dir = cfg, Path(state_dir)
        self.mac, self.api = mac or Mac(), api or Mihomo(cfg)
        self.clock, self.pause, self.dry = clock, pause, dry
        self.state = {"history": [], "deferred": {}, "transaction": None}
        self.actions, self.checks = [], {}
        self.deadline = 0

    def save(self):
        if not self.dry:
            atomic_json(self.dir / "recovery.json", self.state)

    def load(self):
        if not (self.dir / "recovery.json").exists():
            return
        state = read_json(self.dir / "recovery.json")
        if not isinstance(state.get("history"), list) or not isinstance(state.get("deferred"), dict):
            raise GuardError("Invalid recovery state")
        for event in state["history"]:
            if (not isinstance(event, dict) or event.get("kind") not in ("clash", "node", "gpt")
                    or not number(event.get("at"))):
                raise GuardError("Invalid action history")
        deferred = state["deferred"]
        if deferred and (type(deferred.get("count")) is not int or not 0 <= deferred["count"] <= 2
                         or not number(deferred.get("due")) or not isinstance(deferred.get("identity"), str)):
            raise GuardError("Invalid deferral state")
        tx = state.get("transaction")
        if tx is not None and (not isinstance(tx, dict) or not all(
                isinstance(tx.get(k), str) and tx[k] for k in ("group", "original", "candidate"))):
            raise GuardError("Invalid rollback transaction")
        self.state = state

    def wait(self, seconds):
        if self.clock() + seconds > self.deadline:
            raise Halt("CHECK_TIMEOUT", "本轮检查达到 15 分钟上限，停止新增恢复动作。")
        self.pause(seconds)

    def reserve(self, kind):
        if self.dry:
            raise Halt("ACTION_REQUIRED", "只读检查发现异常；未执行恢复。")
        now = self.clock()
        if now >= self.deadline:
            raise Halt("CHECK_TIMEOUT", "本轮恢复时间预算已耗尽。")
        cooldown = {"clash": 1800, "node": 21600, "gpt": 21600}[kind]
        events = [e["at"] for e in self.state["history"] if e["kind"] == kind]
        if any(now - at < cooldown for at in events):
            raise Halt("COOLDOWN", "恢复处于冷却期；保留现场，不重复操作。")
        if kind == "gpt" and sum(now - at < 86400 for at in events) >= 2:
            raise Halt("RESTART_LIMIT", "ChatGPT 在 24 小时内的两次恢复额度已用完。")
        self.state["history"] = [e for e in self.state["history"] if now - e["at"] < 7 * 86400]
        self.state["history"].append({"kind": kind, "at": now})
        self.save()  # Reserve before acting; failures and crashes consume the quota.

    def rollback(self):
        tx = self.state.get("transaction")
        if not tx:
            return
        if self.dry:
            raise Halt("VPN_ROLLBACK_REQUIRED", "存在未完成节点事务；只读模式不回滚。")
        try:
            current = self.api.group(tx["group"]).get("now")
            if current not in (tx["candidate"], tx["original"]):
                raise GuardError("User changed selection during recovery")
            if current != tx["original"]:
                self.api.select(tx["group"], tx["original"])
            self.state["transaction"] = None
            self.save()
            self.actions.append("节点已恢复至原选择")
        except Exception:
            raise Halt("VPN_ROLLBACK_REQUIRED", "原节点回滚未完成或选择被外部修改；停止自动操作，请检查 Clash。")

    def switch_nodes(self):
        group_name = self.cfg["selector_group"]
        if not group_name:
            raise Halt("VPN_CONFIG_REQUIRED", "代理链路故障；请先配置允许操作的 Selector 组和新加坡节点白名单。")
        if self.dry:
            raise Halt("VPN_NEEDS_ATTENTION", "代理链路故障；只读模式未切换节点。")
        try:
            group = self.api.group(group_name)
            if group.get("type") != "Selector" or not group.get("now"):
                raise Halt("VPN_CONFIG_REQUIRED", "仅支持明确指定的手动 Selector 组。")
            candidates = singapore_candidates(self.api.proxies(), group, self.cfg)
        except Halt:
            raise
        except Exception:
            raise Halt("VPN_CONTROLLER_UNAVAILABLE", "无法读取 Clash 控制接口；不据此重启 Clash。")
        if not candidates:
            raise Halt("VPN_NO_SG_NODE", "没有符合配置的新加坡新节点；保持原选择。")
        self.reserve("node")
        ranked = []
        for node in candidates:
            samples = []
            for _ in range(self.cfg["latency_samples"]):
                self.wait(0)
                try:
                    self.api.delay(node, self.cfg["general_url"])
                    samples.append(self.api.delay(node, self.cfg["chatgpt_url"]))
                except Exception:
                    samples = []
                    break
            if samples:
                ranked.append((statistics.median(samples), node))
        if not ranked:
            raise Halt("VPN_NO_SG_NODE", "新加坡候选均未通过双目标连通性测试；没有切换。")
        original = group["now"]
        for latency, node in sorted(ranked)[:self.cfg["max_switches"]]:
            self.wait(0)
            if self.api.group(group_name).get("now") != original:
                raise Halt("VPN_SELECTION_CHANGED", "节点选择已被外部修改，本轮停止。")
            self.state["transaction"] = {"group": group_name, "original": original, "candidate": node}
            self.save()
            accepted = False
            try:
                self.api.select(group_name, node)
                self.wait(5)
                if self.mac.link(self.cfg) == "ok":
                    self.wait(5)
                    accepted = self.mac.link(self.cfg) == "ok"
                if accepted:
                    self.state["transaction"] = None
                    self.save()
                    self.actions.append("已切换至可用新加坡节点，实测中位延迟 %.0f ms" % latency)
                    return
            except (OSError, ValueError, GuardError, http.client.HTTPException):
                pass
            finally:
                if not accepted:
                    self.rollback()
        raise Halt("VPN_NEEDS_ATTENTION", "最多三个候选的切换验证均失败，已恢复原节点。")

    def network(self):
        basic = self.mac.basic(self.cfg)
        self.checks["basic_network"] = basic
        if basic != "ok":
            raise Halt("NETWORK_UNAVAILABLE", "基础网络不可用或无法确认；先检查 Wi-Fi、路由器、认证门户，不切节点或重启 GPT。")
        if not self.mac.core(self.cfg):
            self.checks["clash"] = "failed"
            self.reserve("clash")
            self.mac.start("Clash Verge")
            self.wait(15)
            if not self.mac.core(self.cfg):
                if not self.mac.restart("Clash Verge"):
                    raise Halt("VPN_NEEDS_ATTENTION", "Clash 未能正常退出或启动，未强制结束进程。")
                self.wait(15)
            if not self.mac.core(self.cfg):
                raise Halt("VPN_NEEDS_ATTENTION", "Clash 内核或代理端口仍不可用。")
            self.actions.append("Clash 内核已恢复")
        self.checks["clash"] = "ok"
        link = self.mac.link(self.cfg)
        if link == "failed" and not self.dry:
            self.wait(10)
            link = self.mac.link(self.cfg)
        self.checks["proxy_path"] = link
        if link == "unknown":
            raise Halt("VPN_PATH_UNKNOWN", "收到 4xx/5xx 等响应：不能证明服务可用，也不能认定 VPN 传输故障；不切节点或重启 GPT。")
        if link == "failed":
            self.switch_nodes()
            self.checks["proxy_path"] = "ok"

    def remote(self, proc):
        evidence = fresh_evidence(self.cfg["remote_evidence_file"], proc, self.clock(), 120)
        status = evidence.get("status") if evidence else "unknown"
        if status not in ("healthy", "failed", "auth_required", "pairing_required"):
            status = "unknown"
        self.checks["remote"] = status
        return status, evidence

    def same_app(self, proc):
        current, server = self.mac.app()
        return current is not None and current["pid"] == proc["pid"] and current["started"] == proc["started"], server

    def restart_gpt(self, proc):
        if self.dry:
            raise Halt("REMOTE_NEEDS_ATTENTION", "应用或 Remote 异常；只读模式未重启。")
        if self.mac.link(self.cfg) != "ok":
            raise Halt("VPN_PATH_UNKNOWN", "重启前代理复检未通过；取消 GPT 重启。")
        same, _ = self.same_app(proc)
        if not same:
            raise Halt("REMOTE_UNKNOWN", "应用实例已变化，取消重启，等待下一轮检查。")
        remote, _ = self.remote(proc)
        if remote in ("auth_required", "pairing_required"):
            raise Halt("REMOTE_MANUAL", "Remote 明确需要登录或配对，取消自动重启。")
        activity = fresh_evidence(self.cfg["task_evidence_file"], proc, self.clock(), 30)
        if not activity or activity.get("status") != "idle":
            identity = "%s:%s" % (proc["pid"], proc["started"])
            deferred = self.state["deferred"]
            count = deferred.get("count", 0) if deferred.get("identity") == identity else 0
            if count >= 2:
                raise Halt("TASKS_NEED_ATTENTION", "任务忙碌或状态未知，已两次延后检查；请确认空闲后人工恢复。")
            self.state["deferred"] = {"identity": identity, "count": count + 1, "due": self.clock() + 600}
            self.save()
            raise Halt("RESTART_DEFERRED", "任务忙碌或无法确认空闲；10 分钟后重查，本故障最多延后两次。")
        self.reserve("gpt")
        if not self.mac.restart("ChatGPT"):
            raise Halt("REMOTE_NEEDS_ATTENTION", "ChatGPT 无法正常退出/启动；未强制结束，也未退出账号。")
        self.actions.append("ChatGPT 已正常退出并重新启动")
        self.state["deferred"] = {}
        self.save()
        self.wait(45)
        self.verify_after_start()

    def verify_after_start(self):
        if self.mac.link(self.cfg) != "ok":
            raise Halt("VPN_PATH_UNKNOWN", "启动后的代理复检未通过，不继续重启。")
        proc, server = self.mac.app()
        self.checks["app"] = "ok" if proc else "missing"
        self.checks["app_server"] = "ok" if server else "missing"
        if not proc or not server:
            raise Halt("REMOTE_NEEDS_ATTENTION", "启动后主进程或其 Codex App Server 仍异常；本轮不再重启。")
        status, _ = self.remote(proc)
        if status in ("auth_required", "pairing_required"):
            raise Halt("REMOTE_MANUAL", "需要重新登录、配对或开启远程连接，请人工操作。")
        if status != "healthy":
            raise Halt("REMOTE_UNKNOWN" if status == "unknown" else "REMOTE_NEEDS_ATTENTION",
                       "本地应用已运行，但尚未证实 Remote 恢复；本轮不再次重启。")

    def application(self):
        proc, server = self.mac.app()
        self.checks["app"] = "ok" if proc else "missing"
        self.checks["app_server"] = "ok" if server else "missing"
        if not proc:
            self.reserve("gpt")
            if not self.mac.start("ChatGPT"):
                raise Halt("REMOTE_NEEDS_ATTENTION", "无法启动 ChatGPT，请确认安装位置。")
            self.actions.append("已启动 ChatGPT")
            self.wait(45)
            self.verify_after_start()
            return
        if not server:
            if not self.dry:
                self.wait(60)
            same, server = self.same_app(proc)
            if not same:
                raise Halt("REMOTE_UNKNOWN", "应用实例已变化，本轮不重启。")
            if not server:
                self.restart_gpt(proc)
                return
            self.checks["app_server"] = "ok"
        status, evidence = self.remote(proc)
        if status == "healthy":
            return
        if status in ("auth_required", "pairing_required"):
            raise Halt("REMOTE_MANUAL", "Remote 需要登录、配对或手动启用；不自动重启。")
        if self.dry:
            raise Halt("REMOTE_UNKNOWN" if status == "unknown" else "REMOTE_NEEDS_ATTENTION", "只读 Remote 检查：" + status)
        if status == "unknown":
            for _ in range(2):
                self.wait(60)
                same, server = self.same_app(proc)
                if not same or not server:
                    break
                status, evidence = self.remote(proc)
                if status != "unknown":
                    break
            if status == "healthy":
                return
            if status in ("auth_required", "pairing_required"):
                raise Halt("REMOTE_MANUAL", "请人工处理 Remote 登录或配对。")
            if status == "unknown":
                raise Halt("REMOTE_UNKNOWN", "未获得有效 Remote 证据；不把进程存在当作手机连接正常，也不盲目重启。")
        self.wait(60)
        same, server = self.same_app(proc)
        status2, newer = self.remote(proc) if same and server else ("unknown", None)
        if status2 == "healthy":
            return
        if status2 in ("auth_required", "pairing_required"):
            raise Halt("REMOTE_MANUAL", "请人工处理 Remote 登录或配对。")
        if (status2 != "failed" or not newer or not evidence
                or newer["observed_at"] <= evidence["observed_at"]):
            raise Halt("REMOTE_UNKNOWN", "未获得间隔 60 秒的两次独立失败证据；不重启。")
        self.restart_gpt(proc)

    def check(self):
        self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        with file_lock(self.dir / "check-v2.lock") as locked:
            if not locked:
                return {"result": "CHECK_ALREADY_RUNNING", "detail": "已有检查正在运行。"}
            self.actions, self.checks = [], {}
            self.deadline = self.clock() + 900
            result, detail = "HEALTHY", "网络、本地应用和有效 Remote 证据均通过。"
            try:
                self.load()
                self.rollback()  # Complete an interrupted transaction before other changes.
                if self.state["deferred"].get("due", 0) <= self.clock() and self.state["deferred"]:
                    self.state["deferred"]["due"] = 0  # Consume timer even if network checks fail.
                    self.save()
                self.checks["sleep_assertion"] = self.mac.sleep_protected()
                self.network()
                self.application()
                self.state["deferred"] = {}
                self.save()
                if not self.checks["sleep_assertion"]:
                    result, detail = "HOST_SLEEP_RISK", "连接检查通过，但未观察到防睡眠断言；检查守护程序与电源设置。"
            except Halt as exc:
                result, detail = exc.result, exc.detail
            except Exception as exc:
                # Exception bodies can contain private controller URLs/node names.
                result, detail = "CHECK_ERROR", "检查安全停止（%s）；请检查配置、状态文件与权限。" % type(exc).__name__
            report = {"version": VERSION, "checked_at": self.clock(), "result": result,
                      "detail": detail, "read_only": self.dry, "checks": self.checks, "actions": self.actions}
            if not self.dry:
                previous = {}
                try:
                    previous = read_json(self.dir / "status.json")
                except (OSError, ValueError, GuardError):
                    pass
                atomic_json(self.dir / "status.json", report)
                logging.getLogger("autoConnetGPT").info("%s %s actions=%s", result, detail, self.actions)
                if self.cfg["notifications"] and previous.get("result") != result:
                    self.mac.notify(result + "：" + detail)
            return report


def setup_logging(directory):
    handler = RotatingFileHandler(str(directory / "autoConnetGPT.log"), maxBytes=5 * 1024 * 1024,
                                  backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    logger = logging.getLogger("autoConnetGPT")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)


def deferred_deadline(directory):
    # Read atomic disk state so a manual --check can schedule a daemon follow-up.
    try:
        due = read_json(directory / "recovery.json").get("deferred", {}).get("due", 0)
        return due if number(due) and due > 0 else 0
    except (OSError, ValueError, AttributeError, GuardError):
        return 0  # A full check reports malformed state without resetting history.


def daemon(watchdog):
    def stop(signum, frame):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with file_lock(watchdog.dir / "daemon-v2.lock") as locked:
        if not locked:
            return
        assertion = None
        try:
            next_check = 0
            last_deferred = None
            while True:
                if assertion is None or assertion.poll() is not None:
                    assertion = subprocess.Popen(["/usr/bin/caffeinate", "-is", "-w", str(os.getpid())],
                                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                now = watchdog.clock()
                due = deferred_deadline(watchdog.dir)
                if now >= next_check or (due and now >= due and due != last_deferred):
                    last_deferred = due
                    watchdog.check()
                    next_check = watchdog.clock() + watchdog.cfg["interval_seconds"]
                time.sleep(15)
        finally:
            if assertion and assertion.poll() is None:
                assertion.terminate()
                try:
                    assertion.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    options = parser.add_mutually_exclusive_group(required=True)
    for flag in ("daemon", "check", "diagnose", "status", "diagnostics", "version"):
        options.add_argument("--" + flag, action="store_true")
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    if args.version:
        print(VERSION)
        return 0
    if args.status or args.diagnostics:
        try:
            print(json.dumps(read_json(args.state_dir / "status.json"), ensure_ascii=False, indent=2))
        except FileNotFoundError:
            print("尚无 v2 检查记录。")
        if args.diagnostics and sys.platform == "darwin":
            for query in ("custom", "assertions"):
                print(Mac.run(["/usr/bin/pmset", "-g", query]).stdout)
        return 0
    if sys.platform != "darwin":
        parser.error("Real checks require macOS; unit tests can run on other platforms.")
    cfg = config_load(args.config or args.state_dir / "config.json")
    args.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not args.diagnose:
        setup_logging(args.state_dir)
    watchdog = Watchdog(cfg, args.state_dir, dry=args.diagnose)
    if args.daemon:
        daemon(watchdog)
    else:
        print(json.dumps(watchdog.check(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    os.umask(0o077)
    try:
        sys.exit(main())
    except (GuardError, OSError, ValueError) as error:
        print("autoConnetGPT: configuration or local I/O error (%s)." % type(error).__name__, file=sys.stderr)
        sys.exit(1)
