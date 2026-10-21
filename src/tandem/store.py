"""Persistence on SQLAlchemy Core: SQLite by default, Postgres with `TANDEM_DATABASE_URL`.

Every write publishes the matching bus event (SPEC §10.3) so the UI updates live. Timestamps are
fixed-width UTC ISO strings, so they sort correctly as text on both databases.
"""

from __future__ import annotations

import threading
from typing import Any

from sqlalchemy import (
    JSON,
    Column,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    delete,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

from tandem.bus import EventBus
from tandem.util import Clock, iso, new_id

metadata = MetaData()

messages = Table(
    "messages",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("role", String(16), nullable=False),
    Column("text", Text, nullable=False, default=""),
    Column("card", JSON),
    Column("created_at", String(32), nullable=False),
)

events = Table(
    "events",
    metadata,
    Column("id", String(32), primary_key=True),
    Column("source", String(64), nullable=False),
    Column("kind", String(32), nullable=False),
    Column("external_id", String(255), nullable=False),
    Column("title", Text, nullable=False, default=""),
    Column("body", Text, default=""),
    Column("sender", String(255)),
    Column("occurred_at", String(32)),
    Column("data", JSON),
    Column("status", String(16), nullable=False, default="new"),
    Column("urgency", String(16), nullable=False, default="normal"),
    Column("triage", JSON),
    Column("created_at", String(32), nullable=False),
    UniqueConstraint("source", "external_id", name="uq_event_source_ext"),
)

tasks = Table(
    "tasks",
    metadata,
    Column("id", String(32), primary_key=True),
    Column("title", Text, nullable=False),
    Column("instructions", Text, nullable=False),
    Column("harness", String(32), nullable=False),
    Column("origin", String(16), nullable=False, default="user"),
    Column("state", String(16), nullable=False, default="queued"),
    Column("summary", Text),
    Column("error", Text),
    Column("steps", Integer, nullable=False, default=0),
    Column("sandbox", String(16)),
    Column("created_at", String(32), nullable=False),
    Column("started_at", String(32)),
    Column("finished_at", String(32)),
)

task_logs = Table(
    "task_logs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("task_id", String(32), nullable=False, index=True),
    Column("ts", String(32), nullable=False),
    Column("kind", String(16), nullable=False),
    Column("text", Text, nullable=False),
)

approvals = Table(
    "approvals",
    metadata,
    Column("id", String(32), primary_key=True),
    Column("tool", String(128), nullable=False),
    Column("title", Text, nullable=False),
    Column("summary", Text, default=""),
    Column("reason", Text, default=""),
    Column("args", JSON, nullable=False),
    Column("editable", JSON),
    Column("tags", JSON),
    Column("state", String(16), nullable=False, default="pending"),
    Column("task_id", String(32)),
    Column("created_at", String(32), nullable=False),
    Column("expires_at", String(32), nullable=False),
    Column("decided_at", String(32)),
    Column("decided_by", String(64)),
    Column("note", Text),
    Column("result", JSON),
)

artifacts = Table(
    "artifacts",
    metadata,
    Column("id", String(32), primary_key=True),
    Column("task_id", String(32)),
    Column("kind", String(16), nullable=False),
    Column("title", Text, nullable=False),
    Column("filename", String(255), nullable=False),
    Column("path", Text, nullable=False),
    Column("size", Integer, nullable=False, default=0),
    Column("created_at", String(32), nullable=False),
)

briefs = Table(
    "briefs",
    metadata,
    Column("id", String(32), primary_key=True),
    Column("date", String(10), nullable=False),
    Column("greeting", Text, nullable=False),
    Column("items", JSON, nullable=False),
    Column("created_at", String(32), nullable=False),
)

memory = Table(
    "memory",
    metadata,
    Column("id", String(32), primary_key=True),
    Column("kind", String(24), nullable=False),
    Column("key", String(255)),
    Column("content", Text, nullable=False),
    Column("source", String(32), default="agent"),
    Column("created_at", String(32), nullable=False),
    Column("updated_at", String(32), nullable=False),
)

activity = Table(
    "activity",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ts", String(32), nullable=False),
    Column("kind", String(32), nullable=False),
    Column("title", Text, nullable=False),
    Column("detail", Text),
    Column("task_id", String(32)),
    Column("level", String(8), nullable=False, default="info"),
)

audit = Table(
    "audit",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ts", String(32), nullable=False),
    Column("actor", String(32), nullable=False),
    Column("tool", String(128), nullable=False),
    Column("args", JSON),
    Column("decision", String(16), nullable=False),
    Column("approval_id", String(32)),
    Column("task_id", String(32)),
    Column("ok", Integer),
    Column("detail", Text),
)

kv = Table(
    "kv",
    metadata,
    Column("key", String(128), primary_key=True),
    Column("value", JSON),
)


def _row(r: Any) -> dict[str, Any]:
    return dict(r._mapping)


class Store:
    def __init__(self, url: str, bus: EventBus | None = None, clock: Clock | None = None) -> None:
        kwargs: dict[str, Any] = {"future": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
            if url in ("sqlite://", "sqlite:///:memory:"):
                kwargs["poolclass"] = StaticPool
        self.engine: Engine = create_engine(url, **kwargs)
        metadata.create_all(self.engine)
        self.bus = bus or EventBus()
        self.clock = clock or Clock()
        self._lock = threading.RLock()

    def now(self) -> str:
        return iso(self.clock.now())  # type: ignore[return-value]

    def _exec(self, stmt: Any) -> Any:
        with self._lock, self.engine.begin() as conn:
            return conn.execute(stmt)

    def _all(self, stmt: Any) -> list[dict[str, Any]]:
        with self._lock, self.engine.connect() as conn:
            return [_row(r) for r in conn.execute(stmt)]

    def _one(self, stmt: Any) -> dict[str, Any] | None:
        rows = self._all(stmt)
        return rows[0] if rows else None

    # ---- messages ---------------------------------------------------------------------------------
    def add_message(self, role: str, text: str, card: dict[str, Any] | None = None) -> dict[str, Any]:
        res = self._exec(insert(messages).values(role=role, text=text, card=card, created_at=self.now()))
        msg = self._one(select(messages).where(messages.c.id == res.inserted_primary_key[0]))
        assert msg
        self.bus.publish("message.created", msg)
        return msg

    def list_messages(self, limit: int = 50, before: int | None = None) -> list[dict[str, Any]]:
        q = select(messages).order_by(messages.c.id.desc()).limit(limit)
        if before:
            q = q.where(messages.c.id < before)
        return list(reversed(self._all(q)))

    # ---- inbound events -----------------------------------------------------------------------------
    def add_event(self, ev: dict[str, Any]) -> dict[str, Any] | None:
        """Insert unless (source, external_id) exists; returns the new row or None for a duplicate."""
        existing = self._one(
            select(events.c.id).where(
                events.c.source == ev["source"], events.c.external_id == ev["external_id"]
            )
        )
        if existing:
            return None
        row = {
            "id": new_id("ev"),
            "status": "new",
            "urgency": "normal",
            "created_at": self.now(),
            "body": "",
            "data": {},
            **ev,
        }
        self._exec(insert(events).values(**row))
        out = self.get_event(row["id"])
        self.bus.publish("event.created", out)
        return out

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        return self._one(select(events).where(events.c.id == event_id))

    def update_event(self, event_id: str, **fields: Any) -> dict[str, Any] | None:
        self._exec(update(events).where(events.c.id == event_id).values(**fields))
        return self.get_event(event_id)

    def list_events(
        self, status: str | list[str] | None = None, since: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        q = select(events).order_by(events.c.occurred_at.desc()).limit(limit)
        if isinstance(status, str):
            q = q.where(events.c.status == status)
        elif status:
            q = q.where(events.c.status.in_(status))
        if since:
            q = q.where(events.c.created_at >= since)
        return self._all(q)

    # ---- tasks ----------------------------------------------------------------------------------------
    def _task_out(self, t: dict[str, Any] | None) -> dict[str, Any] | None:
        if t is None:
            return None
        t["artifact_ids"] = [
            r["id"]
            for r in self._all(
                select(artifacts.c.id).where(artifacts.c.task_id == t["id"]).order_by(artifacts.c.created_at)
            )
        ]
        return t

    def create_task(
        self, title: str, instructions: str, harness: str, origin: str = "user"
    ) -> dict[str, Any]:
        tid = new_id("tk")
        self._exec(
            insert(tasks).values(
                id=tid,
                title=title,
                instructions=instructions,
                harness=harness,
                origin=origin,
                state="queued",
                steps=0,
                created_at=self.now(),
            )
        )
        task = self.get_task(tid)
        assert task
        self.bus.publish("task.created", task)
        return task

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        return self._task_out(self._one(select(tasks).where(tasks.c.id == task_id)))

    def update_task(self, task_id: str, **fields: Any) -> dict[str, Any] | None:
        self._exec(update(tasks).where(tasks.c.id == task_id).values(**fields))
        task = self.get_task(task_id)
        if task:
            self.bus.publish("task.updated", task)
        return task

    def list_tasks(self, state: str | list[str] | None = None, limit: int = 100) -> list[dict[str, Any]]:
        q = select(tasks).order_by(tasks.c.created_at.desc()).limit(limit)
        if isinstance(state, str):
            q = q.where(tasks.c.state == state)
        elif state:
            q = q.where(tasks.c.state.in_(state))
        return [self._task_out(t) for t in self._all(q)]  # type: ignore[misc]

    def add_task_log(self, task_id: str, kind: str, text: str) -> dict[str, Any]:
        res = self._exec(insert(task_logs).values(task_id=task_id, ts=self.now(), kind=kind, text=text))
        log = self._one(select(task_logs).where(task_logs.c.id == res.inserted_primary_key[0]))
        assert log
        self.bus.publish("task.log", log)
        return log

    def task_logs(self, task_id: str, limit: int = 500) -> list[dict[str, Any]]:
        return self._all(
            select(task_logs).where(task_logs.c.task_id == task_id).order_by(task_logs.c.id).limit(limit)
        )

    # ---- approvals -------------------------------------------------------------------------------------
    def create_approval(self, **fields: Any) -> dict[str, Any]:
        row = {"id": new_id("ap"), "state": "pending", "created_at": self.now(), **fields}
        self._exec(insert(approvals).values(**row))
        ap = self.get_approval(row["id"])
        assert ap
        self.bus.publish("approval.created", ap)
        return ap

    def get_approval(self, approval_id: str) -> dict[str, Any] | None:
        return self._one(select(approvals).where(approvals.c.id == approval_id))

    def update_approval(self, approval_id: str, **fields: Any) -> dict[str, Any] | None:
        self._exec(update(approvals).where(approvals.c.id == approval_id).values(**fields))
        ap = self.get_approval(approval_id)
        if ap:
            self.bus.publish("approval.updated", ap)
        return ap

    def list_approvals(
        self, state: str | None = None, task_id: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        q = select(approvals).order_by(approvals.c.created_at.desc()).limit(limit)
        if state:
            q = q.where(approvals.c.state == state)
        if task_id:
            q = q.where(approvals.c.task_id == task_id)
        return self._all(q)

    # ---- artifacts ---------------------------------------------------------------------------------------
    def add_artifact(self, **fields: Any) -> dict[str, Any]:
        row = {"id": fields.pop("id", None) or new_id("ar"), "created_at": self.now(), **fields}
        self._exec(insert(artifacts).values(**row))
        art = self.get_artifact(row["id"])
        assert art
        self.bus.publish("artifact.created", art)
        return art

    def get_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        return self._one(select(artifacts).where(artifacts.c.id == artifact_id))

    def list_artifacts(self, task_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        q = select(artifacts).order_by(artifacts.c.created_at.desc()).limit(limit)
        if task_id:
            q = q.where(artifacts.c.task_id == task_id)
        return self._all(q)

    # ---- briefs -----------------------------------------------------------------------------------------
    def add_brief(self, date: str, greeting: str, items: list[dict[str, Any]]) -> dict[str, Any]:
        row = {
            "id": new_id("br"),
            "date": date,
            "greeting": greeting,
            "items": items,
            "created_at": self.now(),
        }
        self._exec(insert(briefs).values(**row))
        self.bus.publish("brief.created", row)
        return row

    def get_brief(self, brief_id: str) -> dict[str, Any] | None:
        return self._one(select(briefs).where(briefs.c.id == brief_id))

    def latest_brief(self) -> dict[str, Any] | None:
        return self._one(select(briefs).order_by(briefs.c.created_at.desc()).limit(1))

    # ---- memory ------------------------------------------------------------------------------------------
    def write_memory(
        self, kind: str, content: str, key: str | None = None, source: str = "agent"
    ) -> dict[str, Any]:
        now = self.now()
        if key:
            existing = self._one(select(memory).where(memory.c.kind == kind, memory.c.key == key))
            if existing:
                self._exec(
                    update(memory)
                    .where(memory.c.id == existing["id"])
                    .values(content=content, updated_at=now, source=source)
                )
                return self._one(select(memory).where(memory.c.id == existing["id"]))  # type: ignore[return-value]
        row = {
            "id": new_id("mm"),
            "kind": kind,
            "key": key,
            "content": content,
            "source": source,
            "created_at": now,
            "updated_at": now,
        }
        self._exec(insert(memory).values(**row))
        return row

    def list_memory(self, kinds: list[str] | None = None) -> list[dict[str, Any]]:
        q = select(memory).order_by(memory.c.created_at)
        if kinds:
            q = q.where(memory.c.kind.in_(kinds))
        return self._all(q)

    def delete_memory(self, memory_id: str) -> bool:
        return bool(self._exec(delete(memory).where(memory.c.id == memory_id)).rowcount)

    # ---- activity and audit ---------------------------------------------------------------------------------
    def add_activity(
        self,
        kind: str,
        title: str,
        detail: str | None = None,
        task_id: str | None = None,
        level: str = "info",
    ) -> dict[str, Any]:
        res = self._exec(
            insert(activity).values(
                ts=self.now(), kind=kind, title=title, detail=detail, task_id=task_id, level=level
            )
        )
        row = self._one(select(activity).where(activity.c.id == res.inserted_primary_key[0]))
        assert row
        self.bus.publish("activity.created", row)
        return row

    def list_activity(
        self, limit: int = 100, before: int | None = None, level: str | None = None
    ) -> list[dict[str, Any]]:
        q = select(activity).order_by(activity.c.id.desc()).limit(limit)
        if before:
            q = q.where(activity.c.id < before)
        if level:
            q = q.where(activity.c.level == level)
        return self._all(q)

    def add_audit(self, **fields: Any) -> dict[str, Any]:
        row = {"ts": self.now(), **fields}
        self._exec(insert(audit).values(**row))
        return row

    def list_audit(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._all(select(audit).order_by(audit.c.id.desc()).limit(limit))

    # ---- kv --------------------------------------------------------------------------------------------------
    def kv_get(self, key: str, default: Any = None) -> Any:
        row = self._one(select(kv).where(kv.c.key == key))
        return row["value"] if row else default

    def kv_set(self, key: str, value: Any) -> None:
        with self._lock, self.engine.begin() as conn:
            if conn.execute(select(kv.c.key).where(kv.c.key == key)).first():
                conn.execute(update(kv).where(kv.c.key == key).values(value=value))
            else:
                conn.execute(insert(kv).values(key=key, value=value))

    def count(self, table: Table, *where: Any) -> int:
        with self._lock, self.engine.connect() as conn:
            return int(conn.execute(select(func.count()).select_from(table).where(*where)).scalar() or 0)
