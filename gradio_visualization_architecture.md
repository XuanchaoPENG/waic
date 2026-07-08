# Gradio 可视化系统架构设计

## 当前结论

当前实现采用本地单场景架构，Gradio 只作为本地 pipeline 的输入、状态和静态 3D 预览界面：

- Gradio 运行在展示电脑本地，手机和电脑访问同一个局域网 URL。
- 本地电脑负责完整 pipeline 编排、文件生成、预览 GLB 转换和 dexsim 启动。
- 远端服务只承担 pipeline 内部已有的 `sam3d`、`sam3`、`zimage` 等服务调用，不作为 Gradio 任务服务器。
- 运行时固定逻辑场景名为 `current`，最终成功结果保存在：
  - `gym_project/current`
  - `gym_project/action_agent_pipeline/configs/current`
  - `gym_project/action_agent_pipeline/images/current.png`
- 初始生成不会直接覆盖 `current`，而是先写入 `_gradio_pending_<token>` staging 目录，成功后再 promote 到 `current`。
- 自然语言编辑直接基于当前 `gym_project/current/gym_export` 执行，并覆盖当前 `current` 文件。
- Auto 模式是连续循环模式：每一轮自动随机选择预置任务和初始图片，随机生成环境提示词，调用豆包生成本轮输入图，运行初始生成 pipeline，等待 dexsim/run-agent 仿真进程退出后清理本轮生成内容并进入下一轮，直到用户点击 Stop 或切换到其他顶部模式。
- Auto 清理会删除本轮生成的 current、staging、backup、outputs 和豆包临时图，但会保护预置初始图片目录：
  - `gym_project/action_agent_pipeline/auto_images`
- 页面有两个场景预览：
  - 左侧 `Initial scene preview`：初始场景，或编辑前的上一次场景。
  - 右侧 `Edited scene preview`：本次编辑后的新场景；初始生成时为空。
- Reset 是全局中断和清理入口：终止当前 pipeline/dexsim subprocess，删除 current、staging、backup、输入图片和 `outputs/`。
- Auto 模式下同一个按钮显示为 Stop：只停止 auto loop、当前 pipeline 和 dexsim，不执行 Reset 的全量清理。
- dexsim 仍用独立弹窗展示，和 Gradio 页面解耦。

Gradio 启动方式：

```python
demo.queue(default_concurrency_limit=1)
demo.launch(
    server_name=os.environ.get("GRADIO_SERVER_NAME", "0.0.0.0"),
    server_port=int(os.environ.get("GRADIO_SERVER_PORT", "7860")),
    allowed_paths=[str(EMBODICHAIN_ROOT)],
)
```

手机访问：

```text
http://电脑局域网IP:7860
```

## 网络环境

`gradio_app.py` 在 import Gradio 前会强制配置直连环境：

```python
HTTP_PROXY / HTTPS_PROXY / ALL_PROXY / FTP_PROXY 及对应小写变量会被移除
NO_PROXY="*"
no_proxy="*"
GRADIO_ANALYTICS_ENABLED="False"
```

同样的环境会传给 pipeline 和 dexsim 子进程。这个设置能阻止 Python/HTTP 客户端使用 proxy 环境变量；如果系统 TUN 已接管默认路由，是否绕过 TUN 仍取决于系统路由规则。

## 输入和命令模式

页面输入区包含：

- 顶部模式按钮：`Auto` / `Robot Model` / `Parallel Env`
- `Input image`
- `Task description`
- `Scene description`
- `Generate`
- `Reset` / `Stop`

默认顶部模式是 `Robot Model`。`Auto`、`Robot Model`、`Parallel Env` 目前主要影响 Gradio 编排逻辑：

- `Robot Model` 和 `Parallel Env` 都走手动输入路径。
- `Auto` 走自动循环路径。
- 如果 Auto loop 正在运行，用户点击 `Robot Model` 或 `Parallel Env` 会先自动 Stop 当前 auto loop，再切换按钮状态。

手动路径中，`Task description` 总是作为 action-agent 任务描述。`Scene description` 用来区分运行模式：

- `Scene description` 为空：初始生成。
- `Scene description` 非空：基于当前场景编辑。

Auto 路径中，用户上传的 `Input image`、手写 `Task description` 和手写 `Scene description` 都会被本轮自动生成值覆盖：

- `Input image` 先显示预置初始图片，豆包图生成后 Gradio 的 `Current saved image` 显示豆包生成图或 promoted 后的 `current.png`。
- `Task description` 来自 `random_input.TASK_DESCRIPTIONS`。
- `Scene description` 来自 `random_input.create_text_input()` 的随机背景物体和空间关系描述。
- Auto 每轮都强制走 initial pipeline，即使 `Scene description` 非空，也不会进入编辑模式。

### 初始生成命令

初始生成要求上传图片且 `Task description` 非空。当前实现先写 staging，而不是直接写 `current`：

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

`--skip-run-agent` 是有意保留的：pipeline 先只生成 prompt2scene 和 action-agent config；成功 promote 到 `current` 后，Gradio 再单独启动 dexsim/run-agent。

### 编辑命令

编辑模式要求 `Task description` 和 `Scene description` 都非空，并且已有成功的当前场景：

```text
gym_project/current/gym_export/scene_state/result.json
gym_project/action_agent_pipeline/configs/current/fast_gym_config.json
```

编辑命令：

```bash
python -m embodichain.gen_sim.action_agent_pipeline.cli.run_agent_pipeline \
  --use-existing-gym-project \
  --gym-project "gym_project/current/gym_export" \
  --prompt2scene-prompt "<Scene description>" \
  --config-output-dir "gym_project/action_agent_pipeline/configs/current" \
  --task_name "current" \
  --task_description "<Task description>" \
  --overwrite-config \
  --regenerate \
  --skip-run-agent
```

这里 `--gym-project` 指向 `gym_project/current/gym_export`，因为 EmbodiChain CLI 在 `--prompt2scene-prompt` 编辑模式下会从这个路径反推出 prompt2scene output root，也就是 `gym_project/current`。

### Auto 输入生成和命令

Auto 模式的 Gradio 入口仍然是 `run_generate_for_top_mode()`，但当 `top_mode == "auto"` 时，不再使用用户当前输入，而是启动一个持续循环。

每一轮 Auto 的输入来源：

```text
random_input.generate_auto_text_input()
random_input.generate_auto_image(auto_input)
```

`generate_auto_text_input()` 的逻辑：

1. 使用 `np.random.default_rng()`。
2. 随机选择 `task_index=(task, sub_task)`：

```text
task:     0..4
sub_task: 0..3
```

3. 根据 task index 读取预置初始图片：

```text
gym_project/action_agent_pipeline/auto_images/task<task>_<sub_task>.png
```

4. 根据 `TASK_DESCRIPTIONS` 得到中文任务描述。
5. 根据 `create_text_input()` 随机生成 `Scene description`：
   - task 类型为 `4` 时直接返回空字符串。
   - 其他 task 从正态分布 `N(2.0, 1.0)` 采样背景物体数量，四舍五入并 clip 到 `0..5`。
   - 从 `OBJECT_LIST` 随机选择不重复背景物体。
   - 空间关系候选来自：
     - 当前 task 对应的 `RELATION_PATTERN`
     - 通用 `AREA_PATTERN`
     - `"on the table"`
   - 输出英文句子，例如 `Place a book at the back of the table.`

`generate_auto_image()` 的逻辑：

1. 将预置初始图片转成 base64 data URL。
2. 调用 Ark/Doubao 图片接口：

```text
AUTO_IMAGE_API_URL 默认 https://ark.cn-beijing.volces.com/api/v3
AUTO_IMAGE_MODEL 默认 doubao-seedream-4-5-251128
AUTO_IMAGE_SIZE 默认 2848x1600
```

3. prompt 由固定 `IMAGE_PROMPT` 加上本轮 `Scene description` 组成，要求保持原图视角、构图、物体位置、光影，只根据 scene description 新增背景物体。
4. 下载接口返回的图片 URL。
5. 保存到：

```text
./tmp_img/auto/auto_task<task>_<sub_task>_<uuid12>.png
```

拿到豆包生成图后，Auto 调用普通 initial pipeline：

```python
run_generate(
    generated_image,
    auto_task,
    auto_scene,
    force_initial=True,
)
```

`force_initial=True` 是 Auto 的关键约束：即使 `auto_scene` 非空，也不会走编辑命令，而是构造初始生成命令，并把 `auto_scene` 作为 `--prompt2scene-prompt` 传给 prompt2scene。

## 用户流程

### 初始生成

1. 用户上传图片，填写 `Task description`，`Scene description` 留空。
2. Gradio 保存图片到：

```text
gym_project/action_agent_pipeline/images/_gradio_pending_<token>.png
```

3. Gradio 启动初始生成命令，输出写入 staging：

```text
gym_project/_gradio_pending_<token>
gym_project/action_agent_pipeline/configs/_gradio_pending_<token>
```

4. 后台 supervisor 根据 stdout 和关键文件更新进度。
5. 发现 staging 的 generated object GLB 时，构建 `object_preview.glb`。
6. 发现 staging 的 `fast_gym_config.json` 时，构建 staging 的 `scene_current.glb` 并在左侧预览。
7. pipeline 返回 0 且 preview 构建成功后，promote staging 到 `current`：
   - 旧 `current` 移到 `_gradio_replaced_<token>` backup。
   - staging prompt root、config dir、image 移动到正式 `current`。
   - 文本文件内的 staging scene id 和路径替换为 `current`。
   - backup 清理。
8. 左侧 `Initial scene preview` 指向正式 `configs/current/gradio_scene/scene_current.glb`。
9. 右侧 `Edited scene preview` 保持为空。
10. Gradio 启动 dexsim/run-agent 子进程。

### Auto 连续循环

Auto 是“持续自动跑初始生成”的模式。它不是一次性随机输入，而是一个由 `auto_loop_active` 和 `auto_loop_token` 控制的循环。

启动流程：

1. 用户点击顶部 `Auto` 按钮。
2. UI 将 `Auto` 按钮设为 primary，并把 Reset 按钮文字改为 `Stop`。
3. 用户点击 `Generate`。
4. `run_generate_for_top_mode()` 检测 `top_mode == "auto"`，调用 `start_auto_loop_state()`。
5. `start_auto_loop_state()`：
   - 如果已有 auto loop 或 pipeline 正在运行，则拒绝启动并提示已有任务。
   - 创建新的 `auto_loop_token`。
   - 设置 `auto_loop_active=True`。
   - 重置 `auto_round=0`。
   - 清空日志和错误。
   - 如果存在旧 pipeline 或旧 dexsim 进程，先终止它们。

每一轮 Auto 的流程：

1. `auto_round += 1`。
2. 设置状态为 `Auto round N: cleaning previous artifacts.`。
3. 调用 `cleanup_auto_generated_artifacts()` 清理上一轮生成内容。
4. 随机生成 `AutoInput`：

```text
task_index
base_image_path
task_description
scene_description
```

5. UI 输入区被覆盖：
   - `Input image` 显示预置初始图 `auto_images/taskX_Y.png`。
   - `Task description` 显示本轮中文 task。
   - `Scene description` 显示本轮随机环境提示词。
6. 状态显示：

```text
Auto round N: selected taskX_Y. Waiting for Doubao image.
```

7. 调用豆包图片生成，生成本轮输入图。
8. 如果豆包生成成功：
   - `runtime.image_path` 指向豆包生成图。
   - UI 状态显示 `Doubao image generated: taskX_Y.`
9. 调用 `run_generate(generated_image, auto_task, auto_scene, force_initial=True)`。
10. `run_generate()` 仍按初始生成处理：
    - 保存输入图到 `_gradio_pending_<token>.png`。
    - 启动 `--use-prompt2scene` pipeline。
    - 生成 object preview。
    - 生成 staging scene preview。
    - 成功后 promote 到 `current`。
    - 启动 dexsim/run-agent。
11. pipeline 阶段结束后，如果 `runtime.phase_key == "failed"`，Auto 归档本轮为 `pipeline_failed`，清理本轮生成内容，然后继续下一轮。
12. 如果 pipeline 成功启动 dexsim，Auto 等待 `runtime.sim_process is None`。

等待 dexsim 的含义：

- 不要求 dexsim return code 为 0。
- 不要求仿真任务成功。
- 只要求 dexsim/run-agent 进程曾经启动，并且已经退出；`sim_returncode` 只作为诊断信息记录。
- Auto 轮次归档的 `Outcome` 不再由 `last_error` 判断，而是由 `sim_started && sim_finished && sim_process is None` 判断。
- 等待期间 UI 会附加状态：

```text
Auto waiting for Dexsim to exit.
```

13. dexsim 退出后，Auto 调用 `cleanup_auto_generated_artifacts(generated_image_path)` 清理本轮生成内容和本轮豆包临时图。
14. 如果 `auto_loop_active` 仍为 true，进入下一轮，重新随机 task、图片和环境提示词。

停止流程：

1. 用户点击 `Stop`，或 Auto 正在运行时点击 `Robot Model` / `Parallel Env`。
2. Gradio 设置：

```text
auto_loop_active=False
auto_loop_token=None
auto_round=0
```

3. 替换 `run_token`，让旧 pipeline supervisor 和 dexsim monitor 失效。
4. 如果当前 pipeline 仍在运行，终止 pipeline 进程组。
5. 如果 dexsim 仍在运行，终止 dexsim 进程组。
6. 清空 UI 输入、预览、状态和日志，状态显示 `Stopped.`。

Stop 不执行 Reset 的全量清理。真正的逐轮清理由 Auto loop 在每轮开始和每轮结束时执行。

### 编辑

1. 用户保留或重新输入 `Task description`，填写 `Scene description`。
2. Gradio 不要求重新上传图片。
3. 编辑开始前，Gradio 确保当前预览 GLB 存在：

```text
gym_project/action_agent_pipeline/configs/current/gradio_scene/scene_current.glb
```

如果不存在，则先根据当前 `fast_gym_config.json` 生成它。

4. Gradio 将当前 GLB 复制为编辑前缓存：

```text
gym_project/action_agent_pipeline/configs/current/gradio_scene/previous_scene.glb
```

5. Gradio 删除当前 `scene_current.glb` 和 `scene_manifest.json`，避免后续误用旧预览。
6. Gradio 启动编辑命令，直接基于 `gym_project/current/gym_export` 修改当前 scene/config。
7. 编辑 pipeline 结束后，Gradio 根据新的 `configs/current/fast_gym_config.json` 重新生成：

```text
gym_project/action_agent_pipeline/configs/current/gradio_scene/scene_current.glb
```

8. 左侧 `Initial scene preview` 指向 `previous_scene.glb`。
9. 右侧 `Edited scene preview` 指向新的 `scene_current.glb`。
10. Gradio 启动 dexsim/run-agent 子进程。

连续编辑时，每次编辑前都会把“当时的 current 预览”复制到 `previous_scene.glb`。因此左侧始终是本次编辑前的场景，右侧是本次编辑后的最新 `current` 场景。本地仍只保存一个正式 scene，只额外保留一个用于对比展示的 GLB 缓存。

### Reset

1. 用户点击 Reset。
2. Gradio 替换 `run_token`，让旧后台线程失效。
3. 如果 pipeline 或 dexsim 仍在运行，终止对应进程组。
4. 删除：

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
outputs
```

5. 清空图片、文本、进度、状态、左右 3D 预览和 object preview。

### Auto 清理策略

Auto loop 使用独立清理函数：

```text
cleanup_auto_generated_artifacts(extra_image_path=None)
```

清理目标：

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
outputs
```

如果传入 `extra_image_path`，还会删除本轮豆包生成图，例如：

```text
./tmp_img/auto/auto_taskX_Y_<uuid>.png
```

保护规则：

```text
is_protected_auto_base_image(path)
```

只要待删路径位于以下目录内，就跳过删除：

```text
/home/oem/桌面/EmbodiChain/gym_project/action_agent_pipeline/auto_images
```

因此 Auto 会删除历史副本、current 图片、pending/replaced 图片和豆包临时图片，但不会删除预置初始图片：

```text
auto_images/task0_0.png
auto_images/task0_1.png
...
auto_images/task4_3.png
```

清理时还会清空 runtime 中的预览路径：

```text
image_path
object_model_path
scene_model_path
edited_scene_model_path
```

这能避免下一轮开始后 UI 继续显示上一轮的 3D 预览。

## UI 结构

当前 Gradio 页面结构：

```text
顶部模式区
  Auto | Robot Model | Parallel Env

输入区
  左列：
    Input image
    Task description | Scene description
    Generate | Reset/Stop
  右列：
    Current saved image
    Current task

状态区
  Progress slider
  Status markdown
  Recent logs / Last error

场景对比区
  Initial scene preview | Edited scene preview

对象预览区
  Generated object GLBs preview
```

两个文本输入框放在同一行，均为 `lines=1`，合计高度接近原来的单个 `Task description(lines=2)`。

顶部按钮只维护 Gradio 的 `top_mode` state：

- `Auto`：`top_mode="auto"`，Reset 按钮显示为 `Stop`。
- `Robot Model`：`top_mode="robot_model"`，Reset 按钮显示为 `Reset`。
- `Parallel Env`：`top_mode="parallel_env"`，Reset 按钮显示为 `Reset`。

如果从 Auto 切换到 `Robot Model` 或 `Parallel Env`，`select_top_mode()` 会先调用 `stop_auto_loop_if_running()`。

## 本地状态模型

运行时状态由进程内 `RuntimeState` 保存，并由 `runtime_lock` 保护：

```python
{
    "is_busy": False,
    "run_token": "uuid",
    "auto_loop_active": False,
    "auto_loop_token": "uuid | None",
    "auto_round": 0,
    "process": None,
    "sim_process": None,
    "sim_started": False,
    "sim_finished": False,
    "sim_returncode": None,
    "phase_key": "idle",
    "status": "Idle.",
    "task_text": "",
    "image_path": Path | None,
    "object_model_path": Path | None,
    "scene_model_path": Path | None,
    "edited_scene_model_path": Path | None,
    "last_error": str | None,
    "log_lines": deque(maxlen=80),
}
```

含义：

- `scene_model_path`：左侧 `Initial scene preview`。
- `edited_scene_model_path`：右侧 `Edited scene preview`。
- `object_model_path`：底部 generated object GLB preview。
- `process`：当前 pipeline 进程。
- `sim_process`：当前 dexsim/run-agent 进程。
- `sim_started`：本轮是否已经成功启动 dexsim/run-agent 进程。
- `sim_finished`：本轮 dexsim/run-agent 进程是否已经退出。
- `sim_returncode`：本轮 dexsim/run-agent 进程退出码，仅用于状态和日志诊断；Auto 轮次完成与否不要求它为 0。
- `run_token`：防止旧任务或旧线程覆盖新 UI。
- `auto_loop_active`：Auto 连续循环是否仍应继续。
- `auto_loop_token`：Auto loop 自己的生命周期 token，防止旧 auto generator 在 Stop 后继续推进下一轮。
- `auto_round`：当前 Auto 已进入第几轮，只用于状态展示。

`run_token` 规则：

- 每次 Generate 创建新 token。
- Reset 创建新 token，让当前后台线程失效。
- supervisor 和 simulation monitor 更新状态前必须检查 token。
- token 不匹配时终止或丢弃旧结果。

Auto 额外 token 规则：

- `start_auto_loop_state()` 创建 `auto_loop_token`。
- 每轮开始、豆包返回后、pipeline 返回后、等待 dexsim 时都会检查 `auto_loop_is_active(loop_token)`。
- Stop 或模式切换会清空 `auto_loop_token`，使当前 auto generator 在下一次检查时退出。
- Stop 同时替换 `run_token`，让 pipeline supervisor 和 dexsim monitor 也失效。

## 路径模型

固定根目录：

```text
EMBODICHAIN_ROOT=/home/oem/桌面/EmbodiChain
```

正式 current：

```text
gym_project/current
gym_project/action_agent_pipeline/configs/current
gym_project/action_agent_pipeline/images/current.png
```

初始生成 staging：

```text
gym_project/_gradio_pending_<token>
gym_project/action_agent_pipeline/configs/_gradio_pending_<token>
gym_project/action_agent_pipeline/images/_gradio_pending_<token>.png
```

promote backup：

```text
gym_project/_gradio_replaced_<token>
gym_project/action_agent_pipeline/configs/_gradio_replaced_<token>
gym_project/action_agent_pipeline/images/_gradio_replaced_<token>.png
```

Auto 预置初始图片：

```text
gym_project/action_agent_pipeline/auto_images/task<task>_<sub_task>.png
```

Auto 豆包生成图片：

```text
./tmp_img/auto/auto_task<task>_<sub_task>_<uuid12>.png
```

Auto 豆包生成图只作为本轮 pipeline 输入使用。本轮结束后会被 `cleanup_auto_generated_artifacts(extra_image_path)` 删除。预置初始图片位于 `auto_images`，受到保护，不会被 Auto 清理删除。

Gradio scene cache：

```text
gym_project/action_agent_pipeline/configs/current/gradio_scene/
  scene_current.glb
  previous_scene.glb
  object_preview.glb
  scene_manifest.json
  object_preview_manifest.json
```

## Pipeline 阶段和进度

进度不要求等于真实耗时，只用于提示当前阶段。

| 进度 | phase key | 阶段 | 触发条件 |
| --- | --- | --- | --- |
| 0% | `idle` | 空闲 | 页面初始或 Reset 后 |
| 5% | `received` | 已接收输入 | 输入校验通过，准备启动 pipeline |
| 10% | `started` | 已启动本地 pipeline | subprocess 创建成功 |
| 20% | `scene_intake` | 场景理解 | stdout 出现 `scene_intake`，或 `scene_intake/result.json` 出现 |
| 35% | `relations` | 分割和空间关系 | stdout 出现 `image_segments` / `image_spatial_relations`，或对应 result 出现 |
| 55% | `asset_generation` | 3D 资产生成 | stdout 出现 `unified_scene_gen` / `glb`，或 glb_gen 下出现 GLB |
| 70% | `gym_export` | 场景导出 | `gym_export/gym_config.json` 出现 |
| 82% | `config` | action config 生成 | `fast_gym_config.json` 出现 |
| 90% | `preview` | Gradio 3D 预览加载 | `scene_current.glb` 生成成功 |
| 100% | `complete` | 完成 | pipeline 成功，promotion/edit preview 处理完成 |
| 100% | `failed` | 失败 | pipeline、preview、promotion 或 simulation setup 失败 |

阶段来源有两类：

- `update_phase_from_log()` 从 stdout 关键词推进。
- `detect_phase_from_files()` 从关键文件是否存在推进。

## 3D 场景展示策略

Gradio 只展示静态场景，不展示机器人、不展示动作执行、不替代 dexsim。

主入口：

```text
gym_project/action_agent_pipeline/configs/current/fast_gym_config.json
```

或初始生成 staging 时：

```text
gym_project/action_agent_pipeline/configs/_gradio_pending_<token>/fast_gym_config.json
```

解析对象：

```text
background[]
rigid_object[]
```

忽略对象：

```text
robot
sensor
light
env.events
env.observations
env.dataset
```

每个 mesh 路径按 config 目录解析：

```python
mesh_path = config_dir / shape.fpath
```

每个对象应用：

- `init_pos`
- `init_rot`
- `body_scale`
- `uid`
- role: `background` 或 `rigid_object`

实现函数：

```text
build_gradio_scene_from_fast_config(config_path, scene_dir) -> scene_current.glb
```

内部使用 `trimesh.Scene()`：

1. 读取 `fast_gym_config.json`。
2. 遍历 `background` 和 `rigid_object`。
3. 只处理 `shape.shape_type == "Mesh"` 的对象。
4. 加载 OBJ/GLB mesh。
5. 应用 translation、Euler rotation、scale。
6. 导出单个 `scene_current.glb`。
7. 写 `scene_manifest.json`。

编辑对比实现：

- 编辑前：`scene_current.glb` 复制为 `previous_scene.glb`。
- 编辑中：删除旧 `scene_current.glb`，避免右侧误显示旧结果。
- 编辑完成：根据新 `fast_gym_config.json` 重新生成 `scene_current.glb`。
- 左侧显示 `previous_scene.glb`。
- 右侧显示新 `scene_current.glb`。

## Object GLB 预览

除了最终场景预览，当前实现还保留 generated object GLB preview。

来源：

```text
<prompt_root>/unified_scene_gen/glb_gen/**/*.glb
```

优先选择：

```text
*_simready.glb
```

如果没有 simready，则选择所有可预览 GLB。

实现：

```text
collect_generated_object_glbs(paths)
build_object_preview_scene(glb_paths, scene_dir)
object_preview_is_current(manifest_path, glb_paths)
```

`build_object_preview_scene()` 会把多个对象按 X 轴横向排布、归一化尺度，并导出：

```text
object_preview.glb
object_preview_manifest.json
```

manifest 使用源文件相对路径、文件大小和 `mtime_ns` 判断 preview 是否需要重建。

## 本地编排实现

关键函数：

```text
save_input(image, task_text, image_path) -> Path
build_initial_pipeline_command(task_text, paths) -> list[str]
build_edit_pipeline_command(task_text, env_text) -> list[str]
start_pipeline(command) -> subprocess.Popen
supervise_pipeline(token, paths, mode, process, task_text, output_queue, reader)
run_generate_for_top_mode(top_mode, image, task_text, env_text)
start_auto_loop_state() -> str | None
auto_loop_is_active(loop_token) -> bool
finish_auto_loop(loop_token, status_text=None) -> None
stop_auto_loop_if_running() -> bool
wait_for_current_simulation_to_exit(loop_token, base_image, auto_task, auto_scene)
cleanup_auto_generated_artifacts(extra_image_path=None) -> list[str]
is_protected_auto_base_image(path) -> bool
prepare_current_scene_for_edit() -> Path
build_gradio_scene_from_fast_config(config_path, scene_dir) -> Path
promote_stage_to_current(stage, run_token) -> list[str]
launch_current_simulation(token) -> str | None
reset_current_scene() -> list[str]
terminate_process_group(process) -> None
```

subprocess 启动：

```python
subprocess.Popen(
    command,
    cwd=EMBODICHAIN_ROOT,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    bufsize=1,
    start_new_session=True,
    env=build_pipeline_env(),
)
```

命令参数全部使用 list 传给 `subprocess.Popen`，不使用 shell 字符串拼接用户输入。

进程输出由独立 reader thread 读取到 `queue.Queue`，supervisor thread 定期 drain queue，并更新 UI 状态。

## 初始生成的 promote 机制

初始生成完成后调用：

```text
promote_stage_to_current(stage, run_token)
```

步骤：

1. 确认 staging 的 prompt root、config dir、image 都存在。
2. 清理同 token 的旧 backup。
3. 将当前正式目录移动到 `_gradio_replaced_<token>`。
4. 将 staging 目录移动到正式 `current`。
5. 调用 `rewrite_promoted_paths(stage)`，替换文本文件中的 staging 路径和 scene id。
6. 清理 backup。

如果 promote 中途失败：

- 已移动到 current 的 staging 会尽量移回 staging。
- backup 中的旧 current 会尽量恢复。
- UI 标记为 `failed` 并展示 promotion error。

## 编辑模式的覆盖机制

编辑模式不使用 staging，因为 EmbodiChain 的编辑 CLI 基于已有 prompt2scene project 修改当前 output root。

编辑前必须缓存可视化 GLB：

```text
scene_current.glb -> previous_scene.glb
```

然后删除旧：

```text
scene_current.glb
scene_manifest.json
```

这样可以保证：

- 左侧仍能展示编辑前状态。
- 右侧不会在编辑中提前显示旧场景。
- 编辑完成后右侧只显示根据新 `fast_gym_config.json` 生成的 GLB。

## 与 dexsim 的边界

Gradio 负责：

- 图片上传；
- `Task description` 和 `Scene description`；
- 初始生成和编辑命令启动；
- 运行状态、阶段进度和日志摘要；
- 静态 3D 场景预览；
- 编辑前后对比；
- Reset。

dexsim/run-agent 负责：

- 本地仿真窗口；
- 机器人动作；
- action-agent 执行效果。

pipeline 使用 `--skip-run-agent`，成功后 Gradio 再单独启动：

```bash
python -m embodichain.gen_sim.action_agent_pipeline.cli.run_agent \
  --task_name "current" \
  --gym_config "/home/oem/桌面/EmbodiChain/gym_project/action_agent_pipeline/configs/current/fast_gym_config.json" \
  --agent_config "/home/oem/桌面/EmbodiChain/gym_project/action_agent_pipeline/configs/current/agent_config.json" \
  --regenerate
```

如果 dexsim 启动失败，不影响 Gradio 已加载的静态场景预览；UI 会把 dexsim 错误写入 `last_error`。

Auto 模式对 dexsim 的额外规则：

- pipeline 成功后仍由 `launch_current_simulation(token)` 启动 dexsim/run-agent。
- Auto 不在 `Pipeline completed successfully.\nDexsim simulation launched.` 后立刻进入下一轮。
- `launch_current_simulation()` 成功创建进程时设置 `sim_started=True`、`sim_finished=False`、`sim_returncode=None`，并把 Popen 保存到 `runtime.sim_process`。
- Auto 会等待 `monitor_simulation()` 将 `runtime.sim_process` 置回 `None`。
- `monitor_simulation()` 观察到进程退出后设置 `sim_finished=True`，并把退出码写入 `sim_returncode`。
- Auto 不要求 dexsim return code 为 0；非 0 return code 会写入 status 和 recent logs，但不会写入 `last_error`，也不会单独导致 `Outcome: simulation_failed`。
- 如果 dexsim 启动失败，`sim_started` 仍为 false，启动错误会写入 `last_error`，本轮按 pipeline/simulation 启动失败处理。

## 并发和锁

当前只允许一个 pipeline 任务运行：

- `is_busy == False` 时，Generate 可以启动。
- `is_busy == True` 时，Generate 返回当前状态，并提示已有任务运行中。
- Reset 不受 `is_busy` 限制，任何时候都可以执行。
- 新 Generate 会先终止旧 dexsim 进程。
- Reset 会终止 pipeline 和 dexsim 进程组。
- Auto loop 运行期间，`auto_loop_active == True`，再次点击 Generate 会被拒绝。
- Auto 每一轮内部仍复用普通 `run_generate()`，所以 pipeline 阶段仍受 `is_busy` 保护。
- pipeline 完成后 `is_busy` 会变回 false，但 Auto loop 不会立刻进入下一轮，而是额外等待 `sim_process` 退出。
- Auto 正在运行时切换到 `Robot Model` 或 `Parallel Env` 会调用 `stop_auto_loop_if_running()`，终止当前 pipeline/dexsim 并停止 auto loop。

锁保护：

- `is_busy`
- `run_token`
- `auto_loop_active`
- `auto_loop_token`
- `auto_round`
- `process`
- `sim_process`
- `sim_started`
- `sim_finished`
- `sim_returncode`
- preview path
- status / logs / last_error

## 错误处理

UI 会展示：

- 图片为空；
- `Task description` 为空；
- Auto 文本随机生成失败；
- Auto 预置初始图片缺失；
- Auto 豆包图片生成失败；
- Auto 豆包返回缺少 image path；
- 编辑模式缺少当前 scene state；
- 当前 `fast_gym_config.json` 缺失；
- 保存图片失败；
- pipeline 启动失败；
- pipeline 返回非 0；
- `fast_gym_config.json` 未生成；
- 3D preview 构建失败；
- promote 失败；
- cleanup 失败；
- dexsim 启动失败。
- dexsim/run-agent 退出码非 0 时显示状态和日志诊断，但 Auto 不把它当作轮次失败条件。

失败时目录策略：

- 初始生成失败：staging 目录保留，方便调试；不会覆盖当前 `current`。
- promote 失败：尽量回滚 current 和 staging。
- 编辑失败：当前 prompt2scene/config 可能已被上游部分覆盖；左侧 `previous_scene.glb` 仍保留编辑前可视化缓存。
- Reset：删除 current、staging、backup、输入图片和 `outputs/`。
- Auto 文本生成、预置图片缺失或豆包图片生成失败：归档本轮失败，UI 显示 failed 和 last error；只要 auto loop 未被 Stop，后续轮次仍可继续。
- Auto pipeline 失败：归档本轮为 `pipeline_failed`，并调用 Auto 清理删除本轮生成内容和豆包临时图；只要 auto loop 未被 Stop，后续轮次仍可继续。
- Auto dexsim 非 0 退出：记录 dexsim return code 到 status/recent logs，但不写入 `last_error`，不停止 Auto loop；进程退出后继续下一轮。
- Auto Stop：不执行 Reset 全量清理；只终止当前 pipeline/dexsim，清空 UI 状态，并让 auto generator 在下一次 token 检查时退出。

## 当前实现范围

已实现：

1. 本地 Gradio app。
2. 顶部模式按钮：Auto / Robot Model / Parallel Env。
3. 图片上传、Task/Scene 双文本输入。
4. 初始生成命令。
5. 基于 current 的自然语言编辑命令。
6. staging -> current promote。
7. 编辑前后双 3D 预览。
8. object GLB preview。
9. stdout 和关键文件驱动的进度。
10. Reset 中断和清理。
11. Auto 连续循环：随机 task、随机 Scene description、豆包生成图、运行 initial pipeline、等待 dexsim 退出、清理并进入下一轮。
12. Auto Stop 和模式切换自动 Stop。
13. Auto 清理保护 `auto_images` 预置初始图片。
14. pipeline 成功后启动 dexsim。
15. proxy 环境变量清理和子进程直连环境。

后续可扩展：

- 在 UI 中单独展示 Scene description 历史；
- 编辑失败后的恢复按钮；
- 多版本场景历史；
- 对象点击、高亮和 uid 标签；
- 更细粒度的 prompt2scene 阶段日志；
- dexsim 状态独立面板；
- 远程服务健康检查。

## 仍需注意的技术点

1. `init_rot` 当前按 degrees 转 radians 处理，需和 dexsim 配置约定保持一致。
2. OBJ/MTL 纹理在浏览器端不直接加载，当前通过本地 trimesh 转成 GLB 绕开大部分兼容问题。
3. 编辑模式直接覆盖 `current`，如果上游中途失败，文件系统可能处于部分更新状态；目前只保证左侧 GLB 缓存仍可展示。
4. Reset 只能终止本地 CLI 进程组，无法保证远端服务端已经取消正在处理的请求。
5. 环境变量只能禁止应用使用 proxy，不能修改系统 TUN 路由。
6. Auto Stop 无法强制中断已经进入同步豆包 API 调用的 Python 线程；Stop 会立即清空 loop token 并终止本地 pipeline/dexsim，但如果豆包请求正在等待网络返回，auto generator 要等该调用返回后才会在下一次 token 检查时退出。
7. Auto 每轮都会删除豆包生成图和 current/pending/replaced 图片，只保护 `auto_images` 预置初始图片；如果需要保留失败样本，需要另加归档目录或 debug 开关。
