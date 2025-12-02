# main.py (Supabase-backed + Langfuse @observe Traced)

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


# ✅ Langfuse OFFICIAL SDK (NO OpenTelemetry)
from langfuse import Langfuse, observe
load_dotenv(".env")

# ---------------------------------------------------------------------
# ✅ Langfuse OFFICIAL Initialization
# ---------------------------------------------------------------------
langfuse = Langfuse(
    public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
    secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
    host=os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"),
)

# ---------------------------------------------------------------------
# SUPABASE
# ---------------------------------------------------------------------
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_KEY in environment")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

GOOGLE_MCP_URL = os.getenv("GOOGLE_MCP_URL", "http://localhost:5000/create-event")
TIMEZONE = ZoneInfo("Asia/Karachi")

# ---------------------------------------------------------------------
# UTILITIES
# ---------------------------------------------------------------------
def next_weekday_date(target_weekday: int, from_date: date = None) -> date:
    if from_date is None:
        from_date = date.today()
    days_ahead = (target_weekday - from_date.weekday() + 7) % 7
    return from_date if days_ahead == 0 else from_date + timedelta(days=days_ahead)


def parse_day_to_date(day_str: str) -> date | None:
    """Try multiple date formats and weekday names. Accepts:
       - ISO: YYYY-MM-DD
       - Day-first: 03 December 2025
       - Month-first: December 03 2025 (user input)
       - Short month: Dec 03 2025 or Dec 3 2025
       - Weekday name: 'wednesday' -> returns next Wednesday (including today)
    """
    if not day_str:
        return None

    s = day_str.strip()
    # common formats to try (cover month-first and day-first)
    formats = [
        "%Y-%m-%d",
        "%d %B %Y",   # 03 December 2025
        "%d %b %Y",   # 03 Dec 2025
        "%B %d %Y",   # December 03 2025
        "%b %d %Y",   # Dec 03 2025
        "%B %d, %Y",  # December 3, 2025
        "%b %d, %Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            pass

    # try flexible parsing of "December 3 2025" without leading zero
    for fmt in ("%B %d %Y", "%b %d %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            pass

    # weekday names
    s_lower = s.lower()
    weekdays = {
        "monday": 0, "tuesday": 1, "wednesday": 2,
        "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6
    }
    if s_lower in weekdays:
        return next_weekday_date(weekdays[s_lower])

    return None


def parse_slot_times(slot_str: str) -> tuple[time, time] | None:
    """
    Robust slot parsing.
    - Accepts "10:00 AM - 11:00 AM"
    - Accepts "4 PM" or "4:00 PM" and converts to 4:00-5:00
    - Returns (start_time, end_time) or None if parsing fails
    """
    if not slot_str:
        return None

    s = slot_str.strip()
    # If it's a range like "10:00 AM - 11:00 AM"
    if "-" in s:
        parts = s.split("-")
        if len(parts) != 2:
            return None
        start_raw = parts[0].strip()
        end_raw = parts[1].strip()
        for fmt in ("%I:%M %p", "%I %p"):
            try:
                start_dt = datetime.strptime(start_raw, fmt)
                end_dt = datetime.strptime(end_raw, fmt)
                return start_dt.time(), end_dt.time()
            except Exception:
                pass
        return None

    # Single time like "4 PM" or "4:00 PM"
    for fmt in ("%I:%M %p", "%I %p"):
        try:
            start_dt = datetime.strptime(s, fmt)
            end_dt = start_dt + timedelta(hours=1)
            return start_dt.time(), end_dt.time()
        except Exception:
            pass

    # fallback: try 24-hour format "16:00"
    for fmt in ("%H:%M", "%H"):
        try:
            start_dt = datetime.strptime(s, fmt)
            end_dt = start_dt + timedelta(hours=1)
            return start_dt.time(), end_dt.time()
        except Exception:
            pass

    return None


# ---------------------------------------------------------------------
# AGENT
# ---------------------------------------------------------------------
class DoctorReceptionist(Agent):

    def __init__(self):
        super().__init__(instructions="You are a friendly and professional voice receptionist for a doctor's clinic.")

        self.time_slots = [
            "10:00 AM - 11:00 AM", "11:00 AM - 12:00 PM", "12:00 PM - 01:00 PM",
            "01:00 PM - 02:00 PM", "04:00 PM - 05:00 PM", "05:00 PM - 06:00 PM",
            "06:00 PM - 07:00 PM", "07:00 PM - 08:00 PM",
        ]

        self.schedule = {
            "sialkot": ["monday", "tuesday", "wednesday"],
            "lahore": ["thursday", "friday", "saturday"],
        }

        self.doctor = {
            "name": "Dr. Sarah Khan",
            "specialty": "Cardiologist",
            "fee": 2500,
        }

        self.appointments = []
        self._load_appointments_from_db()

    # -----------------------------------------------------------------
    @observe(name="supabase_load_appointments")
    def _load_appointments_from_db(self):
        try:
            res = supabase.table("appointments").select("*").execute()
            self.appointments = res.data if res.data else []
        except:
            self.appointments = []

    # -----------------------------------------------------------------
    @observe(name="supabase_insert_appointment")
    def _insert_appointment_db(self, appointment: dict) -> dict | None:
        """Insert appointment into Supabase and return the inserted record."""
        try:
            response = supabase.table("appointments").insert(appointment).execute()
            if response.data and len(response.data) > 0:
                return response.data[0]
            return None
        except Exception as e:
            print("❌ Supabase insert error:", e)
            return None

    # -----------------------------------------------------------------
    @function_tool
    @observe(name="check_availability")
    async def check_availability(self, context: RunContext, city: str, day: str) -> str:

        city = city.lower().strip()
        day_in = day.lower().strip()

        if day_in == "sunday":
            return "Doctor is on leave on Sunday."

        if city not in self.schedule:
            return "Services only available in Sialkot and Lahore."

        if day_in in self.schedule[city]:
            return f"Doctor is available in {city.title()} on {day_in.title()}."

        return "Doctor not available."

    # -----------------------------------------------------------------
    @function_tool
    @observe(name="book_appointment")
    async def book_appointment(
        self,
        context: RunContext,
        patient_name: str,
        city: str,
        day: str,
        slot: str,
        notes: str = "",
    ) -> str:

        try:
            # ✅ Normalize inputs
            city = city.lower().strip()
            slot_input = slot.lower().replace(" ", "")  # converts "4 pm" → "4pm"

            # ✅ Fix slot like "4pm" → "4 PM"
            if slot_input.endswith("pm") or slot_input.endswith("am"):
                num = slot_input[:-2]
                slot_input = f"{num} {slot_input[-2:].upper()}"

            # ✅ Parse date safely
            booking_date = parse_day_to_date(day)
            if not booking_date:
                return "❌ Invalid date provided. Please say the full date like 'December 3 2025'."

            # ✅ Parse time safely
            times = parse_slot_times(slot_input)
            if not times:
                return "❌ Invalid time slot. Example: 4 PM or 10:00 AM - 11:00 AM."

            start_dt = datetime.combine(booking_date, times[0]).replace(tzinfo=TIMEZONE)
            end_dt = datetime.combine(booking_date, times[1]).replace(tzinfo=TIMEZONE)

            # ✅ Format slot as "04:00 PM - 05:00 PM" (matching your DB format)
            formatted_slot = f"{start_dt.strftime('%I:%M %p')} - {end_dt.strftime('%I:%M %p')}"

            # ✅ Prevent duplicate slot booking
            for appt in self.appointments:
                if (
                    appt["city"].lower() == city
                    and appt["date"] == booking_date.strftime("%m/%d/%y")
                    and appt["slot"].lower() == formatted_slot.lower()
                ):
                    return "❌ This slot is already booked. Please choose another time."

            # Build appointment dict
            appointment = {
                "id": f"APT{len(self.appointments) + 1001}",
                "patient_name": patient_name.title(),
                "patient_id": f"PID{len(self.appointments) + 5001}",
                "city": city.title(),
                "date": booking_date.strftime("%m/%d/%y"),  # ✅ MM/DD/YY format
                "slot": formatted_slot,
                "notes": notes if notes else None,
                "calendar_event_id": None,
                "calendar_link": None,
            }

            # Persist to Supabase first (to ensure id is reserved)
            inserted = self._insert_appointment_db(appointment)
            if inserted is None:
                return "❌ Failed to save appointment. Please try again later."

            # Keep local cache synced
            self._load_appointments_from_db()

            # -------------------------------
            # 📌 SEND TO GOOGLE MCP SERVER
            # -------------------------------
            try:
                event_payload = {
                    "patient_name": patient_name,
                    "city": appointment["city"],
                    # Google MCP server expects ISO datetimes like "2025-11-15T10:00:00" and it will add timezone
                    "start_time": start_dt.isoformat(),
                    "end_time": end_dt.isoformat(),
                }

                resp = requests.post(GOOGLE_MCP_URL, json=event_payload, timeout=8)
                if resp.ok:
                    try:
                        data = resp.json()
                        # ✅ Update with htmlLink and eventId from your MCP server
                        if "htmlLink" in data:
                            supabase.table("appointments").update({
                                "calendar_link": data.get("htmlLink")
                            }).eq("id", appointment["id"]).execute()
                            appointment["calendar_link"] = data.get("htmlLink")
                        
                        if "eventId" in data:
                            supabase.table("appointments").update({
                                "calendar_event_id": data.get("eventId")
                            }).eq("id", appointment["id"]).execute()
                            appointment["calendar_event_id"] = data.get("eventId")

                    except Exception as e:
                        print("Failed parsing MCP response:", e)
                else:
                    print("Calendar sync failed:", resp.status_code, resp.text)

            except Exception as e:
                print("⚠️ Calendar sync failed:", str(e))

            return f"✅ Appointment confirmed for {patient_name.title()} on {booking_date.strftime('%m/%d/%y')} at {formatted_slot} in {city.title()}!"

        except Exception as e:
            print("🔥 BOOKING ERROR:", str(e))
            return "❌ A system error occurred while booking. Please try again."


    # -----------------------------------------------------------------
    @function_tool
    @observe(name="show_appointments")
    async def show_appointments(self, context: RunContext) -> str:
        res = supabase.table("appointments").select("*").execute()
        appts = res.data if res.data else []

        return "\n".join([a["id"] for a in appts]) if appts else "No appointments."

    # -----------------------------------------------------------------
    @function_tool
    @observe(name="cancel_appointment")
    async def cancel_appointment(self, context: RunContext, appointment_id: str = "") -> str:
        supabase.table("appointments").delete().eq("id", appointment_id).execute()
        self._load_appointments_from_db()
        return "✅ Appointment cancelled successfully."

# ---------------------------------------------------------------------
# ✅ ✅ ✅ ENTRYPOINT — Traced with @observe
# ---------------------------------------------------------------------
@observe(name="livekit_session")
async def entrypoint(ctx: agents.JobContext):

    session = AgentSession(
        stt=deepgram.STT(
            model="nova-2",
            language=os.getenv("STT_LANGUAGE", "en"),
            api_key=os.getenv("DEEPGRAM_API_KEY"),
        ),
        llm=google.LLM(
            model=os.getenv("LLM_CHOICE", "gemini-2.5-flash"),
            api_key=os.getenv("GEMINI_API_KEY"),
        ),
        tts=deepgram.TTS(
            model=os.getenv("DEEPGRAM_TTS_MODEL", "aura-asteria-en"),
            api_key=os.getenv("DEEPGRAM_API_KEY"),
        ),
        vad=silero.VAD.load(),
    )

    await session.start(room=ctx.room, agent=DoctorReceptionist())

    await session.generate_reply(
        instructions="Greet the patient politely and ask which city they want to visit."
    )

# ---------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------
if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))
