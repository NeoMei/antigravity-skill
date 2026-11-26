"""Bind Codex directories to locally registered Antigravity Projects."""
from dataclasses import dataclass
import json
from pathlib import Path
import shlex
import sys
from urllib.parse import unquote, urlsplit

from local_state import UUID, locked, read_json, write_json

PREFIX = "codex-antigravity-"
HOOK_NAME = "codex-antigravity-reply"
LOCK_NAME = ".codex-antigravity.lock"


@dataclass(frozen=True)
class Route:
    id: str
    workspace: Path
    name: str


def load_project(config, project_id):
    if not isinstance(project_id, str) or not UUID.fullmatch(project_id):
        return None
    path = config / "projects" / f"{project_id}.json"
    try:
        project = read_json(path)
    except (OSError, ValueError):
        return None
    if not isinstance(project, dict) or project.get("id") != project_id:
        return None
    resources = project.get("projectResources") or {}
    folders = resources.get("resources", []) if isinstance(resources, dict) else []
    if not isinstance(folders, list) or len(folders) != 1 or not isinstance(folders[0], dict):
        return None
    resource = folders[0]
    git_folder = resource.get("gitFolder") or {}
    uri = resource.get("folderUri") or (git_folder.get("folderUri") if isinstance(git_folder, dict) else None)
    if not isinstance(uri, str):
        return None
    folder = urlsplit(uri)
    if folder.scheme != "file" or folder.netloc not in {"", "localhost"}:
        return None
    workspace = Path(unquote(folder.path))
    if not workspace.is_absolute():
        return None
    name = project.get("name")
    return Route(project_id, workspace.resolve(), name if isinstance(name, str) and name else workspace.name)


def match(config, cwd):
    workspace = cwd.resolve()
    configured = bindings(config)
    candidates = [load_project(config, path.stem) for path in (config / "projects").glob("*.json")]
    candidates = [project for project in candidates
                  if project and not is_sidecar_projection(config, project, configured)]
    matches = [project for project in candidates if project and project.workspace == workspace]
    if not matches:
        raise ValueError(f"Antigravity 尚未登记此目录的单文件夹本机项目：{workspace}。"
                         "请在 Antigravity 添加该目录后重试。")
    if len(matches) > 1:
        names = "、".join(sorted(project.name for project in matches))
        raise ValueError(f"多个 Antigravity 项目登记了同一目录：{names}。请保留明确的单文件夹项目。")
    return matches[0]


def bindings(config):
    return read_json(config / "config.json").get("sidecars", {})


def bridge_command(config, state, bridge_file, *args):
    return [sys.executable, "-B", str(bridge_file), "--config", str(config.resolve()),
            "--state", str(state.resolve()), *args]


def hook_definition(config, state, bridge_file):
    stop = bridge_command(config, state, bridge_file, "stop")
    return {"Stop": [{"type": "command", "command": shlex.join(stop), "timeout": 5}]}


def setup(config, state, bridge_file):
    if not (config / "config.json").is_file():
        raise ValueError("Antigravity 配置尚未生成。请先启动并登录 Antigravity Desktop。")
    path = config / "hooks.json"
    expected = hook_definition(config, state, bridge_file)
    with locked(config / LOCK_NAME):
        initial = path.read_bytes() if path.is_file() else None
        hooks = json.loads(initial) if initial is not None else {}
        existing = hooks.get(HOOK_NAME)
        if existing == expected:
            return
        if existing is not None:
            raise ValueError(f"{path} 已有同名回调，请确认原 Skill 的安装位置。")
        hooks[HOOK_NAME] = expected
        current = path.read_bytes() if path.is_file() else None
        if initial != current:
            raise ValueError("Antigravity 回调配置刚被更新，请重试。")
        write_json(path, hooks, replace=True)


def check_setup(config, state, bridge_file):
    path = config / "hooks.json"
    if (not path.is_file()
            or read_json(path).get(HOOK_NAME) != hook_definition(config, state, bridge_file)):
        raise ValueError("Antigravity 回复回调尚未配置。请先运行 Skill 中的 bridge.py setup。")


def is_sidecar_projection(config, project, configured):
    if (not project.name.startswith(PREFIX)
            or not UUID.fullmatch(project.name.removeprefix(PREFIX))
            or project.name == PREFIX + project.id):
        return False
    manifest = config / "sidecars" / project.name / "sidecar.json"
    return project.name in configured or manifest.is_file()


def binding_matches(config, name, settings, route):
    actual_id = settings.get("projectId", "")
    if actual_id == route.id:
        return True
    if name != PREFIX + route.id:
        return False
    actual = load_project(config, actual_id)
    return bool(actual and actual.name == name and actual.workspace == route.workspace)


def managed_workspace(config, workspace):
    for name, settings in bindings(config).items():
        if (not isinstance(settings, dict) or not settings.get("enabled")
                or not isinstance(settings.get("projectId"), str)):
            continue
        if not name.startswith(PREFIX):
            continue
        requested = name.removeprefix(PREFIX)
        project = load_project(config, requested)
        if not project or not binding_matches(config, name, settings, project):
            continue
        if project.workspace == workspace:
            return True
    return False


def configured_sidecar(config, route, sidecars):
    name = PREFIX + route.id
    manifest = config / "sidecars" / name / "sidecar.json"
    existing = sidecars.get(name)
    if existing is not None:
        if not binding_matches(config, name, existing, route):
            raise ValueError(f"Sidecar {name} 已绑定其他项目。")
        if not existing.get("enabled"):
            raise ValueError(f"项目 {route.name} 的 ACCowork Sidecar 已停用。")
        if not manifest.is_file():
            raise ValueError(f"项目 {route.name} 缺少 Sidecar 定义：{manifest}。")
    return name, manifest, existing is not None


def ensure_sidecar(config, state, route, bridge_file):
    if configured_sidecar(config, route, bindings(config))[2]:
        return
    config_file = config / "config.json"
    with locked(config / LOCK_NAME):
        data = config_file.read_bytes()
        settings = json.loads(data)
        sidecars = settings.setdefault("sidecars", {})
        name, manifest, exists = configured_sidecar(config, route, sidecars)
        if exists:
            return
        manifest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        command = bridge_command(config, state, bridge_file, "serve", "--project", route.id, "--sidecar", name)
        expected = {
            "display_name": f"Codex · {route.name}",
            "description": "Routes this local Antigravity project to its Codex chats.",
            "command": command[0],
            "args": command[1:],
            "restart_policy": "on-failure",
        }
        if manifest.is_file():
            if read_json(manifest) != expected:
                raise ValueError(f"Sidecar 定义冲突：{manifest}。")
        else:
            write_json(manifest, expected)
        # Antigravity migrates unversioned entries into generated projects.
        sidecars[name] = {"enabled": True, "projectId": route.id, "schemaVersion": 1}
        if config_file.read_bytes() != data:
            raise ValueError("Antigravity 配置刚被更新，请重试。")
        write_json(config_file, settings, replace=True)
