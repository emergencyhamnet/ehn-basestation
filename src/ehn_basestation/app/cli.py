from __future__ import annotations

import argparse
from collections.abc import Sequence
import json
from pathlib import Path
from datetime import datetime, timezone

from ehn_basestation.app.authority_server import run_authority_server
from ehn_basestation.app.bootstrap import create_app
from ehn_basestation.app.dev_profile import initialize_windows_dev_layout
from ehn_basestation.app.station_link import (
    PeerStationLinkServer,
    initialize_station_db,
    list_station_records,
    sync_station_with_peer,
)
from ehn_basestation.app.settings import BasestationSettings, load_env_file, resolve_project_root
from ehn_basestation.directory.delivery_store import DeliveryStore
from ehn_basestation.directory.directory_store import DirectoryStore
from ehn_basestation.app.workbench_server import run_workbench_server


OBSERVED_RETENTION_CLASS = "FIELD_OBSERVED"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="EHN basestation development utilities")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="Optional .env-style file to load before resolving settings.",
    )
    subparsers = parser.add_subparsers(dest="command")

    init_layout = subparsers.add_parser(
        "init-dev-layout",
        help="Create the Windows basestation development folders and local env files.",
    )
    init_layout.add_argument(
        "--root",
        type=Path,
        default=resolve_project_root(),
        help="Repository root to scaffold. Defaults to the current ehn-basestation repo root.",
    )
    init_layout.add_argument(
        "--meshcore-base-url",
        default=None,
        help="MeshCore gateway base URL for the primary Windows dev profile.",
    )
    init_layout.add_argument(
        "--force",
        action="store_true",
        help="Rewrite generated env files if they already exist.",
    )

    show_settings = subparsers.add_parser(
        "show-settings",
        help="Print the resolved basestation settings for the current environment.",
    )
    show_settings.add_argument(
        "--root",
        type=Path,
        default=resolve_project_root(),
        help="Repository root to resolve defaults from.",
    )

    init_station_db = subparsers.add_parser(
        "init-station-db",
        help="Create the active station SQLite file and write the local station record.",
    )
    init_station_db.add_argument(
        "--root",
        type=Path,
        default=resolve_project_root(),
        help="Repository root to resolve defaults from.",
    )

    show_station_records = subparsers.add_parser(
        "show-station-records",
        help="Print the current station records stored in the active SQLite file.",
    )
    show_station_records.add_argument(
        "--root",
        type=Path,
        default=resolve_project_root(),
        help="Repository root to resolve defaults from.",
    )

    init_directory = subparsers.add_parser(
        "init-directory-schema",
        help="Create the shared directory tables in the active station SQLite file.",
    )
    init_directory.add_argument(
        "--root",
        type=Path,
        default=resolve_project_root(),
        help="Repository root to resolve defaults from.",
    )

    create_local_user = subparsers.add_parser(
        "create-local-user",
        help="Create a field-local directory user for emergency/offline use.",
    )
    create_local_user.add_argument("--root", type=Path, default=resolve_project_root())
    create_local_user.add_argument("--display-name", required=True)
    create_local_user.add_argument("--home-region", default="")
    create_local_user.add_argument("--retention-class", default="FIELD_OPERATIONAL")
    create_local_user.add_argument("--promotion-status", default="LOCAL_ONLY")
    create_local_user.add_argument("--identity-display-name", default=None)
    create_local_user.add_argument("--identity-type", default="USER")
    create_local_user.add_argument("--messaging-mode", default="BOTH")
    create_local_user.add_argument("--endpoint-network", default=None)
    create_local_user.add_argument("--endpoint-address", default=None)

    show_local_users = subparsers.add_parser(
        "show-local-users",
        help="Print local station-owned users from the shared directory store.",
    )
    show_local_users.add_argument("--root", type=Path, default=resolve_project_root())
    show_local_users.add_argument("--promotion-status", default=None)

    export_review_candidates = subparsers.add_parser(
        "export-review-candidates",
        help="Write locally created review-candidate users to a JSON bundle for manual authority review.",
    )
    export_review_candidates.add_argument("--root", type=Path, default=resolve_project_root())
    export_review_candidates.add_argument("--promotion-status", default="PENDING_AUTHORITY_REVIEW")
    export_review_candidates.add_argument("--output", type=Path, default=None)
    export_review_candidates.add_argument(
        "--include-observed",
        action="store_true",
        help="Include observed inbound sender records that are excluded from review exports by default.",
    )

    init_authority_directory = subparsers.add_parser(
        "init-authority-directory-schema",
        help="Create the authority directory schema in the separate main database.",
    )
    init_authority_directory.add_argument("--root", type=Path, default=resolve_project_root())

    show_authority_users = subparsers.add_parser(
        "show-authority-users",
        help="Print authority users from the separate main directory database.",
    )
    show_authority_users.add_argument("--root", type=Path, default=resolve_project_root())

    reset_directory_users = subparsers.add_parser(
        "reset-directory-users",
        help="Clear directory user records from either the local or authority database.",
    )
    reset_directory_users.add_argument("--root", type=Path, default=resolve_project_root())
    reset_directory_users.add_argument("--target", choices=("local", "authority"), default="local")
    reset_directory_users.add_argument("--include-audit", action="store_true")

    seed_directory_users = subparsers.add_parser(
        "seed-directory-users",
        help="Load a defined user list into either the local or authority directory database.",
    )
    seed_directory_users.add_argument("--root", type=Path, default=resolve_project_root())
    seed_directory_users.add_argument("--target", choices=("local", "authority"), default="authority")
    seed_directory_users.add_argument("--input", type=Path, required=True)

    seed_sinks = subparsers.add_parser(
        "seed-sinks",
        help="Load sink and sink-endpoint definitions into the delivery database for #sink-id routing.",
    )
    seed_sinks.add_argument("--root", type=Path, default=resolve_project_root())
    seed_sinks.add_argument("--input", type=Path, required=True)

    show_sinks = subparsers.add_parser(
        "show-sinks",
        help="Print configured sinks and their delivery endpoints.",
    )
    show_sinks.add_argument("--root", type=Path, default=resolve_project_root())

    export_directory_seed = subparsers.add_parser(
        "export-directory-seed",
        help="Write users from the local or authority directory database in reusable seed format.",
    )
    export_directory_seed.add_argument("--root", type=Path, default=resolve_project_root())
    export_directory_seed.add_argument("--source", choices=("local", "authority"), default="local")
    export_directory_seed.add_argument("--output", type=Path, required=True)
    export_directory_seed.add_argument("--local-only", action="store_true")
    export_directory_seed.add_argument("--promotion-status", default=None)
    export_directory_seed.add_argument(
        "--include-observed",
        action="store_true",
        help="Include observed inbound sender records that are excluded from seed exports by default.",
    )

    run_peer_server = subparsers.add_parser(
        "run-peer-link-server",
        help="Run the local framed stream link listener for a peer station.",
    )
    run_peer_server.add_argument("--root", type=Path, default=resolve_project_root())
    run_peer_server.add_argument("--host", default=None)
    run_peer_server.add_argument("--port", type=int, default=None)
    run_peer_server.add_argument("--once", action="store_true", help="Handle one request and exit.")

    sync_peer = subparsers.add_parser(
        "sync-peer-station",
        help="Push the local station record to a peer station and record the peer station locally.",
    )
    sync_peer.add_argument("--root", type=Path, default=resolve_project_root())
    sync_peer.add_argument("--host", default=None)
    sync_peer.add_argument("--port", type=int, default=None)

    workbench = subparsers.add_parser(
        "run-workbench-preview",
        help="Serve the Windows-side MeshCore workbench preview backed by the basestation gateway link.",
    )
    workbench.add_argument("--root", type=Path, default=resolve_project_root())
    workbench.add_argument("--host", default="127.0.0.1")
    workbench.add_argument("--port", type=int, default=18098)

    authority = subparsers.add_parser(
        "run-authority-console",
        help="Serve the restricted authority directory console against the main database.",
    )
    authority.add_argument("--root", type=Path, default=resolve_project_root())
    authority.add_argument("--host", default="127.0.0.1")
    authority.add_argument("--port", type=int, default=8105)
    return parser


def _print_settings(settings: BasestationSettings) -> None:
    print("EHN basestation settings")
    for key, value in settings.as_dict().items():
        print(f"{key}: {value}")


def _directory_store(settings: BasestationSettings) -> DirectoryStore:
    store = DirectoryStore(
        settings.active_directory_db_path,
        origin_station_id=settings.station_name,
    )
    store.initialize()
    return store


def _authority_directory_store(settings: BasestationSettings) -> DirectoryStore:
    store = DirectoryStore(
        settings.authority_directory_db_path,
        origin_station_id="authority-main",
    )
    store.initialize()
    return store


def _resolve_target_store(settings: BasestationSettings, target: str) -> DirectoryStore:
    return _authority_directory_store(settings) if str(target or "").strip().lower() == "authority" else _directory_store(settings)


def _load_user_seed_rows(path: Path) -> list[dict[str, object]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("users", "review_candidates"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
    raise ValueError(f"Unsupported seed file format: {path}")


def _seed_sinks_from_file(settings: BasestationSettings, path: Path) -> tuple[int, int]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("sinks") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError(f"Unsupported sink seed file format: {path}")
    store = DeliveryStore(settings.delivery_db_path)
    store.initialize()
    sink_count = 0
    endpoint_count = 0
    for row in rows:
        if not isinstance(row, dict) or not str(row.get("sink_id") or "").strip():
            continue
        store.upsert_sink(
            sink_id=str(row["sink_id"]),
            display_name=str(row.get("display_name") or ""),
            owner_ref=str(row.get("owner_ref") or ""),
            enabled=row.get("enabled", True) not in {False, "false", 0, "0"},
            accepted_content_classes=tuple(row.get("accepted_content_classes") or ("telemetry", "message")),
            accepted_actor_classes=tuple(row.get("accepted_actor_classes") or ("device", "person")),
            max_body_chars=int(row.get("max_body_chars") or 1000),
            notes=str(row.get("notes") or ""),
        )
        sink_count += 1
        for endpoint in row.get("endpoints") or []:
            if not isinstance(endpoint, dict) or not str(endpoint.get("adapter_type") or "").strip():
                continue
            store.upsert_sink_endpoint(
                sink_id=str(row["sink_id"]),
                adapter_type=str(endpoint["adapter_type"]),
                config=endpoint.get("config") if isinstance(endpoint.get("config"), dict) else {},
                sink_endpoint_id=str(endpoint.get("sink_endpoint_id") or "") or None,
                enabled=endpoint.get("enabled", True) not in {False, "false", 0, "0"},
                priority=int(endpoint.get("priority") or 100),
            )
            endpoint_count += 1
    return sink_count, endpoint_count


def _utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _build_review_bundle(settings: BasestationSettings, rows: Sequence[object]) -> dict[str, object]:
    return {
        "bundle_type": "ehn-review-candidates",
        "schema_version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "origin_station": settings.station_name,
        "origin_role": settings.station_role,
        "review_candidates": [row.as_dict() for row in rows],
    }


def _build_seed_bundle(settings: BasestationSettings, rows: Sequence[object], *, source: str) -> dict[str, object]:
    return {
        "bundle_type": "ehn-directory-seed",
        "schema_version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "origin_station": settings.station_name,
        "origin_role": settings.station_role,
        "source": source,
        "users": [row.as_dict() for row in rows],
    }


def _default_excluded_retention_classes(*, include_observed: bool) -> tuple[str, ...]:
    return () if include_observed else (OBSERVED_RETENTION_CLASS,)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.env_file is not None:
        load_env_file(args.env_file)

    if args.command == "init-dev-layout":
        settings = BasestationSettings.from_env(project_root=args.root)
        if args.meshcore_base_url:
            settings = settings.with_meshcore_base_url(args.meshcore_base_url)
        layout = initialize_windows_dev_layout(settings=settings, force=args.force)
        print("Initialized Windows development layout")
        for directory in layout.created_directories:
            print(f"dir: {directory}")
        for file_path in layout.written_files:
            print(f"file: {file_path}")
        return 0

    if args.command == "show-settings":
        settings = BasestationSettings.from_env(project_root=args.root)
        _print_settings(settings)
        return 0

    if args.command == "init-station-db":
        settings = BasestationSettings.from_env(project_root=args.root)
        record = initialize_station_db(settings)
        print("Initialized station db")
        for key, value in record.as_dict().items():
            print(f"{key}: {value}")
        return 0

    if args.command == "show-station-records":
        settings = BasestationSettings.from_env(project_root=args.root)
        rows = list_station_records(settings)
        print(f"station records: {len(rows)}")
        for row in rows:
            print(json.dumps(row, sort_keys=True))
        return 0

    if args.command == "init-directory-schema":
        settings = BasestationSettings.from_env(project_root=args.root)
        initialize_station_db(settings)
        _directory_store(settings)
        print("Initialized directory schema")
        print(f"active_directory_db_path: {settings.active_directory_db_path}")
        return 0

    if args.command == "init-authority-directory-schema":
        settings = BasestationSettings.from_env(project_root=args.root)
        _authority_directory_store(settings)
        print("Initialized authority directory schema")
        print(f"authority_directory_db_path: {settings.authority_directory_db_path}")
        return 0

    if args.command == "create-local-user":
        settings = BasestationSettings.from_env(project_root=args.root)
        initialize_station_db(settings)
        entry = _directory_store(settings).create_local_user(
            display_name=args.display_name,
            home_region=args.home_region,
            retention_class=args.retention_class,
            promotion_status=args.promotion_status,
            identity_display_name=args.identity_display_name,
            identity_type=args.identity_type,
            messaging_mode=args.messaging_mode,
            endpoint_network=args.endpoint_network,
            endpoint_address=args.endpoint_address,
            audit_actor="cli:create-local-user",
        )
        print("Created local user")
        print(json.dumps(entry.as_dict(), sort_keys=True))
        return 0

    if args.command == "show-local-users":
        settings = BasestationSettings.from_env(project_root=args.root)
        initialize_station_db(settings)
        rows = _directory_store(settings).list_local_users(
            promotion_status=args.promotion_status,
        )
        print(f"local users: {len(rows)}")
        for row in rows:
            print(json.dumps(row.as_dict(), sort_keys=True))
        return 0

    if args.command == "export-review-candidates":
        settings = BasestationSettings.from_env(project_root=args.root)
        initialize_station_db(settings)
        rows = _directory_store(settings).list_local_users(
            promotion_status=args.promotion_status,
            exclude_retention_classes=_default_excluded_retention_classes(include_observed=bool(args.include_observed)),
        )
        bundle = _build_review_bundle(settings, rows)
        output_path = args.output
        if output_path is None:
            filename = f"review-candidates-{settings.station_name}-{_utc_now_compact()}.json"
            output_path = settings.replication_root / filename
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(bundle, indent=2, sort_keys=True), encoding="utf-8")
        print("Exported review candidates")
        print(f"output: {output_path}")
        print(f"count: {len(rows)}")
        return 0

    if args.command == "show-authority-users":
        settings = BasestationSettings.from_env(project_root=args.root)
        rows = _authority_directory_store(settings).list_users()
        print(f"authority users: {len(rows)}")
        for row in rows:
            print(json.dumps(row.as_dict(), sort_keys=True))
        return 0

    if args.command == "reset-directory-users":
        settings = BasestationSettings.from_env(project_root=args.root)
        counts = _resolve_target_store(settings, args.target).reset_directory_users(
            audit_actor=f"cli:reset-{args.target}",
            include_audit=bool(args.include_audit),
        )
        print("Reset directory users")
        print(f"target: {args.target}")
        print(json.dumps(counts, sort_keys=True))
        return 0

    if args.command == "seed-directory-users":
        settings = BasestationSettings.from_env(project_root=args.root)
        rows = _load_user_seed_rows(args.input)
        saved = _resolve_target_store(settings, args.target).seed_directory_users(rows, audit_actor=f"cli:seed-{args.target}")
        print("Seeded directory users")
        print(f"target: {args.target}")
        print(f"count: {len(saved)}")
        return 0

    if args.command == "seed-sinks":
        settings = BasestationSettings.from_env(project_root=args.root)
        sink_count, endpoint_count = _seed_sinks_from_file(settings, args.input)
        print("Seeded sinks")
        print(f"delivery_db: {settings.delivery_db_path}")
        print(f"sinks: {sink_count}")
        print(f"endpoints: {endpoint_count}")
        return 0

    if args.command == "show-sinks":
        settings = BasestationSettings.from_env(project_root=args.root)
        store = DeliveryStore(settings.delivery_db_path)
        store.initialize()
        for sink in store.list_sinks():
            print(json.dumps(sink.as_dict(), sort_keys=True))
            for endpoint in store.list_sink_endpoints(sink.sink_id):
                print("  " + json.dumps(endpoint.as_dict(include_config=True), sort_keys=True))
        return 0

    if args.command == "export-directory-seed":
        settings = BasestationSettings.from_env(project_root=args.root)
        store = _resolve_target_store(settings, args.source)
        rows = store.list_users(
            local_only=True if args.local_only else None,
            promotion_status=args.promotion_status,
            exclude_retention_classes=_default_excluded_retention_classes(include_observed=bool(args.include_observed)),
        )
        bundle = _build_seed_bundle(settings, rows, source=args.source)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(bundle, indent=2, sort_keys=True), encoding="utf-8")
        print("Exported directory seed")
        print(f"source: {args.source}")
        print(f"output: {args.output}")
        print(f"count: {len(rows)}")
        return 0

    if args.command == "run-peer-link-server":
        settings = BasestationSettings.from_env(project_root=args.root)
        host = str(args.host or settings.station_link_bind_host).strip() or settings.station_link_bind_host
        port = int(args.port or settings.station_link_port)
        server = PeerStationLinkServer(settings, host=host, port=port)
        print(f"Peer station link server listening on {host}:{port}")
        if args.once:
            server.serve_once()
        else:
            server.serve_forever()
        return 0

    if args.command == "sync-peer-station":
        settings = BasestationSettings.from_env(project_root=args.root)
        host = str(args.host or settings.peer_station_host).strip() or settings.peer_station_host
        port = int(args.port or settings.peer_station_port)
        result = sync_station_with_peer(settings, host=host, port=port)
        print("Peer sync complete")
        print(json.dumps(result.local_station, sort_keys=True))
        print(json.dumps(result.remote_station, sort_keys=True))
        print(f"remote_endpoint: {result.remote_endpoint}")
        return 0

    if args.command == "run-workbench-preview":
        settings = BasestationSettings.from_env(project_root=args.root)
        run_workbench_server(settings=settings, host=args.host, port=args.port)
        return 0

    if args.command == "run-authority-console":
        settings = BasestationSettings.from_env(project_root=args.root)
        run_authority_server(settings=settings, host=args.host, port=args.port)
        return 0

    app = create_app()
    print("EHN basestation bootstrap ready")
    print(f"registered gateways: {len(app.gateway_registry.list_gateways())}")
    print(f"active directory db: {app.settings.active_directory_db_path}")
    print(f"authority directory db: {app.settings.authority_directory_db_path}")
    print(f"peer directory db: {app.settings.peer_directory_db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
