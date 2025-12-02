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

# MCP server URL for Google Calendar (change if needed)
GOOGLE_MCP_URL = os.getenv("GOOGLE_MCP_URL", "http://localhost:5000/create-event")
TIMEZONE = ZoneInfo("Asia/Karachi")  # ensure this zone exists on your Python version


def next_weekday_date(target_weekday: int, from_date: date = None) -> date:
    """Return the next date (including today) for given weekday (0=Monday..6=Sunday)."""
    if from_date is None:
        from_date = date.today()
    days_ahead = (target_weekday - from_date.weekday() + 7) % 7
    if days_ahead == 0:
        return from_date
    return from_date + timedelta(days=days_ahead)


def parse_day_to_date(day_str: str) -> date | None:
    """Try to parse day_str as ISO date (YYYY-MM-DD). If not, try weekday name and return next date."""
    day_str = day_str.strip().lower()
    # Try ISO date first
    try:
        parsed = datetime.strptime(day_str, "%Y-%m-%d").date()
        return parsed
    except Exception:
        pass

    # Try common formats like "15 November 2025"
    for fmt in ("%d %B %Y", "%d %b %Y"):
        try:
            parsed = datetime.strptime(day_str, fmt).date()
            return parsed
        except Exception:
            pass

    # Try weekday name
    weekdays = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }
    if day_str in weekdays:
        return next_weekday_date(weekdays[day_str])

    return None


def parse_slot_times(slot_str: str) -> tuple[time, time] | None:
    """
    Parse slot like "10:00 AM - 11:00 AM" into (start_time, end_time) as time objects.
    Returns None if fails.
    """
    parts = slot_str.split("-")
    if len(parts) != 2:
        return None
    start_raw = parts[0].strip()
    end_raw = parts[1].strip()
    try:
        start_dt = datetime.strptime(start_raw, "%I:%M %p")
        end_dt = datetime.strptime(end_raw, "%I:%M %p")
        return start_dt.time(), end_dt.time()
    except Exception:
        return None


class DoctorReceptionist(Agent):
    """Voice receptionist assistant for doctor clinic with branch and day-based availability.

    This version uses Supabase to persist appointments across agent restarts.
    """

    def __init__(self):
        super().__init__(
            instructions="""You are a friendly and professional voice receptionist for a doctor's clinic. 
            You handle appointment bookings for two branches: Sialkot and Lahore. 
            The doctor is available:
            - Monday, Tuesday, and Wednesday in Sialkot
            - Thursday, Friday, and Saturday in Lahore
            - Sunday is a holiday.
            Each working day has time slots from 10:00 AM to 2:00 PM and 4:00 PM to 8:00 PM, one-hour each.
            Always speak clearly and confirm information with the patient before booking."""
        )

        # Define time slots
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

        # Branch schedule
        self.schedule = {
            "sialkot": ["monday", "tuesday", "wednesday"],
            "lahore": ["thursday", "friday", "saturday"],
        }

        self.doctor = {
            "name": "Dr. Sarah Khan",
            "specialty": "Cardiologist",
            "fee": 2500,
        }

        # Local cache of appointments (kept in sync with Supabase)
        self.appointments = []
        self._load_appointments_from_db()

    # ---------------------- Supabase helpers ----------------------
    def _load_appointments_from_db(self):
        try:
            res = supabase.table("appointments").select("*").execute()
            if res and hasattr(res, "data") and res.data:
                # keep ordering by created_at if needed; here we just store raw data
                self.appointments = res.data
            else:
                self.appointments = []
        except Exception as e:
            print("Failed to load appointments from Supabase:", e)
            self.appointments = []

    def _insert_appointment_db(self, appointment: dict) -> dict | None:
        try:
            res = supabase.table("appointments").insert(appointment).execute()
            if res and hasattr(res, "data") and res.data:
                return res.data[0]
        except Exception as e:
            print("Supabase insert failed:", e)
        return None

    def _delete_appointment_db(self, appointment_id: str) -> bool:
        try:
            res = supabase.table("appointments").delete().eq("id", appointment_id).execute()
            # res.data often contains deleted rows; but API may vary
            return True
        except Exception as e:
            print("Supabase delete failed:", e)
            return False

    def _find_appointment_db_by_id(self, appointment_id: str) -> dict | None:
        try:
            res = supabase.table("appointments").select("*").eq("id", appointment_id).execute()
            if res and hasattr(res, "data") and res.data:
                return res.data[0]
        except Exception as e:
            print("Supabase find by id failed:", e)
        return None

    def _find_appointment_db_by_patient_and_date(self, patient_name: str, date_str: str) -> dict | None:
        try:
            res = (
                supabase.table("appointments")
                .select("*")
                .ilike("patient_name", patient_name)
                .eq("date", date_str)
                .execute()
            )
            if res and hasattr(res, "data") and res.data:
                return res.data[0]
        except Exception as e:
            print("Supabase find by patient/date failed:", e)
        return None

    # ---------------------- FUNCTION TOOLS ----------------------

    @function_tool
    async def get_current_date_and_time(self, context: RunContext) -> str:
        """Return current date and time."""
        now = datetime.now(TIMEZONE).strftime("%A, %B %d, %Y at %I:%M %p")
        return f"The current date and time is {now}."

    @function_tool
    async def check_availability(self, context: RunContext, city: str, day: str) -> str:
        """Check doctor availability based on city and day."""
        city = city.lower().strip()
        day_in = day.lower().strip()

        if day_in == "sunday":
            return "Doctor is on leave on Sunday. Please select any other day."

        if city not in self.schedule:
            return "Sorry, clinic services are only available in Sialkot and Lahore."

        if day_in in self.schedule[city]:
            available_slots = ", ".join(self.time_slots)
            return (
                f"Doctor {self.doctor['name']} is available in {city.title()} on {day_in.title()}.\n"
                f"Available time slots: {available_slots}"
            )
        else:
            alternate_branch = "lahore" if city == "sialkot" else "sialkot"
            if day_in in self.schedule[alternate_branch]:
                return (
                    f"Doctor is not available in {city.title()} on {day_in.title()}.\n"
                    f"However, doctor will be available in {alternate_branch.title()} on that day.\n"
                    f"Would you like to book your appointment there?"
                )
            else:
                return f"Doctor is not available in {city.title()} on {day_in.title()}."

    @function_tool
    async def book_appointment(self, context: RunContext, patient_name: str, city: str, day: str, slot: str, notes: str = "") -> str:
        """
        Book an appointment for the given city, day, and slot.
        day: can be '2025-11-15' or a weekday name like 'tuesday'
        slot: must be one of self.time_slots, e.g. '10:00 AM - 11:00 AM'
        """
        city = city.lower().strip()
        day_input = day.strip()
        slot = slot.strip()

        # Sunday check
        if day_input.lower() == "sunday":
            return "Doctor is on leave on Sunday. Please select another day."

        if city not in self.schedule:
            return "Clinic service only available in Sialkot and Lahore."

        # Convert day to actual date
        booking_date = parse_day_to_date(day_input)
        if booking_date is None:
            return "Sorry, I couldn't understand the day. Please provide a date like '2025-11-15' or weekday name like 'Tuesday'."

        weekday_name = booking_date.strftime("%A").lower()
        if weekday_name == "sunday":
            return "Doctor is on leave on Sunday. Please select another day."

        if weekday_name not in self.schedule[city]:
            alternate_branch = "lahore" if city == "sialkot" else "sialkot"
            return (
                f"Doctor is not available in {city.title()} on {weekday_name.title()}.\n"
                f"Doctor will be available in {alternate_branch.title()} instead."
            )

        if slot not in self.time_slots:
            return f"Invalid time slot. Available slots are: {', '.join(self.time_slots)}"

        # Parse slot times
        times = parse_slot_times(slot)
        if times is None:
            return "Invalid slot time format. Expected format like '10:00 AM - 11:00 AM'."

        start_time_obj, end_time_obj = times

        # Build timezone-aware datetimes
        start_dt = datetime.combine(booking_date, start_time_obj).replace(tzinfo=TIMEZONE)
        end_dt = datetime.combine(booking_date, end_time_obj).replace(tzinfo=TIMEZONE)

        # Build appointment dict
        appointment = {
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
                    if "htmlLink" in data:
                        # update DB with calendar link
                        supabase.table("appointments").update({"calendar_link": data.get("htmlLink")}).eq("id", appointment["id"]).execute()
                        appointment["calendar_link"] = data.get("htmlLink")
                    if "eventId" in data:
                        supabase.table("appointments").update({"calendar_event_id": data.get("eventId")}).eq("id", appointment["id"]).execute()
                        appointment["calendar_event_id"] = data.get("eventId")

                except Exception as e:
                    print("Failed parsing MCP response:", e)
            else:
                print("Calendar sync failed:", resp.status_code, resp.text)

        except Exception as e:
            print("Calendar sync failed:", str(e))

        # Reload local cache to reflect calendar fields
        self._load_appointments_from_db()

        return (
            f"✅ Appointment confirmed!\n\n"
            f"Appointment ID: {appointment['id']}\n"
            f"Patient: {patient_name}\n"
            f"City: {appointment['city']}\n"
            f"Date: {appointment['date']}\n"
            f"Time: {appointment['slot']}\n"
            f"Consultation Fee: Rs.{self.doctor['fee']}\n\n"
            f"You will receive a confirmation message shortly."
        )

    @function_tool
    async def show_appointments(self, context: RunContext) -> str:
        """List all booked appointments (reads from Supabase)."""
        try:
            res = supabase.table("appointments").select("*").order("created_at", desc=False).execute()
            appts = res.data if res and hasattr(res, "data") and res.data else []
        except Exception as e:
            print("Failed to fetch appointments:", e)
            appts = []

        if not appts:
            return "No appointments have been booked yet."

        summary_lines = ["Here are the booked appointments:\n"]
        for appt in appts:
            line = (
                f"{appt.get('id')}: {appt.get('patient_name')} on {appt.get('date')} at {appt.get('slot')} ({appt.get('city')})"
            )
            if appt.get("calendar_link"):
                line += f" | Calendar: {appt.get('calendar_link')}"
            summary_lines.append(line)
        return "\n".join(summary_lines)

    @function_tool
    async def cancel_appointment(
        self,
        context: RunContext,
        appointment_id: str = "",
        patient_name: str = "",
        date: str = ""
    ) -> str:
        """
        Cancel an existing appointment using either:
        - appointment_id  (recommended)
        OR
        - patient_name + date (YYYY-MM-DD)

        This will delete from Supabase and delete the Google Calendar event if present.
        """

        # Normalize input
        appointment_id = appointment_id.strip().upper()
        patient_name = patient_name.strip().lower()
        date = date.strip()

        if not appointment_id and not (patient_name and date):
            return (
                "To cancel an appointment, please provide either:\n"
                "- Appointment ID\n"
                "OR\n"
                "- Patient name and date (YYYY-MM-DD)"
            )

        # Find appointment (prefer DB lookup for cross-session)
        appt_to_cancel = None
        if appointment_id:
            appt_to_cancel = self._find_appointment_db_by_id(appointment_id)
        else:
            appt_to_cancel = self._find_appointment_db_by_patient_and_date(patient_name, date)

        if not appt_to_cancel:
            return "❌ No matching appointment found."

        # ------------------------
        # Cancel on Google Calendar
        # ------------------------
        try:
            event_id = appt_to_cancel.get("calendar_event_id")
            if event_id:
                requests.post(
                    GOOGLE_MCP_URL.replace("create-event", "delete-event"),
                    json={"eventId": event_id},
                    timeout=8,
                )
        except Exception as e:
            print("Calendar cancel failed:", str(e))

        # Delete from Supabase
        deleted = self._delete_appointment_db(appt_to_cancel["id"])
        # Refresh local cache
        self._load_appointments_from_db()

        if not deleted:
            return "❌ Failed to delete appointment from database."

        return (
            f"🗑️ Appointment cancelled successfully!\n"
            f"Appointment ID: {appt_to_cancel['id']}\n"
            f"Patient: {appt_to_cancel['patient_name']}\n"
            f"Date: {appt_to_cancel['date']}\n"
            f"City: {appt_to_cancel['city']}\n"
            f"Slot: {appt_to_cancel['slot']}"
        )


# ---------------------- ENTRY POINT ----------------------
async def entrypoint(ctx: agents.JobContext):
    """Start the Doctor Receptionist Assistant."""

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

    # Initial greeting
    await session.generate_reply(
        instructions="Greet the patient politely and ask which city they want to visit for the appointment — Sialkot or Lahore."
    )


if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))
