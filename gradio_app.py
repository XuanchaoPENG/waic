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
from random_input import (
    auto_image_directories,
    available_auto_task_indices,
    generate_auto_text_input,
)

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
from PIL import Image, ImageDraw, ImageOps


EMBODICHAIN_ROOT = Path(
    os.environ.get("EMBODICHAIN_ROOT", "/home/dex/workspace/sources/EmbodiChain")
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
CURRENT_GYM_EXPORT_DIR = PROMPT2SCENE_ROOT / "gym_export"
CURRENT_GYM_EXPORT_CONFIG = CURRENT_GYM_EXPORT_DIR / "gym_config.json"
GRADIO_SCENE_DIR = CONFIG_DIR / "gradio_scene"
GRADIO_SCENE_GLB = GRADIO_SCENE_DIR / "scene_current.glb"
GRADIO_INITIAL_SCENE_GLB = GRADIO_SCENE_DIR / "initial_scene.glb"
GRADIO_OBJECT_PREVIEW_GLB = GRADIO_SCENE_DIR / "object_preview.glb"
SCENE_MANIFEST = GRADIO_SCENE_DIR / "scene_manifest.json"
PENDING_PREFIX = "_gradio_pending_"
REPLACED_PREFIX = "_gradio_replaced_"
GRADIO_SCENE_TRANSFORM_POLICY = "dexsim_gltf_y_up_to_sim_z_up_v1"

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
VIDEO_SUFFIXES = {".mp4", ".avi", ".mov", ".mkv", ".webm"}
LEROBOT_PREVIEW_DIR = OUTPUTS_DIR / "lerobot_previews"
LEROBOT_PREVIEW_MAX_FRAMES = 360
TOP_MODE_AUTO = "auto"
TOP_MODE_INTERACT = "interact"
TOP_MODE_PARALLEL_ENV = "parallel_env"
LANGUAGE_EN = "en"
LANGUAGE_ZH = "zh"
BUTTON_LABELS = {
    LANGUAGE_EN: {
        "auto": "Auto",
        "interact": "Interact",
        "parallel_env": "Parallel Simulation",
        "generate": "Generate",
        "random_input": "Random Input",
        "reset": "Reset",
        "stop": "Stop",
        "language": "中文",
    },
    LANGUAGE_ZH: {
        "auto": "自动",
        "interact": "交互",
        "parallel_env": "并行仿真",
        "generate": "生成",
        "random_input": "随机填充",
        "reset": "重置",
        "stop": "停止",
        "language": "English",
    },
}
UI_TEXT = {
    LANGUAGE_EN: {
        "heading": "# Generative Simulation User Interface",
        "instruction": (
            "Upload one image, enter one task, then EmbodiChain "
            "will generate what you want."
        ),
        "robot": "Robot",
        "input_image": "Input image",
        "task_description": "Task description",
        "task_placeholder": "Put the middle bottle on the book",
        "scene_description": "Scene description",
        "scene_placeholder": "Optional: describe how to edit the current scene",
        "current_video": "Current saved video",
        "lerobot_preview": "LeRobot data preview",
        "current_task": "Current task",
        "progress": "Progress",
        "initial_preview": "Initial scene preview",
        "edited_preview": "Edited scene preview",
        "object_preview": "Generated object GLBs preview",
    },
    LANGUAGE_ZH: {
        "heading": "# 生成式仿真用户界面",
        "instruction": "上传一张图片，输入一个任务，EmbodiChain 将生成所需的仿真。",
        "robot": "机器人",
        "input_image": "输入图像",
        "task_description": "任务描述",
        "task_placeholder": "把中间的水瓶放到书上",
        "scene_description": "场景描述",
        "scene_placeholder": "可选：描述如何编辑当前场景",
        "current_video": "当前保存的视频",
        "lerobot_preview": "LeRobot 数据预览",
        "current_task": "当前任务",
        "progress": "进度",
        "initial_preview": "初始场景预览",
        "edited_preview": "编辑后场景预览",
        "object_preview": "生成对象 GLB 预览",
    },
}
PIPELINE_MODE_INITIAL = "initial"
PIPELINE_MODE_EDIT = "edit"
PIPELINE_MODE_TASK_ONLY = "task_only"
ROBOT_PROFILE_FRANKA = "Franka"
ROBOT_PROFILE_UR5 = "UR5"
RUN_LOG_MODE_AUTO = "auto"
RUN_LOG_MODE_INTERACT = "interact"
VIDEO_SYNC_JS = r"""
() => {
    const audienceRootId = "embodichain-audience-video";
    const lerobotRootId = "embodichain-lerobot-video";
    let syncing = false;

    function findVideo(rootId) {
        const root = document.getElementById(rootId);
        return root ? root.querySelector("video") : null;
    }

    function sourceLoaded(video) {
        return Boolean(video && (video.currentSrc || video.src));
    }

    function copyTime(source, target) {
        if (!sourceLoaded(source) || !sourceLoaded(target)) {
            return;
        }
        const sourceTime = source.currentTime || 0;
        if (!Number.isFinite(sourceTime)) {
            return;
        }
        const duration = Number.isFinite(target.duration) ? target.duration : sourceTime;
        const targetTime = Math.min(sourceTime, duration);
        if (Math.abs((target.currentTime || 0) - targetTime) > 0.35) {
            try {
                target.currentTime = targetTime;
            } catch (_) {
                // Some browsers reject seeking before metadata is fully available.
            }
        }
    }

    function syncPlayback(sourceRootId, targetRootId, shouldPlay) {
        if (syncing) {
            return;
        }
        const source = findVideo(sourceRootId);
        const target = findVideo(targetRootId);
        if (!sourceLoaded(source) || !sourceLoaded(target)) {
            return;
        }

        syncing = true;
        copyTime(source, target);

        const release = () => {
            window.setTimeout(() => {
                syncing = false;
            }, 0);
        };

        if (shouldPlay) {
            const result = target.play();
            if (result && typeof result.finally === "function") {
                result.catch(() => {}).finally(release);
            } else {
                release();
            }
        } else {
            target.pause();
            release();
        }
    }

    function bindOne(rootId, peerRootId) {
        const video = findVideo(rootId);
        if (!sourceLoaded(video) || video.dataset.embodichainSyncBound === "true") {
            return;
        }
        video.dataset.embodichainSyncBound = "true";
        video.addEventListener("play", () => syncPlayback(rootId, peerRootId, true));
        video.addEventListener("pause", () => syncPlayback(rootId, peerRootId, false));
    }

    function bindVideos() {
        bindOne(audienceRootId, lerobotRootId);
        bindOne(lerobotRootId, audienceRootId);
    }

    bindVideos();
    window.setInterval(bindVideos, 1000);
    const observer = new MutationObserver(bindVideos);
    observer.observe(document.body, { childList: true, subtree: true });
}
"""


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
    input_task_text: str = ""
    input_scene_text: str = ""
    image_path: Path | None = None
    video_path: Path | None = None
    lerobot_video_path: Path | None = None
    lerobot_dataset_path: Path | None = None
    object_model_path: Path | None = None
    scene_model_path: Path | None = None
    edited_scene_model_path: Path | None = None
    last_error: str | None = None
    log_lines: deque[str] = field(default_factory=deque)


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
        runtime.input_task_text = ""
        runtime.input_scene_text = ""
        runtime.image_path = None
        runtime.video_path = None
        runtime.lerobot_video_path = None
        runtime.lerobot_dataset_path = None
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
        IMAGE_PATH,
        *pending_artifact_paths(),
    ]
    for path in paths:
        errors.extend(remove_path(path))
    errors.extend(cleanup_outputs_preserving_videos())
    return errors


def cleanup_auto_generated_artifacts(extra_image_path: Path | None = None) -> list[str]:
    errors: list[str] = []
    paths: list[Path] = [
        PROMPT2SCENE_ROOT,
        CONFIG_DIR,
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
        runtime.input_task_text = ""
        runtime.input_scene_text = ""
        runtime.lerobot_video_path = None
        runtime.lerobot_dataset_path = None
        runtime.object_model_path = None
        runtime.scene_model_path = None
        runtime.edited_scene_model_path = None
    errors.extend(cleanup_outputs_preserving_videos())
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


def cleanup_outputs_preserving_videos() -> list[str]:
    if not OUTPUTS_DIR.exists():
        return []
    if OUTPUTS_DIR.is_file():
        if OUTPUTS_DIR.suffix.lower() in VIDEO_SUFFIXES:
            return []
        return remove_path(OUTPUTS_DIR)

    errors: list[str] = []
    for path in sorted(
        OUTPUTS_DIR.rglob("*"),
        key=lambda item: len(item.parts),
        reverse=True,
    ):
        if path.is_file() and path.suffix.lower() not in VIDEO_SUFFIXES:
            errors.extend(remove_path(path))

    for path in sorted(
        OUTPUTS_DIR.rglob("*"),
        key=lambda item: len(item.parts),
        reverse=True,
    ):
        if not path.is_dir():
            continue
        try:
            path.rmdir()
        except OSError:
            pass
        except Exception as exc:
            errors.append(f"Failed to remove empty output directory {path}: {exc}")
    return errors


def build_initial_pipeline_command(
    task_text: str,
    paths: ScenePaths,
    prompt2scene_prompt: str = "",
    robot_profile: str | None = None,
) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "embodichain.gen_sim.action_agent_pipeline.cli.run_agent_pipeline",
        "--use-prompt2scene",
        "--image",
        str(paths.image_path.resolve()),
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
    profile = robot_profile_cli_value(robot_profile)
    if profile:
        command.extend(["--robot-profile", profile])
    if prompt2scene_prompt.strip():
        command.extend(["--prompt2scene-prompt", prompt2scene_prompt.strip()])
    return command


def build_edit_pipeline_command(
    task_text: str,
    env_text: str,
    robot_profile: str | None = None,
) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "embodichain.gen_sim.action_agent_pipeline.cli.run_agent_pipeline",
        "--use-prompt2scene",
        "--prompt2scene-output-root",
        "gym_project/current",
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
    profile = robot_profile_cli_value(robot_profile)
    if profile:
        command.extend(["--robot-profile", profile])
    return command


def build_task_only_config_command(
    task_text: str,
    robot_profile: str | None = None,
) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "embodichain.gen_sim.action_agent_pipeline.cli.generate_action_agent_config",
        "--gym_project",
        "gym_project/current/gym_export",
        "--output_dir",
        "gym_project/action_agent_pipeline/configs/current",
        "--task_name",
        SCENE_ID,
        "--task_description",
        task_text,
        "--target_body_scale",
        "1.3",
        "--overwrite",
    ]
    profile = robot_profile_cli_value(robot_profile)
    if profile:
        command.extend(["--robot-profile", profile])
    return command


def robot_profile_cli_value(robot_profile: str | None) -> str | None:
    if robot_profile == ROBOT_PROFILE_FRANKA:
        return "franka"
    if robot_profile == ROBOT_PROFILE_UR5:
        return "dual_ur5"
    return None


def format_current_task(task_text: str, env_text: str = "") -> str:
    return "\n".join(
        part for part in ((task_text or "").strip(), (env_text or "").strip()) if part
    )


def archive_run_log(
    *,
    mode: str,
    task_description: str = "",
    scene_description: str = "",
    outcome: str,
    audience_video: Path | None = None,
) -> Path | None:
    with runtime_lock:
        run_logs = list(runtime.log_lines)
        status_text = runtime.status
        last_error = runtime.last_error
        runtime_video = runtime.video_path

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    run_dir = make_next_log_archive_dir()
    video_paths, video_errors = archive_audience_video(
        run_dir,
        audience_video or runtime_video,
    )
    log_path = run_dir / "log.md"
    content = [
        f"mode: {mode}",
        "",
        f"Timestamp: {timestamp}",
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
    if video_paths:
        content.extend(
            [
                "",
                "## Archived audience video",
                "",
                *[path.as_posix() for path in video_paths],
            ]
        )
    if video_errors:
        content.extend(["", "## Video archive errors", "", *video_errors])
    content.extend(
        [
            "",
            "## Logs",
            "",
            "```text",
            "\n".join(run_logs) if run_logs else "(no logs)",
            "```",
            "",
        ]
    )

    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        log_path.write_text("\n".join(content), encoding="utf-8")
    except Exception as exc:
        with runtime_lock:
            runtime.log_lines.append(f"Failed to archive run log: {exc}")
        return None
    return log_path


def make_next_log_archive_dir() -> Path:
    AUTO_LOG_DIR.mkdir(parents=True, exist_ok=True)
    existing_indices = [
        int(path.name)
        for path in AUTO_LOG_DIR.iterdir()
        if path.is_dir() and path.name.isdigit()
    ]
    next_index = (max(existing_indices) + 1) if existing_indices else 1
    while True:
        candidate = AUTO_LOG_DIR / f"{next_index:04d}"
        if not candidate.exists():
            try:
                candidate.mkdir(parents=True, exist_ok=False)
                return candidate
            except FileExistsError:
                pass
        next_index += 1


def archive_audience_video(
    run_dir: Path,
    video_path: Path | None,
) -> tuple[list[Path], list[str]]:
    copied_paths: list[Path] = []
    errors: list[str] = []
    if video_path is None:
        return copied_paths, errors
    if not video_path.is_file():
        return copied_paths, [f"Audience video not found: {video_path}"]
    destination = run_dir / "audience_video" / video_path.name
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(video_path, destination)
    except Exception as exc:
        errors.append(f"Failed to archive audience video {video_path}: {exc}")
        return copied_paths, errors
    copied_paths.append(destination.relative_to(run_dir))
    return copied_paths, errors


def archived_audience_video_path(log_path: Path | None) -> Path | None:
    """Return the audience-video copy created alongside an archived run log."""
    if log_path is None:
        return None
    archive_dir = log_path.parent / "audience_video"
    if not archive_dir.is_dir():
        return None
    videos = [
        path
        for path in archive_dir.iterdir()
        if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES
    ]
    if not videos:
        return None
    return max(videos, key=lambda path: path.stat().st_mtime_ns)


def collect_output_videos() -> list[Path]:
    if not OUTPUTS_DIR.is_dir():
        return []
    return sorted(
        path
        for path in OUTPUTS_DIR.rglob("*")
        if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES
    )


def collect_audience_output_videos() -> list[Path]:
    videos = collect_output_videos()
    audience_videos = [
        path
        for path in videos
        if "audience" in path.relative_to(OUTPUTS_DIR).as_posix().lower()
    ]
    if audience_videos:
        return audience_videos
    return [
        path
        for path in videos
        if "audience" in path.relative_to(OUTPUTS_DIR).as_posix().lower()
    ]


def latest_audience_output_video(min_mtime_ns: int | None = None) -> Path | None:
    latest_path: Path | None = None
    latest_mtime = -1
    for path in collect_audience_output_videos():
        try:
            mtime = path.stat().st_mtime_ns
        except OSError:
            continue
        if min_mtime_ns is not None and mtime < min_mtime_ns:
            continue
        if mtime > latest_mtime:
            latest_path = path
            latest_mtime = mtime
    return latest_path


def configured_lerobot_roots() -> list[Path]:
    roots: list[Path] = []
    env_root = os.environ.get("EMBODICHAIN_DATASET_ROOT")
    if env_root:
        roots.append(Path(env_root).expanduser())
    roots.append(Path("~/.cache/embodichain_datasets").expanduser())

    config_roots = read_lerobot_save_paths(CURRENT_PATHS.fast_gym_config)
    roots.extend(config_roots)

    normalized: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        root = root.expanduser()
        if not root.is_absolute():
            root = EMBODICHAIN_ROOT / root
        try:
            resolved = root.resolve()
        except OSError:
            resolved = root
        if resolved in seen:
            continue
        seen.add(resolved)
        normalized.append(root)
    return normalized


def read_lerobot_save_paths(config_path: Path) -> list[Path]:
    if not config_path.is_file():
        return []
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return []

    paths: list[Path] = []

    def visit(value: Any, key_path: tuple[str, ...] = ()) -> None:
        if isinstance(value, dict):
            if (
                key_path[-2:] == ("lerobot", "params")
                and isinstance(value.get("save_path"), str)
            ):
                paths.append(Path(value["save_path"]))
            for key, child in value.items():
                visit(child, (*key_path, str(key)))
        elif isinstance(value, list):
            for item in value:
                visit(item, key_path)

    visit(config)
    return paths


def collect_lerobot_datasets() -> list[Path]:
    datasets: list[Path] = []
    for root in configured_lerobot_roots():
        if not root.is_dir():
            continue
        try:
            candidates = list(root.iterdir())
        except OSError:
            continue
        for candidate in candidates:
            if not candidate.is_dir():
                continue
            if (candidate / "meta" / "info.json").is_file() or (
                candidate / "data"
            ).is_dir():
                datasets.append(candidate)
    return datasets


def latest_lerobot_dataset(min_mtime_ns: int | None = None) -> Path | None:
    latest_path: Path | None = None
    latest_mtime = -1
    for dataset_path in collect_lerobot_datasets():
        mtime = latest_lerobot_dataset_mtime_ns(dataset_path)
        if min_mtime_ns is not None and mtime < min_mtime_ns:
            continue
        if mtime > latest_mtime:
            latest_path = dataset_path
            latest_mtime = mtime
    return latest_path


def latest_lerobot_dataset_mtime_ns(dataset_path: Path) -> int:
    latest_mtime = -1
    for child in dataset_path.rglob("*"):
        if not child.is_file():
            continue
        try:
            latest_mtime = max(latest_mtime, child.stat().st_mtime_ns)
        except OSError:
            continue
    if latest_mtime >= 0:
        return latest_mtime
    try:
        return dataset_path.stat().st_mtime_ns
    except OSError:
        return -1


def build_lerobot_preview_video(dataset_path: Path) -> Path | None:
    parquet_paths = sorted((dataset_path / "data").rglob("*.parquet"))
    if not parquet_paths:
        return None

    latest_source_mtime = max(
        latest_lerobot_dataset_mtime_ns(dataset_path),
        *(path.stat().st_mtime_ns for path in parquet_paths),
    )
    output_path = LEROBOT_PREVIEW_DIR / f"{dataset_path.name}_data_preview.mp4"
    if output_path.is_file() and output_path.stat().st_mtime_ns >= latest_source_mtime:
        return output_path

    try:
        import imageio.v2 as imageio
        import pandas as pd
    except Exception as exc:
        with runtime_lock:
            runtime.log_lines.append(f"LeRobot preview skipped; missing dependency: {exc}")
        return None

    try:
        data_frame = pd.concat(
            [pd.read_parquet(path) for path in parquet_paths],
            ignore_index=True,
        )
    except Exception as exc:
        with runtime_lock:
            runtime.log_lines.append(f"LeRobot preview skipped; read failed: {exc}")
        return None

    if data_frame.empty:
        return None

    try:
        fps = read_lerobot_fps(dataset_path) or 25
        fps = max(1, min(int(round(fps)), 30))
        frames = render_lerobot_data_frames(data_frame, dataset_path.name)
        if not frames:
            return None
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with imageio.get_writer(output_path, fps=fps, codec="libx264") as writer:
            for frame in frames:
                writer.append_data(frame)
    except Exception as exc:
        with runtime_lock:
            runtime.log_lines.append(f"LeRobot preview skipped; render failed: {exc}")
        return None

    return output_path


def read_lerobot_fps(dataset_path: Path) -> int | None:
    info_path = dataset_path / "meta" / "info.json"
    if not info_path.is_file():
        return None
    try:
        info = json.loads(info_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    fps = info.get("fps")
    if isinstance(fps, (int, float)):
        return int(fps)
    return None


def render_lerobot_data_frames(data_frame: Any, dataset_name: str) -> list[np.ndarray]:
    total_rows = len(data_frame)
    frame_indices = np.linspace(
        0,
        total_rows - 1,
        num=min(total_rows, LEROBOT_PREVIEW_MAX_FRAMES),
        dtype=int,
    )
    state = series_to_matrix(data_frame.get("observation.state"))
    action = series_to_matrix(data_frame.get("action"))
    qvel = series_to_matrix(data_frame.get("observation.qvel"))
    timestamps = numeric_column(data_frame, "timestamp", total_rows)

    frames: list[np.ndarray] = []
    for row_index in frame_indices:
        image = Image.new("RGB", (960, 544), (247, 248, 250))
        draw = ImageDraw.Draw(image)
        draw_lerobot_header(
            draw,
            dataset_name=dataset_name,
            row_index=int(row_index),
            total_rows=total_rows,
            timestamp=float(timestamps[row_index]) if len(timestamps) else None,
        )
        draw_signal_panel(draw, (40, 96, 920, 220), state, row_index, "observation.state")
        draw_signal_panel(draw, (40, 244, 920, 368), action, row_index, "action")
        draw_bar_panel(draw, (40, 392, 920, 506), qvel, row_index, "observation.qvel")
        frames.append(np.asarray(image))
    return frames


def series_to_matrix(series: Any, max_dims: int = 12) -> np.ndarray:
    if series is None:
        return np.empty((0, 0), dtype=float)
    rows: list[np.ndarray] = []
    for value in series:
        array = np.asarray(value, dtype=float).reshape(-1)
        if array.size:
            rows.append(array[:max_dims])
    if not rows:
        return np.empty((0, 0), dtype=float)
    width = max(row.size for row in rows)
    matrix = np.full((len(rows), width), np.nan, dtype=float)
    for index, row in enumerate(rows):
        matrix[index, : row.size] = row
    return matrix


def numeric_column(data_frame: Any, column: str, fallback_length: int) -> np.ndarray:
    if column not in data_frame:
        return np.arange(fallback_length, dtype=float)
    try:
        values = np.asarray(data_frame[column], dtype=float)
    except Exception:
        values = np.arange(fallback_length, dtype=float)
    return values


def draw_lerobot_header(
    draw: ImageDraw.ImageDraw,
    *,
    dataset_name: str,
    row_index: int,
    total_rows: int,
    timestamp: float | None,
) -> None:
    draw.text((40, 28), "LeRobot dataset preview", fill=(17, 24, 39))
    short_name = dataset_name if len(dataset_name) <= 78 else f"{dataset_name[:75]}..."
    draw.text((40, 54), short_name, fill=(75, 85, 99))
    progress = 0 if total_rows <= 1 else row_index / (total_rows - 1)
    draw.text((750, 28), f"frame {row_index + 1}/{total_rows}", fill=(17, 24, 39))
    if timestamp is not None:
        draw.text((750, 54), f"t = {timestamp:.2f}s", fill=(75, 85, 99))
    draw.rectangle((40, 78, 920, 82), fill=(224, 231, 239))
    draw.rectangle((40, 78, int(40 + 880 * progress), 82), fill=(37, 99, 235))


def draw_signal_panel(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    matrix: np.ndarray,
    row_index: int,
    title: str,
) -> None:
    x0, y0, x1, y1 = box
    draw.rounded_rectangle(box, radius=8, fill=(255, 255, 255), outline=(209, 213, 219))
    draw.text((x0 + 14, y0 + 10), title, fill=(17, 24, 39))
    if matrix.size == 0:
        draw.text((x0 + 14, y0 + 48), "No numeric data", fill=(107, 114, 128))
        return
    plot_box = (x0 + 14, y0 + 36, x1 - 14, y1 - 16)
    draw_timeseries(draw, plot_box, matrix, row_index)


def draw_bar_panel(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    matrix: np.ndarray,
    row_index: int,
    title: str,
) -> None:
    x0, y0, x1, y1 = box
    draw.rounded_rectangle(box, radius=8, fill=(255, 255, 255), outline=(209, 213, 219))
    draw.text((x0 + 14, y0 + 10), title, fill=(17, 24, 39))
    if matrix.size == 0 or row_index >= len(matrix):
        draw.text((x0 + 14, y0 + 48), "No numeric data", fill=(107, 114, 128))
        return
    values = matrix[row_index]
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return
    max_abs = max(float(np.nanmax(np.abs(finite))), 1e-6)
    base_y = y1 - 30
    left = x0 + 18
    available_width = x1 - x0 - 36
    bar_count = min(len(values), 12)
    bar_gap = 8
    bar_width = max(8, (available_width - bar_gap * (bar_count - 1)) // bar_count)
    for index in range(bar_count):
        value = values[index]
        if not np.isfinite(value):
            continue
        x = left + index * (bar_width + bar_gap)
        height = int((abs(float(value)) / max_abs) * 58)
        color = (22, 163, 74) if value >= 0 else (220, 38, 38)
        y_top = base_y - height
        draw.rectangle((x, y_top, x + bar_width, base_y), fill=color)
        draw.text((x, base_y + 5), str(index), fill=(107, 114, 128))


def draw_timeseries(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    matrix: np.ndarray,
    row_index: int,
) -> None:
    x0, y0, x1, y1 = box
    draw.rectangle(box, outline=(229, 231, 235))
    sample_count = min(len(matrix), LEROBOT_PREVIEW_MAX_FRAMES)
    if sample_count <= 1:
        return
    sampled = matrix[
        np.linspace(0, len(matrix) - 1, num=sample_count, dtype=int),
        : min(matrix.shape[1], 8),
    ]
    finite = sampled[np.isfinite(sampled)]
    if finite.size == 0:
        return
    minimum = float(np.nanmin(finite))
    maximum = float(np.nanmax(finite))
    if math.isclose(minimum, maximum):
        minimum -= 1.0
        maximum += 1.0
    palette = [
        (37, 99, 235),
        (5, 150, 105),
        (217, 119, 6),
        (220, 38, 38),
        (124, 58, 237),
        (8, 145, 178),
        (79, 70, 229),
        (202, 138, 4),
    ]

    def point(sample_index: int, value: float) -> tuple[int, int]:
        x = int(x0 + (x1 - x0) * sample_index / (sample_count - 1))
        y = int(y1 - (y1 - y0) * (value - minimum) / (maximum - minimum))
        return x, y

    for dim in range(sampled.shape[1]):
        points = [
            point(index, float(value))
            for index, value in enumerate(sampled[:, dim])
            if np.isfinite(value)
        ]
        if len(points) >= 2:
            draw.line(points, fill=palette[dim % len(palette)], width=2)

    cursor_x = int(x0 + (x1 - x0) * row_index / max(len(matrix) - 1, 1))
    draw.line((cursor_x, y0, cursor_x, y1), fill=(17, 24, 39), width=2)


def safe_filename_part(value: str) -> str:
    safe = "".join(
        char if char.isalnum() or char in {"-", "_"} else "_"
        for char in value.strip()
    )
    return safe.strip("_")[:80]


def build_run_agent_command(
    paths: ScenePaths,
    *,
    parallel_env: bool = False,
) -> list[str]:
    command = [
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
        "--renderer",
        "fast-rt"
    ]
    if parallel_env:
        command.extend(
            [
                "--num_envs",
                "9",
                "--arena_space",
                "2.5",
                "--filter_dataset_saving",
            ]
        )
    return command


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
    config_stat = config_path.stat()

    scene = trimesh.Scene()
    manifest: dict[str, Any] = {
        "source_config": os.path.relpath(config_path, scene_dir),
        "source_config_size": config_stat.st_size,
        "source_config_mtime_ns": config_stat.st_mtime_ns,
        "transform_policy": GRADIO_SCENE_TRANSFORM_POLICY,
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
        frame_transform = gltf_to_sim_frame_transform(mesh_path)
        if frame_transform is not None:
            transform = transform @ frame_transform
        add_mesh_to_scene(scene, mesh_path, transform, str(obj.get("uid", "object")))
        manifest["objects"].append(
            {
                "uid": obj.get("uid"),
                "role": role,
                "source_mesh": os.path.relpath(mesh_path, scene_dir),
                "source_mesh_size": mesh_path.stat().st_size,
                "source_mesh_mtime_ns": mesh_path.stat().st_mtime_ns,
                "gltf_to_sim_frame": frame_transform is not None,
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


def gradio_scene_is_current(
    scene_glb: Path,
    manifest_path: Path,
    config_path: Path,
) -> bool:
    if not scene_glb.is_file() or not manifest_path.is_file() or not config_path.is_file():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config_stat = config_path.stat()
    except Exception:
        return False

    if manifest.get("source_config") != os.path.relpath(config_path, manifest_path.parent):
        return False
    if manifest.get("source_config_size") != config_stat.st_size:
        return False
    if manifest.get("source_config_mtime_ns") != config_stat.st_mtime_ns:
        return False
    if manifest.get("transform_policy") != GRADIO_SCENE_TRANSFORM_POLICY:
        return False

    expected_objects = []
    try:
        for role, obj in iter_scene_objects(config):
            shape = obj.get("shape") if isinstance(obj, dict) else None
            if not isinstance(shape, dict) or shape.get("shape_type") != "Mesh":
                continue
            raw_fpath = shape.get("fpath")
            if not raw_fpath:
                continue
            mesh_path = resolve_mesh_path(config_path.parent, str(raw_fpath))
            mesh_stat = mesh_path.stat()
            frame_transform = gltf_to_sim_frame_transform(mesh_path)
            expected_objects.append(
                {
                    "uid": obj.get("uid"),
                    "role": role,
                    "source_mesh": os.path.relpath(mesh_path, manifest_path.parent),
                    "source_mesh_size": mesh_stat.st_size,
                    "source_mesh_mtime_ns": mesh_stat.st_mtime_ns,
                    "gltf_to_sim_frame": frame_transform is not None,
                }
            )
    except OSError:
        return False
    return manifest.get("objects") == expected_objects


def collect_generated_object_glbs(paths: ScenePaths) -> list[Path]:
    if not paths.prompt_root.is_dir():
        return []

    glb_paths: list[Path] = []
    seen: set[Path] = set()
    for glb_dir in sorted(paths.prompt_root.rglob("glb_gen")):
        if not glb_dir.is_dir():
            continue
        candidates = [
            path for path in glb_dir.rglob("*_simready.glb") if is_previewable_glb(path)
        ]
        if not candidates:
            candidates = [
                path for path in glb_dir.rglob("*.glb") if is_previewable_glb(path)
            ]
        for path in sorted(candidates):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            glb_paths.append(path)
    return glb_paths


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
    gltf_to_sim_transform = gltf_to_sim_frame_transform(mesh_path)
    if isinstance(loaded, trimesh.Trimesh):
        mesh = loaded.copy()
        if gltf_to_sim_transform is not None:
            mesh.apply_transform(gltf_to_sim_transform)
        return [mesh]
    if isinstance(loaded, trimesh.Scene):
        meshes: list[trimesh.Trimesh] = []
        for geometry in loaded.dump(concatenate=False):
            if isinstance(geometry, trimesh.Trimesh):
                mesh = geometry.copy()
                if gltf_to_sim_transform is not None:
                    mesh.apply_transform(gltf_to_sim_transform)
                meshes.append(mesh)
        return meshes
    raise TypeError(f"Unsupported mesh type for {mesh_path}: {type(loaded)!r}")


def gltf_to_sim_frame_transform(mesh_path: Path) -> np.ndarray | None:
    if mesh_path.suffix.lower() not in {".glb", ".gltf"}:
        return None
    # Match DexSim's native GLTF Y-up to simulation Z-up conversion.
    transform = np.eye(4)
    transform[:3, :3] = np.array(
        [
            [1.0, 0.0, 0.0],
            [0.0, 0.0, -1.0],
            [0.0, 1.0, 0.0],
        ],
        dtype=float,
    )
    return transform


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
    scale = vector3(obj.get("body_scale"), [1.0, 1.0, 1.0])

    scale_matrix = np.eye(4)
    scale_matrix[0, 0] = scale[0]
    scale_matrix[1, 1] = scale[1]
    scale_matrix[2, 2] = scale[2]

    init_local_pose = matrix4(obj.get("init_local_pose"))
    if init_local_pose is not None:
        return init_local_pose @ scale_matrix

    position = vector3(obj.get("init_pos"), [0.0, 0.0, 0.0])
    rotation_degrees = vector3(obj.get("init_rot"), [0.0, 0.0, 0.0])
    root_matrix = euler_xyz_degrees_matrix(rotation_degrees, position)
    return root_matrix @ scale_matrix


def euler_xyz_degrees_matrix(
    rotation_degrees: list[float],
    position: list[float],
) -> np.ndarray:
    rx, ry, rz = (math.radians(value) for value in rotation_degrees)
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)

    rot_x = np.array(
        [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, cx, -sx, 0.0],
            [0.0, sx, cx, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ],
        dtype=float,
    )
    rot_y = np.array(
        [
            [cy, 0.0, sy, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [-sy, 0.0, cy, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ],
        dtype=float,
    )
    rot_z = np.array(
        [
            [cz, -sz, 0.0, 0.0],
            [sz, cz, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ],
        dtype=float,
    )
    matrix = rot_x @ rot_y @ rot_z
    matrix[:3, 3] = position
    return matrix


def matrix4(value: Any) -> np.ndarray | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        matrix = np.asarray(value, dtype=float)
    except (TypeError, ValueError):
        return None
    if matrix.shape != (4, 4) or not np.all(np.isfinite(matrix)):
        return None
    return matrix


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


def ensure_initial_scene_snapshot(*, overwrite: bool = False) -> Path:
    if not GRADIO_SCENE_GLB.is_file():
        build_gradio_scene_from_fast_config(FAST_GYM_CONFIG, GRADIO_SCENE_DIR)
    if overwrite or not GRADIO_INITIAL_SCENE_GLB.is_file():
        GRADIO_SCENE_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(GRADIO_SCENE_GLB, GRADIO_INITIAL_SCENE_GLB)
    return GRADIO_INITIAL_SCENE_GLB


def prepare_current_scene_for_edit() -> Path:
    scene_state = PROMPT2SCENE_ROOT / "gym_export" / "scene_state" / "result.json"
    if not scene_state.is_file():
        raise FileNotFoundError(
            f"Current prompt2scene scene state not found: {scene_state}"
        )
    if not FAST_GYM_CONFIG.is_file():
        raise FileNotFoundError(f"Current gym config not found: {FAST_GYM_CONFIG}")

    initial_scene_path = ensure_initial_scene_snapshot()
    errors = remove_path(GRADIO_SCENE_GLB)
    errors.extend(remove_path(SCENE_MANIFEST))
    if errors:
        raise RuntimeError("\n".join(errors))
    return initial_scene_path


def current_scene_available_for_task_only() -> bool:
    return CURRENT_GYM_EXPORT_CONFIG.is_file()


def run_generate(
    image_value: str | np.ndarray | Image.Image,
    task_text: str,
    env_text: str,
    *,
    force_initial: bool = False,
    parallel_env: bool = False,
    robot_profile: str | None = None,
    run_log_mode: str = RUN_LOG_MODE_INTERACT,
    preserve_previous_video: bool = False,
):
    task_text = (task_text or "").strip()
    env_text = (env_text or "").strip()
    if env_text and not force_initial:
        mode = PIPELINE_MODE_EDIT
    elif not force_initial and current_scene_available_for_task_only():
        mode = PIPELINE_MODE_TASK_ONLY
    else:
        mode = PIPELINE_MODE_INITIAL
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
    stage = (
        CURRENT_PATHS
        if mode in {PIPELINE_MODE_EDIT, PIPELINE_MODE_TASK_ONLY}
        else make_stage_paths(token)
    )
    initial_scene_path: Path | None = None
    existing_object_preview_path = (
        GRADIO_OBJECT_PREVIEW_GLB
        if mode in {PIPELINE_MODE_EDIT, PIPELINE_MODE_TASK_ONLY}
        and GRADIO_OBJECT_PREVIEW_GLB.is_file()
        else None
    )
    try:
        if mode == PIPELINE_MODE_EDIT:
            if not task_text:
                raise ValueError("Please enter a task description.")
            initial_scene_path = prepare_current_scene_for_edit()
            image_path = IMAGE_PATH if IMAGE_PATH.is_file() else None
        elif mode == PIPELINE_MODE_TASK_ONLY:
            if not task_text:
                raise ValueError("Please enter a task description.")
            if not CURRENT_GYM_EXPORT_CONFIG.is_file():
                raise FileNotFoundError(
                    f"Current gym export not found: {CURRENT_GYM_EXPORT_CONFIG}"
                )
            if GRADIO_INITIAL_SCENE_GLB.is_file():
                initial_scene_path = GRADIO_INITIAL_SCENE_GLB
            elif GRADIO_SCENE_GLB.is_file():
                initial_scene_path = GRADIO_SCENE_GLB
            image_path = IMAGE_PATH if IMAGE_PATH.is_file() else None
        else:
            image_path = save_input(image_value, task_text, stage.image_path)
    except Exception as exc:
        with runtime_lock:
            runtime.phase_key = "failed"
            runtime.status = f"Input error: {exc}"
            runtime.last_error = str(exc)
            runtime.log_lines.clear()
            runtime.log_lines.append(runtime.status)
        if run_log_mode == RUN_LOG_MODE_INTERACT:
            archive_run_log(
                mode=RUN_LOG_MODE_INTERACT,
                task_description=task_text,
                scene_description=env_text,
                outcome="input_error",
            )
        yield ui_snapshot()
        return

    if mode == PIPELINE_MODE_EDIT:
        command = build_edit_pipeline_command(task_text, env_text, robot_profile)
    elif mode == PIPELINE_MODE_TASK_ONLY:
        command = build_task_only_config_command(task_text, robot_profile)
    else:
        command = build_initial_pipeline_command(
            task_text,
            stage,
            env_text,
            robot_profile,
        )
    display_task_text = format_current_task(task_text, env_text)
    with runtime_lock:
        runtime.run_token = token
        runtime.is_busy = True
        runtime.phase_key = "received"
        if mode == PIPELINE_MODE_EDIT:
            runtime.status = "Starting scene edit..."
        elif mode == PIPELINE_MODE_TASK_ONLY:
            runtime.status = "Current scene found. Regenerating action config only..."
        else:
            runtime.status = "Input saved. Starting local pipeline..."
        runtime.task_text = display_task_text
        runtime.input_task_text = task_text
        runtime.input_scene_text = env_text
        runtime.image_path = image_path
        if not preserve_previous_video:
            runtime.video_path = None
        runtime.lerobot_video_path = None
        runtime.lerobot_dataset_path = None
        runtime.object_model_path = existing_object_preview_path
        runtime.scene_model_path = initial_scene_path
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
        if run_log_mode == RUN_LOG_MODE_INTERACT:
            archive_run_log(
                mode=RUN_LOG_MODE_INTERACT,
                task_description=task_text,
                scene_description=env_text,
                outcome="pipeline_start_failed",
            )
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
        args=(
            token,
            stage,
            mode,
            process,
            display_task_text,
            task_text,
            env_text,
            output_queue,
            reader,
            parallel_env,
            robot_profile,
            run_log_mode,
        ),
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

        available_tasks = available_auto_task_indices()
        if not available_tasks:
            image_dirs = ", ".join(str(path) for path in auto_image_directories())
            message = (
                "Auto cannot start: no task input images were found. "
                "Add task0_0.png through task4_3.png to one of: "
                f"{image_dirs}"
            )
            runtime.phase_key = "failed"
            runtime.status = message
            runtime.last_error = message
            runtime.log_lines.clear()
            runtime.log_lines.append(message)
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
        runtime.video_path = None
        runtime.lerobot_video_path = None
        runtime.lerobot_dataset_path = None
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
        runtime.input_task_text = ""
        runtime.input_scene_text = ""
        runtime.image_path = None
        runtime.video_path = None
        runtime.lerobot_video_path = None
        runtime.lerobot_dataset_path = None
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
    run_mode: str,
    action_mode: str | None,
    robot_profile: str | None,
    image_value: str | np.ndarray | Image.Image,
    task_text: str,
    env_text: str,
):
    parallel_env = action_mode == TOP_MODE_PARALLEL_ENV
    if run_mode != TOP_MODE_AUTO:
        for snapshot in run_generate(
            image_value,
            task_text,
            env_text,
            force_initial=False,
            parallel_env=parallel_env,
            robot_profile=robot_profile,
            run_log_mode=RUN_LOG_MODE_INTERACT,
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
            archive_run_log(
                mode=RUN_LOG_MODE_AUTO,
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
            runtime.input_task_text = auto_task
            runtime.input_scene_text = auto_scene
            runtime.image_path = auto_input.base_image_path
            runtime.lerobot_video_path = None
            runtime.lerobot_dataset_path = None
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
            archive_run_log(
                mode=RUN_LOG_MODE_AUTO,
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
            parallel_env=parallel_env,
            robot_profile=robot_profile,
            run_log_mode=RUN_LOG_MODE_AUTO,
            preserve_previous_video=True,
        ):
            yield (base_image, auto_task, auto_scene, *snapshot)
            if not auto_loop_is_active(loop_token):
                break

        if not auto_loop_is_active(loop_token):
            archive_run_log(
                mode=RUN_LOG_MODE_AUTO,
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
            archive_run_log(
                mode=RUN_LOG_MODE_AUTO,
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
            archive_run_log(
                mode=RUN_LOG_MODE_AUTO,
                task_description=auto_task,
                scene_description=auto_scene,
                outcome="stopped",
            )
            break

        with runtime_lock:
            simulation_completed = (
                runtime.sim_started
                and runtime.sim_finished
                and runtime.sim_process is None
            )
            round_outcome = "completed" if simulation_completed else "simulation_failed"
        log_path = archive_run_log(
            mode=RUN_LOG_MODE_AUTO,
            task_description=auto_task,
            scene_description=auto_scene,
            outcome=round_outcome,
        )
        archived_video = archived_audience_video_path(log_path)
        if archived_video is not None:
            with runtime_lock:
                runtime.video_path = archived_video
        yield (
            base_image,
            auto_task,
            auto_scene,
            *ui_snapshot(extra_status="Auto video archived and ready to play."),
        )
        cleanup_errors = cleanup_auto_generated_artifacts()
        if cleanup_errors:
            with runtime_lock:
                runtime.log_lines.extend(cleanup_errors)

    finish_auto_loop(loop_token)


def supervise_pipeline(
    token: str,
    stage: ScenePaths,
    mode: str,
    process: subprocess.Popen[str],
    display_task_text: str,
    task_description: str,
    scene_description: str,
    output_queue: queue.Queue[str],
    reader: threading.Thread,
    parallel_env: bool,
    robot_profile: str | None,
    run_log_mode: str,
) -> None:
    is_edit = mode == PIPELINE_MODE_EDIT
    is_task_only = mode == PIPELINE_MODE_TASK_ONLY
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
                and not is_task_only
                and stage.fast_gym_config.is_file()
                and not gradio_scene_is_current(
                    stage.gradio_scene_glb,
                    stage.scene_manifest,
                    stage.fast_gym_config,
                )
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
            is_task_only
            and process.returncode == 0
            and stage.fast_gym_config.is_file()
        ):
            try:
                scene_path = build_gradio_scene_from_fast_config(
                    stage.fast_gym_config,
                    stage.gradio_scene_dir,
                )
                scene_build_error = None
                if GRADIO_SCENE_GLB.is_file():
                    GRADIO_SCENE_DIR.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(GRADIO_SCENE_GLB, GRADIO_INITIAL_SCENE_GLB)
                with runtime_lock:
                    runtime.scene_model_path = scene_path
                    runtime.edited_scene_model_path = None
                    runtime.phase_key = "preview"
                    runtime.status = "3D preview loaded."
                    runtime.last_error = None
            except Exception as exc:
                scene_build_error = str(exc)
        elif (
            stage.fast_gym_config.is_file()
            and (not is_edit or process.returncode == 0)
            and not gradio_scene_is_current(
                stage.gradio_scene_glb,
                stage.scene_manifest,
                stage.fast_gym_config,
            )
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
        pipeline_output_ready = (
            stage.fast_gym_config.is_file() and stage.agent_config.is_file()
            if is_task_only
            else stage.fast_gym_config.is_file()
        )
        missing_output_name = (
            f"{stage.fast_gym_config.name} and/or {stage.agent_config.name}"
            if is_task_only
            else FAST_GYM_CONFIG.name
        )
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
                        if GRADIO_INITIAL_SCENE_GLB.is_file():
                            runtime.scene_model_path = GRADIO_INITIAL_SCENE_GLB
                        if GRADIO_SCENE_GLB.is_file():
                            runtime.edited_scene_model_path = GRADIO_SCENE_GLB
                simulation_error = launch_current_simulation(
                    token,
                    parallel_env=parallel_env,
                    robot_profile=robot_profile,
                    run_log_mode=run_log_mode,
                    task_description=task_description,
                    scene_description=scene_description,
                )
                simulation_started = simulation_error is None
            elif is_task_only:
                with runtime_lock:
                    if runtime.run_token == token:
                        runtime.image_path = IMAGE_PATH if IMAGE_PATH.is_file() else None
                        if GRADIO_OBJECT_PREVIEW_GLB.is_file():
                            runtime.object_model_path = GRADIO_OBJECT_PREVIEW_GLB
                        if GRADIO_INITIAL_SCENE_GLB.is_file():
                            runtime.scene_model_path = GRADIO_INITIAL_SCENE_GLB
                        elif GRADIO_SCENE_GLB.is_file():
                            runtime.scene_model_path = GRADIO_SCENE_GLB
                        runtime.edited_scene_model_path = None
                simulation_error = launch_current_simulation(
                    token,
                    parallel_env=parallel_env,
                    robot_profile=robot_profile,
                    run_log_mode=run_log_mode,
                    task_description=task_description,
                    scene_description=scene_description,
                )
                simulation_started = simulation_error is None
            else:
                try:
                    cleanup_errors = promote_stage_to_current(stage, token)
                except Exception as exc:
                    promotion_error = str(exc)
                else:
                    initial_scene_error: str | None = None
                    try:
                        ensure_initial_scene_snapshot(overwrite=True)
                    except Exception as exc:
                        initial_scene_error = str(exc)
                    with runtime_lock:
                        if runtime.run_token == token:
                            runtime.image_path = IMAGE_PATH
                            if GRADIO_OBJECT_PREVIEW_GLB.is_file():
                                runtime.object_model_path = GRADIO_OBJECT_PREVIEW_GLB
                            if GRADIO_INITIAL_SCENE_GLB.is_file():
                                runtime.scene_model_path = GRADIO_INITIAL_SCENE_GLB
                            elif GRADIO_SCENE_GLB.is_file():
                                runtime.scene_model_path = GRADIO_SCENE_GLB
                            runtime.edited_scene_model_path = None
                            if initial_scene_error:
                                runtime.log_lines.append(
                                    f"Initial scene snapshot skipped: {initial_scene_error}"
                                )
                    simulation_error = launch_current_simulation(
                        token,
                        parallel_env=parallel_env,
                        robot_profile=robot_profile,
                        run_log_mode=run_log_mode,
                        task_description=task_description,
                        scene_description=scene_description,
                    )
                    simulation_started = simulation_error is None

        archive_after_status = False
        archive_outcome = "completed"
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
                    if GRADIO_INITIAL_SCENE_GLB.is_file():
                        runtime.scene_model_path = GRADIO_INITIAL_SCENE_GLB
                    if GRADIO_SCENE_GLB.is_file():
                        runtime.edited_scene_model_path = GRADIO_SCENE_GLB
                elif GRADIO_INITIAL_SCENE_GLB.is_file():
                    runtime.scene_model_path = GRADIO_INITIAL_SCENE_GLB
                    runtime.edited_scene_model_path = None
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
                runtime.status = f"Pipeline ended without {missing_output_name}."
                runtime.last_error = runtime.status
                archive_outcome = "pipeline_output_missing"
            elif scene_build_error:
                runtime.phase_key = "failed"
                runtime.status = f"3D preview failed: {scene_build_error}"
                runtime.last_error = scene_build_error
                archive_outcome = "preview_failed"
            elif promotion_error:
                runtime.phase_key = "failed"
                runtime.status = f"Scene promotion failed: {promotion_error}"
                runtime.last_error = promotion_error
                archive_outcome = "promotion_failed"
            else:
                runtime.phase_key = "failed"
                runtime.status = f"Pipeline failed with return code {process.returncode}."
                runtime.last_error = runtime.status
                archive_outcome = "pipeline_failed"
            if pipeline_succeeded and not promotion_error:
                archive_outcome = (
                    "dexsim_launch_failed" if simulation_error else "completed"
                )
            archive_after_status = (
                run_log_mode == RUN_LOG_MODE_INTERACT
                and not simulation_started
            )
        if archive_after_status:
            archive_run_log(
                mode=RUN_LOG_MODE_INTERACT,
                task_description=task_description or display_task_text,
                scene_description=scene_description,
                outcome=archive_outcome,
            )
    except Exception as exc:
        should_archive_exception = False
        with runtime_lock:
            if runtime.run_token == token:
                runtime.is_busy = False
                runtime.process = None
                runtime.phase_key = "failed"
                runtime.status = f"Pipeline supervision failed: {exc}"
                runtime.last_error = str(exc)
                runtime.log_lines.append(runtime.status)
                should_archive_exception = run_log_mode == RUN_LOG_MODE_INTERACT
        if should_archive_exception:
            archive_run_log(
                mode=RUN_LOG_MODE_INTERACT,
                task_description=task_description or display_task_text,
                scene_description=scene_description,
                outcome="pipeline_supervision_failed",
            )


def launch_current_simulation(
    token: str,
    *,
    parallel_env: bool = False,
    robot_profile: str | None = None,
    run_log_mode: str = RUN_LOG_MODE_INTERACT,
    task_description: str = "",
    scene_description: str = "",
) -> str | None:
    if not CURRENT_PATHS.fast_gym_config.is_file():
        return f"Dexsim launch skipped; missing {CURRENT_PATHS.fast_gym_config}"
    if not CURRENT_PATHS.agent_config.is_file():
        return f"Dexsim launch skipped; missing {CURRENT_PATHS.agent_config}"

    command = build_run_agent_command(
        CURRENT_PATHS,
        parallel_env=parallel_env,
    )
    started_at_ns = time.time_ns()
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
        args=(
            token,
            process,
            output_queue,
            reader,
            started_at_ns,
            run_log_mode,
            task_description,
            scene_description,
        ),
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
    started_at_ns: int,
    run_log_mode: str,
    task_description: str,
    scene_description: str,
) -> None:
    while process.poll() is None:
        append_simulation_logs(token, process, drain_output_queue(output_queue))
        time.sleep(0.5)

    reader.join(timeout=1.0)
    append_simulation_logs(token, process, drain_output_queue(output_queue))
    latest_video = latest_audience_output_video(min_mtime_ns=started_at_ns)
    latest_dataset = latest_lerobot_dataset(min_mtime_ns=started_at_ns)
    lerobot_video = (
        build_lerobot_preview_video(latest_dataset)
        if latest_dataset is not None
        else None
    )

    should_archive = False
    archive_outcome = "completed"
    with runtime_lock:
        if runtime.run_token != token or runtime.sim_process is not process:
            return
        runtime.sim_process = None
        runtime.sim_finished = True
        runtime.sim_returncode = process.returncode
        runtime.video_path = latest_video
        runtime.lerobot_dataset_path = latest_dataset
        runtime.lerobot_video_path = lerobot_video
        if process.returncode == 0:
            runtime.status = "Pipeline completed successfully.\nDexsim simulation finished."
            if latest_video is None:
                runtime.log_lines.append("Audience video not found in outputs.")
            if latest_dataset is None:
                runtime.log_lines.append("LeRobot dataset not found.")
            elif lerobot_video is None:
                runtime.log_lines.append(
                    f"LeRobot dataset found, but preview was not generated: {latest_dataset}"
                )
        else:
            runtime.status = (
                "Pipeline completed successfully.\n"
                f"Dexsim simulation exited with return code {process.returncode}."
            )
            runtime.log_lines.append(
                f"Dexsim simulation exited with return code {process.returncode}."
            )
            archive_outcome = "simulation_failed"
        should_archive = run_log_mode == RUN_LOG_MODE_INTERACT
    if should_archive:
        archive_run_log(
            mode=RUN_LOG_MODE_INTERACT,
            task_description=task_description,
            scene_description=scene_description,
            outcome=archive_outcome,
            audience_video=latest_video,
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
        runtime.input_task_text = ""
        runtime.input_scene_text = ""
        runtime.image_path = None
        runtime.video_path = None
        runtime.lerobot_video_path = None
        runtime.lerobot_dataset_path = None
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
        None,
        "",
        PHASES["idle"].progress,
        format_status("Stopped."),
        None,
        None,
        None,
    )


def run_reset_or_stop(run_mode: str):
    if run_mode == TOP_MODE_AUTO:
        return stop_current_run_without_cleanup()
    return run_reset()


def randomize_interact_input(run_mode: str | None):
    """Fill the Interact form with one available template scene and task."""
    if run_mode != TOP_MODE_INTERACT:
        return gr.update(), gr.update(), gr.update()
    auto_input = generate_auto_text_input()
    return (
        auto_input.base_image_path.as_posix(),
        auto_input.task_description,
        auto_input.scene_description,
    )


def button_updates(
    language: str | None,
    run_mode: str | None,
    action_mode: str | None,
) -> tuple[Any, Any, Any, Any, Any, Any]:
    """Build localized labels while preserving the selected button variants."""
    labels = BUTTON_LABELS.get(language or LANGUAGE_EN, BUTTON_LABELS[LANGUAGE_EN])
    is_auto = run_mode == TOP_MODE_AUTO
    is_interact = run_mode != TOP_MODE_AUTO
    is_parallel_env = action_mode == TOP_MODE_PARALLEL_ENV
    return (
        gr.update(
            value=labels["auto"],
            variant="primary" if is_auto else "secondary",
        ),
        gr.update(
            value=labels["interact"],
            variant="primary" if is_interact else "secondary",
        ),
        gr.update(
            value=labels["parallel_env"],
            variant="primary" if is_parallel_env else "secondary",
        ),
        gr.update(value=labels["generate"]),
        gr.update(value=labels["random_input"], visible=is_interact),
        gr.update(value=labels["stop"] if is_auto else labels["reset"]),
    )


def localized_ui_updates(language: str | None) -> tuple[Any, ...]:
    """Return updates for every non-button, user-facing static UI string."""
    text = UI_TEXT.get(language or LANGUAGE_EN, UI_TEXT[LANGUAGE_EN])
    instruction_html = (
        "<div style='font-size: 20px; font-weight: 700; "
        "line-height: 1.35; min-height: 86px; display: flex; "
        f"align-items: center;'>{text['instruction']}</div>"
    )
    return (
        gr.update(value=text["heading"]),
        gr.update(value=instruction_html),
        gr.update(label=text["robot"]),
        gr.update(label=text["input_image"]),
        gr.update(
            label=text["task_description"],
            placeholder=text["task_placeholder"],
        ),
        gr.update(
            label=text["scene_description"],
            placeholder=text["scene_placeholder"],
        ),
        gr.update(label=text["current_video"]),
        gr.update(label=text["lerobot_preview"]),
        gr.update(label=text["current_task"]),
        gr.update(label=text["progress"]),
        gr.update(label=text["initial_preview"]),
        gr.update(label=text["edited_preview"]),
        gr.update(label=text["object_preview"]),
    )


def toggle_language(
    language: str | None,
    run_mode: str | None,
    action_mode: str | None,
):
    next_language = LANGUAGE_ZH if language != LANGUAGE_ZH else LANGUAGE_EN
    labels = BUTTON_LABELS[next_language]
    return (
        *button_updates(next_language, run_mode, action_mode),
        gr.update(value=labels["language"]),
        *localized_ui_updates(next_language),
        next_language,
    )


def select_top_mode(
    selected_run_mode: str | None,
    selected_action_mode: str | None,
    current_run_mode: str,
    current_action_mode: str | None,
    language: str | None,
):
    run_mode = selected_run_mode or current_run_mode or TOP_MODE_INTERACT
    action_mode = current_action_mode
    if selected_action_mode == TOP_MODE_PARALLEL_ENV:
        action_mode = (
            None
            if action_mode == TOP_MODE_PARALLEL_ENV
            else TOP_MODE_PARALLEL_ENV
        )
    elif selected_action_mode:
        action_mode = selected_action_mode
    if (
        run_mode != current_run_mode
        or action_mode != current_action_mode
        or run_mode != TOP_MODE_AUTO
    ):
        stop_auto_loop_if_running()
    return (
        *button_updates(language, run_mode, action_mode),
        run_mode,
        action_mode,
    )


def ui_snapshot(extra_status: str | None = None):
    with runtime_lock:
        phase = PHASES.get(runtime.phase_key, PHASES["idle"])
        video_value = (
            runtime.video_path.as_posix()
            if runtime.video_path and runtime.video_path.is_file()
            else None
        )
        lerobot_video_value = (
            runtime.lerobot_video_path.as_posix()
            if runtime.lerobot_video_path and runtime.lerobot_video_path.is_file()
            else None
        )
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
        busy = runtime.is_busy
        last_error = runtime.last_error
    return (
        video_value,
        lerobot_video_value,
        task_text,
        phase.progress,
        format_status(
            status_text,
            phase=phase,
            busy=busy,
            last_error=last_error,
        ),
        model_value,
        edited_model_value,
        object_model_value,
    )


def synced_ui_snapshot(run_mode: str | None = None):
    sync_inputs = False
    with runtime_lock:
        sync_inputs = runtime.auto_loop_active or run_mode == TOP_MODE_AUTO
        image_value = (
            runtime.image_path.as_posix()
            if runtime.image_path and runtime.image_path.is_file()
            else None
        )
        input_task_text = runtime.input_task_text
        input_scene_text = runtime.input_scene_text

    if sync_inputs:
        input_values = (image_value, input_task_text, input_scene_text)
    else:
        input_values = (gr.update(), gr.update(), gr.update())
    return (*input_values, *ui_snapshot())


def format_status(
    status_text: str,
    *,
    phase: Phase | None = None,
    busy: bool = False,
    last_error: str | None = None,
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
    return "\n\n".join(parts)


def build_demo() -> gr.Blocks:
    with gr.Blocks(title="EmbodiChain Gradio", js=VIDEO_SYNC_JS) as demo:
        run_mode = gr.State(TOP_MODE_INTERACT)
        action_mode = gr.State(None)
        language = gr.State(LANGUAGE_EN)
        with gr.Row():
            heading = gr.Markdown(UI_TEXT[LANGUAGE_EN]["heading"])
            auto_button = gr.Button("Auto", variant="secondary")
            interact_button = gr.Button("Interact", variant="primary")
            parallel_env_button = gr.Button("Parallel Simulation", variant="secondary")
            language_button = gr.Button("中文", variant="secondary")
        with gr.Row():
            with gr.Column(scale=4):
                instruction = gr.HTML(
                    "<div style='font-size: 20px; font-weight: 700; "
                    "line-height: 1.35; min-height: 86px; display: flex; "
                    "align-items: center;'>"
                    "Upload one image, enter one task, then EmbodiChain "
                    " will generate what you want."
                    "</div>"
                )
            with gr.Column(scale=1):
                robot_profile = gr.Radio(
                    choices=[ROBOT_PROFILE_FRANKA, ROBOT_PROFILE_UR5],
                    value=ROBOT_PROFILE_UR5,
                    label=UI_TEXT[LANGUAGE_EN]["robot"],
                )

        with gr.Row():
            with gr.Column(scale=1):
                image_input = gr.Image(
                    label=UI_TEXT[LANGUAGE_EN]["input_image"],
                    sources=["upload", "webcam"],
                    type="filepath",
                    format="png",
                    height=320,
                )
                with gr.Row():
                    task_input = gr.Textbox(
                        label=UI_TEXT[LANGUAGE_EN]["task_description"],
                        placeholder=UI_TEXT[LANGUAGE_EN]["task_placeholder"],
                        lines=1,
                    )
                    env_input = gr.Textbox(
                        label=UI_TEXT[LANGUAGE_EN]["scene_description"],
                        placeholder=UI_TEXT[LANGUAGE_EN]["scene_placeholder"],
                        lines=1,
                    )
                with gr.Row():
                    generate_button = gr.Button("Generate", variant="primary")
                    random_input_button = gr.Button("Random Input")
                    reset_button = gr.Button("Reset", variant="stop")
            with gr.Column(scale=2):
                with gr.Row():
                    current_image = gr.Video(
                        label=UI_TEXT[LANGUAGE_EN]["current_video"],
                        height=320,
                        elem_id="embodichain-audience-video",
                    )
                    lerobot_preview = gr.Video(
                        label=UI_TEXT[LANGUAGE_EN]["lerobot_preview"],
                        height=320,
                        elem_id="embodichain-lerobot-video",
                    )
                current_task = gr.Textbox(
                    label=UI_TEXT[LANGUAGE_EN]["current_task"],
                    interactive=False,
                    lines=2,
                )

        progress = gr.Slider(
            minimum=0,
            maximum=100,
            value=0,
            step=1,
            label=UI_TEXT[LANGUAGE_EN]["progress"],
            interactive=False,
        )
        status = gr.Markdown(format_status("Idle."))
        with gr.Row():
            model = gr.Model3D(
                label=UI_TEXT[LANGUAGE_EN]["initial_preview"],
                height=520,
                clear_color=(0.94, 0.94, 0.94, 1.0),
            )
            edited_model = gr.Model3D(
                label=UI_TEXT[LANGUAGE_EN]["edited_preview"],
                height=520,
                clear_color=(0.94, 0.94, 0.94, 1.0),
            )
        object_model = gr.Model3D(
            label=UI_TEXT[LANGUAGE_EN]["object_preview"],
            height=360,
            clear_color=(0.94, 0.94, 0.94, 1.0),
        )

        refresh_timer = gr.Timer(2.0)
        top_mode_outputs = [
            auto_button,
            interact_button,
            parallel_env_button,
            generate_button,
            random_input_button,
            reset_button,
            run_mode,
            action_mode,
        ]
        auto_button.click(
            select_top_mode,
            inputs=[
                gr.State(TOP_MODE_AUTO),
                gr.State(None),
                run_mode,
                action_mode,
                language,
            ],
            outputs=top_mode_outputs,
            queue=False,
        )
        interact_button.click(
            select_top_mode,
            inputs=[
                gr.State(TOP_MODE_INTERACT),
                gr.State(None),
                run_mode,
                action_mode,
                language,
            ],
            outputs=top_mode_outputs,
            queue=False,
        )
        parallel_env_button.click(
            select_top_mode,
            inputs=[
                gr.State(None),
                gr.State(TOP_MODE_PARALLEL_ENV),
                run_mode,
                action_mode,
                language,
            ],
            outputs=top_mode_outputs,
            queue=False,
        )
        language_button.click(
            toggle_language,
            inputs=[language, run_mode, action_mode],
            outputs=[
                auto_button,
                interact_button,
                parallel_env_button,
                generate_button,
                random_input_button,
                reset_button,
                language_button,
                heading,
                instruction,
                robot_profile,
                image_input,
                task_input,
                env_input,
                current_image,
                lerobot_preview,
                current_task,
                progress,
                model,
                edited_model,
                object_model,
                language,
            ],
            queue=False,
        )
        generate_button.click(
            run_generate_for_top_mode,
            inputs=[
                run_mode,
                action_mode,
                robot_profile,
                image_input,
                task_input,
                env_input,
            ],
            outputs=[
                image_input,
                task_input,
                env_input,
                current_image,
                lerobot_preview,
                current_task,
                progress,
                status,
                model,
                edited_model,
                object_model,
            ],
        )
        random_input_button.click(
            randomize_interact_input,
            inputs=[run_mode],
            outputs=[image_input, task_input, env_input],
            queue=False,
        )
        reset_button.click(
            run_reset_or_stop,
            inputs=[run_mode],
            outputs=[
                image_input,
                task_input,
                env_input,
                current_image,
                lerobot_preview,
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
            synced_ui_snapshot,
            inputs=[run_mode],
            outputs=[
                image_input,
                task_input,
                env_input,
                current_image,
                lerobot_preview,
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
