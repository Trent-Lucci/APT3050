from datetime import datetime, timedelta
from db import get_db_connection
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

STAGES = ["Reported", "Assigned", "In Progress", "Resolved"]


def sync_computer_status(cursor, computer_id):
  """Quarantine PC if it has any unresolved Critical incident; otherwise restore to Active."""
  cursor.execute("SELECT status FROM computers WHERE id = %s", (computer_id,))
  pc = cursor.fetchone()
  if not pc or pc["status"] == "Maintenance":
    return

  cursor.execute(
      """
        SELECT COUNT(*) AS cnt FROM incidents
        WHERE computer_id = %s
          AND severity = 'Critical'
          AND workflow_stage != 'Resolved'
        """,
      (computer_id,),
  )
  has_critical = cursor.fetchone()["cnt"] > 0
  new_status = "Quarantined" if has_critical else "Active"
  cursor.execute(
      "UPDATE computers SET status = %s WHERE id = %s",
      (new_status, computer_id),
  )


@app.route("/")
def index():
  return render_template("index.html")


@app.route("/api/dashboard", methods=["GET"])
def get_dashboard():
  with get_db_connection() as conn:
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM computers ORDER BY asset_tag")
    computers = cursor.fetchall()

    cursor.execute("""
            SELECT i.*, c.asset_tag, c.lab_room, c.status AS computer_status
            FROM incidents i
            JOIN computers c ON i.computer_id = c.id
            ORDER BY i.deadline ASC
            """)
    incidents = cursor.fetchall()
    cursor.close()

  now = datetime.now()
  for inc in incidents:
    deadline_dt = inc["deadline"]
    if isinstance(deadline_dt, str):
      deadline_dt = datetime.fromisoformat(deadline_dt)
    inc["is_overdue"] = (
        inc["workflow_stage"] != "Resolved" and now > deadline_dt
    )
    inc["deadline"] = deadline_dt.strftime("%Y-%m-%d %H:%M")

  return jsonify({"computers": computers, "incidents": incidents})


@app.route("/api/computers", methods=["POST"])
def add_computer():
  data = request.get_json(force=True)
  asset_tag = (data.get("asset_tag") or "").strip().upper()
  lab_room = (data.get("lab_room") or "").strip()
  os_version = (data.get("os_version") or "").strip()

  if not asset_tag or not lab_room or not os_version:
    return jsonify({"error": "Asset tag, lab room, and OS are required"}), 400

  with get_db_connection() as conn:
    cursor = conn.cursor()
    try:
      cursor.execute(
          """
                INSERT INTO computers (asset_tag, lab_room, os_version, status)
                VALUES (%s, %s, %s, 'Active')
                """,
          (asset_tag, lab_room, os_version),
      )
      conn.commit()
    except Exception as e:
      conn.rollback()
      return (
          jsonify(
              {"error": f"Could not add computer (duplicate asset tag?): {e}"}
          ),
          400,
      )
    finally:
      cursor.close()

  return jsonify({"message": "Computer added", "asset_tag": asset_tag}), 201


@app.route("/api/incidents/report", methods=["POST"])
def report_incident():
  data = request.get_json(force=True)
  asset_tag = (data.get("asset_tag") or "").strip().upper()
  title = (data.get("title") or "").strip() or "Simulated Security Alert"
  severity = data.get("severity", "Critical")

  with get_db_connection() as conn:
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT id FROM computers WHERE UPPER(asset_tag) = %s", (asset_tag,)
    )
    computer = cursor.fetchone()

    if not computer:
      cursor.close()
      return jsonify({"error": f"Computer '{asset_tag}' not found"}), 404

    hours_allowed = 2 if severity == "Critical" else 12
    deadline = datetime.now() + timedelta(hours=hours_allowed)

    cursor.execute(
        """
            INSERT INTO incidents (computer_id, title, severity, workflow_stage, deadline)
            VALUES (%s, %s, %s, 'Reported', %s)
            """,
        (computer["id"], title, severity, deadline),
    )

    if severity == "Critical":
      sync_computer_status(cursor, computer["id"])

    conn.commit()
    new_id = cursor.lastrowid
    cursor.close()

  return (
      jsonify({
          "message": "Incident logged",
          "id": new_id,
          "deadline": deadline.strftime("%Y-%m-%d %H:%M"),
      }),
      201,
  )


@app.route("/api/incidents/<int:incident_id>/advance", methods=["POST"])
def advance_incident(incident_id):
  with get_db_connection() as conn:
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT computer_id, workflow_stage FROM incidents WHERE id = %s",
        (incident_id,),
    )
    row = cursor.fetchone()
    if not row:
      cursor.close()
      return jsonify({"error": "Incident not found"}), 404

    current_stage = row["workflow_stage"]
    idx = STAGES.index(current_stage) if current_stage in STAGES else 0
    next_stage = STAGES[min(idx + 1, len(STAGES) - 1)]

    cursor.execute(
        "UPDATE incidents SET workflow_stage = %s WHERE id = %s",
        (next_stage, incident_id),
    )
    sync_computer_status(cursor, row["computer_id"])
    conn.commit()
    cursor.close()

  return jsonify({"id": incident_id, "workflow_stage": next_stage})


@app.route("/api/incidents/<int:incident_id>", methods=["DELETE"])
def delete_incident(incident_id):
  with get_db_connection() as conn:
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT computer_id, workflow_stage FROM incidents WHERE id = %s",
        (incident_id,),
    )
    row = cursor.fetchone()
    if not row:
      cursor.close()
      return jsonify({"error": "Incident not found"}), 404

    if row["workflow_stage"] != "Resolved":
      cursor.close()
      return (
          jsonify({"error": "Only resolved incidents can be deleted"}),
          400,
      )

    cursor.execute("DELETE FROM incidents WHERE id = %s", (incident_id,))
    sync_computer_status(cursor, row["computer_id"])
    conn.commit()
    cursor.close()

  return jsonify({"message": "Incident deleted", "id": incident_id})


if __name__ == "__main__":
  app.run(debug=True, port=5000)