# Gradio 可视化系统实现设计

本文档按当前 `gradio_app.py` 的实现记录，不描述未接入的旧方案。相关 Auto 输入逻辑来自 `random_input.py`。

## 当前结论

当前实现是一个本地单场景 Gradio 应用：

- Gradio 负责输入、状态展示、3D 预览、视频预览、Auto 循环和本地进程编排。
- Prompt2Scene pipeline、action-agent config 生成和 dexsim/run-agent 都在本机作为 subprocess 启动。
- EmbodiChain 根目录由环境变量 `EMBODICHAIN_ROOT` 指定，默认是 `/home/dex/桌面/EmbodiChain`。
- 运行时固定 scene id 为 `current`。
- 初始生成先写入 `_gradio_pending_<token>` staging 目录，成功后再 promote 到正式 `current`。
- promote 时会把旧 `current` 临时移动到 `_gradio_replaced_<token>`，失败时尝试回滚。
- 编辑模式和仅修改任务模式都直接基于已有 `current` 操作。
- pipeline 命令都带 `--skip-run-agent`；pipeline 成功后 Gradio 再单独启动 dexsim/run-agent。
- Auto 模式是连续循环：随机选择本地预置图片和任务，强制运行初始生成，等待 dexsim/run-agent 退出，归档日志和视频，清理本轮生成内容，然后进入下一轮。
- Reset 是手动模式的全局中断和清理入口；Auto 模式下同一个按钮显示为 Stop，只停止当前 loop 和进程，不做 Reset 的全量清理。

Gradio 启动方式：

```python
demo.queue(default_concurrency_limit=1)
demo.launch(
    server_name=os.environ.get("GRADIO_SERVER_NAME", "0.0.0.0"),
    server_port=int(os.environ.get("GRADIO_SERVER_PORT", "7860")),
    allowed_paths=[str(EMBODICHAIN_ROOT), str(ASSETS_DIR)],
)
```

同一局域网访问：

```text
http://电脑局域网IP:7860
```

## 网络环境

`gradio_app.py` 在 import Gradio 前会清理 proxy 环境变量：

```text
HTTP_PROXY / HTTPS_PROXY / ALL_PROXY / FTP_PROXY
http_proxy / https_proxy / all_proxy / ftp_proxy
```

并设置：

```text
NO_PROXY=*
no_proxy=*
GRADIO_ANALYTICS_ENABLED=False
```

同样的环境会传给 pipeline 和 dexsim/run-agent 子进程。这个设置只影响 Python 进程及其子进程读取到的环境变量；如果系统层面启用了 TUN 或全局路由，是否绕过仍取决于系统配置。

## UI 结构

当前页面包含：

```text
顶部：
  DexForce logo（assets/dexforce.png 存在时显示）
  Generative Simulation User Interface / 生成式仿真用户界面
  Auto | Interact | Parallel Simulation | 中文 / English

说明和机器人：
  instruction 文案
  Robot: Franka / UR5

输入区：
  Input image
  Task description
  Scene description
  Generation mode: Initial generation / Edit current scene / Change task only
  Random material
  Generate
  Random Input
  Reset / Stop

主输出区：
  LeRobot Data Preview / Parallel Env Data Preview
  Current task

Auto 历史区：
  Previous Auto Run
  Previous input image
  Previous task
  Previous run preview

状态和 3D 预览：
  Progress
  Status
  Initial scene preview
  Edited scene preview
  Generated object GLBs preview
```

默认状态：

- `run_mode = "interact"`。
- `action_mode = None`。
- `language = "en"`。
- Robot 默认选择 `UR5`。
- `Interact` 按钮为 primary。
- `Reset` 按钮显示为 Reset。
- Auto 历史区默认隐藏。

顶部按钮含义：

- `Auto`：设置 `run_mode="auto"`，显示 Auto 历史区，Reset 按钮显示为 Stop。
- `Interact`：设置 `run_mode="interact"`，隐藏 Auto 历史区；如果 Auto loop 正在运行，会停止它。
- `Parallel Simulation`：切换 `action_mode="parallel_env"`；它是叠加模式，不是独立 run mode。
- `中文 / English`：切换所有按钮、标题、说明文字、输入/输出标签和预览标签的中英文文本；不会重置当前 run mode、Parallel Simulation 状态或 Reset/Stop 状态。

Robot 单选：

- `UR5`：pipeline 追加 `--robot-profile dual_ur5`。
- `Franka`：pipeline 追加 `--robot-profile franka`。
- dexsim/run-agent 当前不接收 robot profile 参数，只使用生成好的 config。

Parallel Simulation 打开时，dexsim/run-agent 追加：

```bash
--num_envs 9 --arena_space 2.5 --filter_dataset_saving
```

并且视频预览标签从 `LeRobot Data Preview` 切换为 `Parallel Env Data Preview`。

## 输入模式

### 手动模式

手动模式由 `Interact` 进入，也可以叠加 `Parallel Simulation` 和 Robot 选择。

`Random Input` 仅在 Interact 模式下显示。点击后会从可用本地模板中随机选择一张输入图，填写对应任务描述和随机场景描述，并把 `Generation mode` 设置为 `Initial generation`。它不会直接启动 pipeline，仍需点击 `Generate`。随机任务和场景描述遵循页面当前语言。

输入：

- `Input image`：初始生成时必填，支持 upload 和 webcam，保存为 PNG。
- `Task description`：所有模式都必填，传给 action-agent。
- `Scene description`：初始生成时作为 Prompt2Scene 场景提示；编辑时作为对当前场景的编辑提示；仅修改任务模式不可编辑。
- `Generation mode`：显式选择 `Initial generation`、`Edit current scene` 或 `Change task only`，不再根据 Scene description 是否为空推断模式。
- `Random material`：独立开关，不与 Generation mode 三个选项互斥；选中时在 Prompt2Scene/action config 模板命令中追加 `--load-template-material`。

模式规则：

- `Initial generation`：需要 Input image；可选 Scene description 会追加到初始 Prompt2Scene 命令。
- `Edit current scene`：需要已有 current scene state、current config 和 Scene description；不要求重新上传图片，图片输入不可编辑。
- `Change task only`：需要已有 `current/gym_export/gym_config.json`；保留当前场景，只重新生成 action-agent config，Scene description 不可编辑。

### Auto 模式

Auto 模式不使用用户当前输入，而是每轮调用：

```text
random_input.generate_auto_text_input(language=<当前语言>)
```

每轮生成：

```text
task_index
base_image_path
task_description
scene_description
```

Auto 每轮都强制走 initial pipeline：

```python
run_generate(
    base_image,
    auto_task,
    auto_scene,
    force_initial=True,
    scene_mode=SCENE_MODE_INITIAL,
)
```

即使 `auto_scene` 非空，也不会进入编辑模式，而是作为 `--prompt2scene-prompt` 传给初始生成命令。

当前 Gradio Auto loop 不调用 `random_input.generate_auto_image()`，也不调用 Ark/Doubao 图片生成接口；它只使用本地预置图片。Auto 只从实际存在的 `task<task>_<sub_task>.png` 中选择；若没有任何可用输入图，会在启动前报错并停止。

Auto 图片目录来自 `random_input.py`：

```text
AUTO_IMAGE_DIR 显式设置时：
  <AUTO_IMAGE_DIR>

AUTO_IMAGE_DIR 未设置时，按顺序查找：
  <EMBODICHAIN_ROOT>/gym_project/action_agent_pipeline/auto_images
  <EMBODICHAIN_ROOT>/gym_project/action_agent_pipeline/baseline_image_input
```

注意第一个默认路径在代码中是 `action_agent_pipeline`，不是 `action_agent_pipeline`。

任务索引范围：

```text
task:     0..4
sub_task: 0..3
```

每轮 dexsim/run-agent 结束后，页面会把本轮输入图、完整任务文本和归档视频放入 `Previous Auto Run` 区域。该区域会在下一轮生成期间继续显示，并在切换回 Interact 时隐藏。

## 命令构造

### 初始生成命令

初始生成会先写 staging：

```bash
python -m embodichain.gen_sim.action_agent_pipeline.cli.run_agent_pipeline \
  --use-prompt2scene \
  --image "<staging image png>" \
  --prompt2scene-output-root "gym_project/_gradio_pending_<token>" \
  --config-output-dir "gym_project/action_agent_pipeline/configs/_gradio_pending_<token>" \
  --task_name "current" \
  --task_description "<Task description>" \
  --overwrite-config \
  --regenerate \
  --skip-run-agent
```

选中 `Random material` 时追加：

```bash
  --load-template-material
```

如果选择了 Robot，会追加：

```bash
--robot-profile dual_ur5
```

或：

```bash
--robot-profile franka
```

如果传入 `Scene description`，会追加：

```bash
--prompt2scene-prompt "<Scene description>"
```

### 编辑命令

编辑要求以下文件存在：

```text
<EMBODICHAIN_ROOT>/gym_project/current/gym_export/scene_state/result.json
<EMBODICHAIN_ROOT>/gym_project/action_agent_pipeline/configs/current/fast_gym_config.json
```

编辑命令：

```bash
python -m embodichain.gen_sim.action_agent_pipeline.cli.run_agent_pipeline \
  --use-prompt2scene \
  --prompt2scene-output-root "gym_project/current" \
  --prompt2scene-prompt "<Scene description>" \
  --config-output-dir "gym_project/action_agent_pipeline/configs/current" \
  --task_name "current" \
  --task_description "<Task description>" \
  --overwrite-config \
  --regenerate \
  --skip-run-agent
```

同样会根据 Robot 追加 `--robot-profile dual_ur5` 或 `--robot-profile franka`。

### 仅修改任务命令

仅修改任务要求以下文件存在：

```text
<EMBODICHAIN_ROOT>/gym_project/current/gym_export/gym_config.json
```

命令：

```bash
python -m embodichain.gen_sim.action_agent_pipeline.cli.generate_action_agent_config \
  --gym_project "gym_project/current/gym_export" \
  --output_dir "gym_project/action_agent_pipeline/configs/current" \
  --task_name "current" \
  --task_description "<Task description>" \
  --target_body_scale 1.3 \
  --overwrite
```

选中 `Random material` 时追加：

```bash
  --load-template-material
```

同样会根据 Robot 追加 `--robot-profile dual_ur5` 或 `--robot-profile franka`。

### dexsim/run-agent 命令

pipeline 或仅修改任务命令成功后启动：

```bash
python -m embodichain.gen_sim.action_agent_pipeline.cli.run_agent \
  --task_name current \
  --gym_config "<EMBODICHAIN_ROOT>/gym_project/action_agent_pipeline/configs/current/fast_gym_config.json" \
  --agent_config "<EMBODICHAIN_ROOT>/gym_project/action_agent_pipeline/configs/current/agent_config.json" \
  --regenerate \
  --renderer hybrid
```

Parallel Simulation 打开时追加：

```bash
--num_envs 9 --arena_space 2.5 --filter_dataset_saving
```

## 输出文件

正式 current：

```text
<EMBODICHAIN_ROOT>/gym_project/current
<EMBODICHAIN_ROOT>/gym_project/action_agent_pipeline/configs/current
<EMBODICHAIN_ROOT>/gym_project/action_agent_pipeline/images/current.png
```

初始生成 staging：

```text
<EMBODICHAIN_ROOT>/gym_project/_gradio_pending_<token>
<EMBODICHAIN_ROOT>/gym_project/action_agent_pipeline/configs/_gradio_pending_<token>
<EMBODICHAIN_ROOT>/gym_project/action_agent_pipeline/images/_gradio_pending_<token>.png
```

promote backup：

```text
<EMBODICHAIN_ROOT>/gym_project/_gradio_replaced_<token>
<EMBODICHAIN_ROOT>/gym_project/action_agent_pipeline/configs/_gradio_replaced_<token>
<EMBODICHAIN_ROOT>/gym_project/action_agent_pipeline/images/_gradio_replaced_<token>.png
```

Gradio 预览缓存：

```text
<EMBODICHAIN_ROOT>/gym_project/action_agent_pipeline/configs/current/gradio_scene/
  scene_current.glb
  initial_scene.glb
  object_preview.glb
  scene_manifest.json
  object_preview_manifest.json
```

视频预览缓存：

```text
<EMBODICHAIN_ROOT>/outputs/lerobot_previews/
<EMBODICHAIN_ROOT>/outputs/combined_previews/
```

Auto 和交互日志归档：

```text
<EMBODICHAIN_ROOT>/gym_project/action_agent_pipeline/auto_logs/0001/log.md
<EMBODICHAIN_ROOT>/gym_project/action_agent_pipeline/auto_logs/0001/audience_video/<video>
```

dexsim/run-agent 视频从 `<EMBODICHAIN_ROOT>/outputs` 中查找，只展示路径中包含 `audience` 的最新视频。

LeRobot dataset 查找位置：

```text
EMBODICHAIN_DATASET_ROOT（如设置）
~/.cache/embodichain_datasets
fast_gym_config.json 中 lerobot.params.save_path
```

只有包含 `data/*.parquet` 的 dataset 才会被当作可预览数据集。

## 视频预览

页面只有一个主视频预览位：

- 单环境模式下，优先生成并展示 audience video 和 LeRobot 数据预览的左右拼接视频。
- 如果拼接视频生成失败或缺少 LeRobot 预览，则展示最新 audience video。
- Parallel Simulation 模式下不生成拼接视频，只展示最新 audience video。

LeRobot 数据预览由 parquet 数据渲染为 MP4，最多采样 360 帧，包含：

```text
observation.state
action
observation.qvel
timestamp
```

拼接视频使用 `ffmpeg` 生成，目标帧率为 25 FPS，并按 audience video 与 LeRobot preview 的时长比例同步。

## 3D 预览生成

### 场景预览

`build_gradio_scene_from_fast_config()` 读取 `fast_gym_config.json`，遍历：

```text
background
rigid_object
```

只处理：

```text
shape.shape_type == "Mesh"
```

每个对象应用：

```text
init_local_pose（若存在）
或 init_pos + init_rot
body_scale
uid
```

`.glb` / `.gltf` 会应用 `dexsim_gltf_y_up_to_sim_z_up_v1` 坐标转换，然后用 `trimesh.Scene()` 导出 `scene_current.glb`。

manifest 会记录源 config、mesh 文件大小、mtime 和 transform policy；如果 manifest 与当前文件不匹配，会重新构建预览。

初始生成成功后：

- staging 的 `scene_current.glb` 会随 config 目录 promote 到 current。
- `scene_current.glb` 复制为 `initial_scene.glb`。
- 左侧 `Initial scene preview` 指向 `initial_scene.glb`。
- 右侧 `Edited scene preview` 为空。

编辑前：

- 确保 `initial_scene.glb` 存在。
- 删除旧的 `scene_current.glb` 和 `scene_manifest.json`，避免右侧显示旧编辑结果。

编辑成功后：

- 根据新的 current `fast_gym_config.json` 重新生成 `scene_current.glb`。
- 左侧继续显示 `initial_scene.glb`。
- 右侧显示新的 `scene_current.glb`。

仅修改任务成功后：

- 重新基于 current config 构建 `scene_current.glb`。
- 如果 `scene_current.glb` 存在，会复制为 `initial_scene.glb`。
- 右侧编辑预览清空。

### Object GLB 预览

来源：

```text
<prompt_root>/**/glb_gen/**/*.glb
```

每个 `glb_gen` 目录优先选择 `*_simready.glb`；如果没有 simready，则选择该目录下可预览的全部 GLB。

`build_object_preview_scene()` 会把多个对象沿 X 轴排布、归一化尺度，并导出：

```text
object_preview.glb
object_preview_manifest.json
```

manifest 用源文件相对路径、文件大小和 `mtime_ns` 判断是否需要重建。

## 初始生成流程

1. 用户上传图片，填写 `Task description`，可选填写 `Scene description`。
2. Gradio 保存图片到 staging image。
3. 启动初始生成 pipeline。
4. supervisor 根据 stdout 和关键文件更新进度。
5. 发现 generated object GLB 后生成 object preview。
6. 发现 staging `fast_gym_config.json` 后生成 staging `scene_current.glb`。
7. pipeline 返回 0、`fast_gym_config.json` 存在、preview 构建无错误后，promote staging 到 `current`。
8. promote 后重写文本文件里的 staging path 和 scene id。
9. 生成或覆盖 `initial_scene.glb`。
10. 启动 dexsim/run-agent。
11. dexsim/run-agent 退出后查找 audience video 和 LeRobot dataset，生成预览视频。
12. 交互模式会把日志归档到 `auto_logs/<序号>/log.md`；如果有视频，也会复制到该轮日志目录。

## 编辑流程

1. 用户填写 `Task description` 和 `Scene description`。
2. Gradio 检查 current scene state 和 current config 是否存在。
3. 确保 `initial_scene.glb` 存在。
4. 删除旧的右侧编辑预览文件。
5. 启动编辑 pipeline。
6. 编辑完成后根据新的 `fast_gym_config.json` 生成 `scene_current.glb`。
7. 左侧显示初始快照，右侧显示编辑后场景。
8. 启动 dexsim/run-agent。
9. dexsim/run-agent 退出后更新视频输出并归档日志。

## 仅修改任务流程

1. 用户填写新的 `Task description`。
2. Gradio 检查 `current/gym_export/gym_config.json` 是否存在。
3. 启动 `generate_action_agent_config`。
4. 命令成功且 `fast_gym_config.json`、`agent_config.json` 都存在后，重建 3D 预览。
5. 启动 dexsim/run-agent。
6. dexsim/run-agent 退出后更新视频输出并归档日志。

## Auto 连续循环

启动：

1. 点击 `Auto`。
2. Reset 按钮变为 Stop，Auto 历史区显示。
3. 点击 `Generate`。
4. `start_auto_loop_state()` 检查可用本地输入图，创建 `auto_loop_token`，设置 `auto_loop_active=True`。

每轮：

1. `auto_round += 1`。
2. 清理上一轮 generated artifacts。
3. 根据当前语言随机选择预置任务、预置图片和场景描述。
4. UI 输入框被本轮自动值覆盖。
5. 调用 initial pipeline。
6. pipeline 失败时归档 `pipeline_failed`，清理本轮内容，继续下一轮。
7. pipeline 成功后启动 dexsim/run-agent。
8. Auto 等待 `runtime.sim_process is None`。
9. 记录本轮日志和视频。
10. 把本轮输入图、完整任务和归档视频写入 Previous Auto Run。
11. 清理本轮内容。
12. 如果 loop 仍 active，进入下一轮。

Stop：

- 点击 Stop，或 Auto 运行中切换到 `Interact`。
- 替换 `run_token`，清空 `auto_loop_token`。
- 终止当前 pipeline 和 dexsim/run-agent 进程组。
- 清空 UI 状态和预览。
- 不执行 Reset 的全量清理。

## 清理策略

### Reset

Reset 会：

- 替换 `run_token`。
- 停止当前 pipeline 和 dexsim/run-agent。
- 清空 runtime 状态。
- 删除 current、staging、backup 和 current image。
- 清理 `<EMBODICHAIN_ROOT>/outputs` 中的非视频文件和可删除的空目录。
- 保留 `<EMBODICHAIN_ROOT>/outputs` 下的视频文件。

清理目标包括：

```text
gym_project/current
gym_project/action_agent_pipeline/configs/current
gym_project/action_agent_pipeline/images/current.png
gym_project/_gradio_pending_*
gym_project/_gradio_replaced_*
gym_project/action_agent_pipeline/configs/_gradio_pending_*
gym_project/action_agent_pipeline/configs/_gradio_replaced_*
gym_project/action_agent_pipeline/images/_gradio_pending_*.png
gym_project/action_agent_pipeline/images/_gradio_replaced_*.png
```

### Auto 清理

Auto 使用 `cleanup_auto_generated_artifacts()`，清理目标和 Reset 基本一致，但会保护 `random_input.IMAGE_DIR` 下的预置图片。

`random_input.IMAGE_DIR` 为：

```text
AUTO_IMAGE_DIR（如设置）
否则 <EMBODICHAIN_ROOT>/gym_project/action_agent_pipeline/auto_images
```

因此 Auto 只保护这个目录下的预置图片；回退目录 `baseline_image_input` 不是保护目录，但它通常不在 Auto 清理目标列表中。

## 状态模型

运行时状态保存在进程内 `RuntimeState`，由 `runtime_lock` 保护：

```python
{
    "is_busy": False,
    "run_token": "uuid",
    "auto_loop_active": False,
    "auto_loop_token": None,
    "auto_round": 0,
    "language": "en",
    "process": None,
    "sim_process": None,
    "sim_started": False,
    "sim_finished": False,
    "sim_returncode": None,
    "phase_key": "idle",
    "status": "Idle.",
    "task_text": "",
    "input_task_text": "",
    "input_scene_text": "",
    "image_path": None,
    "video_path": None,
    "lerobot_video_path": None,
    "lerobot_dataset_path": None,
    "previous_auto_image_path": None,
    "previous_auto_task_text": "",
    "previous_auto_video_path": None,
    "submitted_input_revision": 0,
    "object_model_path": None,
    "scene_model_path": None,
    "edited_scene_model_path": None,
    "last_error": None,
    "log_lines": "deque",
}
```

含义：

- `process`：当前 pipeline 或 config 生成进程。
- `sim_process`：当前 dexsim/run-agent 进程。
- `video_path`：主视频预览展示的视频，可能是 combined video，也可能是 audience video。
- `lerobot_video_path`：未拼接时的 LeRobot 数据预览视频路径。
- `lerobot_dataset_path`：最新可预览 LeRobot dataset。
- `scene_model_path`：左侧 Initial scene preview。
- `edited_scene_model_path`：右侧 Edited scene preview。
- `object_model_path`：Generated object GLBs preview。
- `run_token`：防止旧线程覆盖新任务状态。
- `auto_loop_token`：防止 Stop 后旧 Auto generator 继续推进下一轮。
- `submitted_input_revision`：Interact 模式下用于让其他客户端只同步最新一次 Generate 提交，而不持续覆盖本地草稿。

## 进度阶段

| 进度 | phase key | 阶段 | 触发条件 |
| --- | --- | --- | --- |
| 0% | `idle` | Idle | 页面初始、Reset 或 Stop 后 |
| 5% | `received` | Input received | 输入校验通过或 Auto 选择本轮任务 |
| 10% | `started` | Local pipeline started | subprocess 创建成功 |
| 20% | `scene_intake` | Scene understanding | stdout 或 `scene_intake/result.json` |
| 35% | `relations` | Segmentation and spatial relations | stdout 或 relations result |
| 55% | `asset_generation` | 3D asset generation | stdout 或 `unified_scene_gen/**/*.glb` |
| 70% | `gym_export` | Scene export | `gym_export/gym_config.json` |
| 82% | `config` | Action config generated | `fast_gym_config.json` |
| 90% | `preview` | 3D preview loaded | `scene_current.glb` |
| 100% | `complete` | Complete | pipeline、promotion/edit preview 完成 |
| 100% | `failed` | Failed | pipeline、preview、promotion、输入或启动失败 |

状态 Markdown 固定展示：

```text
State: running / ready
Phase: <progress>% - <label>
Status: <status_text>
Last error: <last_error>（如有）
```

## 并发和进程

- Gradio queue 的 `default_concurrency_limit=1`。
- 同一时间只允许一个 pipeline 或 config 生成进程运行。
- `is_busy=True` 时再次点击 Generate 会返回当前状态，并提示已有任务运行。
- 新的手动 Generate 会先终止旧 dexsim/run-agent 进程。
- Reset 可以在任务运行时执行，并终止 pipeline 和 dexsim/run-agent 进程组。
- Auto loop 内部复用普通 `run_generate()`，所以 pipeline 阶段也受 `is_busy` 保护。
- pipeline 完成后 `is_busy=False`，但 Auto 不会立刻进入下一轮，而是等待 dexsim/run-agent 退出。
- subprocess 使用 `start_new_session=True`，停止时按进程组发送 SIGTERM，超时 8 秒后发送 SIGKILL。
- 页面有 `gr.Timer(2.0)`，周期性同步运行状态；Auto 模式下会持续同步输入框内容，Interact 模式下只在某个客户端点击 `Generate` 并成功提交后，把该次提交的图片和文本同步到其他客户端一次。

## 错误处理

UI 会展示：

- 图片为空。
- `Task description` 为空。
- Auto 预置图片缺失。
- 编辑模式缺少 current scene state。
- 编辑模式缺少 current `fast_gym_config.json`。
- 仅修改任务模式缺少 current gym export。
- 保存图片失败。
- pipeline 或 config 命令启动失败。
- pipeline 或 config 命令返回非 0。
- pipeline 返回 0 但缺少必要输出。
- 3D preview 构建失败。
- promote 失败。
- cleanup 失败。
- dexsim/run-agent 启动失败。
- dexsim/run-agent 非 0 退出。

失败目录策略：

- 初始生成失败：staging 保留，方便排查；不会覆盖正式 `current`。
- promote 失败：尽量回滚 staging 和旧 current。
- 编辑失败：上游可能已经部分覆盖 `current`，但 `initial_scene.glb` 会保留。
- 仅修改任务失败：当前场景保留，但 config 可能由上游部分写入。
- dexsim/run-agent 启动失败：Gradio 静态 3D 预览仍可用。
- Auto pipeline 失败：归档后清理本轮内容，并继续下一轮。
- Auto dexsim/run-agent 非 0 退出：记录状态和日志；进程退出后继续下一轮。

## 当前实现范围

已实现：

1. 本地 Gradio UI。
2. DexForce logo 条件显示。
3. Auto / Interact / Parallel Simulation 顶部控制。
4. 中英文 UI 切换。
5. Robot 单选：UR5 / Franka。
6. 图片上传和 webcam 输入。
7. Task / Scene 双文本输入。
8. 初始生成 staging。
9. staging promote 到 current 和失败回滚。
10. 基于 current 的自然语言编辑。
11. 仅修改任务并重建 action-agent config。
12. 初始/编辑双 3D 场景预览。
13. generated object GLB preview。
14. stdout 和关键文件驱动进度。
15. pipeline 成功后启动 dexsim/run-agent。
16. audience video、LeRobot 数据预览和单环境 combined video。
17. 交互运行和 Auto 运行日志归档。
18. Auto 连续循环和 Previous Auto Run 展示。
19. Reset / Stop。
20. proxy 环境变量清理。

仍需注意：

1. 编辑模式直接修改 `current`，上游中途失败时可能留下部分更新。
2. 仅修改任务模式可能部分覆盖 current config。
3. Reset 只能终止本地 subprocess，不能取消远端服务已接收的请求。
4. `outputs` 清理会保留视频文件。
5. Auto 只保护 `random_input.IMAGE_DIR`，且代码里的默认 auto_images 路径拼写为 `action_agent_pipeline`。
6. Auto 不调用图片生成 API；`random_input.py` 中的 Ark/Doubao 图片生成函数目前不是 Gradio 路径的一部分。
