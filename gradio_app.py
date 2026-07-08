from __future__ import annotations

import json
import math
import os
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from random_input import IMAGE_DIR as AUTO_BASE_IMAGE_DIR
from random_input import generate_auto_text_input

PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "FTP_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "ftp_proxy",
)
DIRECT_NO_PROXY_VALUE = "*"


def configure_direct_network_env(env: Any = None) -> None:
    if env is None:
        env = os.environ
    for key in PROXY_ENV_KEYS:
        env.pop(key, None)
    env["NO_PROXY"] = DIRECT_NO_PROXY_VALUE
    env["no_proxy"] = DIRECT_NO_PROXY_VALUE
    env.setdefault("GRADIO_ANALYTICS_ENABLED", "False")


configure_direct_network_env()

import gradio as gr
import numpy as np
import trimesh
from PIL import Image, ImageOps


EMBODICHAIN_ROOT = Path(
    os.environ.get("EMBODICHAIN_ROOT", "/home/oem/桌面/EmbodiChain")
).expanduser()
SCENE_ID = "current"

GYM_PROJECT_ROOT = EMBODICHAIN_ROOT / "gym_project"
ACTION_AGENT_ROOT = GYM_PROJECT_ROOT / "action_agent_pipeline"
IMAGE_DIR = ACTION_AGENT_ROOT / "images"
AUTO_LOG_DIR = ACTION_AGENT_ROOT / "auto_logs"
IMAGE_PATH = IMAGE_DIR / f"{SCENE_ID}.png"
PROMPT2SCENE_ROOT = GYM_PROJECT_ROOT / SCENE_ID
CONFIG_DIR = ACTION_AGENT_ROOT / "configs" / SCENE_ID
FAST_GYM_CONFIG = CONFIG_DIR / "fast_gym_config.json"
OUTPUTS_DIR = EMBODICHAIN_ROOT / "outputs"
GRADIO_SCENE_DIR = CONFIG_DIR / "gradio_scene"
GRADIO_SCENE_GLB = GRADIO_SCENE_DIR / "scene_current.glb"
GRADIO_PREVIOUS_SCENE_GLB = GRADIO_SCENE_DIR / "previous_scene.glb"
GRADIO_OBJECT_PREVIEW_GLB = GRADIO_SCENE_DIR / "object_preview.glb"
SCENE_MANIFEST = GRADIO_SCENE_DIR / "scene_manifest.json"
PENDING_PREFIX = "_gradio_pending_"
REPLACED_PREFIX = "_gradio_replaced_"

LOG_LINE_LIMIT = 80
PROCESS_STOP_TIMEOUT_S = 8.0
TEXT_REWRITE_SUFFIXES = {
    ".json",
    ".jsonl",
    ".txt",
    ".yaml",
    ".yml",
    ".md",
    ".csv",
}
TOP_MODE_AUTO = "auto"
TOP_MODE_INTERACT = "interact"
TOP_MODE_ROBOT_MODEL = "robot_model"
TOP_MODE_PARALLEL_ENV = "parallel_env"


@dataclass(frozen=True)
class Phase:
    progress: int
    label: str


PHASES = {
    "idle": Phase(0, "Idle"),
    "received": Phase(5, "Input received"),
    "started": Phase(10, "Local pipeline started"),
    "scene_intake": Phase(20, "Scene understanding"),
    "relations": Phase(35, "Segmentation and spatial relations"),
    "asset_generation": Phase(55, "3D asset generation"),
    "gym_export": Phase(70, "Scene export"),
    "config": Phase(82, "Action config generated"),
    "preview": Phase(90, "3D preview loaded"),
    "complete": Phase(100, "Complete"),
    "failed": Phase(100, "Failed"),
}


@dataclass
class RuntimeState:
    is_busy: bool = False
    run_token: str = field(default_factory=lambda: uuid.uuid4().hex)
    auto_loop_active: bool = False
    auto_loop_token: str | None = None
    auto_round: int = 0
    process: subprocess.Popen[str] | None = None
    sim_process: subprocess.Popen[str] | None = None
    sim_started: bool = False
    sim_finished: bool = False
    sim_returncode: int | None = None
    phase_key: str = "idle"
    status: str = "Idle."
    task_text: str = ""
    image_path: Path | None = None
    object_model_path: Path | None = None
    scene_model_path: Path | None = None
    edited_scene_model_path: Path | None = None
    last_error: str | None = None
    log_lines: deque[str] = field(default_factory=lambda: deque(maxlen=LOG_LINE_LIMIT))


runtime = RuntimeState()
runtime_lock = threading.Lock()


@dataclass(frozen=True)
class ScenePaths:
    scene_id: str
    image_path: Path
    prompt_root: Path
    config_dir: Path

    @property
    def fast_gym_config(self) -> Path:
        return self.config_dir / "fast_gym_config.json"

    @property
    def agent_config(self) -> Path:
        return self.config_dir / "agent_config.json"

    @property
    def gradio_scene_dir(self) -> Path:
        return self.config_dir / "gradio_scene"

    @property
    def gradio_scene_glb(self) -> Path:
        return self.gradio_scene_dir / "scene_current.glb"

    @property
    def gradio_object_preview_glb(self) -> Path:
        return self.gradio_scene_dir / "object_preview.glb"

    @property
    def scene_manifest(self) -> Path:
        return self.gradio_scene_dir / "scene_manifest.json"

    @property
    def object_preview_manifest(self) -> Path:
        return self.gradio_scene_dir / "object_preview_manifest.json"


CURRENT_PATHS = ScenePaths(
    scene_id=SCENE_ID,
    image_path=IMAGE_PATH,
    prompt_root=PROMPT2SCENE_ROOT,
    config_dir=CONFIG_DIR,
)


def make_stage_paths(run_token: str) -> ScenePaths:
    scene_id = f"{PENDING_PREFIX}{run_token[:12]}"
    return ScenePaths(
        scene_id=scene_id,
        image_path=IMAGE_DIR / f"{scene_id}.png",
        prompt_root=GYM_PROJECT_ROOT / scene_id,
        config_dir=ACTION_AGENT_ROOT / "configs" / scene_id,
    )


def make_replaced_paths(run_token: str) -> ScenePaths:
    scene_id = f"{REPLACED_PREFIX}{run_token[:12]}"
    return ScenePaths(
        scene_id=scene_id,
        image_path=IMAGE_DIR / f"{scene_id}.png",
        prompt_root=GYM_PROJECT_ROOT / scene_id,
        config_dir=ACTION_AGENT_ROOT / "configs" / scene_id,
    )


def save_input(
    image_value: str | np.ndarray | Image.Image,
    task_text: str,
    image_path: Path,
) -> Path:
    if image_value is None:
        raise ValueError("Please upload an image first.")
    if not task_text.strip():
        raise ValueError("Please enter a task description.")

    image_path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(image_value, str):
        image = Image.open(image_value)
    elif isinstance(image_value, np.ndarray):
        image = Image.fromarray(image_value)
    elif isinstance(image_value, Image.Image):
        image = image_value
    else:
        raise TypeError(f"Unsupported image input type: {type(image_value)!r}")

    image = ImageOps.exif_transpose(image).convert("RGB")
    image.save(image_path, format="PNG")
    return image_path


def reset_current_scene() -> list[str]:
    process: subprocess.Popen[str] | None = None
    sim_process: subprocess.Popen[str] | None = None
    with runtime_lock:
        runtime.run_token = uuid.uuid4().hex
        runtime.auto_loop_active = False
        runtime.auto_loop_token = None
        runtime.auto_round = 0
        process = runtime.process
        sim_process = runtime.sim_process
        runtime.process = None
        runtime.sim_process = None
        runtime.sim_started = False
        runtime.sim_finished = False
        runtime.sim_returncode = None
        runtime.is_busy = False
        runtime.phase_key = "idle"
        runtime.status = "Idle."
        runtime.task_text = ""
        runtime.image_path = None
        runtime.object_model_path = None
        runtime.scene_model_path = None
        runtime.edited_scene_model_path = None
        runtime.last_error = None
        runtime.log_lines.clear()

    if process is not None:
        terminate_process_group(process)
    if sim_process is not None:
        terminate_process_group(sim_process)

    return cleanup_current_and_staging()


def cleanup_current_and_staging() -> list[str]:
    errors: list[str] = []
    paths: list[Path] = [
        PROMPT2SCENE_ROOT,
        CONFIG_DIR,
        OUTPUTS_DIR,
        IMAGE_PATH,
        *pending_artifact_paths(),
    ]
    for path in paths:
        errors.extend(remove_path(path))
    return errors


def cleanup_auto_generated_artifacts(extra_image_path: Path | None = None) -> list[str]:
    errors: list[str] = []
    paths: list[Path] = [
        PROMPT2SCENE_ROOT,
        CONFIG_DIR,
        OUTPUTS_DIR,
        IMAGE_PATH,
        *pending_artifact_paths(),
    ]
    if extra_image_path is not None:
        paths.append(extra_image_path)

    for path in paths:
        if is_protected_auto_base_image(path):
            continue
        errors.extend(remove_path(path))

    with runtime_lock:
        runtime.image_path = None
        runtime.object_model_path = None
        runtime.scene_model_path = None
        runtime.edited_scene_model_path = None
    return errors


def is_protected_auto_base_image(path: Path) -> bool:
    try:
        path.resolve().relative_to(AUTO_BASE_IMAGE_DIR.resolve())
    except ValueError:
        return False
    except FileNotFoundError:
        return False
    return True


def pending_artifact_paths() -> list[Path]:
    paths: list[Path] = []
    for root in (GYM_PROJECT_ROOT, ACTION_AGENT_ROOT / "configs"):
        if root.is_dir():
            paths.extend(root.glob(f"{PENDING_PREFIX}*"))
            paths.extend(root.glob(f"{REPLACED_PREFIX}*"))
    if IMAGE_DIR.is_dir():
        paths.extend(IMAGE_DIR.glob(f"{PENDING_PREFIX}*.png"))
        paths.extend(IMAGE_DIR.glob(f"{REPLACED_PREFIX}*.png"))
    return paths


def remove_path(path: Path) -> list[str]:
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    except Exception as exc:
        return [f"Failed to remove {path}: {exc}"]
    return []


def build_initial_pipeline_command(
    task_text: str,
    paths: ScenePaths,
    prompt2scene_prompt: str = "",
) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "embodichain.gen_sim.action_agent_pipeline.cli.run_agent_pipeline",
        "--use-prompt2scene",
        "--image-name",
        paths.scene_id,
        "--prompt2scene-output-root",
        f"gym_project/{paths.scene_id}",
        "--config-output-dir",
        f"gym_project/action_agent_pipeline/configs/{paths.scene_id}",
        "--task_name",
        SCENE_ID,
        "--task_description",
        task_text,
        "--overwrite-config",
        "--regenerate",
        "--skip-run-agent",
    ]
    if prompt2scene_prompt.strip():
        command.extend(["--prompt2scene-prompt", prompt2scene_prompt.strip()])
    return command


def build_edit_pipeline_command(task_text: str, env_text: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "embodichain.gen_sim.action_agent_pipeline.cli.run_agent_pipeline",
        "--use-existing-gym-project",
        "--gym-project",
        "gym_project/current/gym_export",
        "--prompt2scene-prompt",
        env_text,
        "--config-output-dir",
        "gym_project/action_agent_pipeline/configs/current",
        "--task_name",
        SCENE_ID,
        "--task_description",
        task_text,
        "--overwrite-config",
        "--regenerate",
        "--skip-run-agent",
    ]


def format_current_task(task_text: str, env_text: str = "") -> str:
    return "\n".join(
        part for part in ((task_text or "").strip(), (env_text or "").strip()) if part
    )


def archive_auto_round_log(
    *,
    auto_round: int,
    task_label: str,
    task_description: str,
    scene_description: str,
    outcome: str,
) -> Path | None:
    with runtime_lock:
        recent_logs = list(runtime.log_lines)
        status_text = runtime.status
        last_error = runtime.last_error

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    safe_label = safe_filename_part(task_label) or "unknown"
    log_path = AUTO_LOG_DIR / f"{timestamp}_round{auto_round:04d}_{safe_label}.md"
    content = [
        f"# Auto Round {auto_round}",
        "",
        f"Timestamp: {timestamp}",
        f"Task label: {task_label or 'unknown'}",
        f"Outcome: {outcome}",
        "",
        "## Task description",
        "",
        task_description or "",
        "",
        "## Scene description",
        "",
        scene_description or "",
        "",
        "## Status",
        "",
        status_text or "",
    ]
    if last_error:
        content.extend(["", "## Last error", "", last_error])
    content.extend(
        [
            "",
            "## Recent logs",
            "",
            "```text",
            "\n".join(recent_logs) if recent_logs else "(no recent logs)",
            "```",
            "",
        ]
    )

    try:
        AUTO_LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path.write_text("\n".join(content), encoding="utf-8")
    except Exception as exc:
        with runtime_lock:
            runtime.log_lines.append(f"Failed to archive auto log: {exc}")
        return None
    return log_path


def safe_filename_part(value: str) -> str:
    safe = "".join(
        char if char.isalnum() or char in {"-", "_"} else "_"
        for char in value.strip()
    )
    return safe.strip("_")[:80]


def build_run_agent_command(paths: ScenePaths) -> list[str]:
    return [
        sys.executable,
        "-m",
        "embodichain.gen_sim.action_agent_pipeline.cli.run_agent",
        "--task_name",
        SCENE_ID,
        "--gym_config",
        str(paths.fast_gym_config),
        "--agent_config",
        str(paths.agent_config),
        "--regenerate",
    ]


def start_pipeline(command: list[str]) -> subprocess.Popen[str]:
    env = build_pipeline_env()
    env["PYTHONUNBUFFERED"] = "1"
    return subprocess.Popen(
        command,
        cwd=EMBODICHAIN_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        start_new_session=True,
        env=env,
    )


def build_pipeline_env() -> dict[str, str]:
    env = os.environ.copy()
    configure_direct_network_env(env)
    return env


def terminate_process_group(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except Exception:
        process.terminate()

    deadline = time.monotonic() + PROCESS_STOP_TIMEOUT_S
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return
        time.sleep(0.2)

    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except Exception:
        process.kill()


def detect_phase_from_files(current_key: str, paths: ScenePaths) -> str:
    candidates = [
        ("scene_intake", paths.prompt_root / "scene_intake" / "result.json"),
        ("relations", paths.prompt_root / "image_segments" / "result.json"),
        (
            "relations",
            paths.prompt_root / "image_spatial_relations" / "result.json",
        ),
        ("gym_export", paths.prompt_root / "gym_export" / "gym_config.json"),
        ("config", paths.fast_gym_config),
        ("preview", paths.gradio_scene_glb),
    ]
    best_key = current_key
    best_progress = PHASES.get(best_key, PHASES["idle"]).progress

    if any(paths.prompt_root.glob("unified_scene_gen/**/*.glb")):
        best_key, best_progress = _choose_later_phase(
            best_key,
            best_progress,
            "asset_generation",
        )
    for phase_key, marker in candidates:
        if marker.exists():
            best_key, best_progress = _choose_later_phase(
                best_key,
                best_progress,
                phase_key,
            )
    return best_key


def _choose_later_phase(
    current_key: str,
    current_progress: int,
    candidate_key: str,
) -> tuple[str, int]:
    candidate_progress = PHASES[candidate_key].progress
    if candidate_progress > current_progress:
        return candidate_key, candidate_progress
    return current_key, current_progress


def update_phase_from_log(line: str, current_key: str) -> str:
    text = line.lower()
    mapping = [
        ("scene_intake", "scene_intake"),
        ("image_segments", "relations"),
        ("image_spatial_relations", "relations"),
        ("unified_scene_gen", "asset_generation"),
        ("glb", "asset_generation"),
        ("gym_export", "gym_export"),
        ("generated gym config", "config"),
        ("fast_gym_config", "config"),
    ]
    best_key = current_key
    best_progress = PHASES.get(best_key, PHASES["idle"]).progress
    for needle, phase_key in mapping:
        if needle in text:
            best_key, best_progress = _choose_later_phase(
                best_key,
                best_progress,
                phase_key,
            )
    return best_key


def read_process_output(
    process: subprocess.Popen[str],
    output_queue: queue.Queue[str],
) -> None:
    if process.stdout is None:
        return
    for line in process.stdout:
        output_queue.put(line.rstrip())


def build_gradio_scene_from_fast_config(
    config_path: Path,
    scene_dir: Path | None = None,
) -> Path:
    config_dir = config_path.parent
    if scene_dir is None:
        scene_dir = config_dir / "gradio_scene"
    scene_glb = scene_dir / "scene_current.glb"
    scene_manifest = scene_dir / "scene_manifest.json"
    with config_path.open("r", encoding="utf-8") as file:
        config = json.load(file)

    scene = trimesh.Scene()
    manifest: dict[str, Any] = {
        "source_config": os.path.relpath(config_path, scene_dir),
        "objects": [],
    }

    object_count = 0
    for role, obj in iter_scene_objects(config):
        shape = obj.get("shape") if isinstance(obj, dict) else None
        if not isinstance(shape, dict) or shape.get("shape_type") != "Mesh":
            continue
        raw_fpath = shape.get("fpath")
        if not raw_fpath:
            continue
        mesh_path = resolve_mesh_path(config_dir, str(raw_fpath))
        if not mesh_path.is_file():
            raise FileNotFoundError(f"Mesh file not found for {obj.get('uid')}: {mesh_path}")

        transform = object_transform(obj)
        add_mesh_to_scene(scene, mesh_path, transform, str(obj.get("uid", "object")))
        manifest["objects"].append(
            {
                "uid": obj.get("uid"),
                "role": role,
                "source_mesh": os.path.relpath(mesh_path, scene_dir),
            }
        )
        object_count += 1

    if object_count == 0:
        raise ValueError(f"No mesh objects found in {config_path}")

    scene_dir.mkdir(parents=True, exist_ok=True)
    scene.export(scene_glb)
    scene_manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return scene_glb


def collect_generated_object_glbs(paths: ScenePaths) -> list[Path]:
    glb_dir = paths.prompt_root / "unified_scene_gen" / "glb_gen"
    if not glb_dir.is_dir():
        return []

    simready = [
        path
        for path in glb_dir.rglob("*_simready.glb")
        if is_previewable_glb(path)
    ]
    if simready:
        return sorted(simready)

    return sorted(
        path
        for path in glb_dir.rglob("*.glb")
        if is_previewable_glb(path)
    )


def is_previewable_glb(path: Path) -> bool:
    if not path.is_file() or path.name.startswith("."):
        return False
    return not any(part.startswith(".") for part in path.relative_to(path.anchor).parts)


def build_object_preview_scene(
    glb_paths: list[Path],
    scene_dir: Path,
) -> Path:
    if not glb_paths:
        raise ValueError("No generated object GLBs found")

    scene_dir.mkdir(parents=True, exist_ok=True)
    preview_glb = scene_dir / "object_preview.glb"
    preview_manifest = scene_dir / "object_preview_manifest.json"
    scene = trimesh.Scene()
    manifest: dict[str, Any] = {"objects": []}

    cursor = 0.0
    spacing = 0.35
    added_count = 0
    for object_index, mesh_path in enumerate(glb_paths):
        meshes = load_mesh_geometries(mesh_path)
        if not meshes:
            continue

        bounds = combined_bounds(meshes)
        extents = bounds[1] - bounds[0]
        max_extent = float(max(extents.max(), 1e-6))
        scale = 1.0 / max_extent
        scaled_width = max(float(extents[0]) * scale, 0.2)
        placement_x = cursor + scaled_width / 2.0
        cursor += scaled_width + spacing

        transform = (
            trimesh.transformations.translation_matrix(
                [
                    placement_x,
                    0.0,
                    0.0,
                ]
            )
            @ trimesh.transformations.scale_matrix(scale)
            @ trimesh.transformations.translation_matrix(
                [
                    -float((bounds[0][0] + bounds[1][0]) / 2.0),
                    -float((bounds[0][1] + bounds[1][1]) / 2.0),
                    -float(bounds[0][2]),
                ]
            )
        )

        for mesh_index, mesh in enumerate(meshes):
            mesh.apply_transform(transform)
            name = f"object_{object_index}_{mesh_index}"
            scene.add_geometry(mesh, node_name=name, geom_name=name)
            added_count += 1

        manifest["objects"].append(
            {
                "source_mesh": os.path.relpath(mesh_path, scene_dir),
                "size": mesh_path.stat().st_size,
                "mtime_ns": mesh_path.stat().st_mtime_ns,
            }
        )

    if added_count == 0:
        raise ValueError("No renderable meshes found in generated object GLBs")

    if cursor > spacing:
        scene.apply_transform(
            trimesh.transformations.translation_matrix(
                [-(cursor - spacing) / 2.0, 0.0, 0.0]
            )
        )
    scene.export(preview_glb)
    preview_manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return preview_glb


def object_preview_is_current(
    manifest_path: Path,
    glb_paths: list[Path],
) -> bool:
    if not manifest_path.is_file():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    expected = []
    for path in glb_paths:
        try:
            stat = path.stat()
        except OSError:
            return False
        expected.append(
            {
                "source_mesh": os.path.relpath(path, manifest_path.parent),
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
        )
    return manifest.get("objects") == expected


def load_mesh_geometries(mesh_path: Path) -> list[trimesh.Trimesh]:
    loaded = trimesh.load(mesh_path, force="scene", process=False)
    if isinstance(loaded, trimesh.Trimesh):
        return [loaded.copy()]
    if isinstance(loaded, trimesh.Scene):
        meshes: list[trimesh.Trimesh] = []
        for geometry in loaded.dump(concatenate=False):
            if isinstance(geometry, trimesh.Trimesh):
                meshes.append(geometry.copy())
        return meshes
    raise TypeError(f"Unsupported mesh type for {mesh_path}: {type(loaded)!r}")


def combined_bounds(meshes: list[trimesh.Trimesh]) -> np.ndarray:
    valid_bounds = [
        mesh.bounds
        for mesh in meshes
        if mesh.vertices is not None and len(mesh.vertices) > 0
    ]
    if not valid_bounds:
        raise ValueError("Mesh has no vertices")
    bounds = np.asarray(valid_bounds, dtype=float)
    return np.stack([bounds[:, 0, :].min(axis=0), bounds[:, 1, :].max(axis=0)])


def iter_scene_objects(config: dict[str, Any]) -> Iterable[tuple[str, dict[str, Any]]]:
    for role in ("background", "rigid_object"):
        value = config.get(role, [])
        if isinstance(value, dict):
            value = [value]
        if not isinstance(value, list):
            continue
        for obj in value:
            if isinstance(obj, dict):
                yield role, obj


def resolve_mesh_path(config_dir: Path, raw_fpath: str) -> Path:
    mesh_path = Path(raw_fpath).expanduser()
    if not mesh_path.is_absolute():
        mesh_path = config_dir / mesh_path
    return mesh_path.resolve()


def object_transform(obj: dict[str, Any]) -> np.ndarray:
    position = vector3(obj.get("init_pos"), [0.0, 0.0, 0.0])
    rotation_degrees = vector3(obj.get("init_rot"), [0.0, 0.0, 0.0])
    scale = vector3(obj.get("body_scale"), [1.0, 1.0, 1.0])

    scale_matrix = np.eye(4)
    scale_matrix[0, 0] = scale[0]
    scale_matrix[1, 1] = scale[1]
    scale_matrix[2, 2] = scale[2]
    rotation_matrix = trimesh.transformations.euler_matrix(
        math.radians(rotation_degrees[0]),
        math.radians(rotation_degrees[1]),
        math.radians(rotation_degrees[2]),
        axes="sxyz",
    )
    translation_matrix = trimesh.transformations.translation_matrix(position)
    return translation_matrix @ rotation_matrix @ scale_matrix


def vector3(value: Any, default: list[float]) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return list(default)
    return [float(value[0]), float(value[1]), float(value[2])]


def add_mesh_to_scene(
    scene: trimesh.Scene,
    mesh_path: Path,
    transform: np.ndarray,
    uid: str,
) -> None:
    loaded = trimesh.load(mesh_path, force="scene", process=False)
    if isinstance(loaded, trimesh.Trimesh):
        loaded.apply_transform(transform)
        scene.add_geometry(loaded, node_name=uid, geom_name=uid)
        return

    if isinstance(loaded, trimesh.Scene):
        loaded.apply_transform(transform)
        for index, geometry in enumerate(loaded.dump(concatenate=False)):
            if isinstance(geometry, trimesh.Trimesh):
                scene.add_geometry(
                    geometry,
                    node_name=f"{uid}_{index}",
                    geom_name=f"{uid}_{index}",
                )
        return

    raise TypeError(f"Unsupported mesh type for {mesh_path}: {type(loaded)!r}")


def promote_stage_to_current(stage: ScenePaths, run_token: str) -> list[str]:
    backup = make_replaced_paths(run_token)
    promotion_errors: list[str] = []
    cleanup_errors: list[str] = []

    for required_path in (stage.prompt_root, stage.config_dir, stage.image_path):
        if not required_path.exists():
            raise FileNotFoundError(f"Generated artifact missing: {required_path}")

    cleanup_errors.extend(remove_path(backup.prompt_root))
    cleanup_errors.extend(remove_path(backup.config_dir))
    cleanup_errors.extend(remove_path(backup.image_path))

    moved_to_backup: list[tuple[Path, Path]] = []
    moved_to_current: list[tuple[Path, Path]] = []
    try:
        move_if_exists(PROMPT2SCENE_ROOT, backup.prompt_root, moved_to_backup)
        move_if_exists(CONFIG_DIR, backup.config_dir, moved_to_backup)
        move_if_exists(IMAGE_PATH, backup.image_path, moved_to_backup)

        move_required(stage.prompt_root, PROMPT2SCENE_ROOT, moved_to_current)
        move_required(stage.config_dir, CONFIG_DIR, moved_to_current)
        move_required(stage.image_path, IMAGE_PATH, moved_to_current)
        rewrite_promoted_paths(stage)
    except Exception as exc:
        promotion_errors.append(f"Failed to promote generated scene: {exc}")
        restore_promoted_paths(moved_to_current, moved_to_backup, promotion_errors)
        raise RuntimeError("\n".join(promotion_errors)) from exc

    cleanup_errors.extend(remove_path(backup.prompt_root))
    cleanup_errors.extend(remove_path(backup.config_dir))
    cleanup_errors.extend(remove_path(backup.image_path))
    return cleanup_errors


def move_if_exists(src: Path, dst: Path, moved: list[tuple[Path, Path]]) -> None:
    if not src.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)
    moved.append((src, dst))


def move_required(src: Path, dst: Path, moved: list[tuple[Path, Path]]) -> None:
    if not src.exists():
        raise FileNotFoundError(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)
    moved.append((dst, src))


def restore_promoted_paths(
    moved_to_current: list[tuple[Path, Path]],
    moved_to_backup: list[tuple[Path, Path]],
    errors: list[str],
) -> None:
    for current_path, original_stage_path in reversed(moved_to_current):
        try:
            if current_path.exists():
                original_stage_path.parent.mkdir(parents=True, exist_ok=True)
                current_path.rename(original_stage_path)
        except Exception as exc:
            errors.append(f"Failed to restore staging artifact {original_stage_path}: {exc}")

    for original_current_path, backup_path in reversed(moved_to_backup):
        try:
            if backup_path.exists() and not original_current_path.exists():
                original_current_path.parent.mkdir(parents=True, exist_ok=True)
                backup_path.rename(original_current_path)
        except Exception as exc:
            errors.append(f"Failed to restore previous scene {original_current_path}: {exc}")


def rewrite_promoted_paths(stage: ScenePaths) -> None:
    replacements = [
        (str(stage.config_dir), str(CONFIG_DIR)),
        (str(stage.prompt_root), str(PROMPT2SCENE_ROOT)),
        (str(stage.image_path), str(IMAGE_PATH)),
        (stage.scene_id, SCENE_ID),
    ]
    for root in (PROMPT2SCENE_ROOT, CONFIG_DIR):
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in TEXT_REWRITE_SUFFIXES:
                continue
            text = path.read_text(encoding="utf-8")
            new_text = text
            for old, new in replacements:
                new_text = new_text.replace(old, new)
            if new_text != text:
                path.write_text(new_text, encoding="utf-8")


def prepare_current_scene_for_edit() -> Path:
    scene_state = PROMPT2SCENE_ROOT / "gym_export" / "scene_state" / "result.json"
    if not scene_state.is_file():
        raise FileNotFoundError(
            f"Current prompt2scene scene state not found: {scene_state}"
        )
    if not FAST_GYM_CONFIG.is_file():
        raise FileNotFoundError(f"Current gym config not found: {FAST_GYM_CONFIG}")

    if not GRADIO_SCENE_GLB.is_file():
        build_gradio_scene_from_fast_config(FAST_GYM_CONFIG, GRADIO_SCENE_DIR)

    GRADIO_SCENE_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(GRADIO_SCENE_GLB, GRADIO_PREVIOUS_SCENE_GLB)
    errors = remove_path(GRADIO_SCENE_GLB)
    errors.extend(remove_path(SCENE_MANIFEST))
    if errors:
        raise RuntimeError("\n".join(errors))
    return GRADIO_PREVIOUS_SCENE_GLB


def run_generate(
    image_value: str | np.ndarray | Image.Image,
    task_text: str,
    env_text: str,
    *,
    force_initial: bool = False,
):
    task_text = (task_text or "").strip()
    env_text = (env_text or "").strip()
    mode = "edit" if env_text and not force_initial else "initial"
    old_sim_process: subprocess.Popen[str] | None = None
    with runtime_lock:
        if runtime.is_busy:
            yield ui_snapshot(extra_status="A pipeline run is already in progress.")
            return
        old_sim_process = runtime.sim_process
        runtime.sim_process = None
        runtime.sim_started = False
        runtime.sim_finished = False
        runtime.sim_returncode = None

    if old_sim_process is not None:
        terminate_process_group(old_sim_process)

    token = uuid.uuid4().hex
    stage = CURRENT_PATHS if mode == "edit" else make_stage_paths(token)
    previous_scene_path: Path | None = None
    try:
        if mode == "edit":
            if not task_text:
                raise ValueError("Please enter a task description.")
            previous_scene_path = prepare_current_scene_for_edit()
            image_path = IMAGE_PATH if IMAGE_PATH.is_file() else None
        else:
            image_path = save_input(image_value, task_text, stage.image_path)
    except Exception as exc:
        with runtime_lock:
            runtime.phase_key = "failed"
            runtime.status = f"Input error: {exc}"
            runtime.last_error = str(exc)
        yield ui_snapshot()
        return

    command = (
        build_edit_pipeline_command(task_text, env_text)
        if mode == "edit"
        else build_initial_pipeline_command(task_text, stage, env_text)
    )
    display_task_text = format_current_task(task_text, env_text)
    with runtime_lock:
        runtime.run_token = token
        runtime.is_busy = True
        runtime.phase_key = "received"
        runtime.status = (
            "Starting scene edit..."
            if mode == "edit"
            else "Input saved. Starting local pipeline..."
        )
        runtime.task_text = display_task_text
        runtime.image_path = image_path
        runtime.object_model_path = None
        runtime.scene_model_path = previous_scene_path
        runtime.edited_scene_model_path = None
        runtime.last_error = None
        runtime.sim_started = False
        runtime.sim_finished = False
        runtime.sim_returncode = None
        runtime.log_lines.clear()
        runtime.log_lines.append("$ " + " ".join(command))
    yield ui_snapshot()

    try:
        process = start_pipeline(command)
    except Exception as exc:
        with runtime_lock:
            runtime.is_busy = False
            runtime.process = None
            runtime.phase_key = "failed"
            runtime.status = f"Pipeline start failed: {exc}"
            runtime.last_error = str(exc)
        yield ui_snapshot()
        return

    output_queue: queue.Queue[str] = queue.Queue()
    reader = threading.Thread(
        target=read_process_output,
        args=(process, output_queue),
        daemon=True,
    )
    supervisor = threading.Thread(
        target=supervise_pipeline,
        args=(token, stage, mode, process, display_task_text, output_queue, reader),
        daemon=True,
    )

    with runtime_lock:
        if runtime.run_token != token:
            terminate_process_group(process)
            return
        runtime.process = process
        runtime.phase_key = "started"
        runtime.status = "Local pipeline started."
    reader.start()
    supervisor.start()
    yield ui_snapshot()

    while True:
        with runtime_lock:
            still_current = runtime.run_token == token
            busy = runtime.is_busy
        if not still_current or not busy:
            break
        time.sleep(1.0)
        yield ui_snapshot()
    yield ui_snapshot()


def start_auto_loop_state() -> str | None:
    process: subprocess.Popen[str] | None = None
    sim_process: subprocess.Popen[str] | None = None
    with runtime_lock:
        if runtime.auto_loop_active or runtime.is_busy:
            runtime.status = "A pipeline run is already in progress."
            return None

        token = uuid.uuid4().hex
        process = runtime.process
        sim_process = runtime.sim_process
        runtime.process = None
        runtime.sim_process = None
        runtime.sim_started = False
        runtime.sim_finished = False
        runtime.sim_returncode = None
        runtime.auto_loop_active = True
        runtime.auto_loop_token = token
        runtime.auto_round = 0
        runtime.phase_key = "received"
        runtime.status = "Auto loop starting."
        runtime.last_error = None
        runtime.log_lines.clear()

    if process is not None:
        terminate_process_group(process)
    if sim_process is not None:
        terminate_process_group(sim_process)
    return token


def auto_loop_is_active(loop_token: str) -> bool:
    with runtime_lock:
        return (
            runtime.auto_loop_active
            and runtime.auto_loop_token == loop_token
        )


def finish_auto_loop(loop_token: str, status_text: str | None = None) -> None:
    with runtime_lock:
        if runtime.auto_loop_token != loop_token:
            return
        runtime.auto_loop_active = False
        runtime.auto_loop_token = None
        runtime.auto_round = 0
        if status_text is not None:
            runtime.status = status_text


def stop_auto_loop_if_running() -> bool:
    process: subprocess.Popen[str] | None = None
    sim_process: subprocess.Popen[str] | None = None
    with runtime_lock:
        if not runtime.auto_loop_active:
            return False
        runtime.run_token = uuid.uuid4().hex
        runtime.auto_loop_active = False
        runtime.auto_loop_token = None
        runtime.auto_round = 0
        process = runtime.process
        sim_process = runtime.sim_process
        runtime.process = None
        runtime.sim_process = None
        runtime.sim_started = False
        runtime.sim_finished = False
        runtime.sim_returncode = None
        runtime.is_busy = False
        runtime.phase_key = "idle"
        runtime.status = "Stopped."
        runtime.task_text = ""
        runtime.image_path = None
        runtime.object_model_path = None
        runtime.scene_model_path = None
        runtime.edited_scene_model_path = None
        runtime.last_error = None
        runtime.log_lines.clear()

    if process is not None:
        terminate_process_group(process)
    if sim_process is not None:
        terminate_process_group(sim_process)
    return True


def wait_for_current_simulation_to_exit(
    loop_token: str,
    base_image: str,
    auto_task: str,
    auto_scene: str,
):
    while auto_loop_is_active(loop_token):
        with runtime_lock:
            sim_running = runtime.sim_process is not None
        if not sim_running:
            break
        time.sleep(1.0)
        yield (
            base_image,
            auto_task,
            auto_scene,
            *ui_snapshot(extra_status="Auto waiting for Dexsim to exit."),
        )


def run_generate_for_top_mode(
    top_mode: str,
    image_value: str | np.ndarray | Image.Image,
    task_text: str,
    env_text: str,
):
    if top_mode != TOP_MODE_AUTO:
        force_initial = top_mode == TOP_MODE_INTERACT
        for snapshot in run_generate(
            image_value,
            task_text,
            env_text,
            force_initial=force_initial,
        ):
            yield (gr.update(), gr.update(), gr.update(), *snapshot)
        return

    loop_token = start_auto_loop_state()
    if loop_token is None:
        yield (gr.update(), gr.update(), gr.update(), *ui_snapshot())
        return

    while auto_loop_is_active(loop_token):
        auto_task = ""
        auto_scene = ""
        task_label = "unknown"
        with runtime_lock:
            runtime.auto_round += 1
            auto_round = runtime.auto_round
            runtime.status = f"Auto round {auto_round}: cleaning previous artifacts."
            runtime.last_error = None
            runtime.sim_started = False
            runtime.sim_finished = False
            runtime.sim_returncode = None
            runtime.log_lines.clear()
            runtime.log_lines.append(f"Auto round {auto_round} started.")

        cleanup_errors = cleanup_auto_generated_artifacts()
        if cleanup_errors:
            with runtime_lock:
                runtime.log_lines.extend(cleanup_errors)

        if not auto_loop_is_active(loop_token):
            break

        try:
            auto_input = generate_auto_text_input()
        except Exception as exc:
            if not auto_loop_is_active(loop_token):
                break
            with runtime_lock:
                runtime.phase_key = "failed"
                runtime.status = f"Auto text generation failed: {exc}"
                runtime.last_error = str(exc)
                runtime.log_lines.append(runtime.status)
            yield (gr.update(), gr.update(), gr.update(), *ui_snapshot())
            archive_auto_round_log(
                auto_round=auto_round,
                task_label=task_label,
                task_description=auto_task,
                scene_description=auto_scene,
                outcome="text_generation_failed",
            )
            continue

        base_image = auto_input.base_image_path.as_posix()
        auto_task = auto_input.task_description
        auto_scene = auto_input.scene_description
        task_label = f"task{auto_input.task_index[0]}_{auto_input.task_index[1]}"
        with runtime_lock:
            runtime.task_text = format_current_task(auto_task, auto_scene)
            runtime.image_path = auto_input.base_image_path
            runtime.phase_key = "received"
            runtime.status = (
                f"Auto round {auto_round}: selected {task_label}. "
                "Starting prompt2scene pipeline."
            )
            runtime.last_error = None
            runtime.log_lines.append(
                f"Auto selected {task_label}: task={auto_task!r}, scene={auto_scene!r}"
            )
            if auto_scene:
                runtime.log_lines.append(f"Auto prompt2scene prompt: {auto_scene!r}")
        yield (
            base_image,
            auto_task,
            auto_scene,
            *ui_snapshot(extra_status=f"Auto text generated: {task_label}."),
        )

        if not auto_loop_is_active(loop_token):
            archive_auto_round_log(
                auto_round=auto_round,
                task_label=task_label,
                task_description=auto_task,
                scene_description=auto_scene,
                outcome="stopped",
            )
            break

        for snapshot in run_generate(
            base_image,
            auto_task,
            auto_scene,
            force_initial=True,
        ):
            yield (base_image, auto_task, auto_scene, *snapshot)
            if not auto_loop_is_active(loop_token):
                break

        if not auto_loop_is_active(loop_token):
            archive_auto_round_log(
                auto_round=auto_round,
                task_label=task_label,
                task_description=auto_task,
                scene_description=auto_scene,
                outcome="stopped",
            )
            break

        with runtime_lock:
            pipeline_failed = runtime.phase_key == "failed"
            pipeline_error = runtime.last_error
        if pipeline_failed:
            cleanup_auto_generated_artifacts()
            if pipeline_error:
                with runtime_lock:
                    runtime.last_error = pipeline_error
                    runtime.log_lines.append(
                        f"Auto continuing after failure: {pipeline_error}"
                    )
            archive_auto_round_log(
                auto_round=auto_round,
                task_label=task_label,
                task_description=auto_task,
                scene_description=auto_scene,
                outcome="pipeline_failed",
            )
            continue

        for snapshot in wait_for_current_simulation_to_exit(
            loop_token,
            base_image,
            auto_task,
            auto_scene,
        ):
            yield snapshot

        if not auto_loop_is_active(loop_token):
            archive_auto_round_log(
                auto_round=auto_round,
                task_label=task_label,
                task_description=auto_task,
                scene_description=auto_scene,
                outcome="stopped",
            )
            break

        cleanup_errors = cleanup_auto_generated_artifacts()
        if cleanup_errors:
            with runtime_lock:
                runtime.log_lines.extend(cleanup_errors)
        with runtime_lock:
            simulation_completed = (
                runtime.sim_started
                and runtime.sim_finished
                and runtime.sim_process is None
            )
            round_outcome = "completed" if simulation_completed else "simulation_failed"
        archive_auto_round_log(
            auto_round=auto_round,
            task_label=task_label,
            task_description=auto_task,
            scene_description=auto_scene,
            outcome=round_outcome,
        )

    finish_auto_loop(loop_token)


def supervise_pipeline(
    token: str,
    stage: ScenePaths,
    mode: str,
    process: subprocess.Popen[str],
    display_task_text: str,
    output_queue: queue.Queue[str],
    reader: threading.Thread,
) -> None:
    is_edit = mode == "edit"
    scene_build_error: str | None = None
    simulation_error: str | None = None
    simulation_started = False
    try:
        while True:
            with runtime_lock:
                still_current = runtime.run_token == token
            if not still_current:
                terminate_process_group(process)
                return

            drained = drain_output_queue(output_queue)
            if drained:
                with runtime_lock:
                    for line in drained:
                        runtime.log_lines.append(line)
                        runtime.phase_key = update_phase_from_log(line, runtime.phase_key)

            with runtime_lock:
                detected_key = detect_phase_from_files(runtime.phase_key, stage)
                runtime.phase_key = detected_key
                if detected_key in PHASES and runtime.phase_key != "failed":
                    runtime.status = PHASES[detected_key].label + "."

            glb_paths = collect_generated_object_glbs(stage)
            if (
                glb_paths
                and (
                    not stage.gradio_object_preview_glb.is_file()
                    or not object_preview_is_current(
                        stage.object_preview_manifest,
                        glb_paths,
                    )
                )
            ):
                try:
                    object_preview_path = build_object_preview_scene(
                        glb_paths,
                        stage.gradio_scene_dir,
                    )
                    with runtime_lock:
                        runtime.object_model_path = object_preview_path
                        runtime.phase_key = _choose_later_phase(
                            runtime.phase_key,
                            PHASES.get(runtime.phase_key, PHASES["idle"]).progress,
                            "asset_generation",
                        )[0]
                        runtime.status = (
                            f"Generated object GLB preview loaded "
                            f"({len(glb_paths)} files)."
                        )
                except Exception as exc:
                    with runtime_lock:
                        runtime.log_lines.append(f"Object preview pending: {exc}")

            if (
                not is_edit
                and stage.fast_gym_config.is_file()
                and not stage.gradio_scene_glb.is_file()
            ):
                try:
                    scene_path = build_gradio_scene_from_fast_config(
                        stage.fast_gym_config,
                        stage.gradio_scene_dir,
                    )
                    scene_build_error = None
                    with runtime_lock:
                        runtime.scene_model_path = scene_path
                        runtime.phase_key = "preview"
                        runtime.status = "3D preview loaded."
                        runtime.last_error = None
                except Exception as exc:
                    scene_build_error = str(exc)
                    with runtime_lock:
                        runtime.log_lines.append(f"3D preview error: {scene_build_error}")
                        runtime.last_error = scene_build_error

            if process.poll() is not None:
                break
            time.sleep(0.5)

        reader.join(timeout=1.0)
        drained = drain_output_queue(output_queue)
        with runtime_lock:
            for line in drained:
                runtime.log_lines.append(line)
                runtime.phase_key = update_phase_from_log(line, runtime.phase_key)

        glb_paths = collect_generated_object_glbs(stage)
        if (
            glb_paths
            and (
                not stage.gradio_object_preview_glb.is_file()
                or not object_preview_is_current(stage.object_preview_manifest, glb_paths)
            )
        ):
            try:
                object_preview_path = build_object_preview_scene(
                    glb_paths,
                    stage.gradio_scene_dir,
                )
                with runtime_lock:
                    runtime.object_model_path = object_preview_path
            except Exception as exc:
                with runtime_lock:
                    runtime.log_lines.append(f"Object preview skipped: {exc}")

        if (
            stage.fast_gym_config.is_file()
            and (not is_edit or process.returncode == 0)
            and not stage.gradio_scene_glb.is_file()
        ):
            try:
                scene_path = build_gradio_scene_from_fast_config(
                    stage.fast_gym_config,
                    stage.gradio_scene_dir,
                )
                scene_build_error = None
                with runtime_lock:
                    if is_edit:
                        runtime.edited_scene_model_path = scene_path
                    else:
                        runtime.scene_model_path = scene_path
                    runtime.phase_key = "preview"
                    runtime.status = "3D preview loaded."
                    runtime.last_error = None
            except Exception as exc:
                scene_build_error = str(exc)

        cleanup_errors: list[str] = []
        promotion_error: str | None = None
        pipeline_output_ready = stage.fast_gym_config.is_file()
        pipeline_succeeded = (
            process.returncode == 0
            and pipeline_output_ready
            and not scene_build_error
        )
        if pipeline_succeeded:
            if is_edit:
                with runtime_lock:
                    if runtime.run_token == token:
                        runtime.image_path = IMAGE_PATH if IMAGE_PATH.is_file() else None
                        if GRADIO_OBJECT_PREVIEW_GLB.is_file():
                            runtime.object_model_path = GRADIO_OBJECT_PREVIEW_GLB
                        if GRADIO_PREVIOUS_SCENE_GLB.is_file():
                            runtime.scene_model_path = GRADIO_PREVIOUS_SCENE_GLB
                        if GRADIO_SCENE_GLB.is_file():
                            runtime.edited_scene_model_path = GRADIO_SCENE_GLB
                simulation_error = launch_current_simulation(token)
                simulation_started = simulation_error is None
            else:
                try:
                    cleanup_errors = promote_stage_to_current(stage, token)
                except Exception as exc:
                    promotion_error = str(exc)
                else:
                    with runtime_lock:
                        if runtime.run_token == token:
                            runtime.image_path = IMAGE_PATH
                            if GRADIO_OBJECT_PREVIEW_GLB.is_file():
                                runtime.object_model_path = GRADIO_OBJECT_PREVIEW_GLB
                            if GRADIO_SCENE_GLB.is_file():
                                runtime.scene_model_path = GRADIO_SCENE_GLB
                            runtime.edited_scene_model_path = None
                    simulation_error = launch_current_simulation(token)
                    simulation_started = simulation_error is None

        with runtime_lock:
            if runtime.run_token != token:
                return
            runtime.is_busy = False
            runtime.process = None
            if pipeline_succeeded and not promotion_error:
                runtime.phase_key = "complete"
                runtime.status = "Pipeline completed successfully."
                runtime.task_text = display_task_text
                runtime.image_path = IMAGE_PATH if IMAGE_PATH.is_file() else None
                if GRADIO_OBJECT_PREVIEW_GLB.is_file():
                    runtime.object_model_path = GRADIO_OBJECT_PREVIEW_GLB
                if is_edit:
                    if GRADIO_PREVIOUS_SCENE_GLB.is_file():
                        runtime.scene_model_path = GRADIO_PREVIOUS_SCENE_GLB
                    if GRADIO_SCENE_GLB.is_file():
                        runtime.edited_scene_model_path = GRADIO_SCENE_GLB
                elif GRADIO_SCENE_GLB.is_file():
                    runtime.scene_model_path = GRADIO_SCENE_GLB
                    runtime.edited_scene_model_path = None
                if simulation_started:
                    runtime.status += "\nDexsim simulation launched."
                if cleanup_errors:
                    runtime.status += "\nCleanup completed with errors; see Last error."
                    runtime.last_error = "\n".join(cleanup_errors)
                if simulation_error:
                    runtime.status += "\nDexsim launch failed; Gradio preview is still available."
                    runtime.last_error = simulation_error
            elif process.returncode == 0 and not pipeline_output_ready:
                runtime.phase_key = "failed"
                runtime.status = f"Pipeline ended without {FAST_GYM_CONFIG.name}."
                runtime.last_error = runtime.status
            elif scene_build_error:
                runtime.phase_key = "failed"
                runtime.status = f"3D preview failed: {scene_build_error}"
                runtime.last_error = scene_build_error
            elif promotion_error:
                runtime.phase_key = "failed"
                runtime.status = f"Scene promotion failed: {promotion_error}"
                runtime.last_error = promotion_error
            else:
                runtime.phase_key = "failed"
                runtime.status = f"Pipeline failed with return code {process.returncode}."
                runtime.last_error = runtime.status
    except Exception as exc:
        with runtime_lock:
            if runtime.run_token == token:
                runtime.is_busy = False
                runtime.process = None
                runtime.phase_key = "failed"
                runtime.status = f"Pipeline supervision failed: {exc}"
                runtime.last_error = str(exc)


def launch_current_simulation(token: str) -> str | None:
    if not CURRENT_PATHS.fast_gym_config.is_file():
        return f"Dexsim launch skipped; missing {CURRENT_PATHS.fast_gym_config}"
    if not CURRENT_PATHS.agent_config.is_file():
        return f"Dexsim launch skipped; missing {CURRENT_PATHS.agent_config}"

    command = build_run_agent_command(CURRENT_PATHS)
    try:
        process = start_pipeline(command)
    except Exception as exc:
        return f"Dexsim launch failed: {exc}"

    output_queue: queue.Queue[str] = queue.Queue()
    reader = threading.Thread(
        target=read_process_output,
        args=(process, output_queue),
        daemon=True,
    )
    monitor = threading.Thread(
        target=monitor_simulation,
        args=(token, process, output_queue, reader),
        daemon=True,
    )

    with runtime_lock:
        if runtime.run_token != token:
            stale = True
        else:
            stale = False
            runtime.sim_process = process
            runtime.sim_started = True
            runtime.sim_finished = False
            runtime.sim_returncode = None
            runtime.log_lines.append("$ " + " ".join(command))

    if stale:
        terminate_process_group(process)
        return None

    reader.start()
    monitor.start()
    return None


def monitor_simulation(
    token: str,
    process: subprocess.Popen[str],
    output_queue: queue.Queue[str],
    reader: threading.Thread,
) -> None:
    while process.poll() is None:
        append_simulation_logs(token, process, drain_output_queue(output_queue))
        time.sleep(0.5)

    reader.join(timeout=1.0)
    append_simulation_logs(token, process, drain_output_queue(output_queue))

    with runtime_lock:
        if runtime.run_token != token or runtime.sim_process is not process:
            return
        runtime.sim_process = None
        runtime.sim_finished = True
        runtime.sim_returncode = process.returncode
        if process.returncode == 0:
            runtime.status = "Pipeline completed successfully.\nDexsim simulation finished."
        else:
            runtime.status = (
                "Pipeline completed successfully.\n"
                f"Dexsim simulation exited with return code {process.returncode}."
            )
            runtime.log_lines.append(
                f"Dexsim simulation exited with return code {process.returncode}."
            )


def append_simulation_logs(
    token: str,
    process: subprocess.Popen[str],
    lines: list[str],
) -> None:
    if not lines:
        return
    with runtime_lock:
        if runtime.run_token != token or runtime.sim_process is not process:
            return
        for line in lines:
            runtime.log_lines.append(line)


def drain_output_queue(output_queue: queue.Queue[str]) -> list[str]:
    lines: list[str] = []
    while True:
        try:
            lines.append(output_queue.get_nowait())
        except queue.Empty:
            return lines


def run_reset():
    cleanup_errors = reset_current_scene()
    last_error = "\n".join(cleanup_errors) if cleanup_errors else None
    status_text = (
        "Reset complete."
        if not cleanup_errors
        else "Reset completed, but some cleanup failed."
    )
    return (
        None,
        "",
        "",
        None,
        "",
        PHASES["idle"].progress,
        format_status(status_text, last_error=last_error),
        None,
        None,
        None,
    )


def stop_current_run_without_cleanup():
    process: subprocess.Popen[str] | None = None
    sim_process: subprocess.Popen[str] | None = None
    with runtime_lock:
        runtime.run_token = uuid.uuid4().hex
        runtime.auto_loop_active = False
        runtime.auto_loop_token = None
        runtime.auto_round = 0
        process = runtime.process
        sim_process = runtime.sim_process
        runtime.process = None
        runtime.sim_process = None
        runtime.sim_started = False
        runtime.sim_finished = False
        runtime.sim_returncode = None
        runtime.is_busy = False
        runtime.phase_key = "idle"
        runtime.status = "Stopped."
        runtime.task_text = ""
        runtime.image_path = None
        runtime.object_model_path = None
        runtime.scene_model_path = None
        runtime.edited_scene_model_path = None
        runtime.last_error = None
        runtime.log_lines.clear()

    if process is not None:
        terminate_process_group(process)
    if sim_process is not None:
        terminate_process_group(sim_process)

    return (
        None,
        "",
        "",
        None,
        "",
        PHASES["idle"].progress,
        format_status("Stopped."),
        None,
        None,
        None,
    )


def run_reset_or_stop(top_mode: str):
    if top_mode == TOP_MODE_AUTO:
        return stop_current_run_without_cleanup()
    return run_reset()


def select_top_mode(top_mode: str):
    if top_mode != TOP_MODE_AUTO:
        stop_auto_loop_if_running()
    is_auto = top_mode == TOP_MODE_AUTO
    is_interact = top_mode == TOP_MODE_INTERACT
    is_robot_model = top_mode == TOP_MODE_ROBOT_MODEL
    is_parallel_env = top_mode == TOP_MODE_PARALLEL_ENV
    return (
        gr.update(variant="primary" if is_auto else "secondary"),
        gr.update(variant="primary" if is_interact else "secondary"),
        gr.update(variant="primary" if is_robot_model else "secondary"),
        gr.update(variant="primary" if is_parallel_env else "secondary"),
        gr.update(value="Stop" if is_auto else "Reset"),
        top_mode,
    )


def ui_snapshot(extra_status: str | None = None):
    with runtime_lock:
        phase = PHASES.get(runtime.phase_key, PHASES["idle"])
        image_value = runtime.image_path.as_posix() if runtime.image_path else None
        object_model_value = (
            runtime.object_model_path.as_posix()
            if runtime.object_model_path and runtime.object_model_path.is_file()
            else None
        )
        model_value = (
            runtime.scene_model_path.as_posix()
            if runtime.scene_model_path and runtime.scene_model_path.is_file()
            else None
        )
        edited_model_value = (
            runtime.edited_scene_model_path.as_posix()
            if runtime.edited_scene_model_path
            and runtime.edited_scene_model_path.is_file()
            else None
        )
        task_text = runtime.task_text
        status_text = runtime.status
        if extra_status:
            status_text = f"{status_text}\n{extra_status}"
        logs = list(runtime.log_lines)[-16:]
        busy = runtime.is_busy
        last_error = runtime.last_error
    return (
        image_value,
        task_text,
        phase.progress,
        format_status(
            status_text,
            phase=phase,
            busy=busy,
            last_error=last_error,
            logs=logs,
        ),
        model_value,
        edited_model_value,
        object_model_value,
    )


def format_status(
    status_text: str,
    *,
    phase: Phase | None = None,
    busy: bool = False,
    last_error: str | None = None,
    logs: list[str] | None = None,
) -> str:
    if phase is None:
        phase = PHASES["idle"]
    state = "running" if busy else "ready"
    parts = [
        f"**State:** {state}",
        f"**Phase:** {phase.progress}% - {phase.label}",
        f"**Status:** {status_text}",
    ]
    if last_error:
        escaped_error = last_error.replace("`", "'")
        if "\n" in escaped_error:
            parts.append(f"**Last error:**\n```text\n{escaped_error}\n```")
        else:
            parts.append(f"**Last error:** `{escaped_error}`")
    if logs:
        escaped = "\n".join(line.replace("`", "'") for line in logs[-16:])
        parts.append(f"**Recent logs:**\n```text\n{escaped}\n```")
    return "\n\n".join(parts)


def build_demo() -> gr.Blocks:
    with gr.Blocks(title="EmbodiChain Gradio") as demo:
        top_mode = gr.State(TOP_MODE_ROBOT_MODEL)
        with gr.Row():
            gr.Markdown("# Generative Simulation User Interface")
            auto_button = gr.Button("Auto", variant="secondary")
            interact_button = gr.Button("Interact", variant="secondary")
            robot_model_button = gr.Button("Robot Model", variant="primary")
            parallel_env_button = gr.Button("Parallel Env", variant="secondary")
        gr.Markdown(
            "Upload one image, enter one task, and the local EmbodiChain pipeline "
            "will generate the current scene."
        )

        with gr.Row():
            with gr.Column(scale=1):
                image_input = gr.Image(
                    label="Input image",
                    sources=["upload", "webcam"],
                    type="filepath",
                    format="png",
                    height=320,
                )
                with gr.Row():
                    task_input = gr.Textbox(
                        label="Task description",
                        placeholder="把中间的水瓶放到书上",
                        lines=1,
                    )
                    env_input = gr.Textbox(
                        label="Scene description",
                        placeholder="",
                        lines=1,
                    )
                with gr.Row():
                    generate_button = gr.Button("Generate", variant="primary")
                    reset_button = gr.Button("Reset", variant="stop")
            with gr.Column(scale=1):
                current_image = gr.Image(
                    label="Current saved image",
                    type="filepath",
                    interactive=False,
                    height=320,
                )
                current_task = gr.Textbox(
                    label="Current task",
                    interactive=False,
                    lines=2,
                )

        progress = gr.Slider(
            minimum=0,
            maximum=100,
            value=0,
            step=1,
            label="Progress",
            interactive=False,
        )
        status = gr.Markdown(format_status("Idle."))
        with gr.Row():
            model = gr.Model3D(
                label="Initial scene preview",
                height=520,
                clear_color=(0.94, 0.94, 0.94, 1.0),
            )
            edited_model = gr.Model3D(
                label="Edited scene preview",
                height=520,
                clear_color=(0.94, 0.94, 0.94, 1.0),
            )
        object_model = gr.Model3D(
            label="Generated object GLBs preview",
            height=360,
            clear_color=(0.94, 0.94, 0.94, 1.0),
        )

        refresh_timer = gr.Timer(2.0)
        top_mode_outputs = [
            auto_button,
            interact_button,
            robot_model_button,
            parallel_env_button,
            reset_button,
            top_mode,
        ]
        auto_button.click(
            select_top_mode,
            inputs=[gr.State(TOP_MODE_AUTO)],
            outputs=top_mode_outputs,
            queue=False,
        )
        interact_button.click(
            select_top_mode,
            inputs=[gr.State(TOP_MODE_INTERACT)],
            outputs=top_mode_outputs,
            queue=False,
        )
        robot_model_button.click(
            select_top_mode,
            inputs=[gr.State(TOP_MODE_ROBOT_MODEL)],
            outputs=top_mode_outputs,
            queue=False,
        )
        parallel_env_button.click(
            select_top_mode,
            inputs=[gr.State(TOP_MODE_PARALLEL_ENV)],
            outputs=top_mode_outputs,
            queue=False,
        )
        generate_button.click(
            run_generate_for_top_mode,
            inputs=[top_mode, image_input, task_input, env_input],
            outputs=[
                image_input,
                task_input,
                env_input,
                current_image,
                current_task,
                progress,
                status,
                model,
                edited_model,
                object_model,
            ],
        )
        reset_button.click(
            run_reset_or_stop,
            inputs=[top_mode],
            outputs=[
                image_input,
                task_input,
                env_input,
                current_image,
                current_task,
                progress,
                status,
                model,
                edited_model,
                object_model,
            ],
            queue=False,
        )
        refresh_timer.tick(
            ui_snapshot,
            inputs=[],
            outputs=[
                current_image,
                current_task,
                progress,
                status,
                model,
                edited_model,
                object_model,
            ],
            queue=False,
        )
    return demo


def main() -> None:
    if not EMBODICHAIN_ROOT.is_dir():
        raise FileNotFoundError(f"EmbodiChain root not found: {EMBODICHAIN_ROOT}")
    demo = build_demo()
    demo.queue(default_concurrency_limit=1)
    demo.launch(
        server_name=os.environ.get("GRADIO_SERVER_NAME", "0.0.0.0"),
        server_port=int(os.environ.get("GRADIO_SERVER_PORT", "7860")),
        allowed_paths=[str(EMBODICHAIN_ROOT)],
    )


if __name__ == "__main__":
    main()
