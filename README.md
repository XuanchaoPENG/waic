# EmbodiChain Gradio App

Run from this directory with the `embodichain` conda environment:

```bash
conda run -n embodichain python gradio_app.py
```

The app uses a single local scene named `current`:

- input image: `/home/oem/桌面/EmbodiChain/gym_project/action_agent_pipeline/images/current.png`
- prompt2scene output: `/home/oem/桌面/EmbodiChain/gym_project/current`
- action-agent config: `/home/oem/桌面/EmbodiChain/gym_project/action_agent_pipeline/configs/current`
- Gradio GLB preview: `/home/oem/桌面/EmbodiChain/gym_project/action_agent_pipeline/configs/current/gradio_scene/scene_current.glb`

The local generation command is equivalent to:

```bash
python -m embodichain.gen_sim.action_agent_pipeline.cli.run_agent_pipeline \
  --use-prompt2scene \
  --image-name "_gradio_pending_<run>" \
  --prompt2scene-output-root "gym_project/_gradio_pending_<run>" \
  --config-output-dir "gym_project/action_agent_pipeline/configs/_gradio_pending_<run>" \
  --task_name "current" \
  --task_description "把中间的水瓶放到书上" \
  --overwrite-config \
  --regenerate \
  --skip-run-agent
```

After the generated files are promoted to `current`, Gradio launches dexsim with:

```bash
python -m embodichain.gen_sim.action_agent_pipeline.cli.run_agent \
  --task_name "current" \
  --gym_config "/home/oem/桌面/EmbodiChain/gym_project/action_agent_pipeline/configs/current/fast_gym_config.json" \
  --agent_config "/home/oem/桌面/EmbodiChain/gym_project/action_agent_pipeline/configs/current/agent_config.json" \
  --regenerate
```

Set `EMBODICHAIN_ROOT`, `GRADIO_SERVER_NAME`, or `GRADIO_SERVER_PORT` if the defaults need to change.

The app forces `NO_PROXY/no_proxy` for local and private-network addresses before
starting the pipeline subprocess. Internal services such as
`http://192.168.3.23:5014` should be reached directly, not through a proxy.
When opening the Gradio page from a phone or browser, make sure the browser or
system proxy also bypasses the computer's LAN address, for example
`192.168.9.134:7860`.
