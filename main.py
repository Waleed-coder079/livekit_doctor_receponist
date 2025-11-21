# main.py (Supabase-backed)
from dotenv import load_dotenv
from livekit import agents
from livekit.agents import Agent, AgentSession, RunContext
from livekit.agents.llm import function_tool
from livekit.plugins import silero, google, elevenlabs, deepgram
from datetime import datetime, date, timedelta, time
from zoneinfo import ZoneInfo
import os
import requests
from supabase import create_client

# Load environment variables
load_dotenv(".env")

# Supabase client
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_KEY in environment")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# MCP server URL for Google Calendar
GOOGLE_MCP_URL = os.getenv("GOOGLE_MCP_URL", "http://localhost:5000/create-event")
TIMEZONE = ZoneInfo("Asia/Karachi")


def next_weekday_date(target_weekday: int, from_date: date = None) -> date:
    if from_date is None:
        from_date = date.today()
    days_ahead = (target_weekday - from_date.weekday() + 7) % 7
    if days_ahead == 0:
        return from_date
    return from_date + timedelta(days=days_ahead)


def parse_day_to_date(day_str: str) -> date | None:
    day_str = day_str.strip().lower()

    # Try ISO date
    try:
        return datetime.strptime(day_str, "%Y-%m-%d").date()
    except:
        pass

    # Try "15 November 2025"
    for fmt in ("%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(day_str, fmt).date()
        except:
            pass

    # Try weekday names
    weekdays = {
        "monday": 0, "tuesday": 1, "wednesday": 2,
        "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6
    }

    if day_str in weekdays:
        return next_weekday_date(weekdays[day_str])

    return None


def parse_slot_times(slot_str: str):
    parts = slot_str.split("-")
    if len(parts) != 2:
        return None
    try:
        start = datetime.strptime(parts[0].strip(), "%I:%M %p").time()
        end = datetime.strptime(parts[1].strip(), "%I:%M %p").time()
        return start, end
    except:
        return None


class DoctorReceptionist(Agent):
    def __init__(self):
        super().__init__(
            instructions="""You are a friendly and professional voice receptionist..."""
        )

        self.time_slots = [
            "10:00 AM - 11:00 AM",
            "11:00 AM - 12:00 PM",
            "12:00 PM - 01:00 PM",
            "01:00 PM - 02:00 PM",
            "04:00 PM - 05:00 PM",
            "05:00 PM - 06:00 PM",
            "06:00 PM - 07:00 PM",
            "07:00 PM - 08:00 PM",
        ]

        self.schedule = {
            "sialkot": ["monday", "tuesday", "wednesday"],
            "lahore": ["thursday", "friday", "saturday"],
        }

        self.doctor = {"name": "Dr. Sarah Khan", "specialty": "Cardiologist", "fee": 2500}

        self.appointments = []
        self._load_appointments_from_db()

    # ---------------------- Supabase helpers ----------------------

    def _load_appointments_from_db(self):
        try:
            res = supabase.table("appointments").select("*").execute()
            self.appointments = res.data or []
        except:
            self.appointments = []

    def _insert_appointment_db(self, appt):
        try:
            res = supabase.table("appointments").insert(appt).execute()
            return res.data[0] if res.data else None
        except:
            return None

    def _find_slot_on_date(self, date_str, slot):
        try:
            res = (
                supabase.table("appointments")
                .select("*")
                .eq("date", date_str)
                .eq("slot", slot)
                .execute()
            )
            return res.data[0] if res.data else None
        except:
            return None

    # ---------------------- NEW: List free slots ----------------------

    @function_tool
    async def list_available_slots(self, context: RunContext, city: str, day: str) -> str:
        """
        Returns only FREE slots for the given city and day.
        """
        city = city.lower().strip()
        day = day.strip()

        booking_date = parse_day_to_date(day)
        if booking_date is None:
            return "Invalid date. Provide a date like '2025-11-21' or a weekday name."

        weekday = booking_date.strftime("%A").lower()

        if weekday == "sunday":
            return "Doctor is on leave on Sunday."

        if weekday not in self.schedule.get(city, []):
            alt = "lahore" if city == "sialkot" else "sialkot"
            return f"Doctor is not available in {city.title()} on {weekday.title()}. Doctor is available in {alt.title()}."

        # Get booked slots for this date
        res = supabase.table("appointments").select("slot").eq("date", booking_date.isoformat()).execute()
        booked = {r["slot"] for r in res.data} if res.data else set()

        free_slots = [s for s in self.time_slots if s not in booked]

        if not free_slots:
            return f"All slots are booked on {booking_date}. Please choose another day."

        return "Available slots:\n" + "\n".join(free_slots)

    # ---------------------- Book appointment ----------------------

    @function_tool
    async def book_appointment(self, context: RunContext, patient_name: str, city: str, day: str, slot: str, notes: str = "") -> str:
        city = city.lower().strip()
        day = day.strip()
        slot = slot.strip()

        booking_date = parse_day_to_date(day)
        if booking_date is None:
            return "Invalid date. Provide a date like '2025-11-21' or a weekday name."

        weekday = booking_date.strftime("%A").lower()

        if weekday == "sunday":
            return "Doctor is on leave on Sunday."

        if weekday not in self.schedule.get(city, []):
            alt = "lahore" if city == "sialkot" else "sialkot"
            return f"Doctor is not available in {city.title()} on {weekday.title()}. Doctor is available in {alt.title()}."

        if slot not in self.time_slots:
            return "Invalid slot."

        # ------------------- FIX 1: Check double booking -------------------
        existing = self._find_slot_on_date(booking_date.isoformat(), slot)
        if existing:
            return "❌ This slot is already booked. Please choose another one."

        times = parse_slot_times(slot)
        if not times:
            return "Invalid slot time format."

        start_time_obj, end_time_obj = times
        start_dt = datetime.combine(booking_date, start_time_obj).replace(tzinfo=TIMEZONE)
        end_dt = datetime.combine(booking_date, end_time_obj).replace(tzinfo=TIMEZONE)

        appt = {
            "id": f"APT{len(self.appointments) + 1001}",
            "patient_name": patient_name,
            "patient_id": f"PID{len(self.appointments) + 5001}",
            "city": city.title(),
            "date": booking_date.isoformat(),
            "slot": slot,
            "notes": notes,
            "calendar_event_id": None,
            "calendar_link": None,
        }

        inserted = self._insert_appointment_db(appt)
        if not inserted:
            return "Failed to save appointment. Try again."

        self._load_appointments_from_db()

        # Send to Google MCP
        try:
            payload = {
                "patient_name": patient_name,
                "city": appt["city"],
                "start_time": start_dt.isoformat(),
                "end_time": end_dt.isoformat(),
            }

            r = requests.post(GOOGLE_MCP_URL, json=payload)
            if r.ok:
                data = r.json()
                if "htmlLink" in data:
                    supabase.table("appointments").update({"calendar_link": data["htmlLink"]}).eq("id", appt["id"]).execute()
                if "eventId" in data:
                    supabase.table("appointments").update({"calendar_event_id": data["eventId"]}).eq("id", appt["id"]).execute()
        except:
            pass

        self._load_appointments_from_db()

        return (
            f"✅ Appointment confirmed!\n"
            f"ID: {appt['id']}\n"
            f"Patient: {patient_name}\n"
            f"City: {appt['city']}\n"
            f"Date: {appt['date']}\n"
            f"Time: {appt['slot']}\n"
            f"Fee: Rs.{self.doctor['fee']}"
        )

    # ---------------------- Show Appointments ----------------------

    @function_tool
    async def show_appointments(self, context: RunContext) -> str:
        res = supabase.table("appointments").select("*").order("created_at", desc=False).execute()
        appts = res.data or []

        if not appts:
            return "No appointments found."

        lines = ["Appointments:\n"]
        for a in appts:
            line = f"{a['id']} — {a['patient_name']} on {a['date']} at {a['slot']} ({a['city']})"
            lines.append(line)

        return "\n".join(lines)

    # ---------------------- Cancel Appointment ----------------------

    @function_tool
    async def cancel_appointment(self, context: RunContext, appointment_id: str):
        res = supabase.table("appointments").select("*").eq("id", appointment_id).execute()
        if not res.data:
            return "No appointment found."

        appt = res.data[0]

        # Cancel calendar event
        try:
            if appt.get("calendar_event_id"):
                requests.post(
                    GOOGLE_MCP_URL.replace("create-event", "delete-event"),
                    json={"eventId": appt["calendar_event_id"]},
                )
        except:
            pass

        supabase.table("appointments").delete().eq("id", appointment_id).execute()

        return f"Appointment {appointment_id} cancelled successfully."


# ---------------------- ENTRY ----------------------

async def entrypoint(ctx: agents.JobContext):
    session = AgentSession(
        stt=deepgram.STT(model="nova-2", language="en"),
        llm=google.LLM(model=os.getenv("LLM_CHOICE", "gemini-2.5-flash"), api_key=os.getenv("GEMINI_API_KEY")),
        tts=elevenlabs.TTS(
            voice_id=os.getenv("ELEVENLABS_VOICE_ID"),
            model=os.getenv("ELEVENLABS_TTS_MODEL"),
            api_key=os.getenv("ELEVENLABS_API_KEY"),
        ),
        vad=silero.VAD.load(),
    )

    await session.start(room=ctx.room, agent=DoctorReceptionist())

    await session.generate_reply(
        instructions="Greet the patient politely and ask which city they want to visit — Sialkot or Lahore."
    )


if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))
