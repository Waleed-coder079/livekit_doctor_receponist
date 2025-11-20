# mcp_server.py
from flask import Flask, request, redirect, session, jsonify
import os
import json
import datetime
import google.oauth2.credentials
import google_auth_oauthlib.flow
import googleapiclient.discovery
from google.auth.transport.requests import Request
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "some_secret_key")

os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

SCOPES = ["https://www.googleapis.com/auth/calendar"]
CLIENT_SECRETS_FILE = "credentials.json"
REDIRECT_URI = "http://127.0.0.1:5000/callback"
TOKEN_FILE = "token.json"


def load_credentials():
    """Load credentials from token.json if exists, else return None."""
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "r") as f:
            token_data = json.load(f)
        creds = google.oauth2.credentials.Credentials(**token_data)
        # Refresh if expired
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            # Save refreshed token
            save_credentials(creds)
        return creds
    return None


def save_credentials(creds):
    """Save credentials to token.json"""
    token_data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes,
    }
    with open(TOKEN_FILE, "w") as f:
        json.dump(token_data, f, indent=2)


@app.route("/")
def index():
    return "✅ MCP Server is running for Google Calendar integration."


@app.route("/logout")
def logout():
    session.clear()
    if os.path.exists(TOKEN_FILE):
        os.remove(TOKEN_FILE)
    return "🔄 Session cleared! Please restart the OAuth flow."


@app.route("/authorize")
def authorize():
    """Create OAuth flow and redirect user to Google"""
    flow = google_auth_oauthlib.flow.Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE, scopes=SCOPES
    )
    flow.redirect_uri = REDIRECT_URI

    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent"
    )

    session["state"] = state

    return redirect(authorization_url)


@app.route("/callback")
def oauth2callback():
    if "state" not in session:
        return "❌ Missing OAuth state. Start again: /logout"

    if request.args.get("state") != session["state"]:
        session.clear()
        return "❌ State mismatch! Please restart: /logout"

    flow = google_auth_oauthlib.flow.Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        state=session["state"]
    )
    flow.redirect_uri = REDIRECT_URI

    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials

    save_credentials(creds)
    session.clear()

    return "✅ Google Calendar authorized successfully! You can now create events."


@app.route("/create-event", methods=["POST"])
def create_event():
    creds = load_credentials()
    if not creds:
        return jsonify({"error": "Auth missing. Please run /authorize"}), 401

    service = googleapiclient.discovery.build("calendar", "v3", credentials=creds)

    data = request.get_json()
    event = {
        "summary": f"Doctor Appointment - {data['patient_name']}",
        "location": data["city"],
        "description": "Doctor consultation appointment.",
        "start": {"dateTime": data["start_time"], "timeZone": "Asia/Karachi"},
        "end": {"dateTime": data["end_time"], "timeZone": "Asia/Karachi"},
    }

    result = service.events().insert(calendarId="primary", body=event).execute()

    return jsonify({
        "status": "Event created",
        "eventId": result["id"],
        "htmlLink": result["htmlLink"]
    })

@app.route("/delete-event", methods=["POST"])
def delete_event():
    creds = load_credentials()
    if not creds:
        return jsonify({"error": "Auth missing. Please run /authorize"}), 401

    service = googleapiclient.discovery.build("calendar", "v3", credentials=creds)
    data = request.get_json()

    event_id = data.get("eventId")
    if not event_id:
        return jsonify({"error": "Missing eventId"}), 400

    try:
        service.events().delete(calendarId="primary", eventId=event_id).execute()
        return jsonify({"status": "Event deleted"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500




if __name__ == "__main__":
    app.run(port=5000, debug=True)
