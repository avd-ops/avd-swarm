# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""State store for deduplication, escalation timers, rate-limit buckets, and SMS spend caps."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Protocol

from .models import IncidentState


class StateStore(Protocol):
    def get_dedup_timestamp(self, key: str) -> datetime | None: ...
    def set_dedup_timestamp(self, key: str, ts: datetime) -> None: ...
    def get_incident(self, incident_key: str) -> IncidentState | None: ...
    def put_incident(self, incident: IncidentState) -> None: ...
    def list_open_incidents(self) -> list[IncidentState]: ...
    def cancel_incidents_for_plan(self, plan_id: str, now: datetime) -> list[str]: ...
    def increment_rate_bucket(self, bucket: str, hour_key: str, limit: int) -> bool: ...
    def get_monthly_sms_spend(self, month_key: str) -> float: ...
    def add_monthly_sms_spend(self, month_key: str, amount_usd: float) -> float: ...
    def set_last_heartbeat(self, ts: datetime) -> None: ...
    def get_last_heartbeat(self) -> datetime | None: ...


class InMemoryStateStore:
    """In-memory StateStore for offline tests and local simulation."""

    def __init__(self) -> None:
        self._dedup: dict[str, datetime] = {}
        self._incidents: dict[str, IncidentState] = {}
        self._buckets: dict[tuple[str, str], int] = {}
        self._sms_spend: dict[str, float] = {}
        self._last_heartbeat: datetime | None = None

    def get_dedup_timestamp(self, key: str) -> datetime | None:
        return self._dedup.get(key)

    def set_dedup_timestamp(self, key: str, ts: datetime) -> None:
        self._dedup[key] = ts

    def get_incident(self, incident_key: str) -> IncidentState | None:
        return self._incidents.get(incident_key)

    def put_incident(self, incident: IncidentState) -> None:
        self._incidents[incident.incident_key] = incident

    def list_open_incidents(self) -> list[IncidentState]:
        return [i for i in self._incidents.values() if i.status == "open"]

    def cancel_incidents_for_plan(self, plan_id: str, now: datetime) -> list[str]:
        cancelled: list[str] = []
        if not plan_id:
            return cancelled
        for inc in self._incidents.values():
            if inc.plan_id == plan_id and inc.status == "open":
                inc.status = "cancelled"
                inc.next_escalation_at = None
                inc.updated_at = now.astimezone(timezone.utc).isoformat()
                cancelled.append(inc.incident_key)
        return cancelled

    def increment_rate_bucket(self, bucket: str, hour_key: str, limit: int) -> bool:
        k = (bucket, hour_key)
        current = self._buckets.get(k, 0)
        if current >= limit:
            return False
        self._buckets[k] = current + 1
        return True

    def get_monthly_sms_spend(self, month_key: str) -> float:
        return self._sms_spend.get(month_key, 0.0)

    def add_monthly_sms_spend(self, month_key: str, amount_usd: float) -> float:
        total = self._sms_spend.get(month_key, 0.0) + amount_usd
        self._sms_spend[month_key] = round(total, 6)
        return self._sms_spend[month_key]

    def set_last_heartbeat(self, ts: datetime) -> None:
        self._last_heartbeat = ts

    def get_last_heartbeat(self) -> datetime | None:
        return self._last_heartbeat


class TableStorageStateStore:
    """Azure Table Storage StateStore for Consumption Functions / Logic Apps hosting.

    Uses DefaultAzureCredential when `AZURE_TABLES_ENDPOINT` is configured; falls back to
    an in-memory table dictionary when initialized with `table_client=None` in unit tests.
    """

    def __init__(self, table_client: object | None = None) -> None:
        if table_client is not None:
            self._client = table_client
        else:
            endpoint = os.environ.get("AZURE_TABLES_ENDPOINT", "").strip()
            table_name = os.environ.get("NOTIFICATIONS_TABLE_NAME", "NotificationRouterState").strip()
            if not endpoint:
                raise RuntimeError("AZURE_TABLES_ENDPOINT is required for TableStorageStateStore")
            from azure.data.tables import TableServiceClient  # type: ignore[import-not-found]
            from azure.identity import DefaultAzureCredential  # type: ignore[import-not-found]

            service = TableServiceClient(endpoint=endpoint, credential=DefaultAzureCredential())
            service.create_table_if_not_exists(table_name=table_name)
            self._client = service.get_table_client(table_name=table_name)

    def _upsert(self, partition: str, row: str, data: dict) -> None:
        entity = {"PartitionKey": partition, "RowKey": row, **data}
        self._client.upsert_entity(entity=entity)

    def _get(self, partition: str, row: str) -> dict | None:
        try:
            return dict(self._client.get_entity(partition_key=partition, row_key=row))
        except Exception:
            return None

    def get_dedup_timestamp(self, key: str) -> datetime | None:
        ent = self._get("dedup", key.replace("/", "_"))
        if not ent or not ent.get("ts"):
            return None
        return datetime.fromisoformat(str(ent["ts"]))

    def set_dedup_timestamp(self, key: str, ts: datetime) -> None:
        self._upsert("dedup", key.replace("/", "_"), {"ts": ts.astimezone(timezone.utc).isoformat()})

    def get_incident(self, incident_key: str) -> IncidentState | None:
        ent = self._get("incident", incident_key.replace("/", "_"))
        if not ent:
            return None
        return IncidentState(
            incident_key=str(ent["incident_key"]),
            event_type=str(ent["event_type"]),
            plan_id=str(ent["plan_id"]),
            urgency=str(ent["urgency"]),
            status=str(ent["status"]),
            current_step=int(ent["current_step"]),
            next_escalation_at=ent.get("next_escalation_at") or None,
            contact_tier=str(ent["contact_tier"]),
            payload=json.loads(str(ent.get("payload_json", "{}"))),
            updated_at=str(ent["updated_at"]),
        )

    def put_incident(self, incident: IncidentState) -> None:
        self._upsert(
            "incident",
            incident.incident_key.replace("/", "_"),
            {
                "incident_key": incident.incident_key,
                "event_type": incident.event_type,
                "plan_id": incident.plan_id,
                "urgency": incident.urgency,
                "status": incident.status,
                "current_step": incident.current_step,
                "next_escalation_at": incident.next_escalation_at or "",
                "contact_tier": incident.contact_tier,
                "payload_json": json.dumps(incident.payload),
                "updated_at": incident.updated_at,
            },
        )

    def list_open_incidents(self) -> list[IncidentState]:
        rows = self._client.query_entities("PartitionKey eq 'incident' and status eq 'open'")
        out: list[IncidentState] = []
        for ent in rows:
            out.append(
                IncidentState(
                    incident_key=str(ent["incident_key"]),
                    event_type=str(ent["event_type"]),
                    plan_id=str(ent["plan_id"]),
                    urgency=str(ent["urgency"]),
                    status=str(ent["status"]),
                    current_step=int(ent["current_step"]),
                    next_escalation_at=ent.get("next_escalation_at") or None,
                    contact_tier=str(ent["contact_tier"]),
                    payload=json.loads(str(ent.get("payload_json", "{}"))),
                    updated_at=str(ent["updated_at"]),
                )
            )
        return out

    def cancel_incidents_for_plan(self, plan_id: str, now: datetime) -> list[str]:
        cancelled: list[str] = []
        for inc in self.list_open_incidents():
            if inc.plan_id == plan_id:
                inc.status = "cancelled"
                inc.next_escalation_at = None
                inc.updated_at = now.astimezone(timezone.utc).isoformat()
                self.put_incident(inc)
                cancelled.append(inc.incident_key)
        return cancelled

    def increment_rate_bucket(self, bucket: str, hour_key: str, limit: int) -> bool:
        row = f"{bucket}:{hour_key}"
        ent = self._get("ratelimit", row)
        count = int(ent.get("count", 0)) if ent else 0
        if count >= limit:
            return False
        self._upsert("ratelimit", row, {"count": count + 1})
        return True

    def get_monthly_sms_spend(self, month_key: str) -> float:
        ent = self._get("smsspend", month_key)
        return float(ent.get("total", 0.0)) if ent else 0.0

    def add_monthly_sms_spend(self, month_key: str, amount_usd: float) -> float:
        total = round(self.get_monthly_sms_spend(month_key) + amount_usd, 6)
        self._upsert("smsspend", month_key, {"total": total})
        return total

    def set_last_heartbeat(self, ts: datetime) -> None:
        self._upsert("meta", "heartbeat", {"ts": ts.astimezone(timezone.utc).isoformat()})

    def get_last_heartbeat(self) -> datetime | None:
        ent = self._get("meta", "heartbeat")
        if not ent or not ent.get("ts"):
            return None
        return datetime.fromisoformat(str(ent["ts"]))
