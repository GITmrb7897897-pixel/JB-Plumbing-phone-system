"""
JB Plumbing - AI Phone Answering System
-----------------------------------------
A Flask app that answers incoming calls via Twilio, walks the caller
through a short set of qualifying questions using Twilio's built-in
speech recognition, logs everything to a local SQLite database, and
gives Josh a simple web dashboard to review calls and take calls
himself whenever he wants.
"""
import os
from datetime import datetime

from flask import Flask, request, redirect, url_for, render_template, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.rest import Client
from dotenv import load_dotenv

import database as db

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_FROM_NUMBER = os.environ.get("TWILIO_FROM_NUMBER")
JOSH_CELL_NUMBER = os.environ.get("JOSH_CELL_NUMBER")

twilio_client = (
    Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN) if TWILIO_ACCOUNT_SID else None
)

db.init_db()

# Holds the answers collected so far for each in-progress call.
# Keyed by Twilio's CallSid. Fine for a single-server deployment;
# swap for Redis if you ever run more than one web process.
call_sessions = {}


def get_session(call_sid):
    if call_sid not in call_sessions:
        call_sessions[call_sid] = {"created_at": datetime.utcnow().isoformat()}
    return call_sessions[call_sid]


def say(response, text):
    response.say(text, voice="Polly.Matthew")


# ---------------------------------------------------------------------
# Call flow
# ---------------------------------------------------------------------

@app.route("/voice/incoming", methods=["POST"])
def incoming_call():
    call_sid = request.form.get("CallSid")
    caller_number = request.form.get("From")
    get_session(call_sid)["caller_number"] = caller_number

    response = VoiceResponse()

    # If Josh has flipped himself "available" on the dashboard, ring
    # his cell first instead of running the automated flow.
    if db.get_setting("josh_available") == "true" and JOSH_CELL_NUMBER:
        say(response, "Thanks for calling JB Plumbing. Connecting you now.")
        response.dial(
            JOSH_CELL_NUMBER,
            timeout=18,
            action=url_for("after_direct_dial", _external=True),
        )
        return str(response)

    gather = Gather(
        input="speech",
        action=url_for("collect_name", _external=True),
        method="POST",
        speech_timeout="auto",
        language="en-US",
    )
    gather.say(
        "Thanks for calling JB Plumbing. How can we help you today? "
        "First, can I get your name?",
        voice="Polly.Matthew",
    )
    response.append(gather)
    response.redirect(url_for("incoming_call"))
    return str(response)


@app.route("/voice/after-direct-dial", methods=["POST"])
def after_direct_dial():
    # Josh didn't pick up (no-answer, busy, or failed) -> fall back
    # to the automated flow instead of just hanging up on the caller.
    call_sid = request.form.get("CallSid")
    dial_status = request.form.get("DialCallStatus")
    response = VoiceResponse()

    if dial_status in ("no-answer", "busy", "failed"):
        get_session(call_sid)["caller_number"] = request.form.get("From")
        gather = Gather(
            input="speech",
            action=url_for("collect_name", _external=True),
            method="POST",
            speech_timeout="auto",
        )
        gather.say(
            "Sorry, Josh is on another job right now. Can I get your name "
            "so we can get you taken care of?",
            voice="Polly.Matthew",
        )
        response.append(gather)

    return str(response)


@app.route("/voice/collect-name", methods=["POST"])
def collect_name():
    call_sid = request.form.get("CallSid")
    name = request.form.get("SpeechResult", "").strip()
    get_session(call_sid)["name"] = name or "Unknown"

    response = VoiceResponse()
    gather = Gather(
        input="speech",
        action=url_for("collect_phone", _external=True),
        method="POST",
        speech_timeout="auto",
    )
    gather.say(
        f"Thanks{', ' + name if name else ''}. What's the best phone number "
        "to reach you at?",
        voice="Polly.Matthew",
    )
    response.append(gather)
    return str(response)


@app.route("/voice/collect-phone", methods=["POST"])
def collect_phone():
    call_sid = request.form.get("CallSid")
    sess = get_session(call_sid)
    phone = request.form.get("SpeechResult", "").strip()
    sess["callback_phone"] = phone or sess.get("caller_number", "")

    response = VoiceResponse()
    gather = Gather(
        input="speech",
        action=url_for("collect_address", _external=True),
        method="POST",
        speech_timeout="auto",
    )
    gather.say(
        "And what's the address where you're having the issue?",
        voice="Polly.Matthew",
    )
    response.append(gather)
    return str(response)


@app.route("/voice/collect-address", methods=["POST"])
def collect_address():
    call_sid = request.form.get("CallSid")
    address = request.form.get("SpeechResult", "").strip()
    get_session(call_sid)["address"] = address

    response = VoiceResponse()
    gather = Gather(
        input="speech",
        action=url_for("collect_issue", _external=True),
        method="POST",
        speech_timeout="auto",
    )
    gather.say(
        "Got it. In a few words, what's going on — for example, a backed "
        "up sewer line, a camera inspection, or directional drilling?",
        voice="Polly.Matthew",
    )
    response.append(gather)
    return str(response)


@app.route("/voice/collect-issue", methods=["POST"])
def collect_issue():
    call_sid = request.form.get("CallSid")
    issue = request.form.get("SpeechResult", "").strip()
    get_session(call_sid)["issue"] = issue

    response = VoiceResponse()
    gather = Gather(
        input="speech",
        action=url_for("collect_callback_time", _external=True),
        method="POST",
        speech_timeout="auto",
    )
    gather.say(
        "Thanks. What's the best time for Josh to call you back?",
        voice="Polly.Matthew",
    )
    response.append(gather)
    return str(response)


@app.route("/voice/collect-callback-time", methods=["POST"])
def collect_callback_time():
    call_sid = request.form.get("CallSid")
    callback_time = request.form.get("SpeechResult", "").strip()
    get_session(call_sid)["preferred_callback_time"] = callback_time

    response = VoiceResponse()
    gather = Gather(
        input="dtmf speech",
        num_digits=1,
        action=url_for("handle_next_step", _external=True),
        method="POST",
        speech_timeout="auto",
    )
    gather.say(
        "Last thing — press 1, or say 'callback', to schedule a callback "
        "with Josh. Press 2, or say 'voicemail', to leave a voicemail. "
        "Press 3, or say 'text', to get a text confirmation instead.",
        voice="Polly.Matthew",
    )
    response.append(gather)
    return str(response)


@app.route("/voice/handle-next-step", methods=["POST"])
def handle_next_step():
    call_sid = request.form.get("CallSid")
    digits = request.form.get("Digits", "")
    speech = (request.form.get("SpeechResult") or "").lower()
    sess = get_session(call_sid)

    response = VoiceResponse()
    wants_voicemail = digits == "2" or "voicemail" in speech
    wants_text = digits == "3" or "text" in speech

    if wants_voicemail:
        sess["next_step"] = "voicemail"
        say(response, "Go ahead and leave your message after the beep. "
                       "Press the pound key when you're done.")
        response.record(
            action=url_for("save_voicemail", _external=True),
            method="POST",
            max_length=120,
            finish_on_key="#",
            play_beep=True,
        )
    elif wants_text:
        sess["next_step"] = "text_confirmation"
        log_call(call_sid, sess)
        send_text_confirmation(sess)
        say(response, "Perfect, we've texted you a confirmation and Josh "
                       "will follow up soon. Thanks for calling JB Plumbing!")
        response.hangup()
    else:
        sess["next_step"] = "callback_requested"
        log_call(call_sid, sess)
        notify_josh(sess)
        say(response, "Great, Josh will call you back at your preferred "
                       "time. Thanks for calling JB Plumbing!")
        response.hangup()

    return str(response)


@app.route("/voice/save-voicemail", methods=["POST"])
def save_voicemail():
    call_sid = request.form.get("CallSid")
    recording_url = request.form.get("RecordingUrl", "")
    sess = get_session(call_sid)
    sess["voicemail_url"] = recording_url
    log_call(call_sid, sess)
    notify_josh(sess, voicemail=True)

    response = VoiceResponse()
    say(response, "Thanks, your message has been saved. Josh will get "
                   "back to you soon. Goodbye!")
    response.hangup()
    return str(response)


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def log_call(call_sid, sess):
    db.insert_call(
        call_sid=call_sid,
        name=sess.get("name", ""),
        phone=sess.get("callback_phone") or sess.get("caller_number", ""),
        address=sess.get("address", ""),
        issue=sess.get("issue", ""),
        preferred_time=sess.get("preferred_callback_time", ""),
        next_step=sess.get("next_step", ""),
        voicemail_url=sess.get("voicemail_url", ""),
        created_at=sess.get("created_at", datetime.utcnow().isoformat()),
    )
    call_sessions.pop(call_sid, None)


def notify_josh(sess, voicemail=False):
    """Texts Josh a summary the moment a call wraps up, so he never
    has to check the dashboard to know a lead came in."""
    if not twilio_client or not JOSH_CELL_NUMBER:
        return
    kind = "New voicemail" if voicemail else "New callback request"
    body = (
        f"{kind} - JB Plumbing\n"
        f"Name: {sess.get('name', 'Unknown')}\n"
        f"Phone: {sess.get('callback_phone') or sess.get('caller_number', '')}\n"
        f"Address: {sess.get('address', '')}\n"
        f"Issue: {sess.get('issue', '')}\n"
        f"Preferred time: {sess.get('preferred_callback_time', '')}"
    )
    twilio_client.messages.create(to=JOSH_CELL_NUMBER, from_=TWILIO_FROM_NUMBER, body=body)


def send_text_confirmation(sess):
    if not twilio_client:
        return
    to_number = sess.get("callback_phone") or sess.get("caller_number")
    if not to_number:
        return
    body = (
        "Thanks for calling JB Plumbing! We've got your info:\n"
        f"Issue: {sess.get('issue', '')}\n"
        f"Address: {sess.get('address', '')}\n"
        "Josh will follow up with you soon."
    )
    twilio_client.messages.create(to=to_number, from_=TWILIO_FROM_NUMBER, body=body)
    notify_josh(sess)


# ---------------------------------------------------------------------
# Admin dashboard
# ---------------------------------------------------------------------

@app.route("/admin")
def admin_dashboard():
    calls = db.get_all_calls()
    josh_available = db.get_setting("josh_available") == "true"
    return render_template("dashboard.html", calls=calls, josh_available=josh_available)


@app.route("/admin/toggle-availability", methods=["POST"])
def toggle_availability():
    """Josh flips this ON when he wants calls to ring straight to his
    cell, and OFF to let the AI answer everything again."""
    current = db.get_setting("josh_available") == "true"
    db.set_setting("josh_available", "false" if current else "true")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/export-csv")
def export_csv():
    csv_data = db.export_calls_csv()
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=jb_plumbing_calls.csv"},
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)
