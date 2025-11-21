# Tuya Automation Toolkit

This folder concentrates everything needed to orchestrate Tuya Cloud automations: device discovery, datapoint inspection, heuristic scene generation, lifecycle management, and agent-friendly wrappers.

## Module Layout (integrations/tuya)
- `client.py` – Signed HTTP client with token refresh, rate-limit backoff, and helpers for listing devices, managing scenes, triggering rules, updating/deleting rules, and setting scene state.
- `models.py` – Pydantic models for Tuya devices, properties, scene conditions/actions, and payload serialization.
- `mapping.py` – Logical → DP/function code registry with fallbacks by product category.
- `heuristics.py` – Pure functions that translate inverter/plug datapoints into scene conditions/actions (Battery Protect, Solar Surplus, Night Guard).
- `workflow.py` – High-level coordinator that discovers devices, fetches shadows, builds payloads from heuristics, and wraps scene CRUD/state operations.
- `cli.py` – Typer CLI exposing device inspection, heuristic proposal, scene creation/update/deletion/state, and trigger commands.
- `ai_tools.py` – Confirmation-gated helpers for Gemini/Alexa agents (describe space, inspect device, propose/create/update/delete/trigger scenes).

## Cloud API Coverage
| Operation | TuyaClient method | Endpoint |
|-----------|------------------|----------|
| List devices in a space | `list_space_devices` | `GET /v2.0/cloud/thing/space/device` |
| Fetch device shadow | `get_device_shadow` | `GET /v2.0/cloud/thing/{device_id}/shadow/properties` |
| List scenes | `list_scenes` | `GET /v2.0/cloud/scene/rule` |
| Scene details | `get_scene` | `GET /v2.0/cloud/scene/rule/{rule_id}` |
| Create scene | `create_scene` | `POST /v2.0/cloud/scene/rule` |
| Modify scene | `update_scene` | `PUT /v2.0/cloud/scene/rule/{rule_id}` |
| Delete scenes | `delete_scenes` | `DELETE /v2.0/cloud/scene/rule?ids=...&space_id=...` |
| Enable/disable scenes | `set_scenes_state` / `set_scene_state` | `PUT /v2.0/cloud/scene/rule/state` |
| Trigger scene | `trigger_scene` | `POST /v2.0/cloud/scene/rule/{rule_id}/actions/trigger` |

## Automation Flow
1. **Discover** – `workflow.discover_devices` enumerates devices for the configured space.
2. **Inspect** – `workflow.inspect_properties` reads DP codes/values (useful for verifying thresholds and code mappings).
3. **Propose** – `heuristics.build_heuristic_proposals` maps inverter metrics to scene payloads.
4. **Create / Update** – `workflow.create_scenes` or `workflow.update_scene` submits payloads to Tuya Cloud.
5. **Activate** – `workflow.set_scenes_state` enables/disables rules; `workflow.trigger_scene` can run them immediately.
6. **Cleanup** – `workflow.delete_scenes` removes obsolete rules.

## CLI Usage
Run commands with `python -m integrations.tuya.cli ...` after populating `.env` (Tuya keys, device id, space id). Key commands:
- `devices --config configs/automation.yaml`
- `shadow 2655b6c92c0586a4a5cx6f`
- `propose --config configs/automation.yaml`
- `create --config configs/automation.yaml --enable --confirm`
- `update <rule_id> --payload payload.json --confirm`
- `delete <rule_id> [<rule_id> ...] --config configs/automation.yaml --confirm`
- `state <rule_id> [<rule_id> ...] --enable/--no-enable --confirm`
- `trigger <rule_id> --confirm`

Payloads for `create`/`update` follow Tuya's scene schema. Provide JSON or YAML files when using `--payload`.

## Tests
Run `./.venv/Scripts/python.exe -m unittest tests/test_tuya_client.py tests/test_heuristics.py`.
- `test_tuya_client.py` – Exercises request signing, token refresh on 401, and exponential backoff on HTTP 429.
- `test_heuristics.py` – Ensures Battery Protect / Solar Surplus / Night Guard heuristics produce valid Tuya payloads (including required `code` indices).

## Agent Hooks
`integrations.tuya.ai_tools` offers confirmation-gated wrappers:
- `describe_space` / `inspect_device` – read-only helpers.
- `propose_automation` – generate payloads without side effects.
- `create_and_enable_automation`, `update_automation`, `delete_automations`, `set_automation_state`, `trigger_scene` – mutating operations guarded by a `confirm` flag and token redaction in responses.

## FAQ
- **Which DP code should I use?** Always reference the `code` returned by `get_device_shadow` (e.g., `Bateria`, `Producao_Solar_Atual`).
- **Region mismatch?** Override `TUYA_API_BASE_URL` in `.env` (e.g., `https://openapi.tuyacn.com`).
- **Rate limits?** The client retries with exponential backoff on 429 responses. For bulk operations batch your calls when possible.
- **Why enable fails?** Use `set_scenes_state`/`state` CLI command; older `/status` path is deprecated and returns `1108 uri path invalid`.
