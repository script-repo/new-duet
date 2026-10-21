"""Deterministic offline model (SPEC §6.4). It plays each role well enough to demo and test every flow.

The engine puts structured context in a `<data>{...}</data>` block of the last user message (real
models read it as plain JSON). The mock parses that block; for chat and worker roles it looks at the
conversation and the offered tools and plays a short, plausible plan.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from tandem.models.base import Message, ModelClient, ModelResponse, ToolCall, ToolSpec, Usage

DATA_RE = re.compile(r"<data>\s*(.*?)\s*</data>", re.S)

WORK_VERBS = re.compile(
    r"\b(build|make|create|draft|prepare|write|put together|email|send|schedule|set up|book|find|research|"
    r"analy[sz]e|summari[sz]e|compile|plan|pull|update|reply)\b",
    re.I,
)
STATUS_WORDS = re.compile(
    r"\b(what needs|needs me|status|what's going on|whats going on|anything|today|brief|update me)\b", re.I
)
URGENT = re.compile(
    r"\b(urgent|asap|immediately|declined|failed|failure|overdue|expires?|expiring|suspend\w*|"
    r"deadline|by noon|by eod|today|tonight|final notice|past due|outage|down)\b",
    re.I,
)
CRITICAL = re.compile(r"\b(suspend\w*|outage|breach|fraud|locked out)\b", re.I)
MEETING = re.compile(
    r"\b(find (?:some )?time|grab (?:a )?(?:call|coffee)|meet(?:ing)?|30 minutes|schedule a)\b", re.I
)


def _data(messages: list[Message]) -> dict[str, Any] | None:
    for m in reversed(messages):
        if m.role == "user":
            match = DATA_RE.search(m.content)
            if match:
                try:
                    val = json.loads(match.group(1))
                    return val if isinstance(val, dict) else None
                except json.JSONDecodeError:
                    return None
            return None
    return None


def _json(text: str) -> Any:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


def _title(text: str) -> str:
    """'Build me a one-page cash summary ... and email it to Sam.' -> 'One-page cash summary ...'"""
    t = re.sub(r"^(please |can you |could you |hey,? )*", "", text.strip(), flags=re.I)
    t = re.sub(
        r"^(build|make|create|draft|prepare|write|put together|pull together)( me| us)?( an?| the)?\s+",
        "",
        t,
        flags=re.I,
    )
    t = re.sub(r"\s+and (email|send|share)\b.*$", "", t, flags=re.I).rstrip(".?! ")
    t = _short(t, 60)
    return t[:1].upper() + t[1:]


def _short(text: str, n: int = 60) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


class MockModel(ModelClient):
    async def _complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        *,
        json_mode: bool,
        max_tokens: int,
        temperature: float | None,
    ) -> ModelResponse:
        data = _data(messages)
        kind = (data or {}).get("kind") or self.role
        if kind == "triage":
            out: Any = self.triage(data or {})
        elif kind == "brief":
            out = self.brief(data or {})
        elif kind == "summarize":
            out = {"summary": _short(" ".join(str(x) for x in (data or {}).get("messages", [])), 400)}
        elif kind == "reflect":
            out = self.reflect(data or {})
        elif self.role == "chat":
            return self.chat(messages, tools)
        else:
            return self.worker(messages, tools)
        text = json.dumps(out)
        return ModelResponse(
            text,
            [],
            Usage(sum(len(m.content) for m in messages) // 4, len(text) // 4),
            self.profile.model,
            "end_turn",
        )

    # ---- triage -------------------------------------------------------------------------------------------
    def triage(self, data: dict[str, Any]) -> dict[str, Any]:
        results = []
        for ev in data.get("events", []):
            text = f"{ev.get('title', '')} {ev.get('body', '')}"
            floor = ev.get("rule_floor") or "low"
            flags = set(ev.get("rule_flags") or [])
            if "mute" in flags or "bulk" in flags:
                results.append(
                    {"id": ev["id"], "urgency": "low", "action": "ignore", "reason": "Bulk or muted sender"}
                )
                continue
            if CRITICAL.search(text) and URGENT.search(text):
                urgency, reason = "critical", "Service at risk with a hard deadline"
            elif URGENT.search(text) or floor in ("high", "critical"):
                urgency, reason = (
                    "high",
                    "Time-sensitive: " + (", ".join(sorted(flags)) or "deadline or failure words"),
                )
            elif "?" in text or ev.get("kind") in ("calendar", "payment"):
                urgency, reason = "normal", "Needs a reply or touches your schedule or money, not urgent"
            else:
                urgency, reason = "low", "FYI"
            order = ["low", "normal", "high", "critical"]
            if order.index(floor) > order.index(urgency):
                urgency = floor
            action = "notify" if urgency in ("high", "critical") else "brief"
            item: dict[str, Any] = {"id": ev["id"], "urgency": urgency, "action": action, "reason": reason}
            if ev.get("kind") == "email" and MEETING.search(text) and urgency != "critical":
                sender = ev.get("sender") or "them"
                item["action"] = "act"
                item["reason"] = "Meeting request I can handle: I'll draft a reply with times"
                item["task"] = {
                    "title": f"Reply to {sender.split('@')[0].title()} with meeting times",
                    "instructions": f"Reply to {sender} proposing two open times from my calendar this week "
                    f"for: {ev.get('title')}. Check the calendar first, then email the reply.",
                }
            results.append(item)
        return {"results": results}

    # ---- brief ----------------------------------------------------------------------------------------------
    def brief(self, data: dict[str, Any]) -> dict[str, Any]:
        events = data.get("events", [])
        approvals = data.get("approvals", [])
        running = data.get("tasks_running", [])
        done = data.get("tasks_done", [])
        draft_tool = data.get("draft_tool")
        items: list[dict[str, Any]] = []
        order = {"critical": 0, "high": 1, "normal": 2, "low": 3}
        for ev in sorted(events, key=lambda e: order.get(e.get("urgency", "low"), 3))[:6]:
            needs = ev.get("urgency") in ("high", "critical")
            body = re.sub(r"^(hi|hello|hey)\b[^\n]*\n", "", (ev.get("body") or "").strip(), flags=re.I)
            item: dict[str, Any] = {
                "topic": _short(re.sub(r"^[^:]{0,24}:\s*", "", ev.get("title", "Update")), 42),
                "summary": _short(body or ev.get("title", ""), 170),
                "needs_you": needs,
                "event_id": ev.get("id"),
            }
            sender = ev.get("sender") or ""
            if (
                draft_tool
                and ev.get("kind") == "email"
                and "@" in sender
                and "?" in (ev.get("body") or "")
                and len([i for i in items if i.get("draft")]) < 2
            ):
                first = sender.split("@")[0].split(".")[0].title()
                item["draft"] = {
                    "tool": draft_tool,
                    "args": {
                        "to": sender,
                        "subject": f"Re: {ev.get('title', '')}",
                        "body": f"Hi {first},\n\nThanks for the note. Yes, that works for us. I'll confirm the details by end of day.\n\nBest,\n{data.get('user_name') or 'Me'}",
                    },
                }
                item["needs_you"] = True
                item["summary"] += " A reply is drafted."
            items.append(item)
        cal = data.get("calendar", [])
        if cal:
            first = ", ".join(f"{c.get('start_local', '')} {c.get('title', '')}".strip() for c in cal[:3])
            items.append(
                {"topic": "Today", "summary": f"{len(cal)} on your calendar: {first}.", "needs_you": False}
            )
        if done:
            items.append(
                {
                    "topic": "Done overnight",
                    "summary": "; ".join(_short(t.get("title", ""), 50) for t in done[:3]) + ".",
                    "needs_you": False,
                }
            )
        needs_n = len([i for i in items if i.get("needs_you")]) + len(approvals)
        name = data.get("user_name")
        greet = f"Good morning{', ' + name if name else ''}. "
        greet += f"{len(running) + len(events)} things in progress and {needs_n} that need you."
        return {"greeting": greet, "items": items}

    def reflect(self, data: dict[str, Any]) -> dict[str, Any]:
        tool = data.get("tool", "a draft")
        changed = ", ".join(sorted((data.get("edited") or {}).keys())) or "the text"
        to = (data.get("original") or {}).get("to")
        where = f" to {to}" if to else ""
        return {
            "preference": f"When drafting {tool}{where}, the user edited {changed}; follow their edited version's style next time."
        }

    # ---- chat -------------------------------------------------------------------------------------------------
    def chat(self, messages: list[Message], tools: list[ToolSpec]) -> ModelResponse:
        names = {t.name for t in tools}
        last = messages[-1]
        if last.role == "tool":
            result = _json(last.content)
            if last.name == "start_task":
                title = (result or {}).get("title", "that") if isinstance(result, dict) else "that"
                text = f"On it. I'm working on “{title}” in the background and will message you when it's done or needs your OK."
            elif last.name == "memory_write":
                text = "Got it. I'll remember that."
            elif last.name == "today_summary" and isinstance(result, dict):
                text = self._status_text(result)
            else:
                text = "Done."
            return ModelResponse(text, [], Usage(), self.profile.model, "end_turn")
        user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        low = user.lower().strip()
        if low.startswith("remember") and "memory_write" in names:
            content = re.sub(r"^remember( that)?[:,]?\s*", "", user, flags=re.I).strip()
            kind = (
                "preference"
                if re.search(r"\b(prefer|always|never|vip|don't|do not|call me)\b", low)
                else "fact"
            )
            return self._call("memory_write", {"content": content, "kind": kind})
        if STATUS_WORDS.search(low) and "today_summary" in names and not WORK_VERBS.search(low):
            return self._call("today_summary", {})
        if WORK_VERBS.search(low) and "start_task" in names:
            title = _short(
                re.sub(r"^(please|can you|could you|hey)[, ]+", "", user, flags=re.I).rstrip(".?!"), 60
            )
            return self._call("start_task", {"title": title[:1].upper() + title[1:], "instructions": user})
        if re.search(r"\b(hi|hello|hey|morning)\b", low):
            return ModelResponse(
                "Hi! I'm keeping an eye on things. Ask me for anything, or check Today for what's waiting.",
                [],
                Usage(),
                self.profile.model,
                "end_turn",
            )
        return ModelResponse(
            "Noted. Tell me if you want me to act on it: I can draft, schedule, build a doc, sheet or deck, "
            "or dig into your inbox.",
            [],
            Usage(),
            self.profile.model,
            "end_turn",
        )

    def _status_text(self, s: dict[str, Any]) -> str:
        parts = []
        if s.get("needs_you"):
            parts.append(
                f"{len(s['needs_you'])} need you: "
                + "; ".join(a.get("title", "") for a in s["needs_you"][:3])
            )
        if s.get("in_progress"):
            parts.append(
                f"{len(s['in_progress'])} in progress: "
                + "; ".join(t.get("title", "") for t in s["in_progress"][:3])
            )
        if s.get("done"):
            parts.append(f"{len(s['done'])} done today")
        return (". ".join(parts) + ".") if parts else "All quiet. Nothing needs you right now."

    def _call(self, name: str, args: dict[str, Any], step: int = 0) -> ModelResponse:
        return ModelResponse(
            "",
            [ToolCall(id=f"call_{name}_{step}", name=name, args=args)],
            Usage(),
            self.profile.model,
            "tool_use",
        )

    # ---- worker --------------------------------------------------------------------------------------------
    def worker(self, messages: list[Message], tools: list[ToolSpec]) -> ModelResponse:
        names = {t.name for t in tools}
        user_msgs = [m.content for m in messages if m.role == "user"]
        instructions = user_msgs[0] if user_msgs else ""
        steers = [u.split("]", 1)[-1].strip() for u in user_msgs[1:] if u.startswith("[Steer")]
        text = " ".join([instructions, *steers])
        low = text.lower()
        results: dict[str, Any] = {}
        for m in messages:
            if m.role == "tool" and m.name:
                results[m.name] = _json(m.content) if _json(m.content) is not None else m.content
        done_steps = sum(1 for m in messages if m.role == "assistant" and m.tool_calls)

        plan = self._plan(low, text, names, results)
        if done_steps < len(plan):
            name, args = plan[done_steps]
            return self._call(name, args, done_steps)

        made = [r for k, r in results.items() if k == "artifact_create"]
        rejected = any(
            isinstance(v, str) and ("rejected" in v.lower() or "blocked" in v.lower())
            for v in results.values()
        )
        summary = "Finished: " + _short(instructions, 90)
        if made:
            summary += ". The deliverable is attached"
        if any(k.endswith("_send") for k in results):
            summary += ". The email was " + (
                "not sent (you declined)" if rejected else "sent after your approval"
            )
        if any(k == "calendar_create" for k in results):
            summary += ". The invite is " + ("not sent (you declined)" if rejected else "on your calendar")
        if steers:
            summary += ". Applied your note: " + "; ".join(_short(s, 60) for s in steers)
        return ModelResponse(summary + ".", [], Usage(), self.profile.model, "end_turn")

    def _plan(
        self, low: str, text: str, names: set[str], results: dict[str, Any]
    ) -> list[tuple[str, dict[str, Any]]]:
        plan: list[tuple[str, dict[str, Any]]] = []
        money = re.search(
            r"\b(cash|payment|payments|revenue|invoice|invoices|sales|money|finance|financial|stripe|refund)",
            low,
        )
        meetings = re.search(r"\b(calendar|meeting|meet|schedule|availability|times to|book)\b", low)
        if money and "payments_list" in names:
            plan.append(("payments_list", {}))
        if re.search(r"\b(inbox|emails|thread|reply to|replies)\b", low) and "email_search" in names:
            plan.append(("email_search", {"query": "" if "inbox" in low else _short(text, 30)}))
        if meetings and "calendar_list" in names:
            plan.append(("calendar_list", {"days": 7}))
        if "sandbox_write" in names:
            plan.append(
                (
                    "sandbox_write",
                    {
                        "path": "notes/plan.md",
                        "content": f"# Plan\n\nTask: {_short(text, 200)}\n\n1. Gather data\n2. Build the deliverable\n3. Share it\n",
                    },
                )
            )
        payments = results.get("payments_list")
        rows = (
            payments.get("payments", [])
            if isinstance(payments, dict)
            else (payments if isinstance(payments, list) else [])
        )
        if rows and "sandbox_write" in names:
            csv = "date,customer,amount,status\n" + "\n".join(
                f"{p.get('date', '')},{p.get('customer', '')},{p.get('amount', 0)},{p.get('status', '')}"
                for p in rows
            )
            plan.append(("sandbox_write", {"path": "data/payments.csv", "content": csv}))

        kind = self._artifact_kind(low)
        wants_artifact = kind != "none"
        if wants_artifact and "artifact_create" in names:
            plan.append(("artifact_create", self._artifact_args(kind, text, rows)))

        recipient = self._recipient(text)
        send_tool = next((n for n in ("email_send",) if n in names), None) or next(
            (n for n in sorted(names) if n.endswith("_send")), None
        )
        if (
            re.search(r"\b(schedule|set up|book)\b.*\b(meeting|call|time)\b", low)
            and "calendar_create" in names
        ):
            plan.append(
                (
                    "calendar_create",
                    {
                        "title": _short(text, 50),
                        "start": self._slot(results),
                        "duration_min": 30,
                        "attendees": [recipient] if recipient else [],
                    },
                )
            )
        elif recipient and send_tool and re.search(r"\b(email|send|reply|share)\b", low):
            to = recipient
            if "@" not in recipient and "crm_lookup" in names:
                plan.append(("crm_lookup", {"name": recipient}))
                look = results.get("crm_lookup")
                if look is None:
                    return plan
                if isinstance(look, dict) and look.get("email"):
                    to = look["email"]
            body = self._email_body(text, results, recipient)
            plan.append((send_tool, {"to": to, "subject": self._subject(text), "body": body}))
        return plan

    def _artifact_kind(self, low: str) -> str:
        if re.search(r"\b(deck|slides|presentation|pitch)\b", low):
            return "pptx"
        if re.search(r"\b(sheet|spreadsheet|model|dashboard|budget|forecast|tracker|table|summary)\b", low):
            return "xlsx"
        if re.search(r"\b(doc|document|memo|report|proposal|brief|letter|plan|checklist)\b", low):
            return "docx"
        if re.search(r"\b(page|html|one-pager|web)\b", low):
            return "html"
        if re.search(r"\b(note|notes|outline|draft|write)\b", low):
            return "md"
        return "none"

    def _artifact_args(self, kind: str, text: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        title = _title(text)
        total = sum(float(p.get("amount", 0) or 0) for p in rows)
        failed = [p for p in rows if p.get("status") in ("failed", "overdue")]
        if kind == "xlsx":
            data_rows: list[list[Any]] = [["Date", "Customer", "Amount", "Status"]]
            data_rows += [
                [
                    p.get("date", ""),
                    p.get("customer", ""),
                    float(p.get("amount", 0) or 0),
                    p.get("status", ""),
                ]
                for p in rows
            ]
            if rows:
                data_rows.append(["", "Total", f"=SUM(C2:C{len(rows) + 1})", ""])
            else:
                data_rows += [["Item", "Owner", "Due", "Status"], ["Kickoff", "You", "Mon", "open"]]
            return {
                "kind": "xlsx",
                "title": title,
                "content": json.dumps(
                    {
                        "sheets": [
                            {
                                "name": "Summary",
                                "rows": [
                                    ["Metric", "Value"],
                                    ["Payments", len(rows)],
                                    ["Total", total],
                                    ["Needs attention", len(failed)],
                                ],
                            },
                            {"name": "Detail", "rows": data_rows},
                        ]
                    }
                ),
            }
        if kind == "pptx":
            return {
                "kind": "pptx",
                "title": title,
                "content": json.dumps(
                    {
                        "slides": [
                            {
                                "title": title,
                                "bullets": ["Prepared by Tandem", datetime.now().strftime("%B %Y")],
                            },
                            {
                                "title": "Where we are",
                                "bullets": ["Momentum is good", "Two risks to watch", "One decision needed"],
                            },
                            {
                                "title": "Numbers",
                                "bullets": [
                                    f"{len(rows)} payments, ${total:,.0f} total",
                                    f"{len(failed)} need attention",
                                ]
                                if rows
                                else ["Revenue up week over week", "Costs flat"],
                            },
                            {"title": "Next steps", "bullets": ["Confirm the plan", "Share with the team"]},
                        ]
                    }
                ),
            }
        body = f"# {title}\n\n## Summary\n\n"
        body += (
            f"- {len(rows)} payments totaling **${total:,.2f}**\n- {len(failed)} need attention\n"
            if rows
            else "- Context gathered from your connected apps\n- Recommendations below\n"
        )
        if failed:
            body += (
                "\n## Needs attention\n\n"
                + "\n".join(
                    f"- {p.get('customer')}: ${float(p.get('amount', 0)):,.2f} ({p.get('status')})"
                    for p in failed
                )
                + "\n"
            )
        body += "\n## Next steps\n\n1. Review this summary\n2. Approve any drafts waiting for you\n"
        return {"kind": kind, "title": title, "content": body}

    def _recipient(self, text: str) -> str | None:
        email = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text)
        if email:
            return email.group(0)
        m = re.search(
            r"\b(?:email|send|share)\b(?:\s+(?:it|this|that|them|the \w+))?\s+(?:over\s+)?to\s+([A-Z][a-z]+)",
            text,
        )
        return m.group(1) if m else None

    def _subject(self, text: str) -> str:
        if re.search(r"meeting times|propos", text, re.I):
            return "Times to meet"
        return _title(text)

    def _slot(self, results: dict[str, Any]) -> str:
        cal = results.get("calendar_list")
        free = cal.get("free_slots") if isinstance(cal, dict) else None
        return free[0] if free else "tomorrow 10:00"

    def _email_body(self, text: str, results: dict[str, Any], recipient: str) -> str:
        first = recipient.split("@")[0].split(".")[0].title()
        cal = results.get("calendar_list")
        free = (cal.get("free_slots") if isinstance(cal, dict) else None) or []
        if free and re.search(r"times|meet|schedule", text, re.I):
            opts = "\n".join(f"- {s}" for s in free[:2])
            return f"Hi {first},\n\nHappy to find time. Would one of these work?\n\n{opts}\n\nBest,"
        art = results.get("artifact_create")
        att = (
            f" I've attached {art.get('filename')}." if isinstance(art, dict) and art.get("filename") else ""
        )
        return f"Hi {first},\n\nHere is the summary you asked for.{att} Let me know if you have questions.\n\nBest,"
