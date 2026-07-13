# Gradio 可视化系统实现设计

本文档按当前 `gradio_app.py` 的实现记录，不描述未接入的旧方案。

## 当前结论

当前实现是本地单场景 Gradio 应用：

- Gradio 只负责输入、状态展示、静态 3D 预览、仿真视频展示和本地进程编排。
- pipeline 和 dexsim/run-agent 都在本机作为 subprocess 启动。
- EmbodiChain 根目录由环境变量 `EMBODICHAIN_ROOT` 指定，默认是 `/home/oem/桌面/EmbodiChain`。
- 运行时固定 scene id 为 `current`。
- 初始生成先写入 `_gradio_pending_<token>` staging 目录，成功后再 promote 到正式 `current`。
- 编辑模式直接基于已有 `current` 修改当前项目。
- pipeline 命令都带 `--skip-run-agent`，pipeline 成功后 Gradio 再单独启动 dexsim/run-agent。
- Auto 模式是连续循环：随机选择预置图片和任务，运行初始生成，等待 dexsim/run-agent 退出，归档日志，清理本轮生成内容，然后进入下一轮。
- Reset 是手动模式的全局中断和清理入口；Auto 模式下同一个按钮显示为 Stop，只停止当前 loop 和进程，不做 Reset 的全量清理。

Gradio 启动方式：

```python
demo.queue(default_concurrency_limit=1)
demo.launch(
    server_name=os.environ.get("GRADIO_SERVER_NAME", "0.0.0.0"),
    server_port=int(os.environ.get("GRADIO_SERVER_PORT", "7860")),
    allowed_paths=[str(EMBODICHAIN_ROOT)],
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
  Auto | Interact | Parallel Simulation | 中文 / English

机器人选择：
  Robot: Franka / UR5

输入区：
  Input image
  Task description
  Scene description
  Generation mode: Initial generation / Edit current scene / Change task only
  Random Input
  Generate
  Reset / Stop

输出区：
  LeRobot Data Preview / Parallel Env Data Preview
  Current task
  Progress
  Status
  Initial scene preview
  Edited scene preview
  Generated object GLBs preview
```

Auto 模式下还会显示 `Previous Auto Run` 区域，保留上一轮已完成运行的输入图、完整任务（包括场景描述）和归档视频。它会在下一轮生成期间继续可见，并在切换回 Interact 时隐藏。

页面只保留一个视频预览位。单环境运行在同时生成 audience video 和包含记录帧的 LeRobot dataset 时，会生成一个左右拼接、按时长同步的 combined video，并显示在 `LeRobot Data Preview`。打开 Parallel Simulation 后，该预览位标题改为 `Parallel Env Data Preview`。若 LeRobot dataset 只包含 `meta/info.json` 而没有 `data/*.parquet`，页面会记录该数据集没有可预览帧。

默认状态：

- `run_mode = "interact"`。
- `action_mode = None`。
- Robot 默认选择 `UR5`。
- `Interact` 按钮为 primary。
- `Reset` 按钮显示为 Reset。

顶部按钮含义：

- `Auto`：设置 `run_mode="auto"`，Reset 按钮显示为 Stop。
- `Interact`：设置 `run_mode="interact"`。
- `Parallel Simulation`：切换 `action_mode="parallel_env"`；它是叠加模式，不是独立 run mode。
- `中文 / English`：切换所有页面按钮、标题、说明文字、输入/输出标签和预览标签的中英文文本；不会重置当前 run mode、Parallel Simulation 状态或 Reset/Stop 状态。

Robot 单选：

- `UR5`：run-agent 不追加 robot profile 参数。
- `Franka`：run-agent 追加 `--robot-profile franka`。

Parallel Simulation 打开时，run-agent 追加：

```bash
--num_envs 9 --arena_space 3 --filter_dataset_saving
```

## 输入模式

### 手动模式

手动模式由 `Interact` 进入，也可以叠加 `Parallel Simulation` 和 Robot 选择。

`Random Input` 仅在 Interact 模式下显示。点击后会从可用本地模板中随机选择一张输入图，填写对应的任务描述，并生成随机场景描述；它不会直接启动 pipeline，仍需点击 `Generate`。随机任务和场景描述遵循页面的全局语言选择：English 生成英文描述，中文生成中文描述；Auto 模式的新一轮随机输入也遵循该选择。

输入：

- `Input image`：初始生成时必填，支持 upload 和 webcam，保存为 PNG。
- `Task description`：必填，传给 action-agent。
- `Scene description`：初始生成时作为 Prompt2Scene 场景提示；编辑时作为对当前场景的编辑提示。
- `Generation mode`：显式选择 `Initial generation`、`Edit current scene` 或 `Change task only`，不再根据 Scene description 是否为空推断模式。

模式规则：

- `Initial generation`：需要 Input image；可选 Scene description 会加入初始 Prompt2Scene 生成。
- `Edit current scene`：需要已有成功的当前场景和 Scene description；不要求重新上传图片。
- `Change task only`：保留当前场景，只重新生成 action-agent config。

### Auto 模式

Auto 模式不使用用户当前输入，而是每轮调用：

```text
random_input.generate_auto_text_input()
```

每轮生成：

```text
task_index
base_image_path
task_description
scene_description
```

预置初始图片位置：

```text
<EMBODICHAIN_ROOT>/gym_project/action_agent_pipeline/auto_images/task<task>_<sub_task>.png
```

任务索引范围：

```text
task:     0..4
sub_task: 0..3
```

Auto 每轮都强制走 initial pipeline：

```python
run_generate(
    base_image,
    auto_task,
    auto_scene,
    force_initial=True,
)
```

即使 `auto_scene` 非空，也不会进入编辑模式，而是作为 `--prompt2scene-prompt` 传给初始生成命令。

当前 Gradio Auto loop 不调用 `random_input.generate_auto_image()`，也不调用 Ark/Doubao 图片生成接口；它只使用本地预置图片。默认优先读取 `auto_images`；若该目录没有对应图片且没有设置 `AUTO_IMAGE_DIR`，则回退到仓库自带的 `baseline_image_input`。Auto 只从实际存在的 `task<task>_<sub_task>.png` 中选择；若没有任何可用输入图，会在启动前报错并停止，而不会无限重试。每轮 DexSim 成功后的 audience video 会复制到该轮 `auto_logs/<index>/audience_video/`，并作为页面当前视频保留到下一轮视频完成。

## 命令构造

### 初始生成命令

初始生成会先写 staging：

```bash
python -m embodichain.gen_sim.action_agent_pipeline.cli.run_agent_pipeline \
  --use-prompt2scene \
  --image-name "_gradio_pending_<token>" \
  --prompt2scene-output-root "gym_project/_gradio_pending_<token>" \
  --config-output-dir "gym_project/action_agent_pipeline/configs/_gradio_pending_<token>" \
  --task_name "current" \
  --task_description "<Task description>" \
  --overwrite-config \
  --regenerate \
  --skip-run-agent
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

### dexsim/run-agent 命令

pipeline 成功后启动：

```bash
python -m embodichain.gen_sim.action_agent_pipeline.cli.run_agent \
  --task_name current \
  --gym_config "<EMBODICHAIN_ROOT>/gym_project/action_agent_pipeline/configs/current/fast_gym_config.json" \
  --agent_config "<EMBODICHAIN_ROOT>/gym_project/action_agent_pipeline/configs/current/agent_config.json" \
  --regenerate
```

根据 UI 状态可能追加 Franka 或 Parallel Simulation 参数。

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

Auto 和交互日志归档：

```text
<EMBODICHAIN_ROOT>/gym_project/action_agent_pipeline/auto_logs/0001/log.md
<EMBODICHAIN_ROOT>/gym_project/action_agent_pipeline/auto_logs/0001/audience_video/<video>
```

dexsim/run-agent 视频从 `<EMBODICHAIN_ROOT>/outputs` 中查找，优先展示路径中包含 `audience` 的最新视频。

## 预览生成

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
init_pos
init_rot
body_scale
uid
```

然后用 `trimesh.Scene()` 导出 `scene_current.glb`。

初始生成成功后：

- `scene_current.glb` 复制为 `initial_scene.glb`。
- 左侧 `Initial scene preview` 指向 `initial_scene.glb`。
- 右侧 `Edited scene preview` 为空。

编辑前：

- 确保 `initial_scene.glb` 存在。
- 删除旧的 `scene_current.glb` 和 `scene_manifest.json`，避免右侧显示旧编辑结果。

编辑成功后：

- 重新生成 `scene_current.glb`。
- 左侧继续显示 `initial_scene.glb`。
- 右侧显示新的 `scene_current.glb`。

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

1. 用户上传图片，填写 `Task description`，`Scene description` 留空。
2. Gradio 保存图片到 staging image。
3. 启动初始生成 pipeline。
4. supervisor 根据 stdout 和关键文件更新进度。
5. 发现 generated object GLB 后生成底部 object preview。
6. 发现 staging `fast_gym_config.json` 后生成 staging `scene_current.glb`。
7. pipeline 返回 0、`fast_gym_config.json` 存在、preview 构建无错误后，promote staging 到 `current`。
8. promote 后重写文本文件里的 staging path 和 scene id。
9. 生成或覆盖 `initial_scene.glb`。
10. 启动 dexsim/run-agent。
11. dexsim/run-agent 退出后，如果有 audience 视频，UI 的 `Current saved video` 显示该视频。
12. 交互模式会把日志归档到 `auto_logs/<序号>/log.md`；如果有 audience 视频，也会复制到该轮日志目录。

## 编辑流程

1. 用户填写 `Task description` 和 `Scene description`。
2. Gradio 检查当前 scene state 和 current config 是否存在。
3. 确保 `initial_scene.glb` 存在。
4. 删除旧的右侧编辑预览文件。
5. 启动编辑 pipeline。
6. 编辑完成后根据新的 `fast_gym_config.json` 生成 `scene_current.glb`。
7. 左侧显示初始快照，右侧显示编辑后场景。
8. 启动 dexsim/run-agent。
9. dexsim/run-agent 退出后更新视频输出并归档日志。

## Auto 连续循环

启动：

1. 点击 `Auto`。
2. Reset 按钮变为 Stop。
3. 点击 `Generate`。
4. `start_auto_loop_state()` 创建 `auto_loop_token`，设置 `auto_loop_active=True`。

每轮：

1. `auto_round += 1`。
2. 清理上一轮 generated artifacts。
3. 随机选择预置任务和预置图片。
4. UI 输入框被本轮自动值覆盖。
5. 调用 initial pipeline。
6. pipeline 失败时归档 `pipeline_failed`，清理本轮内容，继续下一轮。
7. pipeline 成功后启动 dexsim/run-agent。
8. Auto 等待 `runtime.sim_process is None`。
9. 记录本轮日志和视频。
10. 清理本轮内容。
11. 如果 loop 仍 active，进入下一轮。

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

Auto 使用 `cleanup_auto_generated_artifacts()`，清理目标和 Reset 基本一致，但会保护预置初始图片目录：

```text
<AUTO_IMAGE_DIR>
```

`AUTO_IMAGE_DIR` 默认是：

```text
<EMBODICHAIN_ROOT>/gym_project/action_agent_pipeline/auto_images
```

因此 Auto 不会删除：

```text
auto_images/task0_0.png
...
auto_images/task4_3.png
```

## 状态模型

运行时状态保存在进程内 `RuntimeState`，由 `runtime_lock` 保护：

```python
{
    "is_busy": False,
    "run_token": "uuid",
    "auto_loop_active": False,
    "auto_loop_token": None,
    "auto_round": 0,
    "process": None,
    "sim_process": None,
    "sim_started": False,
    "sim_finished": False,
    "sim_returncode": None,
    "phase_key": "idle",
    "status": "Idle.",
    "task_text": "",
    "image_path": None,
    "video_path": None,
    "object_model_path": None,
    "scene_model_path": None,
    "edited_scene_model_path": None,
    "last_error": None,
    "log_lines": "deque",
}
```

含义：

- `process`：当前 pipeline 进程。
- `sim_process`：当前 dexsim/run-agent 进程。
- `video_path`：UI 中展示的 audience video。
- `scene_model_path`：左侧 Initial scene preview。
- `edited_scene_model_path`：右侧 Edited scene preview。
- `object_model_path`：底部 Generated object GLBs preview。
- `run_token`：防止旧线程覆盖新任务状态。
- `auto_loop_token`：防止 Stop 后旧 Auto generator 继续推进下一轮。

## 进度阶段

| 进度 | phase key | 阶段 | 触发条件 |
| --- | --- | --- | --- |
| 0% | `idle` | Idle | 页面初始、Reset 或 Stop 后 |
| 5% | `received` | Input received | 输入校验通过或 Auto 选择本轮任务 |
| 10% | `started` | Local pipeline started | subprocess 创建成功 |
| 20% | `scene_intake` | Scene understanding | stdout 或 `scene_intake/result.json` |
| 35% | `relations` | Segmentation and spatial relations | stdout 或 relations result |
| 55% | `asset_generation` | 3D asset generation | stdout 或 `glb_gen` 下出现 GLB |
| 70% | `gym_export` | Scene export | `gym_export/gym_config.json` |
| 82% | `config` | Action config generated | `fast_gym_config.json` |
| 90% | `preview` | 3D preview loaded | `scene_current.glb` |
| 100% | `complete` | Complete | pipeline、promotion/edit preview 完成 |
| 100% | `failed` | Failed | pipeline、preview、promotion 或启动失败 |

## 并发和进程

- 同一时间只允许一个 pipeline 运行。
- `is_busy=True` 时再次点击 Generate 会返回当前状态，并提示已有任务运行。
- 新的手动 Generate 会先终止旧 dexsim/run-agent 进程。
- Reset 可以在任务运行时执行，并终止 pipeline 和 dexsim/run-agent 进程组。
- Auto loop 内部仍复用普通 `run_generate()`，所以 pipeline 阶段也受 `is_busy` 保护。
- pipeline 完成后 `is_busy=False`，但 Auto 不会立刻进入下一轮，而是等待 dexsim/run-agent 退出。
- subprocess 使用 `start_new_session=True`，停止时按进程组发送 SIGTERM，超时后发送 SIGKILL。

## 错误处理

UI 会展示：

- 图片为空。
- `Task description` 为空。
- Auto 预置图片缺失。
- 编辑模式缺少 current scene state。
- 编辑模式缺少 current `fast_gym_config.json`。
- 保存图片失败。
- pipeline 启动失败。
- pipeline 返回非 0。
- pipeline 返回 0 但没有生成 `fast_gym_config.json`。
- 3D preview 构建失败。
- promote 失败。
- cleanup 失败。
- dexsim/run-agent 启动失败。
- dexsim/run-agent 非 0 退出。

失败目录策略：

- 初始生成失败：staging 保留，方便排查；不会覆盖正式 `current`。
- promote 失败：尽量回滚 staging 和旧 current。
- 编辑失败：上游可能已经部分覆盖 `current`，但 `initial_scene.glb` 会保留。
- dexsim/run-agent 启动失败：Gradio 静态预览仍可用。
- Auto pipeline 失败：归档后清理本轮内容，并继续下一轮。
- Auto dexsim/run-agent 非 0 退出：记录状态和日志；进程退出后继续下一轮。

## 当前实现范围

已实现：

1. 本地 Gradio UI。
2. Auto / Interact / Parallel Simulation 顶部控制。
3. Robot 单选：UR5 / Franka。
4. 图片上传和 webcam 输入。
5. Task / Scene 双文本输入。
6. 初始生成 staging。
7. staging promote 到 current。
8. 基于 current 的自然语言编辑。
9. 初始/编辑双 3D 场景预览。
10. generated object GLB preview。
11. stdout 和关键文件驱动进度。
12. pipeline 成功后启动 dexsim/run-agent。
13. audience video 展示和日志归档。
14. Auto 连续循环。
15. Reset / Stop。
16. proxy 环境变量清理。

仍需注意：

1. 编辑模式直接修改 `current`，上游中途失败时可能留下部分更新。
2. Reset 只能终止本地 subprocess，不能取消远端服务已接收的请求。
3. `outputs` 清理会保留视频文件。
4. Auto 只保护 `AUTO_IMAGE_DIR` 下的预置图片。
5. Auto 不调用图片生成 API；`random_input.py` 中的 Ark/Doubao 图片生成函数目前不是 Gradio 路径的一部分。
