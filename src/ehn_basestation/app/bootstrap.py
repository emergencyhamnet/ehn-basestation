from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from _thread import RLock

from ehn_basestation.core.delivery_queue import DeliveryQueueService
from ehn_basestation.app.settings import BasestationSettings
from ehn_basestation.core.events import MessageEventStore
from ehn_basestation.core.maintenance import DeliveryMaintenanceService
from ehn_basestation.core.orchestrator import DeliveryOrchestrator
from ehn_basestation.core.presence import PresenceService
from ehn_basestation.core.reply_templates import ReplyTemplateResolver
from ehn_basestation.core.router import InboundMessageRouter
from ehn_basestation.core.sink_delivery import SinkDeliveryService
from ehn_basestation.directory.delivery_store import DeliveryStore
from ehn_basestation.directory.directory_store import DirectoryStore
from ehn_basestation.directory.station_store import StationStore
from ehn_basestation.gateways.meshcore import (
    MeshCoreGatewayAdapter,
    MeshCoreRuntimeClient,
)
from ehn_basestation.gateways.meshtastic import (
    MeshtasticGatewayAdapter,
    MeshtasticGatewayBroker,
)
from ehn_basestation.gateways.registry import GatewayRegistry
from ehn_basestation.gateways.sms import AndroidSmsGatewayAdapter
from ehn_basestation.sinks import default_sink_adapters


@dataclass(slots=True)
class BasestationApp:
    settings: BasestationSettings
    gateway_registry: GatewayRegistry
    message_events: MessageEventStore
    directory_store: DirectoryStore
    authority_directory_store: DirectoryStore
    station_registry_store: StationStore
    delivery_store: DeliveryStore
    delivery_queue: DeliveryQueueService
    reply_templates: ReplyTemplateResolver
    delivery_orchestrator: DeliveryOrchestrator
    inbound_router: InboundMessageRouter
    sink_delivery: SinkDeliveryService
    presence_service: PresenceService
    maintenance: DeliveryMaintenanceService
    meshtastic_broker: MeshtasticGatewayBroker | None
    group_forward_echoes: deque[tuple[str, int, str, float]] = field(default_factory=lambda: deque(maxlen=200))
    group_echo_lock: RLock = field(default_factory=RLock, repr=False)


def create_app(*, settings: BasestationSettings | None = None) -> BasestationApp:
    resolved_settings = settings or BasestationSettings.from_env()
    gateway_registry = GatewayRegistry()
    message_events = MessageEventStore()
    directory_store = DirectoryStore(
        resolved_settings.active_directory_db_path,
        origin_station_id=resolved_settings.station_name,
    )
    directory_store.initialize()
    authority_directory_store = DirectoryStore(
        resolved_settings.authority_directory_db_path,
        origin_station_id="authority-main",
    )
    authority_directory_store.initialize()
    station_registry_store = StationStore(resolved_settings.registry_db_path)
    station_registry_store.initialize()
    delivery_store = DeliveryStore(resolved_settings.delivery_db_path)
    delivery_store.initialize()
    delivery_queue = DeliveryQueueService(delivery_store=delivery_store)
    reply_templates = ReplyTemplateResolver(delivery_store=delivery_store)
    reply_templates.ensure_defaults()
    delivery_orchestrator = DeliveryOrchestrator(
        gateway_registry=gateway_registry,
        message_events=message_events,
        delivery_store=delivery_store,
        station_id=resolved_settings.station_id,
    )
    presence_service = PresenceService(
        delivery_store=delivery_store,
        gateway_registry=gateway_registry,
        local_directory=directory_store,
        authority_directory=authority_directory_store,
        message_events=message_events,
        station_id=resolved_settings.station_id,
    )
    inbound_router = InboundMessageRouter(
        gateway_registry=gateway_registry,
        message_events=message_events,
        local_directory=directory_store,
        authority_directory=authority_directory_store,
        delivery_store=delivery_store,
        station_id=resolved_settings.station_id,
        presence_service=presence_service,
    )
    sink_delivery = SinkDeliveryService(
        delivery_store=delivery_store,
        delivery_queue=delivery_queue,
        message_events=message_events,
        station_id=resolved_settings.station_id,
        # Relative local-file sink paths resolve under the delivery DB's directory.
        adapters=default_sink_adapters(base_dir=resolved_settings.delivery_db_path.parent),
    )
    maintenance = DeliveryMaintenanceService(
        delivery_store=delivery_store,
        delivery_queue=delivery_queue,
        presence_service=presence_service,
        gateway_registry=gateway_registry,
        local_directory=directory_store,
        reply_templates=reply_templates,
        message_events=message_events,
        station_id=resolved_settings.station_id,
        extra_dispatchers={"sink": lambda limit: sink_delivery.dispatch(limit=limit)},
    )

    meshtastic_broker: MeshtasticGatewayBroker | None = None

    if resolved_settings.meshcore_enabled:
        gateway_registry.register(
            MeshCoreGatewayAdapter(
                runtime_client=MeshCoreRuntimeClient.from_settings(resolved_settings),
                delivery_queue=delivery_queue,
                station_id=resolved_settings.station_id,
            )
        )

    if resolved_settings.meshtastic_enabled:
        meshtastic_broker = MeshtasticGatewayBroker(
            gateway_id=resolved_settings.meshtastic_gateway_id,
            display_name=resolved_settings.meshtastic_display_name,
            serial_port=resolved_settings.meshtastic_serial_port,
            group_channel_index=resolved_settings.meshtastic_group_channel_index,
        )
        gateway_registry.register(
            MeshtasticGatewayAdapter(
                broker=meshtastic_broker,
                delivery_queue=delivery_queue,
                station_id=resolved_settings.station_id,
            )
        )

    if resolved_settings.sms_enabled:
        gateway_registry.register(
            AndroidSmsGatewayAdapter(
                gateway_id=resolved_settings.sms_gateway_id,
                display_name=resolved_settings.sms_display_name,
                delivery_queue=delivery_queue,
                station_id=resolved_settings.station_id,
            )
        )

    return BasestationApp(
        settings=resolved_settings,
        gateway_registry=gateway_registry,
        message_events=message_events,
        directory_store=directory_store,
        authority_directory_store=authority_directory_store,
        station_registry_store=station_registry_store,
        delivery_store=delivery_store,
        delivery_queue=delivery_queue,
        reply_templates=reply_templates,
        delivery_orchestrator=delivery_orchestrator,
        inbound_router=inbound_router,
        sink_delivery=sink_delivery,
        presence_service=presence_service,
        maintenance=maintenance,
        meshtastic_broker=meshtastic_broker,
    )
