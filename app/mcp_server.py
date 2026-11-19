"""The agent's tools as an MCP server (MCP = a standard way for AI programs to use tools).

SAFETY: one server process is tied to ONE business and ONE customer phone, decided by whoever starts it
(environment variables), never by the AI program that calls the tools. The tools have no business or customer
parameters, so a caller cannot ask for anyone else's data.

Run (talks over standard input/output):
    MCP_BUSINESS_ID=<id> MCP_CUSTOMER_PHONE=+15125550147 python -m app.mcp_server
"""
from __future__ import annotations

import os
from typing import Any

from mcp.server.mcpserver import MCPServer

from . import db as dbmod
from .agent.tools import ToolBox, ToolContext
from .models import Business, Conversation
from .services.adapters import get_adapters
from .services.conversations import get_or_create_customer, new_conversation
from sqlalchemy import select


def make_server(business_id: str, customer_phone: str | None) -> MCPServer:
    server = MCPServer("front-desk", instructions="Tools for one small business's front desk. Use only facts the tools return.")

    def run(tool: str, args: dict[str, Any]) -> dict:
        now = dbmod.utcnow()
        with dbmod.tenant_session(business_id) as db:
            b = db.get(Business, business_id)
            if b is None:
                return {"ok": False, "error": "unknown_business"}
            cust = get_or_create_customer(db, b, customer_phone, now)
            ext = f"mcp:{cust.id}"
            conv = db.scalar(select(Conversation).where(Conversation.business_id == b.id, Conversation.external_id == ext))
            if conv is None:
                conv = new_conversation(db, b, cust, "web", now, external_id=ext)
            ctx = ToolContext(db=db, business=b, customer=cust, conversation=conv, now=now, adapters=get_adapters())
            return ToolBox(ctx).run(tool, {k: v for k, v in args.items() if v is not None})

    @server.tool()
    def get_business_info() -> dict:
        """Opening hours, address, services with prices, and answers. The only facts you may state."""
        return run("get_business_info", {})

    @server.tool()
    def check_free_times(date: str, around_time: str | None = None, service: str | None = None) -> dict:
        """Find free appointment times for a local date (YYYY-MM-DD). around_time is HH:MM (24 hour)."""
        return run("check_free_times", {"date": date, "around_time": around_time, "service": service})

    @server.tool()
    def book_appointment(start: str, customer_name: str, service: str | None = None, contact_phone: str | None = None,
                         phone_confirmed: bool | None = None, notes: str | None = None) -> dict:
        """Book an appointment for the current customer. start is local time YYYY-MM-DDTHH:MM."""
        return run("book_appointment", {"start": start, "customer_name": customer_name, "service": service,
                                        "contact_phone": contact_phone, "phone_confirmed": phone_confirmed, "notes": notes})

    @server.tool()
    def list_my_appointments() -> dict:
        """The current customer's own upcoming appointments."""
        return run("list_my_appointments", {})

    @server.tool()
    def move_appointment(appointment_id: str, new_start: str) -> dict:
        """Move one of the current customer's own appointments."""
        return run("move_appointment", {"appointment_id": appointment_id, "new_start": new_start})

    @server.tool()
    def cancel_appointment(appointment_id: str) -> dict:
        """Cancel one of the current customer's own appointments. The owner is told."""
        return run("cancel_appointment", {"appointment_id": appointment_id})

    @server.tool()
    def save_lead(need: str, name: str | None = None, preferred_time: str | None = None, contact_phone: str | None = None,
                  phone_confirmed: bool | None = None, reason: str | None = None) -> dict:
        """Save the customer's details for a person to follow up."""
        return run("save_lead", {"need": need, "name": name, "preferred_time": preferred_time,
                                 "contact_phone": contact_phone, "phone_confirmed": phone_confirmed, "reason": reason})

    @server.tool()
    def handoff_to_human(reason: str, summary: str, urgent: bool | None = None) -> dict:
        """Hand the conversation to a person at the business (set urgent for emergencies)."""
        return run("handoff_to_human", {"reason": reason, "summary": summary, "urgent": urgent})

    @server.tool()
    def notify_owner(message: str, urgent: bool | None = None) -> dict:
        """Send a short note to the business owner."""
        return run("notify_owner", {"message": message, "urgent": urgent})

    return server


def main() -> None:
    business_id = os.environ.get("MCP_BUSINESS_ID", "")
    if not business_id:
        raise SystemExit("Set MCP_BUSINESS_ID (the business these tools belong to).")
    make_server(business_id, os.environ.get("MCP_CUSTOMER_PHONE") or None).run(transport="stdio")


if __name__ == "__main__":
    main()
