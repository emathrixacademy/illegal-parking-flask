from flask import Flask, request, jsonify, render_template_string
import requests
import os

app = Flask(__name__)

UNISMS_API_URL = "https://unismsapi.com/api/sms"

# PRE-GENERATED DATA
REQUESTS = [
    {
        "id": 1,
        "requestor_name": "GERALD RAY SALAZAR",
        "date": "2026-04-10",
        "time": "9:00 AM - 10:00 AM",
        "status": "for-review",
        "phone_number": "+639497428824",
        "appearance_date": "",
        "appearance_time": ""
    },
    {
        "id": 2,
        "requestor_name": "RACHELYN ESONA",
        "date": "2026-04-11",
        "time": "10:00 AM - 11:00 AM",
        "status": "for-review",
        "phone_number": "+639626836556",
        "appearance_date": "",
        "appearance_time": ""
    },

    {
        "id": 3,
        "requestor_name": "KRISTEL ALOQUIN",
        "date": "2026-04-10",
        "time": "9:00 AM - 10:00 AM",
        "status": "for-review",
        "phone_number": "+639206765494",
        "appearance_date": "",
        "appearance_time": ""
    },

    {
        "id": 4,
        "requestor_name": "ENGR. MARICAR CABANSAG",
        "date": "2026-04-10",
        "time": "9:00 AM - 10:00 AM",
        "status": "for-review",
        "phone_number": "+639497428824",
        "appearance_date": "",
        "appearance_time": ""
    },

    {
        "id": 5,
        "requestor_name": "ENGR. DARREN RONQUEZ",
        "date": "2026-04-10",
        "time": "9:00 AM - 10:00 AM",
        "status": "for-review",
        "phone_number": "+639497428824",
        "appearance_date": "",
        "appearance_time": ""
    },

    {
        "id": 6,
        "requestor_name": "ENGR. HELCY ALON",
        "date": "2026-04-10",
        "time": "9:00 AM - 10:00 AM",
        "status": "for-review",
        "phone_number": "+639497428824",
        "appearance_date": "",
        "appearance_time": ""
    },

]

with open("admin.html", "r", encoding="utf-8") as f:
    ADMIN_HTML = f.read()


@app.route("/")
def home():
    return render_template_string(ADMIN_HTML)


@app.route("/api/requests")
def get_requests():
    return jsonify(REQUESTS)


@app.route("/api/requests/<int:req_id>/decision", methods=["POST"])
def save_decision(req_id):
    data = request.get_json()

    req = next((r for r in REQUESTS if r["id"] == req_id), None)
    if not req:
        return jsonify({"error": "Request not found"}), 404

    req["status"] = data.get("status")
    req["appearance_date"] = data.get("appearance_date", "")
    req["appearance_time"] = data.get("appearance_time", "")

    return jsonify({"ok": True})


@app.route("/send-sms", methods=["POST"])
def send_sms():
    data = request.get_json() or {}

    recipient = (data.get("recipient") or "").strip()
    content = (data.get("content") or "").strip()
    key = os.getenv("UNISMS_SECRET_KEY")

    if not recipient:
        return jsonify({"error": "Recipient is required."}), 400

    if not content:
        return jsonify({"error": "Message content is required."}), 400

    if len(content) > 160:
        return jsonify({"error": "SMS content exceeds 160 characters."}), 400

    if not key:
        return jsonify({"error": "UNISMS_SECRET_KEY is missing."}), 500

    try:
        res = requests.post(
            UNISMS_API_URL,
            auth=(key, ""),
            json={
                "recipient": recipient,
                "content": content
            },
            timeout=20
        )

        result = res.json()

        if res.status_code == 201:
            msg = result["message"]
            return jsonify({
                "message_status": msg["status"],
                "recipient": msg["recipient"]
            })

        return jsonify({
            "error": f"UniSMS error {res.status_code}",
            "details": result
        }), res.status_code

    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True)