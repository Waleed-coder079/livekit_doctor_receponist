# mcp_server.py
from flask import Flask, request, redirect, session, jsonify
import os
import datetime
import google.oauth2.credentials
import google_auth_oauthlib.flow
import googleapiclient.discovery
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "some_secret_key")

# OAuth2 setup
SCOPES = ['https://www.googleapis.com/auth/calendar']
CLIENT_SECRETS_FILE = "credentials.json"

@app.route('/')
def index():
    return "✅ MCP Server is running for Google Calendar integration."

@app.route('/authorize')
def authorize():
    """Step 1: Redirect user to Google OAuth"""
    flow = google_auth_oauthlib.flow.Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE, scopes=SCOPES
    )
    flow.redirect_uri = "http://localhost:5000/callback"

    authorization_url, state = flow.authorization_url(
        access_type='offline', include_granted_scopes='true'
    )
    session['state'] = state
    return redirect(authorization_url)

@app.route('/callback')
def oauth2callback():
    """Step 2: Exchange authorization code for token"""
    state = session['state']

    flow = google_auth_oauthlib.flow.Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE, scopes=SCOPES, state=state
    )
    flow.redirect_uri = "http://localhost:5000/callback"

    authorization_response = request.url
    flow.fetch_token(authorization_response=authorization_response)

    credentials = flow.credentials
    session['credentials'] = {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'scopes': credentials.scopes
    }
    return "✅ Google Calendar authorized successfully! You can now create events."

@app.route('/create-event', methods=['POST'])
def create_event():
    """Step 3: Create event in Google Calendar"""
    if 'credentials' not in session:
        return jsonify({"error": "Please authorize via /authorize first"}), 401

    credentials = google.oauth2.credentials.Credentials(**session['credentials'])
    service = googleapiclient.discovery.build('calendar', 'v3', credentials=credentials)

    data = request.get_json()
    patient_name = data.get("patient_name")
    city = data.get("city")
    start_time = data.get("start_time")  # ISO 8601 format: '2025-11-15T10:00:00'
    end_time = data.get("end_time")

    event = {
        'summary': f'Doctor Appointment - {patient_name}',
        'location': city,
        'description': 'Doctor consultation appointment.',
        'start': {'dateTime': start_time, 'timeZone': 'Asia/Karachi'},
        'end': {'dateTime': end_time, 'timeZone': 'Asia/Karachi'},
    }

    event_result = service.events().insert(calendarId='primary', body=event).execute()

    return jsonify({
        "status": "Event created",
        "htmlLink": event_result.get('htmlLink')
    })


if __name__ == '__main__':
    app.run(port=5000, debug=True)
