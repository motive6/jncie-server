import json
import shutil
import socket
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ..config import settings


@dataclass
class FileSpec:
    dest: str
    content: str
    mode: str = "0644"
    owner: str = "root:root"


@dataclass
class ApplyResult:
    ok: bool
    messages: list[str] = field(default_factory=list)

    def text(self) -> str:
        return "\n".join(self.messages) if self.messages else ("应用成功" if self.ok else "应用失败")


def apply_service(
    service: str,
    files: list[FileSpec] | None = None,
    validate: list[str] | None = None,
    restart: list[str] | None = None,
    health: list[str] | None = None,
    health_ports: list[int] | None = None,
    actions: list[str] | None = None,
) -> ApplyResult:
    files = files or []
    if settings.dry_run:
        return ApplyResult(
            True,
            ["dry-run：已渲染 " + (", ".join(f.dest for f in files) if files else "（无文件）")],
        )

    stage = settings.staging_dir / service
    manifest = {
        "service": service,
        "files": [],
        "validate": [validate] if validate else [],
        "restart": restart or [],
        "health": [health] if health else [],
        "health_ports": health_ports or [],
        "actions": actions or [],
    }
    try:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        stage.mkdir(parents=True, exist_ok=True)
        for index, spec in enumerate(files):
            name = f"{index:02d}-{Path(spec.dest).name}"
            (stage / name).write_text(spec.content, encoding="utf-8")
            manifest["files"].append(
                {"dest": spec.dest, "src": name, "mode": spec.mode, "owner": spec.owner}
            )
        (stage / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        return ApplyResult(False, [f"暂存目录不可写：{exc}（请检查 {settings.staging_dir} 的属主）"])

    try:
        proc = subprocess.run(
            ["sudo", "-n", settings.apply_helper, service],
            capture_output=True,
            text=True,
            timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return ApplyResult(False, [f"调用应用助手失败：{exc}"])

    payload = None
    for line in reversed(proc.stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                payload = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    if payload is None:
        detail = (proc.stdout + proc.stderr).strip()[-1500:]
        return ApplyResult(False, [f"应用助手返回异常（exit {proc.returncode}）：{detail}"])
    return ApplyResult(bool(payload.get("ok")), list(payload.get("messages") or []))


def unit_state(unit: str) -> dict:
    if settings.dry_run:
        return {"active": True, "state": "active (dry-run)", "since": ""}
    try:
        proc = subprocess.run(
            ["systemctl", "show", unit, "-p", "ActiveState", "-p", "SubState", "-p", "ActiveEnterTimestamp"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        info = {}
        for line in proc.stdout.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                info[key] = value
        active = info.get("ActiveState") == "active"
        state = info.get("SubState") or info.get("ActiveState") or "unknown"
        return {"active": active, "state": state, "since": info.get("ActiveEnterTimestamp", "")}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"active": False, "state": f"unknown ({exc})", "since": ""}


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.5, udp: bool = False) -> bool:
    if settings.dry_run:
        return True
    if udp:
        try:
            proc = subprocess.run(
                ["ss", "-H", "-lun", f"sport = :{port}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return bool(proc.stdout.strip())
        except (OSError, subprocess.TimeoutExpired):
            return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def run_command(args: list[str], timeout: int = 20) -> tuple[int, str]:
    if settings.dry_run:
        return 0, f"dry-run: {' '.join(args)}"
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, (proc.stdout + proc.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
