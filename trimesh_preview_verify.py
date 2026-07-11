from __future__ import annotations

import argparse
import functools
import html
import http.server
import json
import math
import os
from pathlib import Path
import webbrowser
from typing import Any, Iterable

import numpy as np
import trimesh


EMBODICHAIN_ROOT = Path(
    os.environ.get("EMBODICHAIN_ROOT", "/home/dex/桌面/EmbodiChain")
).expanduser()
DEFAULT_CONFIG = (
    EMBODICHAIN_ROOT
    / "gym_project/action_agent_pipeline/configs/current/fast_gym_config.json"
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a DexSim-frame trimesh preview from fast_gym_config.json."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--no-serve", action="store_true")
    parser.add_argument("--show", action="store_true")
    args = parser.parse_args()

    config_path = args.config.expanduser().resolve()
    out_path = (
        args.out.expanduser().resolve()
        if args.out is not None
        else config_path.parent / "gradio_scene/scene_current_trimesh_verify.glb"
    )

    scene, object_bounds = build_scene(config_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    scene.export(out_path)
    html_path = write_preview_html(out_path, object_bounds)

    print(f"config: {config_path}", flush=True)
    print(f"exported: {out_path}", flush=True)
    print(f"html: {html_path}", flush=True)
    print_bounds_summary(object_bounds)

    if args.show:
        scene.show()
    if not args.no_serve:
        serve_preview(
            html_path,
            host=args.host,
            port=args.port,
            open_browser=not args.no_browser,
        )


def build_scene(config_path: Path) -> tuple[trimesh.Scene, list[dict[str, Any]]]:
    with config_path.open("r", encoding="utf-8") as file:
        config = json.load(file)

    scene = trimesh.Scene()
    object_bounds: list[dict[str, Any]] = []
    for role, obj in iter_scene_objects(config):
        shape = obj.get("shape") if isinstance(obj, dict) else None
        if not isinstance(shape, dict) or shape.get("shape_type") != "Mesh":
            continue
        raw_fpath = shape.get("fpath")
        if not raw_fpath:
            continue

        uid = str(obj.get("uid", "object"))
        mesh_path = resolve_mesh_path(config_path.parent, str(raw_fpath))
        transform = object_transform(obj)
        frame_transform = gltf_to_sim_frame_transform(mesh_path)
        if frame_transform is not None:
            transform = transform @ frame_transform

        bounds = add_mesh_to_scene(scene, mesh_path, transform, uid)
        object_bounds.append(
            {
                "uid": uid,
                "role": role,
                "bounds": bounds,
                "gltf_to_sim_frame": frame_transform is not None,
            }
        )

    if not object_bounds:
        raise ValueError(f"No mesh objects found in {config_path}")
    return scene, object_bounds


def print_bounds_summary(object_bounds: list[dict[str, Any]]) -> None:
    table_top = None
    for item in object_bounds:
        if item["uid"] == "table":
            table_top = float(item["bounds"][1][2])
            break

    if table_top is not None:
        print(f"table top z: {table_top:.6f}")

    for item in object_bounds:
        bounds = item["bounds"]
        minimum = bounds[0]
        maximum = bounds[1]
        print(
            f"{item['role']} {item['uid']}: "
            f"min={np.round(minimum, 6).tolist()} "
            f"max={np.round(maximum, 6).tolist()} "
            f"gltf_to_sim={item['gltf_to_sim_frame']}"
        )
        if table_top is not None and item["uid"] != "table":
            print(f"  bottom minus table_top: {float(minimum[2] - table_top):.6f}")


def write_preview_html(
    glb_path: Path,
    object_bounds: list[dict[str, Any]],
) -> Path:
    html_path = glb_path.with_name("trimesh_preview_verify.html")
    glb_name = html.escape(glb_path.name, quote=True)
    object_bounds = with_table_offsets(object_bounds)
    bounds_rows = "\n".join(format_bounds_row(item) for item in object_bounds)
    html_path.write_text(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>trimesh preview verify</title>
  <script type="module" src="https://ajax.googleapis.com/ajax/libs/model-viewer/4.1.0/model-viewer.min.js"></script>
  <style>
    html, body {{
      height: 100%;
      margin: 0;
      font-family: Arial, sans-serif;
      background: #f4f5f7;
      color: #111827;
    }}
    .layout {{
      display: grid;
      grid-template-rows: minmax(0, 1fr) auto;
      height: 100%;
    }}
    model-viewer {{
      width: 100%;
      height: 100%;
      min-height: 520px;
      background: #ffffff;
    }}
    .panel {{
      border-top: 1px solid #d1d5db;
      background: #ffffff;
      padding: 12px 16px;
      font-size: 13px;
      line-height: 1.45;
    }}
    table {{
      border-collapse: collapse;
      width: 100%;
    }}
    th, td {{
      border-bottom: 1px solid #e5e7eb;
      padding: 6px 8px;
      text-align: left;
      white-space: nowrap;
    }}
    th {{
      font-weight: 600;
      color: #374151;
    }}
  </style>
</head>
<body>
  <div class="layout">
    <model-viewer
      src="{glb_name}"
      camera-controls
      auto-rotate
      interaction-prompt="none"
      shadow-intensity="0.7"
      exposure="1"
      camera-orbit="45deg 58deg 2.0m"
      field-of-view="35deg">
    </model-viewer>
    <div class="panel">
      <table>
        <thead>
          <tr>
            <th>role</th>
            <th>uid</th>
            <th>min xyz</th>
            <th>max xyz</th>
            <th>bottom-table</th>
          </tr>
        </thead>
        <tbody>
{bounds_rows}
        </tbody>
      </table>
    </div>
  </div>
</body>
</html>
""",
        encoding="utf-8",
    )
    return html_path


def format_bounds_row(item: dict[str, Any]) -> str:
    bounds = item["bounds"]
    minimum = np.round(bounds[0], 6).tolist()
    maximum = np.round(bounds[1], 6).tolist()
    bottom_offset = item.get("bottom_minus_table_top", "")
    if isinstance(bottom_offset, float):
        bottom_offset = f"{bottom_offset:.6f}"
    return (
        "          <tr>"
        f"<td>{html.escape(str(item['role']))}</td>"
        f"<td>{html.escape(str(item['uid']))}</td>"
        f"<td>{html.escape(str(minimum))}</td>"
        f"<td>{html.escape(str(maximum))}</td>"
        f"<td>{html.escape(str(bottom_offset))}</td>"
        "</tr>"
    )


def with_table_offsets(object_bounds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    table_top = None
    for item in object_bounds:
        if item["uid"] == "table":
            table_top = float(item["bounds"][1][2])
            break
    if table_top is None:
        return object_bounds

    annotated = []
    for item in object_bounds:
        copy = dict(item)
        if copy["uid"] != "table":
            copy["bottom_minus_table_top"] = float(copy["bounds"][0][2] - table_top)
        annotated.append(copy)
    return annotated


def serve_preview(
    html_path: Path,
    *,
    host: str,
    port: int,
    open_browser: bool,
) -> None:
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler,
        directory=str(html_path.parent),
    )
    try:
        server = http.server.ThreadingHTTPServer((host, port), handler)
    except OSError:
        if port == 0:
            raise
        server = http.server.ThreadingHTTPServer((host, 0), handler)
    actual_host, actual_port = server.server_address[:2]
    url = f"http://{actual_host}:{actual_port}/{html_path.name}"
    print(f"preview url: {url}", flush=True)
    print("press Ctrl+C to stop the preview server", flush=True)
    if open_browser:
        webbrowser.open(url, new=2)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped preview server")
    finally:
        server.server_close()


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
    return euler_xyz_degrees_matrix(rotation_degrees, position) @ scale_matrix


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


def gltf_to_sim_frame_transform(mesh_path: Path) -> np.ndarray | None:
    if mesh_path.suffix.lower() not in {".glb", ".gltf"}:
        return None
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


def add_mesh_to_scene(
    scene: trimesh.Scene,
    mesh_path: Path,
    transform: np.ndarray,
    uid: str,
) -> np.ndarray:
    loaded = trimesh.load(mesh_path, force="scene", process=False)
    meshes: list[trimesh.Trimesh] = []

    if isinstance(loaded, trimesh.Trimesh):
        mesh = loaded.copy()
        mesh.apply_transform(transform)
        scene.add_geometry(mesh, node_name=uid, geom_name=uid)
        meshes.append(mesh)
    elif isinstance(loaded, trimesh.Scene):
        loaded.apply_transform(transform)
        for index, geometry in enumerate(loaded.dump(concatenate=False)):
            if isinstance(geometry, trimesh.Trimesh):
                mesh = geometry.copy()
                scene.add_geometry(
                    mesh,
                    node_name=f"{uid}_{index}",
                    geom_name=f"{uid}_{index}",
                )
                meshes.append(mesh)
    else:
        raise TypeError(f"Unsupported mesh type for {mesh_path}: {type(loaded)!r}")

    if not meshes:
        raise ValueError(f"No renderable meshes found in {mesh_path}")
    bounds = np.asarray(
        [mesh.bounds for mesh in meshes if len(mesh.vertices) > 0],
        dtype=float,
    )
    return np.stack([bounds[:, 0, :].min(axis=0), bounds[:, 1, :].max(axis=0)])


if __name__ == "__main__":
    main()
