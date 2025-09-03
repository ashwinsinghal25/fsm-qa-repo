from fastmcp import FastMCP
from pydantic import BaseModel
import os
import time
import random
import string
from datetime import datetime, timezone
import json
import requests
import pymysql
from dotenv import load_dotenv
import asyncio
import logging
import functools
from typing import Any

DEFAULT_JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0cyI6IjE3NTAwNDg4NTQwMDAiLCJreWNfcHJvZmlsZV9pZCI6ICIxNTYzODkwMDM1MDAifQ.JBsJubSnUfD-EF+5moXo79B2PUQxwblbbL2EnmU69lU"


# Define request models
class BeatInput(BaseModel):
    mid: str
    tag: str
    ecode: str

class Attendance(BaseModel):
    lat: float
    lon: float
    deviceIdentifier: str
    notes: str | None = None

load_dotenv()
app = FastMCP("fsm-qa-mcp-server")

# Structured logging for all tool calls
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger("fsm-qa-mcp-server")


def with_tool_logging(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            def _ser(x):
                try:
                    from pydantic import BaseModel as _BM
                    if isinstance(x, _BM):
                        return x.model_dump()
                except Exception:
                    pass
                return x

            req = {
                "args": [_ser(a) for a in args],
                "kwargs": {k: _ser(v) for k, v in kwargs.items()},
            }
            logger.info("tool %s request: %s", func.__name__, json.dumps(req))
            result = func(*args, **kwargs)
            try:
                logger.info("tool %s response: %s", func.__name__, json.dumps(result))
            except Exception:
                logger.info("tool %s response (non-serializable): %r", func.__name__, result)
            return result
        except Exception:
            logger.exception("tool %s errored", func.__name__)
            raise

    return wrapper

# Example tool: beat.create
# Example tools: fse.checkin / fse.checkout
@app.tool()
@with_tool_logging
def fse_checkin(input: Any):
    """
    Clone the most recent CHECKIN_DONE row for this emp_id and insert a new
    CHECK-IN with current timestamps (NOW/CURDATE). Returns new row id/time.
    Accepts input as JSON string, dict, or {"emp_id": "..."} object.
    """
    # Normalize input
    try:
        if isinstance(input, str):
            data = json.loads(input)
        elif isinstance(input, dict):
            data = input
        else:
            # Attempt to pull from pydantic-like object
            data = getattr(input, 'model_dump', lambda: input)()
    except Exception as e:
        return {"ok": False, "error": f"Invalid input: {e}"}

    emp_id = None
    if isinstance(data, dict):
        emp_id = data.get('emp_id') or data.get('ecode') or data.get('empId')
    if not emp_id or not str(emp_id).strip():
        return {"ok": False, "error": "emp_id is required"}
    emp_id = str(emp_id).strip()

    # DB settings
    db_host = os.getenv("DB_HOST", "10.104.41.139")
    db_user = os.getenv("DB_USER", "fse_staging_user")
    db_pass = os.getenv("DB_PASS", "FSeStc12!8799#beD*fvg")
    db_name = os.getenv("DB_NAME", "fse_db_staging")

    insert_sql = (
        "INSERT INTO fse_checkin_checkout (status, fse_name, cust_id, emp_id, phone_number, check_in_time, "
        "check_out_time, reject_count, failure_reason, liveliness_failure_count, asm_id, asm_emp_id, "
        "selfie_dms_id, profile_dms_id, created_at, updated_at, date, meta_data, manual_checkin_reason) "
        "SELECT status, fse_name, cust_id, emp_id, phone_number, NOW(), NULL, reject_count, failure_reason, "
        "liveliness_failure_count, asm_id, asm_emp_id, selfie_dms_id, profile_dms_id, NOW(), NOW(), CURDATE(), "
        "meta_data, manual_checkin_reason FROM fse_checkin_checkout WHERE emp_id = %s AND status = 'CHECKIN_DONE' "
        "ORDER BY check_in_time DESC LIMIT 1"
    )

    fetch_latest_sql = (
        "SELECT id, emp_id, check_in_time FROM fse_checkin_checkout WHERE emp_id = %s "
        "ORDER BY check_in_time DESC, id DESC LIMIT 1"
    )

    try:
        conn = pymysql.connect(
            host=db_host,
            user=db_user,
            password=db_pass,
            database=db_name,
            autocommit=False,
            cursorclass=pymysql.cursors.DictCursor,
        )
        with conn:
            with conn.cursor() as cur:
                # Insert cloned row with NOW()
                rows = cur.execute(insert_sql, (emp_id,))
                if rows == 0:
                    conn.rollback()
                    return {"ok": False, "error": "No source CHECKIN_DONE row found for emp_id"}
                conn.commit()
                # Fetch the latest (just inserted) record
                cur.execute(fetch_latest_sql, (emp_id,))
                inserted = cur.fetchone()
    except Exception as e:
        return {"ok": False, "error": f"DB error: {e}"}

    if not inserted:
        return {"ok": False, "error": "Insert appears to have succeeded but no row was fetched"}
    # After DB insert: clear redis key checkInStatus::YYYY-MM-DD_empId
    date_str = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    base_url = os.getenv("BASE_URL", "https://fse-staging.paytm.com").rstrip('/')
    jwt_token = os.getenv("JWT_TOKEN") or DEFAULT_JWT
    redis_key = f"checkInStatus::{date_str}_{emp_id}"
    redis_url = f"{base_url}/fse/admin/redis/delete"
    redis_headers = {"x-jwt-token": jwt_token}
    try:
        r = requests.post(redis_url, params={"redisKey": redis_key}, headers=redis_headers, timeout=20)
        try:
            r_body = r.json()
        except Exception:
            r_body = r.text
        redis_ok = r.status_code < 300
    except Exception as e:
        redis_ok = False
        r_body = str(e)

    return {
        "ok": True,
        "id": inserted.get("id"),
        "emp_id": inserted.get("emp_id"),
        "check_in_time": str(inserted.get("check_in_time")),
        "redis_key": redis_key,
        "redis_clear_ok": redis_ok,
        "redis_response": r_body,
    }

@app.tool()
@with_tool_logging
def fse_checkout(payload: Attendance):
    return {"ok": True, "action": "fse.checkout", "payload": payload.model_dump()}


def _random_10_digits() -> str:
    return ''.join(random.choices(string.digits, k=10))


@app.tool()
@with_tool_logging
def beat_create(input: Any):
    """
    Create a service beat for a MID with a tag and ecode, then validate in DB.
    Returns beat_id and created_at from database.
    """
    # Accept either a JSON string, dict, or already-validated object
    try:
        if isinstance(input, str):
            input = BeatInput.model_validate(json.loads(input))
        elif isinstance(input, dict):
            input = BeatInput.model_validate(input)
        elif not isinstance(input, BeatInput):
            # Last resort, attempt pydantic validation
            input = BeatInput.model_validate(input)
    except Exception as e:
        return {"ok": False, "error": f"Invalid input format: {e}"}

    base_url = os.getenv("BASE_URL", "https://fse-staging.paytm.com").rstrip('/')
    jwt_token = os.getenv("JWT_TOKEN") or DEFAULT_JWT

    dsn = _random_10_digits()
    ticket = _random_10_digits()
    created_at = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")

    payload = {
        "referenceType": "pgmid",
        "referenceValue": input.mid,
        "priority": "Regular",
        "empCode": input.ecode,
        "tags": [input.tag],
        "tasks": "Testing Lead closure",
        "reasonForService": "EDC device lost",
        "deviceSerialNumber": dsn,
        "freshdeskTicketNumber": ticket,
        "product": "Soundbox",
        "typeOfProductModel": "Soundbox 3.0 4G",
        "languageOfProduct": "English",
        "created_at": created_at,
        "address": {
            "country": "India",
            "city": "Delhi",
            "pincode": "110059",
            "locality": "Uttam Nagar",
            "state": "New Delhi",
            "landmark": "near Gautam Nursing Home",
            "line1": "b-45",
            "latitute": "28.618799",
            "longitute": "77.073250",
        },
    }

    url = f"{base_url}/service-flow/service-beat"
    headers = {
        "cache-control": "no-cache",
        "content-type": "application/json",
        "x-jwt-token": jwt_token,
    }

    logs = []
    resp = None
    for attempt in range(3):
        try:
            start = time.time()
            resp = requests.post(url, headers=headers, data=json.dumps(payload), timeout=30)
            latency_ms = int((time.time() - start) * 1000)
            logs.append({"attempt": attempt + 1, "status": getattr(resp, "status_code", "?"), "latency_ms": latency_ms})
            if resp is not None and (resp.status_code < 500):
                break
        except Exception as e:
            logs.append({"attempt": attempt + 1, "error": str(e)})
            if attempt < 2:
                time.sleep(1 * (attempt + 1))
            else:
                return {"ok": False, "error": f"HTTP error: {e}", "logs": logs}


    try:
        body = resp.json()
    except Exception:
        body = resp.text

    if resp.status_code >= 300:
        return {"ok": False, "status": resp.status_code, "response": body, "logs": logs}

    # DB validation
    db_host = os.getenv("DB_HOST", "10.104.41.139")
    db_user = os.getenv("DB_USER", "fse_staging_user")
    db_pass = os.getenv("DB_PASS", "FSeStc12!8799#beD*fvg")
    db_name = os.getenv("DB_NAME", "fse_db_staging")

    query = (
        "SELECT id, created_at FROM fse_beat_mapping "
        "WHERE pg_mid = %s ORDER BY created_at DESC LIMIT 1"
    )

    try:
        conn = pymysql.connect(
            host=db_host,
            user=db_user,
            password=db_pass,
            database=db_name,
            cursorclass=pymysql.cursors.DictCursor,
        )
        with conn:
            with conn.cursor() as cur:
                cur.execute(query, (input.mid,))
                row = cur.fetchone()
    except Exception as e:
        return {"ok": False, "error": f"DB error: {e}", "http_response": body, "logs": logs}

    if not row:
        return {"ok": False, "error": "No beat found in DB for MID", "http_response": body, "logs": logs}

    return {
        "ok": True,
        "beat_id": row.get("id"),
        "created_at": str(row.get("created_at")),
        "http_response": body,
        "logs": logs,
    }

if __name__ == "__main__":
    # Expose HTTP SSE MCP server at /fsm-qa-mcp-server
    asyncio.run(app.run_http_async(host="127.0.0.1", port=8100, path="/fsm-qa-mcp-server"))

 
