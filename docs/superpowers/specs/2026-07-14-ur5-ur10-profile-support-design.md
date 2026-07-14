# UR5 and UR10 profile support

## Goal

Keep UR5 as the default robot profile in the Gradio app while allowing users to select UR10.

## Design

`gradio_app.py` will retain explicit profile constants and CLI mappings. The robot radio control will expose Franka, UR5, and UR10, with UR5 selected initially. UR5 maps to `dual_ur5` and UR10 maps to `dual_ur10`; Franka behavior is unchanged.

## Error handling

The existing unknown-profile behavior remains unchanged: it returns no CLI value.

## Verification

Add a focused regression test that verifies both UR profile mappings and that the Gradio selector presents UR5 and UR10 with UR5 as its default. Run the relevant test and the project test suite available in this checkout.

## Scope

No backend changes, robot-model discovery, or unrelated UI changes are included.
