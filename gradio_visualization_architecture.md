# Gradio 可视化系统架构

本文档描述当前代码中的三引擎 Debug 页面、Demo 页面，以及它们共享的场景和仿真能力。`gradio_app.py` 只负责启动；UI、资产处理和场景工作流分别由专用模块承担。

## 架构总览

```text
gradio_app.py
    │ 启动、队列、allowed_paths
    ▼
app_services.py（兼容门面）
    ▼
app_ui.py ───────────────► app_asset_engine.py
    │ Gradio 布局与事件绑定      │ SimReady 上传适配、预览、子进程日志
    ▼                             ▼
app_workflows.py ───────────── EmbodiChain SimReady CLI
    │ 场景生成、GLB 预览、DexSim
    ├──────────────► app_media.py     视频、数据集与日志归档
    ├──────────────► app_processes.py 子进程、stdout、阶段检测
    ├──────────────► app_state.py     RuntimeState、锁与进度
    ├──────────────► app_commands.py  场景 / action-agent 命令构造
    └──────────────► app_config.py    路径、文案、模式与固定参数
                                      ▼
                           EmbodiChain scene pipeline / DexSim
```

| 模块 | 职责 |
| --- | --- |
| `gradio_app.py` | 唯一启动入口；校验 `EMBODICHAIN_ROOT`、创建 Blocks、设置队列和本地文件访问路径。 |
| `app_ui.py` | Demo/Debug 布局、引擎切换及回调绑定。 |
| `app_asset_engine.py` | 单资产上传目录适配、输入/输出 GLB 预览、SimReady 命令和流式日志。 |
| `app_workflows.py` | 场景 staging/promote、场景/对象 GLB 构建、Demo/Scene/Action 回调。 |
| `app_processes.py` | pipeline 子进程环境、启动/终止、stdout 读取与阶段检测。 |
| `app_state.py` | 共享 `RuntimeState`、锁、阶段和耗时。 |
| `app_commands.py` | 场景生成、配置生成、run-agent CLI 参数构造。 |
| `app_media.py` | 视频和 LeRobot 数据预览、运行日志归档。 |
| `app_config.py` | 环境变量、路径、UI 文案、引擎模式、CLI 固定参数。 |

## 启动与本地文件访问

从项目根目录启动：

```bash
python gradio_app.py
```

`app_config.py` 支持：

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `EMBODICHAIN_ROOT` | `/home/dex/桌面/EmbodiChain` | EmbodiChain 根目录。 |
| `GRADIO_SERVER_NAME` | `0.0.0.0` | 监听地址。 |
| `GRADIO_SERVER_PORT` | `7860` | 监听端口。 |

应用会清除代理环境变量，设置 `NO_PROXY=*`，并禁用 Gradio analytics。`allowed_paths` 开放 EmbodiChain、页面静态资源和 `.debug_engine/`，使处理中的预览和产物可以被浏览器安全读取。

## 页面模式

页面顶部有 `Demo` 与 `Debug`。切换到 Debug 时，Demo 的 `demo-only` 组件被隐藏；切回 Demo 时，已有运行状态保持不变。

### Demo

Demo 是完整端到端流程：图像 + 任务 + 场景描述 → 场景生成/编辑 → 动作配置 → DexSim。它包含：

- `Auto`、`Interact`、`Parallel Simulation`；
- 图像、任务、场景描述、生成模式、随机输入和机器人选择；
- 视频、进度、状态、初始场景/编辑后场景/对象组合预览；
- 生成、任务重跑和重置/停止。

`Auto`、`Interact`、`Parallel Simulation` 是 Demo 内部状态，不等同于顶部的 Demo/Debug 页面模式。

### Debug：三个独立入口

| Engine | 输入 | 可视化 | 输出/行为 | 是否启动 DexSim |
| --- | --- | --- | --- | --- |
| Asset engine | 一个网格和可选材质附件、类别提示 | 原始资产 GLB 与 SimReady 资产 GLB | 可下载 SimReady 网格及处理日志 | 否 |
| Scene engine | 图像、任务、场景描述、生成模式、机器人 | 初始、编辑后、对象 GLB | 生成并 promote 为 `current` Gym 场景 | 否 |
| Action engine | 已由 Scene engine 生成的 `current` Gym 场景、任务、机器人 | 输入 Gym 场景及 DexSim 视频 | 使用当前 action 配置启动 DexSim | 是 |

三个按钮仅切换面板，不会因为切换动作本身启动 pipeline。

## Asset engine：SimReady 单资产处理

上游 SimReady CLI 的输入是目录，Gradio 的输入是一个或多个上传文件。`app_asset_engine.py` 是两者之间的适配层：

```text
上传 mesh + sidecar
  → .debug_engine/assets/runs/<token>/input/
  → 将上传内容复制为一个隔离资产目录
  → trimesh 转换为 input_preview.glb
  → SimReady CLI
  → output/<asset-id>/asset_simready/{asset_simready.glb|asset_simready.obj}
  → Model3D 预览 + File 下载
```

支持的主网格格式为 `.glb`、`.gltf`、`.obj`、`.ply`、`.stl`；可同时上传 `.mtl`、纹理和 `.bin` 等附件。上传文件名会被规整为 basename，重复名会加序号，避免上传路径影响本地目录。

实际执行命令：

```bash
python -m embodichain.gen_sim.simready_pipeline.cli.start \
  --input_dir <isolated-input-dir> \
  --output_root <isolated-output-dir> \
  --category <category>
```

处理期间保留最近的 stdout 日志；完成后优先查找 `asset_simready.glb`，没有时使用 `asset_simready.obj` 并转换为 GLB 预览。Asset engine 不依赖也不唤起 DexSim。

## Scene engine：与 Demo/Interact 的复用边界

Scene engine 复用 `run_generate()`、`ScenePaths`、staging/promote、`build_gradio_scene_from_fast_config()` 和对象组合预览。没有复制第二条场景流水线。

复用的关键是 `run_generate(..., launch_simulation=False)`：

```text
图像/编辑输入
  → shared run_generate
  → prompt2scene + action config + GLB preview
  → promote current
  → 完成

Demo/Interact: 同一流程完成后 launch_simulation=True → DexSim
Debug/Scene:   同一流程完成后 launch_simulation=False → 不启动仿真
```

因此 Scene engine 与 Demo/Interact 保持相同的生成、编辑、预览和持久化语义；它只移除了视频、任务重跑和并行环境这一仿真阶段。

场景操作仍由 `Generation mode` 决定：

| 操作 | 要求 | 结果 |
| --- | --- | --- |
| `Initial generation` | 图像和任务 | 生成新场景。 |
| `Edit current scene` | 任务、场景描述、已有 `current` | 修改当前场景。 |
| `Change task only` | 任务、已有 `current` | 复用场景并更新动作配置。 |

## Action engine：为什么不能把普通 GLB 当场景输入

普通 GLB 只能用于渲染；DexSim 还需要对象的碰撞、物理参数、初始位姿、资源相对路径以及 Gym/action 配置。因此 Action engine 的输入契约是 Scene engine 已产出的 `current` Gym 场景：

```text
Scene engine / Demo 产出
  gym_project/current/gym_export/
  gym_project/action_agent_pipeline/configs/current/
       │ fast_gym_config.json + agent_config.json
       ▼
Action engine 载入场景 GLB 预览
       ▼
run_agent / DexSim
```

这避免了“能预览但无法仿真”的裸 GLB 假输入。若后续要支持上传外部场景，应定义可移植的 Scene Bundle（例如 zip），至少包含 `gym_export/`、所有网格与纹理、`fast_gym_config.json`，并在 Action engine 中重新生成或校验 `agent_config.json`；不能只接收一个 GLB。

当前 Action engine 使用现有 `agent_config.json` 启动 run-agent。任务文本会写入运行时显示和日志；若任务改变需要重建动作图，则应走 Scene engine 的 `Change task only`，或在后续 Scene Bundle 改造中显式调用 `generate_action_agent_config`。

在启动前，`action_agent_cli_is_available()` 会检测 `embodichain.gen_sim.action_agent_pipeline.cli.run_agent`。缺失时页面给出清晰错误，不会启动必然失败的子进程。

## 场景生命周期与并发

正式场景固定为 `current`：

```text
gym_project/current
gym_project/action_agent_pipeline/configs/current
gym_project/action_agent_pipeline/images/current.png
```

初始生成使用 staging，避免未完成任务覆盖现有场景：

```text
上传图像
  → _gradio_pending_<token> 图像、prompt2scene、config
  → pipeline / GLB preview
  → 成功：promote 到 current
  → 失败：保留 current，清理 staging
```

`RuntimeState` 通过 `runtime_lock` 管理 pipeline、DexSim 子进程、token、输入、视频、预览、日志和进度。`demo.queue(default_concurrency_limit=1)` 防止高成本流程并发；Demo 的 `Timer(2.0)` 和 Action engine 的独立 `Timer(2.0)` 轮询共享状态。

场景阶段为：

```text
idle → received → started → scene_intake → relations
→ asset_generation → gym_export → config → preview → complete
```

## 环境前置条件与验证

Asset engine 需要安装 SimReady 依赖（Blender、trimesh、LLM 配置等）并能导入：

```text
embodichain.gen_sim.simready_pipeline.cli.start
```

Scene/Action 的完整运行还需要 action-agent 模块，特别是：

```text
embodichain.gen_sim.action_agent_pipeline.cli.run_agent_pipeline
embodichain.gen_sim.action_agent_pipeline.cli.run_agent
```

当前验证环境可导入 SimReady CLI，但未发现 `action_agent_pipeline.cli.run_agent`；因此 Asset engine 可运行，Action engine 会在预检阶段提示缺少依赖。安装/恢复 action-agent 包后，既有 Scene/Action 复用路径无需再改 UI。

每次修改后至少执行：

```bash
python -m py_compile \
  gradio_app.py app_config.py app_state.py app_commands.py \
  app_processes.py app_media.py app_workflows.py app_ui.py \
  app_asset_engine.py app_services.py

env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  -u http_proxy -u https_proxy -u all_proxy \
  conda run -n embodichain python -c \
  "from app_ui import build_demo; assert build_demo() is not None"
```

手动检查：

1. Asset engine 上传简单网格后显示输入预览；执行后显示 SimReady 输出或明确报错；
2. Scene engine 生成后出现 GLB 预览，且没有 DexSim 子进程；
3. Action engine 可载入 `current` 场景；缺少 CLI 时显示预检错误；
4. Demo 的 Interact / Auto / Parallel Simulation 行为不因 Debug 面板切换改变；
5. 初始生成失败不覆盖 `current`，Reset/Stop 能终止对应子进程。
