from __future__ import annotations

import base64
import json
import os
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import numpy as np


EMBODICHAIN_ROOT = Path(
    os.environ.get("EMBODICHAIN_ROOT", "/home/dex/桌面/EmbodiChain")
).expanduser()
IMAGE_DIR = Path(
    os.environ.get(
        "AUTO_IMAGE_DIR",
        str(EMBODICHAIN_ROOT / "gym_project/action_agent_pipeline/auto_images"),
    )
).expanduser()
GENERATED_IMAGE_DIR = Path(
    os.environ.get("AUTO_GENERATED_IMAGE_DIR", "./tmp_img/auto")
).expanduser()
IMAGE_API_KEY = os.environ.get(
    "AUTO_IMAGE_API_KEY",
    os.environ.get("ARK_API_KEY", "ark-1891f346-53b3-431d-97bc-a6b82b9c0efb-213db"),
)
IMAGE_API_URL = os.environ.get(
    "AUTO_IMAGE_API_URL",
    "https://ark.cn-beijing.volces.com/api/v3",
)
IMAGE_MODEL = os.environ.get("AUTO_IMAGE_MODEL", "doubao-seedream-4-5-251128")
IMAGE_SIZE = os.environ.get("AUTO_IMAGE_SIZE", "2848x1600")
IMAGE_PROMPT = (
    "以原图为基础参考，严格保持原图的相机视角、拍摄距离、透视关系、画面构图、背景环境、桌面材质、桌面纹理、光影方向、阴影位置和整体明暗关系。"
    "原图中已有物体的类别、大小、形状、轮廓、空间位置、朝向、部件数量和结构比例必须保持不变。"
    "如果提供了 Scene description，只根据该描述在桌面上新增对应背景物体；新增物体必须放在描述指定的位置，尺寸、透视、遮挡和阴影要与原图自然一致。"
    "不要删除原有物体，不要移动原有物体，不要改变主任务物体的结构。高清细节，真实自然。"
)

TASK_DESCRIPTIONS: dict[tuple[int, int], str] = {
    (0, 0): "把罐头放到篮子里",
    (0, 1): "把两个方块放到篮子里",
    (0, 2): "把方块放到篮子里",
    (0, 3): "把苹果放到篮子里",
    (1, 0): "双手共同搬运锅",
    (1, 1): "双手共同搬运雨伞",
    (1, 2): "双手共同搬运盒子",
    (1, 3): "双手共同搬运托盘",
    (2, 0): "把左边的瓶子扶正",
    (2, 1): "把左边的瓶子扶正",
    (2, 2): "把左边的瓶子扶正",
    (2, 3): "分别扶正瓶子和纸杯",
    (3, 0): "把桌面上的三个方块叠在一起",
    (3, 1): "底下叠底下两个正方体，最上面一个纸杯",
    (3, 2): "一个方块放最底下，上面放一个爆米花桶，最上面再叠一个纸杯",
    (3, 3): "一个爆米花桶最下面，叠上一个纸杯，最上面再叠上一个水杯",
    (4, 0): "方块摆一排",
    (4, 1): "方块和瓶子交替排列",
    (4, 2): "罐头按一排排列",
    (4, 3): "方块、瓶子、罐头按类别分组",
}

RELATION_PATTERN = {
    (0, 0): ["at the left side of the can", "at the right side of the bottle"],
    (0, 1): [
        "at the left side of the left cheese cube",
        "at the right side of the right cheese cube",
    ],
    (0, 2): ["at the left side of the cube", "at the right side of the cup"],
    (0, 3): ["at the left side of the cube", "at the right side of the apple"],
    (1, 0): [],
    (1, 1): [],
    (1, 2): [],
    (1, 3): [],
    (2, 0): [
        "at the left side of the left bottle",
        "at the right side of the right bottle",
    ],
    (2, 1): [
        "at the left side of the left soda can",
        "at the right side of the right soda can",
    ],
    (2, 2): ["at the left side of the bottle", "at the right side of the can"],
    (2, 3): ["at the left side of the paper cup", "at the right side of the soda can"],
    (3, 0): [],
    (3, 1): [],
    (3, 2): [],
    (3, 3): [],
    (4, 0): [],
    (4, 1): [],
    (4, 2): [],
    (4, 3): [],
}

AREA_PATTERN = [
    "at the left side of the table",
    "at the right side of the table",
    "at the back of the table",
    "at the back right corner of the table",
    "at the back left corner of the table",
]

OBJECT_LIST = [
    "cup",
    "potted plant",
    "clock",
    "book",
    "pen",
    "bottle",
    "soda can",
    "photo frame",
    "apple",
    "peach",
    "bread",
    "chocolate bar",
    "cookie",
    "penholder",
    "desk lamp",
    "stapler",
    "headphones",
    "desk calendar",
    "eyeglasses",
    "fan",
    "bluetooth speaker",
    "table mirror",
    "computer mouse",
    "keyboard",
]


@dataclass(frozen=True)
class AutoInput:
    task_index: tuple[int, int]
    base_image_path: Path
    image_path: Path | None
    task_description: str
    scene_description: str

    def to_json_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["task_index"] = list(self.task_index)
        value["base_image_path"] = self.base_image_path.as_posix()
        value["image_path"] = self.image_path.as_posix() if self.image_path else None
        return value


def random_task(rng: np.random.Generator) -> tuple[int, int]:
    task = rng.integers(0, 5)
    sub_task = rng.integers(0, 4)
    return int(task), int(sub_task)


def get_base_image_path(task_index: tuple[int, int]) -> Path:
    return IMAGE_DIR / f"task{task_index[0]}_{task_index[1]}.png"


def get_task_description(task_index: tuple[int, int]) -> str:
    try:
        return TASK_DESCRIPTIONS[task_index]
    except KeyError as exc:
        raise KeyError(f"No task description configured for task{task_index}") from exc


def image_to_base64(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in (".jpg", ".jpeg"):
        mime = "image/jpeg"
    elif ext == ".png":
        mime = "image/png"
    else:
        raise ValueError(f"Not supported: {ext}, only jpg/jpeg/png are supported")
    with path.open("rb") as file:
        b64_str = base64.b64encode(file.read()).decode("utf-8")
    return f"data:{mime};base64,{b64_str}"


def build_image_prompt(scene_description: str = "") -> str:
    scene_description = (scene_description or "").strip()
    if not scene_description:
        return IMAGE_PROMPT
    return (
        f"{IMAGE_PROMPT}\n\n"
        "Scene description:\n"
        f"{scene_description}\n\n"
        "严格执行 Scene description 中的新增物体和空间位置要求。"
    )


def create_image_input(
    task_index: tuple[int, int],
    *,
    scene_description: str = "",
    output_dir: Path = GENERATED_IMAGE_DIR,
) -> Path:
    base_image_path = get_base_image_path(task_index)
    if not base_image_path.is_file():
        raise FileNotFoundError(f"Base auto image not found: {base_image_path}")

    from volcenginesdkarkruntime import Ark
    import requests

    image_base64 = image_to_base64(base_image_path)
    client = Ark(api_key=IMAGE_API_KEY, base_url=IMAGE_API_URL)
    response = client.images.generate(
        model=IMAGE_MODEL,
        prompt=build_image_prompt(scene_description),
        image=image_base64,
        size=IMAGE_SIZE,
        response_format="url",
        watermark=False,
    )
    image_url = response.data[0].url
    resp = requests.get(image_url, timeout=60)
    resp.raise_for_status()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = (
        output_dir
        / f"auto_task{task_index[0]}_{task_index[1]}_{uuid.uuid4().hex[:12]}.png"
    )
    output_path.write_bytes(resp.content)
    return output_path


def create_text_input(task_index: tuple[int, int], rng: np.random.Generator) -> str:
    text_parts: list[str] = []
    if task_index[0] == 4:
        return ""

    mu, sigma = 2.0, 1.0
    raw = rng.normal(loc=mu, scale=sigma)
    num_background_objects = int(np.clip(np.round(raw), 0, 5))

    if num_background_objects == 0:
        return ""

    selected_objects = rng.choice(
        OBJECT_LIST,
        size=num_background_objects,
        replace=False,
    ).tolist()

    spatial_candidates = []
    spatial_candidates.extend(RELATION_PATTERN.get(task_index, []))
    spatial_candidates.extend(AREA_PATTERN)
    spatial_candidates.append("on the table")

    for obj in selected_objects:
        selected_spatial = rng.choice(spatial_candidates)
        article = "an" if obj[0].lower() in {"a", "e", "i", "o", "u"} else "a"
        text_parts.append(f"Place {article} {obj} {selected_spatial}.")

    return " ".join(text_parts)


def generate_auto_text_input(
    *,
    rng: np.random.Generator | None = None,
    task_index: tuple[int, int] | None = None,
) -> AutoInput:
    rng = rng or np.random.default_rng()
    task_index = task_index or random_task(rng)
    base_image_path = get_base_image_path(task_index)
    if not base_image_path.is_file():
        raise FileNotFoundError(f"Base auto image not found: {base_image_path}")
    return AutoInput(
        task_index=task_index,
        base_image_path=base_image_path,
        image_path=None,
        task_description=get_task_description(task_index),
        scene_description=create_text_input(task_index, rng),
    )


def generate_auto_image(
    auto_input: AutoInput,
    *,
    output_dir: Path = GENERATED_IMAGE_DIR,
) -> AutoInput:
    image_path = create_image_input(
        auto_input.task_index,
        scene_description=auto_input.scene_description,
        output_dir=output_dir,
    )
    return replace(auto_input, image_path=image_path)


def generate_auto_input(
    *,
    rng: np.random.Generator | None = None,
    task_index: tuple[int, int] | None = None,
    output_dir: Path = GENERATED_IMAGE_DIR,
) -> AutoInput:
    auto_input = generate_auto_text_input(rng=rng, task_index=task_index)
    return generate_auto_image(auto_input, output_dir=output_dir)


def main() -> None:
    auto_input = generate_auto_input()
    print(json.dumps(auto_input.to_json_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
