from dotenv import load_dotenv
from livekit import agents
from livekit.agents import Agent, AgentSession, RunContext
from livekit.agents.llm import function_tool
from livekit.plugins import silero, google, elevenlabs, deepgram
from datetime import datetime
import os

# Load environment variables
load_dotenv(".env")

class DoctorReceptionist(Agent):
    """Voice receptionist assistant for doctor clinic with branch and day-based availability."""

    def __init__(self):
        super().__init__(
            instructions="""You are a friendly and professional voice receptionist for a doctor's clinic. 
            You handle appointment bookings for two branches: Sialkot and Lahore. 
            The doctor is available:
            - Monday, Tuesday, and Wednesday in **Sialkot**
            - Thursday, Friday, and Saturday in **Lahore**
            - Sunday is a **holiday**.
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
            "lahore": ["thursday", "friday", "saturday"]
        }

        self.doctor = {
            "name": "Dr. Sarah Khan",
            "specialty": "Cardiologist",
            "fee": 2500
        }

        self.appointments = []

    # ---------------------- FUNCTION TOOLS ----------------------

    @function_tool
    async def get_current_date_and_time(self, context: RunContext) -> str:
        """Return current date and time."""
        now = datetime.now().strftime("%A, %B %d, %Y at %I:%M %p")
        return f"The current date and time is {now}."

    @function_tool
    async def check_availability(self, context: RunContext, city: str, day: str) -> str:
        """Check doctor availability based on city and day.

        Args:
            city: City name (Sialkot or Lahore)
            day: Desired day of appointment
        """
        city = city.lower()
        day = day.lower()

        if day == "sunday":
            return "Doctor is on leave on Sunday. Please select any other day."

        if city not in self.schedule:
            return "Sorry, clinic services are only available in Sialkot and Lahore."

        # Doctor availability logic
        if day in self.schedule[city]:
            available_slots = ", ".join(self.time_slots)
            return (f"Doctor {self.doctor['name']} is available in {city.title()} on {day.title()}.\n"
                    f"Available time slots: {available_slots}")
        else:
            # Suggest alternate branch automatically
            alternate_branch = "lahore" if city == "sialkot" else "sialkot"
            if day in self.schedule[alternate_branch]:
                return (f"Doctor is not available in {city.title()} on {day.title()}.\n"
                        f"However, doctor will be available in {alternate_branch.title()} on that day.\n"
                        f"Would you like to book your appointment there?")
            else:
                return f"Doctor is not available in {city.title()} on {day.title()}."

    @function_tool
    async def book_appointment(self, context: RunContext, patient_name: str, city: str, day: str, slot: str) -> str:
        """Book an appointment for the given city, day, and slot.

        Args:
            patient_name: Name of patient
            city: Sialkot or Lahore
            day: Appointment day (Mon–Sat)
            slot: Time slot (e.g., '10:00 AM - 11:00 AM')
        """
        city = city.lower()
        day = day.lower()

        if day == "sunday":
            return "Doctor is on leave on Sunday. Please select another day."

        # Ensure valid city and day
        if city not in self.schedule:
            return "Clinic service only available in Sialkot and Lahore."

        if day not in self.schedule[city]:
            alternate_branch = "lahore" if city == "sialkot" else "sialkot"
            return (f"Doctor is not available in {city.title()} on {day.title()}.\n"
                    f"Doctor will be available in {alternate_branch.title()} instead.")

        if slot not in self.time_slots:
            return f"Invalid time slot. Available slots are: {', '.join(self.time_slots)}"

        # Save appointment
        appointment = {
            "id": f"APT{len(self.appointments) + 1001}",
            "patient_name": patient_name,
            "doctor_name": self.doctor["name"],
            "specialty": self.doctor["specialty"],
            "fee": self.doctor["fee"],
            "day": day.title(),
            "city": city.title(),
            "slot": slot
        }

        self.appointments.append(appointment)

        return (f"✅ Appointment confirmed!\n\n"
                f"Appointment ID: {appointment['id']}\n"
                f"Patient: {patient_name}\n"
                f"Doctor: {self.doctor['name']} ({self.doctor['specialty']})\n"
                f"City: {appointment['city']}\n"
                f"Day: {appointment['day']}\n"
                f"Time: {appointment['slot']}\n"
                f"Consultation Fee: Rs.{appointment['fee']}\n\n"
                f"You will receive a confirmation message shortly.")

    @function_tool
    async def show_appointments(self, context: RunContext) -> str:
        """List all booked appointments."""
        if not self.appointments:
            return "No appointments have been booked yet."
        summary = "Here are the booked appointments:\n\n"
        for appt in self.appointments:
            summary += (f"{appt['id']}: {appt['patient_name']} → {appt['doctor_name']} "
                        f"on {appt['day']} at {appt['slot']} ({appt['city']})\n")
        return summary.strip()


# ---------------------- ENTRY POINT ----------------------
from livekit.agents import AgentSession

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
