"""Command-line utilities for Tuya automation workflows."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import typer
from dotenv import load_dotenv
import yaml

from integrations.tuya import TuyaApiError, TuyaClient
from integrations.tuya.client import DEFAULT_TUYA_API_BASE_URL
from integrations.tuya.workflow import TuyaAutomationWorkflow, load_automation_config

app = typer.Typer(add_completion=False, help="Utilities to inspect Tuya spaces and manage scenes")


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise typer.BadParameter(f"Environment variable {name} is required")
    return value


def _build_client(base_url: Optional[str] = None) -> TuyaClient:
    client_id = _require_env("TUYA_CLIENT_ID")
    client_secret = _require_env("TUYA_CLIENT_SECRET")
    resolved_base_url = base_url or os.getenv("TUYA_API_BASE_URL", DEFAULT_TUYA_API_BASE_URL)
    return TuyaClient(client_id=client_id, client_secret=client_secret, base_url=resolved_base_url)


def _load_workflow(config_path: Optional[str]) -> tuple[TuyaAutomationWorkflow, dict]:
    client = _build_client()
    workflow = TuyaAutomationWorkflow(client)
    config = load_automation_config(config_path)
    return workflow, config


def _echo_json(data: object) -> None:
    typer.echo(json.dumps(data, indent=2, ensure_ascii=False))


def _load_payload_file(path: str) -> Dict[str, Any]:
    payload_path = Path(path)
    if not payload_path.exists():
        raise typer.BadParameter(f"Payload file not found: {payload_path}")
    try:
        content = payload_path.read_text(encoding="utf-8")
        if payload_path.suffix.lower() in {".yaml", ".yml"}:
            data = yaml.safe_load(content)
        else:
            data = json.loads(content)
    except (OSError, ValueError, yaml.YAMLError) as exc:  # type: ignore[attr-defined]
        raise typer.BadParameter(f"Failed to parse payload file {payload_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise typer.BadParameter("Scene payload must be a JSON/YAML object")
    return data


@app.command()
def devices(
    space_id: Optional[str] = typer.Option(None, help="Space ID to query (falls back to TUYA_SPACE_ID)"),
    config: Optional[str] = typer.Option(None, "--config", help="Path to automation config YAML"),
) -> None:
    """List devices attached to a space."""
    load_dotenv(".env")
    workflow, cfg = _load_workflow(config)
    space = space_id or cfg.get("space_id") or os.getenv("TUYA_SPACE_ID")
    if not space:
        raise typer.BadParameter("space_id is required via option, config, or TUYA_SPACE_ID")
    try:
        devices = workflow.discover_devices([space])
    except TuyaApiError as exc:
        typer.secho(f"Tuya API error: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc
    _echo_json([device.model_dump(exclude_none=True) for device in devices])


@app.command()
def shadow(
    device_id: str = typer.Argument(..., help="Device ID to inspect"),
    codes: List[str] = typer.Option(None, help="Optional DP codes to filter"),
) -> None:
    """Fetch shadow properties for a device."""
    load_dotenv(".env")
    workflow, _ = _load_workflow(None)
    try:
        shadow_props = workflow.inspect_properties([device_id], codes=codes or None)
    except TuyaApiError as exc:
        typer.secho(f"Tuya API error: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc
    device_props = shadow_props.get(device_id, {})
    _echo_json({code: prop.model_dump(exclude_none=True) for code, prop in device_props.items()})


@app.command()
def scenes(
    space_id: Optional[str] = typer.Option(None, help="Space ID to list scenes"),
    config: Optional[str] = typer.Option(None, "--config", help="Path to automation config YAML"),
) -> None:
    """List automation scenes defined for a space."""
    load_dotenv(".env")
    workflow, cfg = _load_workflow(config)
    space = space_id or cfg.get("space_id") or os.getenv("TUYA_SPACE_ID")
    if not space:
        raise typer.BadParameter("space_id is required via option, config, or TUYA_SPACE_ID")
    try:
        scenes_payload = workflow.list_scenes(space)
    except TuyaApiError as exc:
        typer.secho(f"Tuya API error: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc
    _echo_json(scenes_payload)


@app.command()
def scene(rule_id: str = typer.Argument(..., help="Scene rule id")) -> None:
    """Show details for a scene rule."""
    load_dotenv(".env")
    workflow, _ = _load_workflow(None)
    try:
        detail = workflow.get_scene(rule_id)
    except TuyaApiError as exc:
        typer.secho(f"Tuya API error: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc
    _echo_json(detail)


def _collect_device_ids(config: dict) -> List[str]:
    device_ids = set()
    for params in (config.get("heuristics") or {}).values():
        for key in ("inverter_device_id", "load_device_id", "sensor_device_id"):
            value = params.get(key)
            if value:
                device_ids.add(value)
    return sorted(device_ids)


def _prepare_proposals(
    *,
    space_id: str,
    workflow: TuyaAutomationWorkflow,
    config: dict,
    heuristics: Optional[Sequence[str]],
    inspect_codes: Optional[List[str]] = None,
):
    devices = workflow.discover_devices([space_id])
    device_map = workflow.build_device_map(devices)
    device_ids = _collect_device_ids(config)
    if not device_ids:
        device_ids = list(device_map.keys())
    properties = workflow.inspect_properties(device_ids, codes=inspect_codes)
    proposals = workflow.propose_scene_rules(
        space_id=space_id,
        devices=device_map,
        properties=properties,
        config=config,
        heuristics=heuristics,
    )
    return proposals, device_map, properties


@app.command()
def propose(
    config: Optional[str] = typer.Option(None, "--config", help="Path to automation config YAML"),
    heuristics: List[str] = typer.Option(None, help="Heuristic keys to evaluate"),
    codes: List[str] = typer.Option(None, help="Optional DP codes filter when fetching properties"),
    dry_run: bool = typer.Option(True, help="Only print payloads (default)"),
) -> None:
    """Evaluate heuristics and show resulting scene payloads."""
    load_dotenv(".env")
    workflow, cfg = _load_workflow(config)
    space_id = cfg.get("space_id") or os.getenv("TUYA_SPACE_ID")
    if not space_id:
        raise typer.BadParameter("space_id must be provided in config or TUYA_SPACE_ID")

    try:
        proposals, _, _ = _prepare_proposals(
            space_id=space_id,
            workflow=workflow,
            config=cfg,
            heuristics=heuristics or None,
            inspect_codes=codes or None,
        )
    except (TuyaApiError, ValueError) as exc:
        typer.secho(f"Failed to prepare proposals: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc

    payloads = workflow.build_scene_payloads(space_id=space_id, proposals=proposals)
    if dry_run:
        for payload in payloads:
            typer.echo(workflow.serialize_payload(payload))
        return
    typer.secho("Use the 'create' command to submit payloads.", fg=typer.colors.YELLOW)


@app.command()
def create(
    config: Optional[str] = typer.Option(None, "--config", help="Path to automation config YAML"),
    heuristics: List[str] = typer.Option(None, help="Heuristic keys to evaluate"),
    enable: bool = typer.Option(False, help="Enable created scenes after creation"),
    submit: bool = typer.Option(True, help="Actually create scenes (disable for dry-run)"),
    confirm: bool = typer.Option(False, help="Explicitly allow scene creation actions"),
) -> None:
    """Create automation scenes from heuristics."""
    load_dotenv(".env")
    workflow, cfg = _load_workflow(config)
    space_id = cfg.get("space_id") or os.getenv("TUYA_SPACE_ID")
    if not space_id:
        raise typer.BadParameter("space_id must be provided in config or TUYA_SPACE_ID")

    try:
        proposals, _, _ = _prepare_proposals(
            space_id=space_id,
            workflow=workflow,
            config=cfg,
            heuristics=heuristics or None,
        )
    except (TuyaApiError, ValueError) as exc:
        typer.secho(f"Failed to prepare proposals: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc
    payloads = workflow.build_scene_payloads(space_id=space_id, proposals=proposals)

    if not submit:
        for payload in payloads:
            typer.echo(workflow.serialize_payload(payload))
        typer.secho("Dry-run mode: set --submit to create scenes", fg=typer.colors.YELLOW)
        return

    if not confirm:
        typer.secho("Add --confirm to allow creating scenes", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)

    try:
        results = workflow.create_scenes(payloads)
    except TuyaApiError as exc:
        typer.secho(f"Failed to create scenes: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc

    typer.echo(json.dumps(results, indent=2, ensure_ascii=False))

    if enable:
        try:
            ids = [result.get("rule_id") or result.get("id") for result in results]
            ids = [rule_id for rule_id in ids if rule_id]
            if ids:
                workflow.set_scenes_state(ids, True)
                typer.secho(f"Enabled {len(ids)} scene(s)", fg=typer.colors.GREEN)
        except TuyaApiError as exc:
            typer.secho(f"Failed to enable scenes: {exc}", err=True, fg=typer.colors.RED)


@app.command()
def trigger(
    rule_id: str = typer.Argument(..., help="Scene rule id"),
    confirm: bool = typer.Option(False, help="Require explicit confirmation"),
) -> None:
    """Trigger a scene rule immediately."""
    if not confirm:
        typer.secho("Add --confirm to allow triggering scenes", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)

    load_dotenv(".env")
    workflow, _ = _load_workflow(None)
    try:
        result = workflow.trigger_scene(rule_id)
    except TuyaApiError as exc:
        typer.secho(f"Tuya API error: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc
    _echo_json(result)


@app.command()
def update(
    rule_id: str = typer.Argument(..., help="Scene rule id to update"),
    payload: str = typer.Option(..., "--payload", "-p", help="Path to JSON/YAML payload"),
    confirm: bool = typer.Option(False, help="Explicitly allow modifying the scene"),
) -> None:
    """Modify an existing scene rule using a provided payload."""
    if not confirm:
        typer.secho("Add --confirm to allow updating scenes", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)

    load_dotenv(".env")
    workflow, _ = _load_workflow(None)
    payload_data = _load_payload_file(payload)
    try:
        result = workflow.update_scene(rule_id, payload_data)
    except (TuyaApiError, ValueError) as exc:
        typer.secho(f"Failed to update scene: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc
    _echo_json(result or {"id": rule_id})


@app.command()
def delete(
    rule_ids: List[str] = typer.Argument(..., help="One or more scene rule ids to delete"),
    space_id: Optional[str] = typer.Option(None, help="Space ID (defaults to config/TUYA_SPACE_ID)"),
    config: Optional[str] = typer.Option(None, "--config", help="Path to automation config YAML"),
    confirm: bool = typer.Option(False, help="Explicitly allow deleting scenes"),
) -> None:
    """Delete one or more scenes from a space."""
    if not confirm:
        typer.secho("Add --confirm to allow deleting scenes", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)

    load_dotenv(".env")
    workflow, cfg = _load_workflow(config)
    space = space_id or cfg.get("space_id") or os.getenv("TUYA_SPACE_ID")
    if not space:
        raise typer.BadParameter("space_id must be provided via option, config, or TUYA_SPACE_ID")
    try:
        result = workflow.delete_scenes(rule_ids, space)
    except (TuyaApiError, ValueError) as exc:
        typer.secho(f"Failed to delete scenes: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc
    _echo_json(result or {"deleted": rule_ids})


@app.command("state")
def set_state(
    rule_ids: List[str] = typer.Argument(..., help="One or more scene rule ids"),
    enable: bool = typer.Option(True, help="Set to True to enable, False to disable"),
    confirm: bool = typer.Option(False, help="Explicitly allow state changes"),
) -> None:
    """Enable or disable one or more scenes."""
    if not confirm:
        typer.secho("Add --confirm to allow updating scene state", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)

    load_dotenv(".env")
    workflow, _ = _load_workflow(None)
    try:
        result = workflow.set_scenes_state(rule_ids, enable)
    except (TuyaApiError, ValueError) as exc:
        typer.secho(f"Failed to update scene state: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc
    _echo_json(result or {"ids": rule_ids, "is_enable": enable})


def main(argv: Optional[Sequence[str]] = None) -> None:
    """Entrypoint for invocation via python -m integrations.tuya.cli."""
    try:
        app(prog_name="tuya", args=list(argv) if argv is not None else None)
    except typer.Exit as exc:
        raise SystemExit(exc.exit_code)


if __name__ == "__main__":
    main(sys.argv[1:])
