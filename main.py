# main.py
from dotenv import load_dotenv
from livekit import agents
from livekit.agents import Agent, AgentSession, RunContext
from livekit.agents.llm import function_tool
from livekit.plugins import silero, google, elevenlabs, deepgram
from datetime import datetime, date, timedelta, time
from zoneinfo import ZoneInfo
import os
import requests

# Load environment variables
load_dotenv(".env")

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
    """Voice receptionist assistant for doctor clinic with branch and day-based availability."""

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

        self.appointments = []

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
    async def book_appointment(self, context: RunContext, patient_name: str, city: str, day: str, slot: str) -> str:
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

        # Save appointment locally
        appointment = {
            "id": f"APT{len(self.appointments) + 1001}",
            "patient_name": patient_name,
            "doctor_name": self.doctor["name"],
            "specialty": self.doctor["specialty"],
            "fee": self.doctor["fee"],
            "date": booking_date.isoformat(),
            "day": weekday_name.title(),
            "city": city.title(),
            "slot": slot,
        }

        self.appointments.append(appointment)

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
            # Optional: log response for debugging
            if resp.ok:
                # If MCP server returns an htmlLink or such, you could attach it to appointment
                try:
                    data = resp.json()
                    # attach link if present
                    if "htmlLink" in data:
                        appointment["calendar_link"] = data["htmlLink"]
                except Exception:
                    pass
            else:
                # Keep appointment but log error
                print("Calendar sync failed:", resp.status_code, resp.text)

        except Exception as e:
            # If MCP server is down or unreachable, we still keep local appointment
            print("Calendar sync failed:", str(e))

        # -------------------------------

        # Return confirmation message
        return (
            f"✅ Appointment confirmed!\n\n"
            f"Appointment ID: {appointment['id']}\n"
            f"Patient: {patient_name}\n"
            f"Doctor: {self.doctor['name']} ({self.doctor['specialty']})\n"
            f"City: {appointment['city']}\n"
            f"Date: {appointment['date']}\n"
            f"Day: {appointment['day']}\n"
            f"Time: {appointment['slot']}\n"
            f"Consultation Fee: Rs.{appointment['fee']}\n\n"
            f"You will receive a confirmation message shortly."
        )

    @function_tool
    async def show_appointments(self, context: RunContext) -> str:
        """List all booked appointments."""
        if not self.appointments:
            return "No appointments have been booked yet."
        summary = "Here are the booked appointments:\n\n"
        for appt in self.appointments:
            line = (
                f"{appt['id']}: {appt['patient_name']} → {appt['doctor_name']} "
                f"on {appt.get('date', appt.get('day', ''))} at {appt['slot']} ({appt['city']})"
            )
            if "calendar_link" in appt:
                line += f" | Calendar: {appt['calendar_link']}"
            summary += line + "\n"
        return summary.strip()


# ---------------------- ENTRY POINT ----------------------
async def entrypoint(ctx: agents.JobContext):
    """Start the Doctor Receptionist Assistant."""

    session = AgentSession(
        stt=deepgram.STT(model="nova-2", language=os.getenv("STT_LANGUAGE", "en")),
        llm=google.LLM(
            model=os.getenv("LLM_CHOICE", "gemini-2.5-flash"),
            api_key=os.getenv("GEMINI_API_KEY"),
        ),
        tts=elevenlabs.TTS(
            **{
                k: v
                for k, v in {
                    "voice_id": os.getenv("ELEVENLABS_VOICE_ID"),
                    "model": os.getenv("ELEVENLABS_TTS_MODEL"),
                    "api_key": os.getenv("ELEVENLABS_API_KEY"),
                }.items()
                if v
            },
            streaming_latency=int(os.getenv("ELEVENLABS_STREAMING_LATENCY", "0")),
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
