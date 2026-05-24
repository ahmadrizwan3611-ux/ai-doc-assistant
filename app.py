from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import ast, re, os, json, logging, requests, hmac, hashlib, time
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

# Railway/production React build serving
# Prefer frontend/build because Create React App references assets as /static/js/... and /static/css/...
# If frontend/build is not available, fall back to root static for older deployments.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_BUILD_DIR = os.path.join(BASE_DIR, "frontend", "build")
ROOT_STATIC_DIR = os.path.join(BASE_DIR, "static")
REACT_BUILD_DIR = (
    FRONTEND_BUILD_DIR
    if os.path.exists(os.path.join(FRONTEND_BUILD_DIR, "index.html"))
    else ROOT_STATIC_DIR
)

app = Flask(__name__, static_folder=REACT_BUILD_DIR, static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("MAX_CONTENT_LENGTH", 5 * 1024 * 1024))
MAX_CODE_CHARS = int(os.getenv("MAX_CODE_CHARS", "1200000"))

allowed_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")
CORS(app, resources={r"/*": {"origins": [o.strip() for o in allowed_origins if o.strip()]}}, supports_credentials=True)

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

GROQ_API_KEY         = os.getenv("GROQ_API_KEY", "")
GROQ_URL             = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL           = "llama-3.3-70b-versatile"
SUPABASE_URL         = os.getenv("SUPABASE_URL", "")
SUPABASE_ANON_KEY    = os.getenv("SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "") or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

# Phase 4: Stripe billing configuration
STRIPE_SECRET_KEY      = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET  = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRO_PRICE_ID    = os.getenv("STRIPE_PRO_PRICE_ID", "")
STRIPE_TEAM_PRICE_ID   = os.getenv("STRIPE_TEAM_PRICE_ID", "")
FRONTEND_URL           = os.getenv("FRONTEND_URL", "http://localhost:3000").rstrip("/")


def is_ai_enabled():
    return bool(GROQ_API_KEY)


# ── Groq ───────────────────────────────────────────────────────────────────────
# ── Improved Groq Call ─────────────────────────────────────────────────────
def call_groq(prompt: str, max_tokens: int = 4200) -> dict:
    if not GROQ_API_KEY:
        return {"success": False, "error": "GROQ_API_KEY not set"}
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": "You are an elite senior software architect and technical writer."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,      # Lower = more consistent & professional
        "max_tokens": max_tokens,
    }
    try:
        r = requests.post(GROQ_URL, headers=headers, json=payload, timeout=75)
        data = r.json()
        if r.status_code != 200:
            return {"success": False, "error": data.get("error", {}).get("message", "Groq error")}
        return {"success": True, "text": data["choices"][0]["message"]["content"]}
    except requests.exceptions.Timeout:
        return {"success": False, "error": "Groq API timed out."}
    except Exception as e:
        return {"success": False, "error": str(e)}
    if not GROQ_API_KEY:
        return {"success": False, "error": "GROQ_API_KEY not set"}
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": "You are a senior software engineer and technical documentation expert."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": max_tokens,
    }
    try:
        r = requests.post(GROQ_URL, headers=headers, json=payload, timeout=60)
        data = r.json()
        if r.status_code != 200:
            return {"success": False, "error": data.get("error", {}).get("message", "Groq error")}
        return {"success": True, "text": data["choices"][0]["message"]["content"]}
    except requests.exceptions.Timeout:
        return {"success": False, "error": "Groq API timed out."}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ── Supabase ───────────────────────────────────────────────────────────────────
def supabase_request(method, path, data=None, token=None, use_service_key=False):
    """Small Supabase REST helper.

    Important stability fix:
    - Service-key requests MUST use the service key as the Bearer token.
    - User-token requests use the user's access token.

    This prevents workspace/document features from breaking because of frontend
    token/RLS mismatch while still requiring auth before protected routes run.
    """
    key = SUPABASE_SERVICE_KEY if use_service_key else SUPABASE_ANON_KEY
    bearer = key if use_service_key else (token or key)
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {bearer}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    try:
        if method == "GET":
            r = requests.get(url, headers=headers, timeout=15)
        elif method == "POST":
            r = requests.post(url, headers=headers, json=data, timeout=15)
        elif method == "PATCH":
            r = requests.patch(url, headers=headers, json=data, timeout=15)
        elif method == "DELETE":
            r = requests.delete(url, headers=headers, timeout=15)
        else:
            return {"error": "Unknown method"}

        if r.status_code >= 400:
            try:
                return {"error": r.json(), "status_code": r.status_code}
            except Exception:
                return {"error": r.text, "status_code": r.status_code}

        return r.json() if r.text else {}
    except Exception as e:
        return {"error": str(e)}


def has_workspace_access(user_id, workspace_id):
    """Backend-level workspace permission check.

    This makes DevFlow stable even if Supabase RLS policies are still being tuned.
    The frontend must still log in, but the backend uses the service key to verify
    ownership or membership safely.
    """
    if not user_id or not workspace_id:
        return False

    owned = supabase_request(
        "GET",
        f"workspaces?id=eq.{workspace_id}&owner_id=eq.{user_id}&select=id",
        use_service_key=True,
    )
    if isinstance(owned, list) and len(owned) > 0:
        return True

    member = supabase_request(
        "GET",
        f"workspace_members?workspace_id=eq.{workspace_id}&user_id=eq.{user_id}&select=id",
        use_service_key=True,
    )
    return isinstance(member, list) and len(member) > 0


def get_user_from_token(token):
    if not token: return None
    headers = {"apikey": SUPABASE_ANON_KEY, "Authorization": f"Bearer {token}"}
    try:
        r = requests.get(f"{SUPABASE_URL}/auth/v1/user", headers=headers, timeout=10)
        return r.json() if r.status_code == 200 else None
    except:
        return None


def require_auth(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get("Authorization", "").replace("Bearer ", "").strip()
        user = get_user_from_token(token)
        if not user:
            return jsonify({"error": "Unauthorized. Please log in."}), 401
        request.user = user
        request.token = token
        return f(*args, **kwargs)
    return decorated


# ── Phase 4: SaaS usage limits + upgrade prompts ─────────────────────────────
FREE_PLAN_LIMITS = {
    "documentation_generations": 5,
    "bug_analyzer": 3,
    "project_health": 2,
    "task_generator": 3,
    "workspaces": 1,
}

PAID_PLANS = {"pro", "team", "enterprise"}
VALID_PLANS = {"free", "pro", "team", "enterprise"}

FEATURE_LABELS = {
    "documentation_generations": "AI documentation / GitHub repository docs",
    "bug_analyzer": "Bug Analyzer",
    "project_health": "Project Health",
    "task_generator": "Task Generator",
    "workspaces": "Workspaces",
}


def current_usage_period():
    return datetime.now(timezone.utc).strftime("%Y-%m")


def normalize_plan(plan):
    plan = str(plan or "free").strip().lower()
    return plan if plan in VALID_PLANS else "free"


def get_user_plan(user_id):
    rows = supabase_request(
        "GET",
        f"user_plans?user_id=eq.{user_id}&select=plan,updated_at&limit=1",
        use_service_key=True,
    )
    if isinstance(rows, list) and rows:
        return normalize_plan(rows[0].get("plan"))
    return "free"


def get_user_plan_record(user_id):
    rows = supabase_request(
        "GET",
        f"user_plans?user_id=eq.{user_id}&select=* &limit=1".replace(" ", ""),
        use_service_key=True,
    )
    if isinstance(rows, list) and rows:
        return rows[0]
    return None


def upsert_user_plan(user_id, plan, extra=None):
    plan = normalize_plan(plan)
    extra = extra or {}
    existing = supabase_request(
        "GET",
        f"user_plans?user_id=eq.{user_id}&select=user_id",
        use_service_key=True,
    )
    payload = {"user_id": user_id, "plan": plan, **extra}
    if isinstance(existing, list) and existing:
        result = supabase_request(
            "PATCH",
            f"user_plans?user_id=eq.{user_id}",
            {"plan": plan, **extra},
            use_service_key=True,
        )
    else:
        result = supabase_request("POST", "user_plans", payload, use_service_key=True)
    return result


def stripe_price_for_plan(plan):
    plan = normalize_plan(plan)
    if plan == "pro":
        return STRIPE_PRO_PRICE_ID
    if plan == "team":
        return STRIPE_TEAM_PRICE_ID
    return ""


def stripe_is_configured():
    return bool(STRIPE_SECRET_KEY and STRIPE_PRO_PRICE_ID and STRIPE_TEAM_PRICE_ID)


def stripe_api_request(method, path, data=None):
    if not STRIPE_SECRET_KEY:
        return {"error": "Stripe secret key is not configured."}

    url = f"https://api.stripe.com/v1/{path.lstrip('/')}"
    try:
        if method == "POST":
            r = requests.post(url, auth=(STRIPE_SECRET_KEY, ""), data=data or {}, timeout=25)
        elif method == "GET":
            r = requests.get(url, auth=(STRIPE_SECRET_KEY, ""), timeout=25)
        else:
            return {"error": "Unsupported Stripe method."}

        try:
            payload = r.json()
        except Exception:
            payload = {"error": r.text}

        if r.status_code >= 400:
            msg = payload.get("error", {}).get("message") if isinstance(payload.get("error"), dict) else payload.get("error")
            return {"error": msg or "Stripe request failed.", "status_code": r.status_code, "raw": payload}
        return payload
    except Exception as e:
        return {"error": str(e)}


def verify_stripe_signature(payload_bytes, signature_header):
    if not STRIPE_WEBHOOK_SECRET:
        return False
    try:
        parts = dict(item.split("=", 1) for item in signature_header.split(",") if "=" in item)
        timestamp = parts.get("t")
        signature = parts.get("v1")
        if not timestamp or not signature:
            return False
        signed_payload = timestamp.encode("utf-8") + b"." + payload_bytes
        expected = hmac.new(STRIPE_WEBHOOK_SECRET.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)
    except Exception:
        return False


ACTIVE_STRIPE_STATUSES = {"active", "trialing", "past_due"}
INACTIVE_STRIPE_STATUSES = {"canceled", "unpaid", "incomplete_expired", "incomplete"}


def stripe_timestamp_to_iso(value):
    try:
        if value is None or value == "":
            return None
        return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat()
    except Exception:
        return None


def plan_is_paid_active(plan_record):
    if not plan_record:
        return False
    plan = normalize_plan(plan_record.get("plan"))
    if plan == "enterprise":
        return True
    if plan not in {"pro", "team"}:
        return False
    status = str(plan_record.get("subscription_status") or "").lower()
    return status in ACTIVE_STRIPE_STATUSES


def sync_plan_from_subscription(subscription):
    metadata = subscription.get("metadata") or {}
    user_id = metadata.get("user_id")
    plan = normalize_plan(metadata.get("plan"))
    status = str(subscription.get("status") or "").lower()
    subscription_id = subscription.get("id")
    customer_id = subscription.get("customer")
    current_period_end = stripe_timestamp_to_iso(subscription.get("current_period_end"))

    if not user_id:
        return {"error": "Stripe subscription does not include DevFlow user metadata."}

    if plan not in {"pro", "team"}:
        plan = "free"

    if status in INACTIVE_STRIPE_STATUSES:
        result = upsert_user_plan(user_id, "free", {
            "stripe_customer_id": customer_id,
            "stripe_subscription_id": subscription_id,
            "subscription_status": status or "canceled",
            "current_period_end": current_period_end,
        })
        return {"success": True, "plan": "free", "status": status, "result": result}

    if status in ACTIVE_STRIPE_STATUSES and plan in {"pro", "team"}:
        result = upsert_user_plan(user_id, plan, {
            "stripe_customer_id": customer_id,
            "stripe_subscription_id": subscription_id,
            "subscription_status": status,
            "current_period_end": current_period_end,
        })
        return {"success": True, "plan": plan, "status": status, "result": result}

    result = upsert_user_plan(user_id, "free", {
        "stripe_customer_id": customer_id,
        "stripe_subscription_id": subscription_id,
        "subscription_status": status or "unknown",
        "current_period_end": current_period_end,
    })
    return {"success": True, "plan": "free", "status": status, "result": result}


def sync_plan_from_checkout_session(session):
    metadata = session.get("metadata") or {}
    user_id = session.get("client_reference_id") or metadata.get("user_id")
    plan = normalize_plan(metadata.get("plan"))

    if not user_id or plan not in {"pro", "team"}:
        return {"error": "Stripe session does not include valid DevFlow metadata."}

    status = session.get("status")
    payment_status = session.get("payment_status")
    if status != "complete" and payment_status != "paid":
        return {"error": "Stripe checkout is not completed yet."}

    subscription_id = session.get("subscription")
    subscription_status = "active"
    current_period_end = None

    if subscription_id:
        subscription = stripe_api_request("GET", f"subscriptions/{subscription_id}")
        if isinstance(subscription, dict) and not subscription.get("error"):
            subscription_status = subscription.get("status") or "active"
            current_period_end = stripe_timestamp_to_iso(subscription.get("current_period_end"))

    result = upsert_user_plan(user_id, plan, {
        "stripe_customer_id": session.get("customer"),
        "stripe_subscription_id": subscription_id,
        "subscription_status": subscription_status,
        "current_period_end": current_period_end,
    })
    if isinstance(result, dict) and "error" in result:
        return {"error": result["error"]}
    return {"success": True, "plan": plan, "user_id": user_id}


def get_usage_count(user_id, feature, period=None):
    period = period or current_usage_period()
    rows = supabase_request(
        "GET",
        f"usage_events?user_id=eq.{user_id}&feature=eq.{feature}&period=eq.{period}&select=id",
        use_service_key=True,
    )
    return len(rows) if isinstance(rows, list) else 0


def get_owned_workspace_count(user_id):
    rows = supabase_request(
        "GET",
        f"workspaces?owner_id=eq.{user_id}&select=id",
        use_service_key=True,
    )
    return len(rows) if isinstance(rows, list) else 0


def build_usage_summary(user_id):
    period = current_usage_period()
    plan_record = get_user_plan_record(user_id)
    stored_plan = normalize_plan(plan_record.get("plan") if plan_record else "free")
    is_paid = plan_is_paid_active(plan_record)
    plan = stored_plan if is_paid or stored_plan == "free" else "free"

    features = {}
    for feature in ["documentation_generations", "bug_analyzer", "project_health", "task_generator"]:
        used = get_usage_count(user_id, feature, period)
        limit = None if is_paid else FREE_PLAN_LIMITS[feature]
        features[feature] = {
            "label": FEATURE_LABELS[feature],
            "used": used,
            "limit": limit,
            "remaining": None if limit is None else max(limit - used, 0),
            "unlimited": limit is None,
        }

    workspace_count = get_owned_workspace_count(user_id)
    workspace_limit = None if is_paid else FREE_PLAN_LIMITS["workspaces"]

    return {
        "plan": plan,
        "period": period,
        "features": features,
        "workspace": {
            "label": "Workspaces",
            "used": workspace_count,
            "limit": workspace_limit,
            "remaining": None if workspace_limit is None else max(workspace_limit - workspace_count, 0),
            "unlimited": workspace_limit is None,
            "can_create": True if workspace_limit is None else workspace_count < workspace_limit,
        },
        "billing": {
            "stripe_configured": stripe_is_configured(),
            "stored_plan": stored_plan,
            "effective_plan": plan,
            "subscription_status": (plan_record or {}).get("subscription_status") or ("active" if is_paid else "free"),
            "stripe_customer_id": (plan_record or {}).get("stripe_customer_id"),
            "stripe_subscription_id": (plan_record or {}).get("stripe_subscription_id"),
            "current_period_end": (plan_record or {}).get("current_period_end"),
        },
        "upgrade_message": "Upgrade to Pro for unlimited AI usage and more workspaces.",
    }


def usage_limit_response(feature, summary):
    label = FEATURE_LABELS.get(feature, feature)
    return jsonify({
        "error": f"Free plan limit reached for {label}. Upgrade to Pro to continue.",
        "limitReached": True,
        "feature": feature,
        "feature_label": label,
        "usage": summary,
        "upgrade_required": True,
    }), 403


def ensure_usage_allowed(user_id, feature):
    summary = build_usage_summary(user_id)
    plan = summary.get("plan", "free")
    if plan in PAID_PLANS:
        return True, summary

    if feature == "workspaces":
        return summary["workspace"]["can_create"], summary

    feature_usage = summary["features"].get(feature)
    if not feature_usage:
        return True, summary

    return feature_usage["used"] < feature_usage["limit"], summary


def record_usage_event(user_id, feature, metadata=None):
    if not user_id or not feature:
        return
    supabase_request(
        "POST",
        "usage_events",
        {
            "user_id": user_id,
            "feature": feature,
            "period": current_usage_period(),
            "metadata": metadata or {},
        },
        use_service_key=True,
    )


# ── AI wrappers ────────────────────────────────────────────────────────────────
def generate_ai_documentation(code, file_name=""):
    prompt = f"""You are a senior software architect and technical documentation engineer.

Analyze this source code and generate professional documentation.

Include ALL sections:
- Project / File Purpose
- Function and Method Explanations (each function separately)
- Architecture Overview
- API Routes (if any)
- Important Logic
- Dependencies
- Security Observations
- Suggested Improvements
- How to Run / Setup

File Name: {file_name}

Code:
{code}
"""
    result = call_groq(prompt, max_tokens=3000)
    return {"success": True, "doc": result["text"], "error": ""} if result["success"] else {"success": False, "doc": "", "error": result["error"]}


def analyze_bug_with_ai(error_log):
    prompt = (
        "You are a senior production debugging engineer.\n\n"
        "Analyze the error log and return a clear developer-ready bug report.\n\n"
        "Formatting rules:\n"
        "- Plain text only.\n"
        "- Do not use markdown bold.\n"
        "- Do not use double asterisks.\n"
        "- Do not use code fences unless a small code fix is necessary.\n"
        "- Do not give generic advice.\n"
        "- Be specific to the pasted error.\n"
        "- If the error belongs to Apache, Nginx, React, Flask, Python, JavaScript, Node, Railway, Supabase, Stripe, Git, or SQL, name that system clearly.\n"
        "- Explain the likely file or configuration area where the developer should check.\n"
        "- Keep it practical and directly actionable.\n\n"
        "Return exactly these sections:\n\n"
        "Bug Summary\n"
        "Explain the error in simple terms.\n\n"
        "Root Cause\n"
        "Explain the most likely reason this happened.\n\n"
        "Where To Check\n"
        "List the file, config, command, route, dependency, or environment variable the developer should inspect.\n\n"
        "Fix Steps\n"
        "Give numbered steps to fix it.\n\n"
        "Example Fix\n"
        "Provide a short example only if it is useful. Otherwise write: No example fix needed.\n\n"
        "Verification Checklist\n"
        "List how the developer can confirm the issue is fixed.\n\n"
        "Prevention Notes\n"
        "Explain how to avoid the same issue in the future.\n\n"
        f"Error Log:\n{error_log}\n"
    )
    result = call_groq(prompt, max_tokens=2600)
    return {"success": True, "analysis": result["text"]} if result["success"] else {"success": False, "analysis": "", "error": result["error"]}


def generate_tasks_with_ai(requirements):
    prompt = (
        "You are a senior product manager, technical lead, and QA planner.\n\n"
        "Convert the provided requirements or meeting notes into developer-ready implementation tasks.\n\n"
        "Return ONLY a valid JSON array.\n"
        "Do not return markdown.\n"
        "Do not wrap the JSON in code fences.\n"
        "Do not add explanation outside JSON.\n\n"
        "Each task object must include:\n"
        "- \"title\": short action-oriented task title\n"
        "- \"summary\": one sentence explaining the business or product value\n"
        "- \"priority\": \"High\", \"Medium\", or \"Low\"\n"
        "- \"role\": \"Frontend Developer\", \"Backend Developer\", \"Full Stack Developer\", \"QA Engineer\", \"DevOps Engineer\", or \"Product Manager\"\n"
        "- \"feature_area\": short module name such as \"Authentication\", \"Billing\", \"Dashboard\", \"API\", \"UX\", \"Database\"\n"
        "- \"estimated_time\": realistic estimate like \"2-4 hours\", \"1 day\", or \"2-3 days\"\n"
        "- \"user_story\": one user story in this format: As a ..., I want ..., so that ...\n"
        "- \"implementation_notes\": array of 2-4 technical notes\n"
        "- \"subtasks\": array of 3-6 concrete subtasks\n"
        "- \"acceptance_criteria\": array of 3-5 testable done conditions\n"
        "- \"dependencies\": array of blockers or related tasks. Use [] if none.\n"
        "- \"qa_notes\": array of 2-4 testing notes\n"
        "- \"definition_of_done\": array of 2-4 final completion checks\n\n"
        "Task quality rules:\n"
        "- Make tasks clear enough to paste into Jira, Linear, Trello, or ClickUp.\n"
        "- Split frontend, backend, database, QA, and DevOps work where needed.\n"
        "- Avoid vague tasks like Improve UI unless the UI work is clearly described.\n"
        "- Add priority based on user impact and technical dependency.\n"
        "- Keep every string plain text, without markdown bold or double asterisks.\n\n"
        f"Requirements:\n{requirements}\n"
    )
    result = call_groq(prompt, max_tokens=4200)
    if not result["success"]:
        return {"success": False, "tasks": [], "error": result["error"]}

    raw = result["text"].strip()
    raw = re.sub(r"^```(?:json)?", "", raw).strip()
    raw = re.sub(r"```$", "", raw).strip()

    try:
        tasks = json.loads(raw)
        if not isinstance(tasks, list):
            return {"success": False, "tasks": [], "error": "AI returned JSON but not an array."}

        normalized = []
        for task in tasks:
            if not isinstance(task, dict):
                continue
            normalized.append({
                "title": str(task.get("title", "Untitled Task")).strip(),
                "summary": str(task.get("summary", "")).strip(),
                "priority": str(task.get("priority", "Medium")).strip(),
                "role": str(task.get("role", "Full Stack Developer")).strip(),
                "feature_area": str(task.get("feature_area", "General")).strip(),
                "estimated_time": str(task.get("estimated_time", "Not estimated")).strip(),
                "user_story": str(task.get("user_story", "")).strip(),
                "implementation_notes": task.get("implementation_notes", []) if isinstance(task.get("implementation_notes", []), list) else [],
                "subtasks": task.get("subtasks", []) if isinstance(task.get("subtasks", []), list) else [],
                "acceptance_criteria": task.get("acceptance_criteria", []) if isinstance(task.get("acceptance_criteria", []), list) else [],
                "dependencies": task.get("dependencies", []) if isinstance(task.get("dependencies", []), list) else [],
                "qa_notes": task.get("qa_notes", []) if isinstance(task.get("qa_notes", []), list) else [],
                "definition_of_done": task.get("definition_of_done", []) if isinstance(task.get("definition_of_done", []), list) else [],
            })

        return {"success": True, "tasks": normalized}

    except Exception as e:
        return {"success": False, "tasks": [], "error": str(e)}


def v11_split_health_files(code):
    code = str(code or "")
    pattern = re.compile(r"^\s*---\s*FILE:\s*(.*?)\s*---\s*$", re.MULTILINE)
    matches = list(pattern.finditer(code))

    if not matches:
        return [{
            "file_name": "pasted-code.txt",
            "content": code,
            "is_pasted": True,
        }]

    files = []
    for index, match in enumerate(matches):
        file_name = match.group(1).strip() or f"file-{index + 1}.txt"
        content_start = match.end()
        content_end = matches[index + 1].start() if index + 1 < len(matches) else len(code)
        content = code[content_start:content_end].strip()
        files.append({"file_name": file_name, "content": content, "is_pasted": False})

    return files


def v11_count_lines(text):
    if not text:
        return 0
    return len(str(text).splitlines())


def v11_detect_routes_in_file(file_name, content):
    routes = []
    name = str(file_name or "")
    text = str(content or "")

    for route, methods in re.findall(r"@[\w\.]+\.route\(\s*['\"]([^'\"]+)['\"](?:\s*,\s*methods\s*=\s*\[([^\]]+)\])?", text):
        clean_methods = []
        if methods:
            clean_methods = re.findall(r"['\"]([A-Z]+)['\"]", methods)
        method_label = ",".join(clean_methods) if clean_methods else "GET"
        routes.append(f"{method_label} {route} ({name})")

    for method, route in re.findall(r"@(?:app|router)\.(get|post|put|patch|delete)\(\s*['\"]([^'\"]+)['\"]", text, flags=re.I):
        routes.append(f"{method.upper()} {route} ({name})")

    for method, route in re.findall(r"\b(?:app|router)\.(get|post|put|patch|delete)\(\s*['\"]([^'\"]+)['\"]", text, flags=re.I):
        routes.append(f"{method.upper()} {route} ({name})")

    for route in re.findall(r"\b(?:path|re_path)\(\s*['\"]([^'\"]+)['\"]", text):
        routes.append(f"DJANGO {route} ({name})")

    normalized = name.replace("\\", "/").lower()
    if "/pages/api/" in normalized or normalized.startswith("pages/api/"):
        api_path = normalized.split("pages/api/", 1)[-1]
        api_path = "/" + re.sub(r"\.(js|jsx|ts|tsx)$", "", api_path)
        api_path = api_path.replace("/index", "")
        routes.append(f"NEXT_API {api_path} ({name})")

    return sorted(set(routes))


def v11_detect_health_frameworks(files):
    frameworks = set()
    tech_stack = set()

    for file in files:
        name = file["file_name"].lower()
        text = file["content"].lower()

        if name.endswith(".py"):
            tech_stack.add("Python")
        if name.endswith((".js", ".jsx")):
            tech_stack.add("JavaScript")
        if name.endswith((".ts", ".tsx")):
            tech_stack.add("TypeScript")
        if name.endswith(".php"):
            tech_stack.add("PHP")
        if name.endswith(".java"):
            tech_stack.add("Java")
        if name.endswith((".html", ".css")):
            tech_stack.add("HTML/CSS")

        if "from flask" in text or "import flask" in text or "@app.route" in text:
            frameworks.add("Flask")
        if "flask_cors" in text or "from flask_cors" in text:
            frameworks.add("Flask-CORS")
        if "from fastapi" in text or "import fastapi" in text:
            frameworks.add("FastAPI")
        if "django" in text or "manage.py" in name:
            frameworks.add("Django")
        if "import react" in text or "from 'react'" in text or 'from "react"' in text:
            frameworks.add("React")
        if "express()" in text or "from 'express'" in text or 'require("express")' in text or "require('express')" in text:
            frameworks.add("Express")
        if "next/" in text or "next.config" in name:
            frameworks.add("Next.js")
        if "@supabase/supabase-js" in text or "supabase" in text:
            frameworks.add("Supabase")
        if "stripe" in text:
            frameworks.add("Stripe")
        if "railway" in name or "procfile" in name:
            frameworks.add("Railway/Procfile")

    return sorted(tech_stack), sorted(frameworks)


def v11_detect_health_scope(code, files):
    total_files = len(files)
    total_lines = sum(v11_count_lines(file["content"]) for file in files)
    names = [file["file_name"].replace("\\", "/").lower() for file in files]
    has_markers = bool(re.search(r"^\s*---\s*FILE:", str(code or ""), re.MULTILINE))

    project_indicators = {
        "package.json", "requirements.txt", "pyproject.toml", "manage.py", "app.py",
        "procfile", "railway.json", "dockerfile", "docker-compose.yml", "vite.config.js",
        "next.config.js", "tsconfig.json", "src/app.js", "src/app.jsx", "src/main.jsx",
    }

    has_project_file = any(name.split("/")[-1] in project_indicators or name in project_indicators for name in names)
    has_nested_paths = sum(1 for name in names if "/" in name) >= 3
    looks_like_github_doc = "github repository:" in str(code or "").lower() or "files documented:" in str(code or "").lower()

    if looks_like_github_doc:
        return {
            "scope": "github_repo",
            "review_type": "Full Project Health Report",
            "score_label": "Project Health Score",
            "production_relevance": "Full project/repository review",
            "scope_note": "This review is based on the GitHub repository or repository documentation available to DevFlow.",
        }

    if total_files == 1:
        content = files[0]["content"]
        line_count = v11_count_lines(content)
        if not has_markers and line_count <= 80:
            return {
                "scope": "pasted_snippet",
                "review_type": "Code Quality Review",
                "score_label": "Code Quality Score",
                "production_relevance": "Production readiness is not applicable to a small snippet.",
                "scope_note": "This is a focused review of pasted code, not a full project health report.",
            }

        return {
            "scope": "single_file",
            "review_type": "File Health Review",
            "score_label": "File Quality Score",
            "production_relevance": "Production readiness is limited because only one file was provided.",
            "scope_note": "This review is limited to one uploaded file and should not be treated as a full project audit.",
        }

    if total_files >= 8 or total_lines >= 700 or has_project_file or has_nested_paths:
        return {
            "scope": "full_project",
            "review_type": "Full Project Health Report",
            "score_label": "Project Health Score",
            "production_relevance": "Full project-level production readiness can be assessed from the uploaded project structure.",
            "scope_note": "This review is based on the uploaded project files and visible source code.",
        }

    return {
        "scope": "multi_file",
        "review_type": "Project Health Snapshot",
        "score_label": "Snapshot Health Score",
        "production_relevance": "Production readiness is only a snapshot because the uploaded files may not represent the complete project.",
        "scope_note": "This is a partial project snapshot based only on the uploaded files.",
    }


def v11_build_health_context(code):
    files = v11_split_health_files(code)
    tech_stack, frameworks = v11_detect_health_frameworks(files)
    routes = []
    important_files = []

    for file in files:
        file_routes = v11_detect_routes_in_file(file["file_name"], file["content"])
        routes.extend(file_routes)
        lower_name = file["file_name"].lower()

        if (
            file_routes
            or lower_name.endswith(("app.py", "main.py", "server.js", "index.js", "app.js", "manage.py"))
            or lower_name.split("/")[-1] in {"package.json", "requirements.txt", "procfile", "railway.json", "dockerfile"}
        ):
            important_files.append(file["file_name"])

    if not important_files:
        important_files = [file["file_name"] for file in files[:6]]

    scope = v11_detect_health_scope(code, files)

    return {
        **scope,
        "files": files,
        "total_files": len(files),
        "total_lines": sum(v11_count_lines(file["content"]) for file in files),
        "tech_stack": sorted(set(tech_stack)),
        "frameworks": sorted(set(frameworks)),
        "routes": sorted(set(routes)),
        "important_files": sorted(set(important_files)),
    }


def generate_health_report_with_ai(code):
    context = v11_build_health_context(code)

    routes_text = "\n".join("- " + route for route in context["routes"]) or "- No routes detected in the provided input."
    frameworks_text = ", ".join(context["frameworks"]) or "None detected"
    stack_text = ", ".join(context["tech_stack"]) or "Unknown"
    important_files_text = "\n".join("- " + name for name in context["important_files"]) or "- None detected"

    if context["scope"] == "pasted_snippet":
        scoring_instruction = (
            "This is pasted code or a small function. Do not call it a project. "
            "Do not give project production readiness. Review only code clarity, correctness, maintainability, edge cases, and risk."
        )
    elif context["scope"] == "single_file":
        scoring_instruction = (
            "This is one uploaded file. Do not call it a full project. "
            "Review file quality, visible framework usage, visible routes, visible error handling, and risks in this file only."
        )
    elif context["scope"] == "multi_file":
        scoring_instruction = (
            "This is a partial multi-file upload. Call it a project snapshot, not a complete audit. "
            "Only assess what is visible in the uploaded files."
        )
    else:
        scoring_instruction = (
            "This looks like a full project or repository. You may assess architecture, production readiness, security, testing, and deployment."
        )

    prompt = (
        "You are a senior software architect, DevOps reviewer, and code quality engineer.\n\n"
        "Analyze the provided code using the detected input scope below. Return a realistic health review.\n\n"
        "Strict rules:\n"
        "- Return ONLY valid JSON.\n"
        "- Do not return markdown.\n"
        "- Do not wrap JSON in code fences.\n"
        "- Do not invent risks that are not supported by the code.\n"
        "- Do not mention SQL injection unless SQL/database query code is visible.\n"
        "- Do not mention XSS unless frontend user-rendered content or HTML injection risk is visible.\n"
        "- Do not recommend changing frameworks unless there is a clear reason.\n"
        "- Do not say microservices unless multiple independently deployable services are visible.\n"
        "- If routes are listed in Rule-based Detected Routes, include them in the routes array.\n"
        "- If no routes are visible, say no routes detected in this input, not no routes exist in the full project.\n\n"
        f"Detected input scope: {context['scope']}\n"
        f"Required review title: {context['review_type']}\n"
        f"Score label: {context['score_label']}\n"
        f"Scope note: {context['scope_note']}\n"
        f"Production relevance: {context['production_relevance']}\n"
        f"Scoring instruction: {scoring_instruction}\n"
        f"Total files visible: {context['total_files']}\n"
        f"Total lines visible: {context['total_lines']}\n"
        f"Rule-based tech stack: {stack_text}\n"
        f"Rule-based frameworks: {frameworks_text}\n"
        f"Rule-based important files:\n{important_files_text}\n"
        f"Rule-based detected routes:\n{routes_text}\n\n"
        "Return exactly these JSON keys:\n"
        "- \"review_type\": string\n"
        "- \"scope\": string\n"
        "- \"scope_note\": string\n"
        "- \"score_label\": string\n"
        "- \"score\": string like \"78/100\" or \"Not scored\" if score is not appropriate\n"
        "- \"production_readiness\": \"Not applicable\", \"Not Ready\", \"Almost Ready\", or \"Production Ready\"\n"
        "- \"score_explanation\": array of 3-6 short reasons explaining the score\n"
        "- \"total_files_detected\": integer\n"
        "- \"tech_stack\": array of detected technologies\n"
        "- \"detected_frameworks\": array of detected frameworks/libraries\n"
        "- \"routes\": array of detected route strings or a single honest no-routes message\n"
        "- \"important_files\": array of important file names or modules\n"
        "- \"architecture_notes\": array of architecture observations that match the input scope\n"
        "- \"issues\": array of concrete issues found\n"
        "- \"security_risks\": array of concrete security risks found\n"
        "- \"priority_fixes\": array of highest priority fixes\n"
        "- \"suggestions\": array of practical improvements\n"
        "- \"testing_notes\": array of testing recommendations\n\n"
        "Score guidance:\n"
        "- For pasted_snippet: score code quality only, not deployment readiness.\n"
        "- For single_file: score file quality only, not the full project.\n"
        "- For multi_file: score the visible snapshot only and mention incomplete scope.\n"
        "- For full_project/github_repo: score full project health.\n\n"
        f"Code:\n{str(code or '')[:15000]}\n"
    )

    result = call_groq(prompt, max_tokens=3600)
    if not result["success"]:
        return {"success": False, "report": None, "error": result["error"]}

    raw = result["text"].strip()
    raw = re.sub(r"^```(?:json)?", "", raw).strip()
    raw = re.sub(r"```$", "", raw).strip()

    try:
        report = json.loads(raw)
        if not isinstance(report, dict):
            return {"success": False, "report": None, "error": "AI returned JSON but not an object."}

        report["review_type"] = context["review_type"]
        report["scope"] = context["scope"]
        report["scope_note"] = context["scope_note"]
        report["score_label"] = context["score_label"]
        report["total_files_detected"] = context["total_files"]

        if context["scope"] in {"pasted_snippet", "single_file"}:
            report["production_readiness"] = "Not applicable"

        if context["tech_stack"] and not report.get("tech_stack"):
            report["tech_stack"] = context["tech_stack"]

        if context["frameworks"] and not report.get("detected_frameworks"):
            report["detected_frameworks"] = context["frameworks"]

        ai_routes = report.get("routes") if isinstance(report.get("routes"), list) else []
        route_text = " ".join(str(x).lower() for x in ai_routes)
        if context["routes"] and (not ai_routes or "no route" in route_text):
            report["routes"] = context["routes"]
        elif not context["routes"] and not ai_routes:
            if context["scope"] == "pasted_snippet":
                report["routes"] = ["No routes detected in this pasted code."]
            elif context["scope"] == "single_file":
                report["routes"] = ["No routes detected in this file."]
            elif context["scope"] == "multi_file":
                report["routes"] = ["No routes detected in the uploaded file snapshot."]
            else:
                report["routes"] = ["No routes detected in the visible project files."]

        if context["important_files"] and not report.get("important_files"):
            report["important_files"] = context["important_files"]

        report.setdefault("score", "Not scored")
        report.setdefault("score_explanation", [])
        report.setdefault("architecture_notes", [])
        report.setdefault("issues", [])
        report.setdefault("security_risks", [])
        report.setdefault("priority_fixes", [])
        report.setdefault("suggestions", [])
        report.setdefault("testing_notes", [])

        return {"success": True, "report": report}

    except Exception as e:
        return {"success": False, "report": None, "error": str(e)}




# ── Smart GitHub documentation helpers ────────────────────────────────────────
def github_path_parts(path: str):
    return [part.strip().lower() for part in path.replace("\\", "/").split("/") if part.strip()]


def should_skip_github_path(path: str) -> bool:
    """Return True when a repo file is generated, vendor, build, asset, or low-value noise."""
    normalized = path.replace("\\", "/")
    lower_path = normalized.lower()
    parts = github_path_parts(normalized)
    file_name = parts[-1] if parts else lower_path

    ignored_dirs = {
        "node_modules", "venv", ".venv", "env", ".env", "__pycache__", ".git",
        "dist", "build", ".next", "out", "coverage", ".cache", ".pytest_cache",
        ".mypy_cache", ".idea", ".vscode", "vendor", "target", "bin", "obj",
        "assets", "media", "staticfiles", "public/build", "logs", "tmp", "temp",
        "migrations", "__snapshots__"
    }

    if any(part in ignored_dirs for part in parts):
        return True

    # Skip Django/admin generated files and other generated frontend bundles.
    ignored_substrings = [
        "staticfiles/admin/", "static/admin/", "/admin/css/", "/admin/js/",
        ".min.js", ".min.css", ".bundle.js", ".bundle.css", ".chunk.js",
        "compiled", "generated", "vendor/", "bootstrap.min", "jquery.min"
    ]
    if any(item in lower_path for item in ignored_substrings):
        return True

    ignored_exact_files = {
        "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "composer.lock", "poetry.lock",
        "pipfile.lock", "cargo.lock", ".ds_store", "thumbs.db", "db.sqlite3",
        "sqlite.db", "database.sqlite", ".env", ".env.local", ".env.production"
    }
    if file_name in ignored_exact_files:
        return True

    ignored_exts = {
        ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp", ".avif",
        ".pdf", ".zip", ".rar", ".7z", ".tar", ".gz", ".exe", ".dll", ".so",
        ".mp4", ".mp3", ".wav", ".mov", ".woff", ".woff2", ".ttf", ".eot",
        ".map", ".pyc", ".pyo", ".class", ".log"
    }
    ext = os.path.splitext(lower_path)[1]
    if ext in ignored_exts:
        return True

    return False


def is_allowed_github_source_file(path: str) -> bool:
    lower_path = path.lower()
    file_name = os.path.basename(lower_path)

    allowed_exts = {
        ".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css", ".sql", ".php",
        ".java", ".cpp", ".c", ".h", ".cs", ".kt", ".swift", ".dart", ".md",
        ".json", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".txt", ".sh"
    }
    important_files_without_ext = {
        "dockerfile", "makefile", "procfile", "readme", "license", "requirements"
    }

    ext = os.path.splitext(lower_path)[1]
    return ext in allowed_exts or file_name in important_files_without_ext


def github_file_priority(path: str) -> int:
    """Lower number means higher importance for repo documentation."""
    lower_path = path.replace("\\", "/").lower()
    file_name = os.path.basename(lower_path)

    top_priority_files = {
        "readme.md", "readme", "package.json", "requirements.txt", "pyproject.toml",
        "pipfile", "dockerfile", "docker-compose.yml", "docker-compose.yaml", "manage.py",
        "app.py", "main.py", "server.py", "index.js", "app.js", "main.js", "main.tsx",
        "index.tsx", "settings.py", "urls.py"
    }
    if file_name in top_priority_files:
        return 0

    important_backend = [
        "models.py", "views.py", "serializers.py", "forms.py", "admin.py", "routes.py",
        "controllers", "services", "repositories", "schemas", "api", "auth", "middleware"
    ]
    if any(item in lower_path for item in important_backend):
        return 1

    important_frontend = [
        "src/app", "src/index", "src/main", "pages/", "app/", "components/", "layouts/",
        "routes/", "store/", "hooks/", "utils/", "lib/"
    ]
    if any(item in lower_path for item in important_frontend):
        return 2

    config_patterns = [
        "config", "settings", "requirements", "package", "vite", "next.config", "tailwind",
        "tsconfig", "eslint", "webpack", "babel", "supabase", "firebase"
    ]
    if any(item in lower_path for item in config_patterns):
        return 3

    tests_patterns = ["test", "tests", "spec", "__tests__"]
    if any(item in lower_path for item in tests_patterns):
        return 5

    return 4


def select_smart_github_files(tree_items):
    """Filter and rank GitHub tree items so DevFlow documents valuable source files first."""
    max_files = int(os.getenv("GITHUB_MAX_FILES", "32"))
    candidates = []
    skipped = 0

    for item in tree_items:
        if item.get("type") != "blob":
            continue
        path = item.get("path", "")
        if not path:
            continue
        if should_skip_github_path(path):
            skipped += 1
            continue
        if not is_allowed_github_source_file(path):
            skipped += 1
            continue
        candidates.append({"path": path, "url": item.get("url", ""), "priority": github_file_priority(path)})

    candidates.sort(key=lambda file: (file["priority"], len(file["path"].split("/")), len(file["path"]), file["path"].lower()))
    selected = candidates[:max_files]
    return selected, len(candidates), skipped


def generate_github_repo_documentation_fast(code: str, repo_name: str, file_count: int, branch: str = "main", candidate_count: int = 0, skipped_count: int = 0) -> dict:
    """
    Fast GitHub documentation mode.
    It creates one repo-level architecture report from the most important files,
    then appends a clear file inventory. This avoids slow per-file AI calls.
    """
    uploaded_files = split_multiple_files(code) or [{"file_name": repo_name, "content": code}]

    languages = []
    file_inventory = []
    total_lines = 0

    for file in uploaded_files:
        file_name = file["file_name"]
        content = file["content"]
        language = detect_language(content, file_name)
        metrics = get_file_metrics(content)
        total_lines += metrics["total_lines"]
        languages.append(language)
        file_inventory.append(
            f"- {file_name} | {language} | {metrics['total_lines']} lines | {metrics['non_empty_lines']} non-empty"
        )

    # Already sorted by smart priority in fetch_github_repo_files, so first files are the best files.
    key_files = uploaded_files[:12]

    preview_parts = []
    preview_budget = 18000
    used_chars = 0
    for file in key_files:
        file_header = f"--- FILE: {file['file_name']} ---\n"
        available = max(1200, min(3500, preview_budget - used_chars - len(file_header)))
        if available <= 0:
            break
        snippet = file["content"][:available]
        preview_parts.append(file_header + snippet)
        used_chars += len(file_header) + len(snippet)
        if used_chars >= preview_budget:
            break

    key_code_preview = "\n\n".join(preview_parts)
    inventory_preview = "\n".join(file_inventory[:60])

    if is_ai_enabled():
        prompt = f"""You are a senior software architect creating documentation for a software company.

Create a professional GitHub repository documentation report.
Focus on useful company onboarding, architecture understanding, and developer handover.
Do not write line-by-line docs. Explain the system clearly.

Repository: {repo_name}
Branch: {branch}
Smart selected files analyzed: {file_count}
Candidate source files found after filtering: {candidate_count or file_count}
Generated/noise files skipped: {skipped_count}
Total selected source lines: {total_lines}
Detected languages: {', '.join(sorted(set(languages)))}

Return these sections:
1. Executive Summary
2. What This Project Does
3. Technology Stack
4. Architecture Overview
5. Important Files and Their Purpose
6. Main Workflows
7. API / Route Observations
8. Setup and Run Guide
9. Security Observations
10. Improvement Roadmap
11. Onboarding Notes for New Developers

Smart file inventory:
{inventory_preview}

Important selected file content:
{key_code_preview}
"""
        ai_result = call_groq(prompt, max_tokens=3500)
        if ai_result["success"]:
            repo_summary = ai_result["text"]
        else:
            repo_summary = (
                "AI Notice: Groq failed, so DevFlow generated a fast rule-based GitHub report.\n"
                f"Reason: {ai_result['error']}\n\n"
                "Repository Overview\n"
                f"- Repository: {repo_name}\n"
                f"- Branch: {branch}\n"
                f"- Smart selected files: {file_count}\n"
                f"- Candidate source files: {candidate_count or file_count}\n"
                f"- Generated/noise files skipped: {skipped_count}\n"
                f"- Total selected source lines: {total_lines}\n"
                f"- Detected languages: {', '.join(sorted(set(languages)))}\n"
            )
    else:
        repo_summary = (
            "Repository Overview\n"
            f"- Repository: {repo_name}\n"
            f"- Branch: {branch}\n"
            f"- Smart selected files: {file_count}\n"
            f"- Candidate source files: {candidate_count or file_count}\n"
            f"- Generated/noise files skipped: {skipped_count}\n"
            f"- Total selected source lines: {total_lines}\n"
            f"- Detected languages: {', '.join(sorted(set(languages)))}\n\n"
            "Enable Groq AI to generate a full architecture summary."
        )

    final_doc = "\n".join([
        f"GitHub Repository: {repo_name}",
        f"Branch: {branch}",
        f"Smart Selected Files: {file_count}",
        f"Candidate Source Files: {candidate_count or file_count}",
        f"Generated / Noise Files Skipped: {skipped_count}",
        f"Total Selected Source Lines: {total_lines}",
        "Documentation Mode: Smart Fast Repository Summary",
        "",
        "========================================",
        "REPOSITORY SUMMARY",
        "========================================",
        "",
        repo_summary,
        "",
        "========================================",
        "SMART FILE INVENTORY",
        "========================================",
        "",
        "\n".join(file_inventory),
        "",
        "========================================",
        "DEVFLOW NOTE",
        "========================================",
        "",
        "DevFlow used smart GitHub filtering. Generated/static/vendor files were skipped, and the most important source files were prioritized for faster, cleaner company onboarding documentation."
    ])

    return {
        "doc": final_doc,
        "language": ", ".join(sorted(set(languages))),
        "file_count": file_count,
        "candidate_count": candidate_count or file_count,
        "skipped_count": skipped_count,
        "aiEnabled": is_ai_enabled(),
    }

# ── GitHub integration ─────────────────────────────────────────────────────────
def fetch_github_repo_files(repo_url: str, github_token: str = "") -> dict:
    """
    Fetch important source files from a public or private GitHub repo URL.
    DevFlow uses smart filtering to skip generated/vendor/static files and prioritize
    files that explain architecture, routes, models, config, setup, and core workflows.
    """
    pattern = r"github\.com/([^/]+)/([^/]+)"
    match = re.search(pattern, repo_url)
    if not match:
        return {"success": False, "error": "Invalid GitHub URL. Use format: https://github.com/username/reponame"}

    owner = match.group(1)
    repo = match.group(2).replace(".git", "").rstrip("/")

    headers = {"Accept": "application/vnd.github+json"}
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"

    try:
        repo_response = requests.get(f"https://api.github.com/repos/{owner}/{repo}", headers=headers, timeout=15)
        repo_info = repo_response.json()
        if repo_response.status_code >= 400:
            return {"success": False, "error": repo_info.get("message", "Repository not found or GitHub API error.")}
        default_branch = repo_info.get("default_branch", "main")
    except Exception as e:
        return {"success": False, "error": f"Could not reach GitHub API: {e}"}

    try:
        tree_url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{default_branch}?recursive=1"
        tree_response = requests.get(tree_url, headers=headers, timeout=20)
        tree_resp = tree_response.json()
        if tree_response.status_code >= 400:
            return {"success": False, "error": tree_resp.get("message", "Could not fetch repository tree.")}
        tree = tree_resp.get("tree", [])
        tree_truncated = bool(tree_resp.get("truncated", False))
    except Exception as e:
        return {"success": False, "error": f"Could not fetch repo tree: {e}"}

    source_files, candidate_count, skipped_count = select_smart_github_files(tree)

    if not source_files:
        return {"success": False, "error": "No useful source code files found after smart filtering."}

    max_file_chars = int(os.getenv("GITHUB_MAX_FILE_CHARS", "80000"))
    all_code_parts = []
    fetched = 0

    for file_info in source_files:
        path = file_info["path"]
        try:
            # Use GitHub contents API first because it supports private repos with a token.
            contents_headers = dict(headers)
            contents_headers["Accept"] = "application/vnd.github.raw"
            contents_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={default_branch}"
            content_resp = requests.get(contents_url, headers=contents_headers, timeout=12)

            if content_resp.status_code != 200:
                # Fallback for public repos.
                raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{default_branch}/{path}"
                content_resp = requests.get(raw_url, headers=headers, timeout=12)

            if content_resp.status_code == 200:
                content = content_resp.text
                if content and len(content) <= max_file_chars:
                    all_code_parts.append(f"--- FILE: {path} ---\n{content}")
                    fetched += 1
        except Exception:
            continue

    if not all_code_parts:
        return {"success": False, "error": "Could not read any selected source files from the repository."}

    combined_code = "\n\n".join(all_code_parts)

    return {
        "success": True,
        "code": combined_code,
        "file_count": fetched,
        "candidate_count": candidate_count,
        "skipped_count": skipped_count,
        "tree_truncated": tree_truncated,
        "repo_name": f"{owner}/{repo}",
        "branch": default_branch,
    }


# ── Rule-based fallbacks ───────────────────────────────────────────────────────
def detect_language(code, file_name=""):
    lc = code.lower().strip()
    lf = file_name.lower().strip()
    ext_map = {
        ".py":"Python",".jsx":"React",".tsx":"React / TypeScript",".ts":"TypeScript",
        ".js":"JavaScript",".html":"HTML",".css":"CSS",".sql":"SQL",".php":"PHP",
        ".java":"Java",".cpp":"C/C++",".c":"C/C++",".h":"C/C++",".cs":"C#",
        ".kt":"Kotlin",".swift":"Swift",".dart":"Flutter",
    }
    for ext, lang in ext_map.items():
        if lf.endswith(ext): return lang
    if "def " in code and ":" in code: return "Python"
    if any(x in lc for x in ["from 'react'", 'from "react"']): return "React"
    if any(x in lc for x in ["function ", "const ", "let "]): return "JavaScript"
    if any(x in lc for x in ["<html", "<div", "<body"]): return "HTML"
    if "{" in code and "color:" in lc: return "CSS"
    if "select " in lc or "insert into" in lc: return "SQL"
    return "Unknown"


def detect_return_type(node):
    if isinstance(node, ast.Constant): return type(node.value).__name__
    if isinstance(node, ast.BinOp): return "number"
    if isinstance(node, ast.Dict): return "dict"
    if isinstance(node, ast.List): return "list"
    return "value"


def generate_function_doc(func):
    name = func.name
    args = [a.arg for a in func.args.args]
    returns = [detect_return_type(n.value) for n in ast.walk(func) if isinstance(n, ast.Return) and n.value]
    params = "\n".join([f"- `{a}`: input parameter" for a in args]) if args else "- No parameters."
    example = f"{name}({', '.join(['1' for _ in args])})"
    return "\n".join([
        f"## `{name}`", "", "### Parameters", params, "",
        "### Returns", f"- `{returns[0] if returns else 'None'}`", "",
        "### Example", "```python", example, "```",
    ])


def analyze_single_file(code, file_name=""):
    language = detect_language(code, file_name)
    if language not in ("Python", "Unknown"):
        return f"{language} File\n\nLanguage: {language}\n\nEnable Groq AI for full documentation.", language
    try:
        tree = ast.parse(code)
        fns = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
        if len(fns) > 8:
            return "Large Python File\n\nFunctions:\n" + "\n".join([f"- `{n.name}`" for n in fns]), language
        docs = [generate_function_doc(n) for n in fns]
        return ("\n\n".join(docs) if docs else "No functions found.", language)
    except:
        return "Could not parse file.", "Unknown"


def split_multiple_files(code):
    matches = list(re.finditer(r"^--- FILE: (.*?) ---$", code, re.MULTILINE))
    if not matches: return []
    files = []
    for i, m in enumerate(matches):
        end = matches[i+1].start() if i+1 < len(matches) else len(code)
        files.append({"file_name": m.group(1).strip(), "content": code[m.end():end].strip()})
    return files


def get_file_metrics(code):
    lines = code.splitlines()
    return {"total_lines": len(lines), "non_empty_lines": len([l for l in lines if l.strip()])}


def generate_project_health_report(code):
    lc = code.lower()
    issues, suggestions = [], []
    routes = re.findall(r'@app\.route\(["\'](.*?)["\']', code)
    if "readme" not in lc: issues.append("README file may be missing.")
    if "requirements.txt" not in lc and "package.json" not in lc: issues.append("Dependency file missing.")
    if "secret_key" in lc: issues.append("Possible secret key exposure.")
    suggestions += ["Add onboarding docs.", "Add error handling.", "Add automated tests."]
    score = max(100 - len(issues) * 10, 40)
    return {
        "total_files_detected": len(re.findall(r"^--- FILE:", code, re.MULTILINE)),
        "routes": routes, "issues": issues, "suggestions": suggestions,
        "security_risks": [], "production_readiness": "Needs Work" if score < 70 else "Almost Ready",
        "tech_stack": [], "score": f"{score}/100",
    }


def generate_task_plan_fallback(requirements_text):
    lines = [l.strip("-•1234567890. ") for l in requirements_text.splitlines() if l.strip()]
    tasks = []
    for i, line in enumerate(lines, 1):
        lw = line.lower()
        priority = "High" if any(w in lw for w in ["login","auth","payment","api","database"]) else "Medium" if any(w in lw for w in ["ui","design","form"]) else "Low"
        role = "Backend Developer" if any(w in lw for w in ["api","database","backend"]) else "Frontend Developer" if any(w in lw for w in ["ui","design","page"]) else "Full Stack Developer"
        tasks.append({
            "title": f"Task {i}: {line[:70]}", "priority": priority, "role": role,
            "estimated_time": "2-4 hours",
            "subtasks": ["Understand requirement","Plan implementation","Develop feature","Test changes"],
            "acceptance_criteria": ["Feature works","No breaking errors","Code is readable"],
        })
    return tasks


def rule_based_bug_analysis(error_log):
    lw = error_log.lower()
    if "quota" in lw or "429" in lw:
        return "Error: API quota exceeded.\n\nFix:\n1. Check billing.\n2. Use a different API key.\n3. Wait for quota reset."
    if "not defined" in lw:
        return "Error: Variable or function used before it exists.\n\nFix:\n1. Check spelling.\n2. Confirm it is imported.\n3. Check scope."
    if "connection" in lw or "database" in lw:
        return "Error: Database connection failed.\n\nFix:\n1. Check DB credentials in .env.\n2. Confirm DB server is running."
    if "no module" in lw or "modulenotfound" in lw:
        return "Error: Package not installed.\n\nFix:\n1. Run: pip install <package-name>\n2. Activate your virtual environment."
    return "Error detected.\n\nFix:\n1. Read the error line carefully.\n2. Check file and line number.\n3. Search the exact message online.\n4. Confirm all environment variables are set."


# ═══════════════════════════════════════════════════════════════════════════════
# AUTH ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/auth/signup", methods=["POST"])
def signup():
    data = request.get_json(silent=True) or {}
    email = data.get("email","").strip()
    password = data.get("password","").strip()
    full_name = data.get("full_name","").strip()
    if not email or not password:
        return jsonify({"error": "Email and password are required."}), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters."}), 400
    headers = {"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"}
    payload = {"email": email, "password": password, "data": {"full_name": full_name}}
    try:
        r = requests.post(f"{SUPABASE_URL}/auth/v1/signup", headers=headers, json=payload, timeout=15)
        result = r.json()
        if r.status_code >= 400:
            return jsonify({"error": result.get("msg", result.get("error_description", "Signup failed."))}), 400
        return jsonify({"success": True, "message": "Account created! Please log in.", "user": result.get("user")}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/auth/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    email = data.get("email","").strip()
    password = data.get("password","").strip()
    if not email or not password:
        return jsonify({"error": "Email and password are required."}), 400
    headers = {"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"}
    try:
        r = requests.post(f"{SUPABASE_URL}/auth/v1/token?grant_type=password", headers=headers,
                          json={"email": email, "password": password}, timeout=15)
        result = r.json()
        if r.status_code >= 400:
            return jsonify({"error": result.get("error_description", "Invalid email or password.")}), 401
        return jsonify({
            "success": True,
            "access_token": result.get("access_token"),
            "refresh_token": result.get("refresh_token"),
            "user": result.get("user"),
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/auth/refresh", methods=["POST"])
def refresh_auth_token():
    data = request.get_json(silent=True) or {}
    refresh_token = data.get("refresh_token", "").strip()
    if not refresh_token:
        return jsonify({"error": "Refresh token is required."}), 400

    headers = {"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"}
    try:
        r = requests.post(
            f"{SUPABASE_URL}/auth/v1/token?grant_type=refresh_token",
            headers=headers,
            json={"refresh_token": refresh_token},
            timeout=15,
        )
        result = r.json()
        if r.status_code >= 400:
            return jsonify({"error": result.get("error_description", "Session expired. Please log in again.")}), 401
        return jsonify({
            "success": True,
            "access_token": result.get("access_token"),
            "refresh_token": result.get("refresh_token"),
            "user": result.get("user"),
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/auth/me", methods=["GET"])
@require_auth
def get_me():
    return jsonify({"user": request.user}), 200


# ═══════════════════════════════════════════════════════════════════════════════
# BILLING / USAGE ROUTES — PHASE 4
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/billing/usage", methods=["GET"])
@require_auth
def billing_usage():
    return jsonify({"success": True, "usage": build_usage_summary(request.user["id"])}), 200


@app.route("/billing/demo-upgrade", methods=["POST"])
@require_auth
def billing_demo_upgrade():
    """Development-only upgrade helper.

    This lets you test the Pro/Team UI before Stripe is connected.
    Set ALLOW_DEMO_PLAN_CHANGE=false in production.
    """
    allow_demo = os.getenv("ALLOW_DEMO_PLAN_CHANGE", "false").lower() == "true"
    if not allow_demo:
        return jsonify({"error": "Demo plan changes are disabled."}), 403

    data = request.get_json(silent=True) or {}
    plan = normalize_plan(data.get("plan", "free"))
    if plan not in {"free", "pro", "team"}:
        return jsonify({"error": "Invalid plan."}), 400

    existing = supabase_request(
        "GET",
        f"user_plans?user_id=eq.{request.user['id']}&select=user_id",
        use_service_key=True,
    )

    payload = {"user_id": request.user["id"], "plan": plan}
    if isinstance(existing, list) and existing:
        result = supabase_request(
            "PATCH",
            f"user_plans?user_id=eq.{request.user['id']}",
            {"plan": plan},
            use_service_key=True,
        )
    else:
        result = supabase_request("POST", "user_plans", payload, use_service_key=True)

    if isinstance(result, dict) and "error" in result:
        return jsonify({"error": str(result["error"])}), 400

    return jsonify({
        "success": True,
        "message": f"Demo plan changed to {plan.title()}.",
        "usage": build_usage_summary(request.user["id"]),
    }), 200




@app.route("/billing/create-checkout-session", methods=["POST"])
@require_auth
def billing_create_checkout_session():
    data = request.get_json(silent=True) or {}
    plan = normalize_plan(data.get("plan", "pro"))

    if plan not in {"pro", "team"}:
        return jsonify({"error": "Only Pro and Team plans can be purchased through Stripe."}), 400

    price_id = stripe_price_for_plan(plan)
    if not STRIPE_SECRET_KEY:
        return jsonify({"error": "Stripe is not configured. Add STRIPE_SECRET_KEY to your .env file."}), 400
    if not price_id:
        return jsonify({"error": f"Stripe price ID for {plan.title()} is missing. Add STRIPE_{plan.upper()}_PRICE_ID to your .env file."}), 400

    user = request.user
    success_url = f"{FRONTEND_URL}?stripe_success=true&session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = f"{FRONTEND_URL}?stripe_cancelled=true"

    session = stripe_api_request("POST", "checkout/sessions", {
        "mode": "subscription",
        "success_url": success_url,
        "cancel_url": cancel_url,
        "customer_email": user.get("email", ""),
        "client_reference_id": user.get("id"),
        "line_items[0][price]": price_id,
        "line_items[0][quantity]": "1",
        "allow_promotion_codes": "true",
        "metadata[user_id]": user.get("id"),
        "metadata[plan]": plan,
        "subscription_data[metadata][user_id]": user.get("id"),
        "subscription_data[metadata][plan]": plan,
    })

    if isinstance(session, dict) and session.get("error"):
        return jsonify({"error": session.get("error")}), 400

    return jsonify({
        "success": True,
        "checkout_url": session.get("url"),
        "session_id": session.get("id"),
    }), 200


@app.route("/billing/sync-session", methods=["POST"])
@require_auth
def billing_sync_session():
    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id", "").strip()
    if not session_id:
        return jsonify({"error": "Missing Stripe session id."}), 400

    session = stripe_api_request("GET", f"checkout/sessions/{session_id}")
    if isinstance(session, dict) and session.get("error"):
        return jsonify({"error": session.get("error")}), 400

    session_user_id = session.get("client_reference_id") or (session.get("metadata") or {}).get("user_id")
    if session_user_id != request.user["id"]:
        return jsonify({"error": "This Stripe session does not belong to the logged-in user."}), 403

    synced = sync_plan_from_checkout_session(session)
    if synced.get("error"):
        return jsonify({"error": str(synced.get("error"))}), 400

    return jsonify({
        "success": True,
        "message": f"Subscription activated: {synced.get('plan', 'pro').title()} plan.",
        "usage": build_usage_summary(request.user["id"]),
    }), 200


@app.route("/billing/refresh-subscription", methods=["POST"])
@require_auth
def billing_refresh_subscription():
    """Production-safe billing refresh.

    Use this after returning from Stripe Customer Portal or when testing local webhooks.
    It asks Stripe for the latest subscription status and updates Supabase.
    """
    if not STRIPE_SECRET_KEY:
        return jsonify({"error": "Stripe is not configured. Add STRIPE_SECRET_KEY to your .env file."}), 400

    plan_record = get_user_plan_record(request.user["id"]) or {}
    subscription_id = plan_record.get("stripe_subscription_id")

    if not subscription_id:
        return jsonify({
            "success": True,
            "message": "No Stripe subscription found for this user yet.",
            "usage": build_usage_summary(request.user["id"]),
        }), 200

    subscription = stripe_api_request("GET", f"subscriptions/{subscription_id}")
    if isinstance(subscription, dict) and subscription.get("error"):
        return jsonify({"error": subscription.get("error")}), 400

    # Ensure metadata exists even if Stripe returns an older subscription object.
    subscription.setdefault("metadata", {})
    subscription["metadata"]["user_id"] = request.user["id"]
    subscription["metadata"].setdefault("plan", plan_record.get("plan") or "free")

    synced = sync_plan_from_subscription(subscription)
    if synced.get("error"):
        return jsonify({"error": synced["error"]}), 400

    return jsonify({
        "success": True,
        "message": f"Stripe subscription synced. Current plan: {synced.get('plan', 'free').title()}.",
        "usage": build_usage_summary(request.user["id"]),
    }), 200


@app.route("/billing/create-portal-session", methods=["POST"])
@require_auth
def billing_create_portal_session():
    if not STRIPE_SECRET_KEY:
        return jsonify({"error": "Stripe is not configured. Add STRIPE_SECRET_KEY to your .env file."}), 400

    plan_record = get_user_plan_record(request.user["id"]) or {}
    customer_id = plan_record.get("stripe_customer_id")
    if not customer_id:
        return jsonify({"error": "No Stripe customer found yet. Upgrade with Stripe first."}), 400

    portal = stripe_api_request("POST", "billing_portal/sessions", {
        "customer": customer_id,
        "return_url": FRONTEND_URL,
    })
    if isinstance(portal, dict) and portal.get("error"):
        return jsonify({"error": portal.get("error")}), 400

    return jsonify({"success": True, "portal_url": portal.get("url")}), 200


@app.route("/stripe/webhook", methods=["POST"])
def stripe_webhook():
    payload_bytes = request.get_data()
    signature_header = request.headers.get("Stripe-Signature", "")

    if STRIPE_WEBHOOK_SECRET and not verify_stripe_signature(payload_bytes, signature_header):
        return jsonify({"error": "Invalid Stripe webhook signature."}), 400

    try:
        event = json.loads(payload_bytes.decode("utf-8"))
    except Exception:
        return jsonify({"error": "Invalid webhook payload."}), 400

    event_type = event.get("type")
    obj = (event.get("data") or {}).get("object") or {}

    try:
        if event_type == "checkout.session.completed":
            sync_plan_from_checkout_session(obj)

        elif event_type in {"customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"}:
            if event_type == "customer.subscription.deleted":
                obj.setdefault("status", "canceled")
            sync_plan_from_subscription(obj)
    except Exception as e:
        logger.exception("Stripe webhook processing failed")
        return jsonify({"error": str(e)}), 500

    return jsonify({"received": True}), 200

# ═══════════════════════════════════════════════════════════════════════════════
# WORKSPACE ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/workspaces", methods=["GET"])
@require_auth
def get_workspaces():
    user_id = request.user["id"]

    owned = supabase_request(
        "GET",
        f"workspaces?owner_id=eq.{user_id}&select=*&order=created_at.desc",
        use_service_key=True,
    )
    memberships = supabase_request(
        "GET",
        f"workspace_members?user_id=eq.{user_id}&select=workspace_id,role",
        use_service_key=True,
    )

    member_ids = [m["workspace_id"] for m in (memberships if isinstance(memberships, list) else [])]
    member_role_by_id = {m["workspace_id"]: m.get("role", "member") for m in (memberships if isinstance(memberships, list) else [])}

    member_workspaces = []
    if member_ids:
        ids_str = ",".join(member_ids)
        member_workspaces = supabase_request(
            "GET",
            f"workspaces?id=in.({ids_str})&select=*&order=created_at.desc",
            use_service_key=True,
        )

    all_workspaces = []
    seen = set()
    for w in (owned if isinstance(owned, list) else []):
        if w.get("id") not in seen:
            w["role"] = "owner"
            all_workspaces.append(w)
            seen.add(w.get("id"))
    for w in (member_workspaces if isinstance(member_workspaces, list) else []):
        if w.get("id") not in seen:
            w["role"] = member_role_by_id.get(w.get("id"), "member")
            all_workspaces.append(w)
            seen.add(w.get("id"))

    return jsonify({"workspaces": all_workspaces}), 200


@app.route("/workspaces", methods=["POST"])
@require_auth
def create_workspace():
    data = request.get_json(silent=True) or {}
    name = data.get("name","").strip()
    if not name:
        return jsonify({"error": "Workspace name is required."}), 400

    allowed, usage_summary = ensure_usage_allowed(request.user["id"], "workspaces")
    if not allowed:
        return usage_limit_response("workspaces", usage_summary)

    result = supabase_request(
        "POST",
        "workspaces",
        {"name": name, "owner_id": request.user["id"]},
        use_service_key=True,
    )
    if "error" in result:
        return jsonify({"error": str(result["error"])}), 400

    workspace = result[0] if isinstance(result, list) else result
    workspace["role"] = "owner"

    # Add the owner to workspace_members as well. If the schema has a unique
    # constraint and this already exists, we ignore that error safely.
    supabase_request(
        "POST",
        "workspace_members",
        {"workspace_id": workspace["id"], "user_id": request.user["id"], "role": "owner"},
        use_service_key=True,
    )

    return jsonify({"success": True, "workspace": workspace}), 201


@app.route("/workspaces/<workspace_id>/members", methods=["POST"])
@require_auth
def invite_member(workspace_id):
    if not has_workspace_access(request.user["id"], workspace_id):
        return jsonify({"error": "You do not have access to this workspace."}), 403

    data = request.get_json(silent=True) or {}
    email = data.get("email","").strip().lower()
    if not email:
        return jsonify({"error": "Email is required."}), 400

    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
    }
    try:
        r = requests.get(f"{SUPABASE_URL}/auth/v1/admin/users", headers=headers, timeout=15)
        users = r.json().get("users", [])
        target_user = next((u for u in users if str(u.get("email", "")).lower() == email), None)
        if not target_user:
            return jsonify({"error": "No DevFlow account found with that email."}), 404

        result = supabase_request(
            "POST",
            "workspace_members",
            {"workspace_id": workspace_id, "user_id": target_user["id"], "role": "member"},
            use_service_key=True,
        )
        if "error" in result:
            return jsonify({"error": "Could not add member. They may already be in this workspace."}), 400
        return jsonify({"success": True, "message": f"{email} added to workspace."}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════════
# DOCUMENT ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/workspaces/<workspace_id>/documents", methods=["GET"])
@require_auth
def get_documents(workspace_id):
    if not has_workspace_access(request.user["id"], workspace_id):
        return jsonify({"error": "You do not have access to this workspace."}), 403

    docs = supabase_request(
        "GET",
        f"documents?workspace_id=eq.{workspace_id}&select=id,title,language,file_count,created_at&order=created_at.desc",
        use_service_key=True,
    )
    return jsonify({"documents": docs if isinstance(docs, list) else []}), 200


@app.route("/workspaces/<workspace_id>/documents", methods=["POST"])
@require_auth
def save_document(workspace_id):
    if not has_workspace_access(request.user["id"], workspace_id):
        return jsonify({"error": "You do not have access to this workspace."}), 403

    data = request.get_json(silent=True) or {}
    content = data.get("content","")
    if not content:
        return jsonify({"error": "No content to save."}), 400

    result = supabase_request(
        "POST",
        "documents",
        {
            "workspace_id": workspace_id,
            "created_by": request.user["id"],
            "title": data.get("title","Untitled Documentation"),
            "language": data.get("language","Unknown"),
            "content": content,
            "file_count": data.get("file_count",1),
        },
        use_service_key=True,
    )
    if "error" in result:
        return jsonify({"error": str(result["error"])}), 400
    doc = result[0] if isinstance(result, list) else result
    return jsonify({"success": True, "document": doc}), 201


@app.route("/documents/<doc_id>", methods=["GET"])
@require_auth
def get_document(doc_id):
    result = supabase_request("GET", f"documents?id=eq.{doc_id}&select=*", use_service_key=True)
    if not result or not isinstance(result, list) or len(result) == 0:
        return jsonify({"error": "Document not found."}), 404

    doc = result[0]
    if not has_workspace_access(request.user["id"], doc.get("workspace_id")):
        return jsonify({"error": "You do not have access to this document."}), 403

    return jsonify({"document": doc}), 200


@app.route("/documents/<doc_id>", methods=["DELETE"])
@require_auth
def delete_document(doc_id):
    result = supabase_request("GET", f"documents?id=eq.{doc_id}&select=id,workspace_id", use_service_key=True)
    if not result or not isinstance(result, list) or len(result) == 0:
        return jsonify({"error": "Document not found."}), 404

    if not has_workspace_access(request.user["id"], result[0].get("workspace_id")):
        return jsonify({"error": "You do not have access to this document."}), 403

    supabase_request("DELETE", f"documents?id=eq.{doc_id}", use_service_key=True)
    return jsonify({"success": True}), 200


# ═══════════════════════════════════════════════════════════════════════════════
# GITHUB INTEGRATION ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/github/fetch", methods=["POST"])
def github_fetch():
    """
    Fetch all source files from a GitHub repo URL and return combined code.
    No auth required so users can try it before logging in.
    Body: { "repo_url": "https://github.com/owner/repo", "github_token": "optional" }
    """
    data = request.get_json(silent=True) or {}
    repo_url = data.get("repo_url","").strip()
    github_token = data.get("github_token","").strip()

    if not repo_url:
        return jsonify({"success": False, "error": "Please provide a GitHub repository URL."}), 400

    result = fetch_github_repo_files(repo_url, github_token)
    if not result["success"]:
        return jsonify(result), 400

    return jsonify({
        "success": True,
        "code": result["code"],
        "file_count": result["file_count"],
        "repo_name": result["repo_name"],
        "branch": result["branch"],
    }), 200


@app.route("/github/document", methods=["POST"])
@require_auth
def github_document():
    """
    Fetch a GitHub repo and generate smart fast repository-level documentation.
    Body: { "repo_url": "...", "github_token": "optional" }
    """
    data = request.get_json(silent=True) or {}
    repo_url = data.get("repo_url", "").strip()
    github_token = data.get("github_token", "").strip()

    if not repo_url:
        return jsonify({"success": False, "error": "Please provide a GitHub repository URL."}), 400

    allowed, usage_summary = ensure_usage_allowed(request.user["id"], "documentation_generations")
    if not allowed:
        return usage_limit_response("documentation_generations", usage_summary)

    fetch_result = fetch_github_repo_files(repo_url, github_token)
    if not fetch_result["success"]:
        return jsonify(fetch_result), 400

    generated = generate_github_repo_documentation_fast(
        fetch_result["code"],
        fetch_result["repo_name"],
        fetch_result["file_count"],
        branch=fetch_result.get("branch", "main"),
        candidate_count=fetch_result.get("candidate_count", fetch_result["file_count"]),
        skipped_count=fetch_result.get("skipped_count", 0),
    )

    record_usage_event(request.user["id"], "documentation_generations", {"source": "github", "repo": fetch_result["repo_name"], "file_count": generated["file_count"]})

    return jsonify({
        "success": True,
        "doc": generated["doc"],
        "language": generated["language"],
        "file_count": generated["file_count"],
        "candidate_count": generated.get("candidate_count"),
        "skipped_count": generated.get("skipped_count"),
        "repo_name": fetch_result["repo_name"],
        "branch": fetch_result.get("branch", "main"),
        "tree_truncated": fetch_result.get("tree_truncated", False),
        "aiEnabled": generated["aiEnabled"],
        "mode": "smart_fast_repo_summary",
        "usage": build_usage_summary(request.user["id"]),
    }), 200


# ═══════════════════════════════════════════════════════════════════════════════
# CORE AI ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/health", methods=["GET"])
def health_check():
    return jsonify({
        "ok": True, "status": "healthy", "service": "DevFlow API",
        "ai_enabled": is_ai_enabled(),
        "ai_provider": "Groq (Llama 3.3 70B)" if is_ai_enabled() else "Rule-based engine",
        "auth": "Supabase" if SUPABASE_URL else "Not configured",
        "github": "Enabled",
    })



# ── Smart documentation engine ────────────────────────────────────────────────
def clean_devflow_text(value):
    """Normalize AI/rule-based output for a cleaner professional UI."""
    text = str(value or "")
    replacements = {
        "**": "",
        "â€”": "-",
        "â€“": "-",
        "â€˜": "'",
        "â€™": "'",
        "â€œ": '"',
        "â€": '"',
        "â€¦": "...",
        "Â": "",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()


def smart_files_from_code(code, file_name=""):
    """Return normalized file objects from pasted code, one file, or multi-file/project upload."""
    code = str(code or "")
    files = split_multiple_files(code)

    if files:
        return [
            {
                "file_name": f.get("file_name") or f"file-{index + 1}.txt",
                "content": f.get("content") or "",
            }
            for index, f in enumerate(files)
        ]

    return [{
        "file_name": file_name or "pasted-code.txt",
        "content": code,
    }]


def smart_documentation_scope(code, files):
    """Classify input so docs are useful for snippets, files, and full projects."""
    code_text = str(code or "")
    file_count = len(files)
    total_lines = sum(v11_count_lines(file.get("content", "")) for file in files)
    names = [str(file.get("file_name", "")).replace("\\", "/").lower() for file in files]
    has_file_markers = bool(re.search(r"^\s*---\s*FILE:", code_text, re.MULTILINE))

    project_files = {
        "package.json", "requirements.txt", "pyproject.toml", "pipfile", "manage.py",
        "app.py", "main.py", "server.py", "index.js", "app.js", "vite.config.js",
        "next.config.js", "tailwind.config.js", "tsconfig.json", "procfile",
        "railway.json", "dockerfile", "docker-compose.yml", "docker-compose.yaml",
        "readme.md", "readme",
    }

    has_project_file = any(name.split("/")[-1] in project_files for name in names)
    has_nested_paths = sum(1 for name in names if "/" in name) >= 3

    if file_count == 1 and not has_file_markers and total_lines <= 120:
        return "pasted_code"

    if file_count == 1:
        return "single_file"

    if file_count >= 8 or total_lines >= 700 or has_project_file or has_nested_paths:
        return "full_project"

    return "multi_file"


def smart_detect_dependencies(content):
    """Detect imports/dependencies from common source files."""
    dependencies = []
    for raw_line in str(content or "").splitlines():
        line = raw_line.strip()
        if line.startswith("import ") or line.startswith("from "):
            dependencies.append(line)
        elif line.startswith("const ") and "require(" in line:
            dependencies.append(line)
        elif line.startswith("import ") and " from " in line:
            dependencies.append(line)
    return dependencies[:20]


def smart_detect_symbols(file_name, content):
    """Extract important functions/classes/components without failing on syntax issues."""
    file_name = str(file_name or "")
    content = str(content or "")
    symbols = []

    if file_name.lower().endswith(".py") or detect_language(content, file_name) == "Python":
        try:
            tree = ast.parse(content)
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    symbols.append(f"function {node.name}")
                elif isinstance(node, ast.AsyncFunctionDef):
                    symbols.append(f"async function {node.name}")
                elif isinstance(node, ast.ClassDef):
                    symbols.append(f"class {node.name}")
        except Exception:
            pass

    if not symbols:
        patterns = [
            r"\bfunction\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(",
            r"\bconst\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:async\s*)?\(",
            r"\bconst\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:async\s*)?function",
            r"\bexport\s+default\s+function\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(",
            r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)\b",
            r"\bdef\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(",
        ]
        for pattern in patterns:
            for match in re.findall(pattern, content):
                symbols.append(match)

    cleaned = []
    for item in symbols:
        item = str(item).strip()
        if item and item not in cleaned:
            cleaned.append(item)
    return cleaned[:25]


def build_smart_file_inventory(files):
    inventory = []
    for file in files:
        file_name = file.get("file_name", "unknown")
        content = file.get("content", "")
        metrics = get_file_metrics(content)
        language = detect_language(content, file_name)
        symbols = smart_detect_symbols(file_name, content)
        inventory.append({
            "file_name": file_name,
            "language": language,
            "total_lines": metrics["total_lines"],
            "non_empty_lines": metrics["non_empty_lines"],
            "symbols": symbols,
            "dependencies": smart_detect_dependencies(content),
            "routes": v11_detect_routes_in_file(file_name, content),
        })
    return inventory


def build_rule_based_smart_documentation(code, file_name="", source="upload"):
    """Safe premium fallback used when AI is disabled or unavailable."""
    files = smart_files_from_code(code, file_name)
    input_type = smart_documentation_scope(code, files)
    inventory = build_smart_file_inventory(files)

    languages = sorted(set(item["language"] for item in inventory if item["language"]))
    tech_stack, frameworks = v11_detect_health_frameworks([
        {"file_name": file.get("file_name", ""), "content": file.get("content", "")}
        for file in files
    ])

    all_routes = []
    all_symbols = []
    all_dependencies = []
    for item in inventory:
        all_routes.extend(item["routes"])
        for symbol in item["symbols"]:
            all_symbols.append(f"{symbol} ({item['file_name']})")
        for dep in item["dependencies"]:
            all_dependencies.append(dep)

    total_lines = sum(item["total_lines"] for item in inventory)
    title = "Project Documentation"
    if input_type == "pasted_code":
        title = "Code Explanation"
    elif input_type == "single_file":
        title = "File Documentation"
    elif input_type == "multi_file":
        title = "Multi-file Documentation"

    inventory_lines = [
        f"- {item['file_name']} | {item['language']} | {item['total_lines']} lines"
        for item in inventory[:40]
    ]

    doc_parts = [
        f"# {title}",
        "",
        "## Overview",
        "",
    ]

    if input_type == "pasted_code":
        doc_parts.append("This input appears to be a pasted code snippet. The documentation focuses on what the snippet does, how it is structured, and what should be improved before using it in a larger project.")
    elif input_type == "single_file":
        doc_parts.append("This input contains one uploaded file. The documentation focuses on the file purpose, important logic, dependencies, routes, and improvement opportunities visible in this file.")
    elif input_type == "multi_file":
        doc_parts.append("This input contains multiple files. The documentation summarizes the visible file set as a partial project snapshot and explains the important parts that can be detected from the uploaded files.")
    else:
        doc_parts.append("This input appears to be a full project or a meaningful project folder. The documentation explains the project purpose, structure, core files, routes, frameworks, and improvement roadmap.")

    doc_parts.extend([
        "",
        "## Input Summary",
        "",
        f"- Source: {source}",
        f"- Input type: {input_type.replace('_', ' ').title()}",
        f"- Files analyzed: {len(files)}",
        f"- Total lines analyzed: {total_lines}",
        f"- Detected languages: {', '.join(languages or tech_stack or ['Unknown'])}",
        f"- Detected frameworks: {', '.join(frameworks or ['None detected'])}",
        "",
        "## File Inventory",
        "",
        "\n".join(inventory_lines) if inventory_lines else "- No files detected.",
        "",
        "## Important Functions, Classes, or Components",
        "",
        "\n".join(f"- {item}" for item in sorted(set(all_symbols))[:30]) if all_symbols else "- No major functions, classes, or components detected.",
        "",
        "## API Routes",
        "",
        "\n".join(f"- {route}" for route in sorted(set(all_routes))[:30]) if all_routes else "- No API routes detected in the provided input.",
        "",
        "## Dependencies and Imports",
        "",
        "\n".join(f"- {dep}" for dep in sorted(set(all_dependencies))[:30]) if all_dependencies else "- No imports or dependencies detected in the provided input.",
        "",
        "## Practical Improvement Roadmap",
        "",
        "- Add clear validation for user input and request payloads.",
        "- Add structured error handling so production failures are logged without exposing private details to users.",
        "- Keep secrets and API keys in environment variables only.",
        "- Add tests around important functions, API routes, and edge cases.",
        "- Document setup steps, environment variables, and deployment commands in the README.",
        "",
        "## Developer Notes",
        "",
        "This documentation is based only on the provided code. For a deeper project-level review, upload the full project folder or connect the GitHub repository."
    ])

    return {
        "doc": clean_devflow_text("\n".join(doc_parts)),
        "language": ", ".join(languages or tech_stack or ["Unknown"]),
        "file_count": len(files),
        "input_type": input_type,
        "ai_error": "",
    }


# ── ELITE Smart Documentation Engine ───────────────────────────────────────
def generate_ai_smart_documentation(code, file_name="", source="upload"):
    """
    Main smart documentation engine used by /generate-doc.
    Major upgrade: Better prompt, more consistent output, better structure.
    """
    try:
        files = smart_files_from_code(code, file_name)
        input_type = smart_documentation_scope(code, files)
        inventory = build_smart_file_inventory(files)

        languages = sorted(set(item["language"] for item in inventory if item["language"]))
        tech_stack, frameworks = v11_detect_health_frameworks([
            {"file_name": file.get("file_name", ""), "content": file.get("content", "")}
            for file in files
        ])

        if not is_ai_enabled():
            return build_rule_based_smart_documentation(code, file_name, source)

        # Better preview for large projects
        preview_parts = []
        used_chars = 0
        max_preview_chars = 22000

        for file in files[:30]:
            header = f"--- FILE: {file.get('file_name', 'unknown')} ---\n"
            body = str(file.get("content", ""))
            remaining = max_preview_chars - used_chars - len(header)
            if remaining <= 0:
                break
            snippet = body[:min(len(body), max(1400, remaining))]
            preview_parts.append(header + snippet)
            used_chars += len(header) + len(snippet)
            if used_chars >= max_preview_chars:
                break

        file_inventory_text = "\n".join(
            f"- {item['file_name']} | {item['language']} | {item['total_lines']} lines"
            for item in inventory[:60]
        )

        all_routes = []
        for item in inventory:
            all_routes.extend(item["routes"])
        routes_text = "\n".join(f"- {route}" for route in sorted(set(all_routes))[:40]) or "- No routes detected."

        # ── ELITE PROMPT ─────────────────────────────────────────────────
        prompt = f"""You are an elite senior software architect and technical writer.

Create **exceptionally clear, professional, and actionable** documentation.

### STRICT RULES:
- Use clean Markdown with proper heading hierarchy
- No **bold**, no emojis, no decorative formatting
- Short paragraphs (max 5 lines)
- Be precise and evidence-based
- Focus on developer value

### CONTEXT:
Input Type: {input_type}
Source: {source}
Files: {len(files)}
Languages: {', '.join(languages or tech_stack or ['Unknown'])}
Frameworks: {', '.join(frameworks or ['None detected'])}

### REQUIRED SECTIONS (in exact order):

1. Executive Summary
2. What This Project Does
3. Technology Stack
4. Architecture Overview
5. Important Files & Modules
6. Main Workflows
7. API Routes
8. Security & Risk Observations
9. Suggested Improvements
10. Quick Start / Setup
11. Recommended Next Steps

File Inventory:
{file_inventory_text}

Detected Routes:
{routes_text}

Code:
{chr(10).join(preview_parts)}
"""

        result = call_groq(prompt, max_tokens=4200)

        if result.get("success"):
            return {
                "doc": clean_devflow_text(result.get("text", "")),
                "language": ", ".join(languages or tech_stack or ["Unknown"]),
                "file_count": len(files),
                "input_type": input_type,
                "ai_error": "",
            }

        fallback = build_rule_based_smart_documentation(code, file_name, source)
        fallback["ai_error"] = result.get("error", "AI generation failed.")
        return fallback

    except Exception as e:
        logger.exception("Smart documentation engine failed")
        fallback = build_rule_based_smart_documentation(code, file_name, source)
        fallback["ai_error"] = str(e)
        return fallback
    """
    Main smart documentation engine used by /generate-doc.

    It supports:
    - pasted functions or snippets
    - one uploaded file
    - multiple uploaded files
    - full project folder uploads

    It never raises errors to the route. If AI fails, it returns a safe
    rule-based documentation result.
    """
    try:
        files = smart_files_from_code(code, file_name)
        input_type = smart_documentation_scope(code, files)
        inventory = build_smart_file_inventory(files)

        languages = sorted(set(item["language"] for item in inventory if item["language"]))
        tech_stack, frameworks = v11_detect_health_frameworks([
            {"file_name": file.get("file_name", ""), "content": file.get("content", "")}
            for file in files
        ])

        if not is_ai_enabled():
            return build_rule_based_smart_documentation(code, file_name, source)

        preview_parts = []
        used_chars = 0
        max_preview_chars = 18000

        for file in files[:25]:
            header = f"--- FILE: {file.get('file_name', 'unknown')} ---\n"
            body = str(file.get("content", ""))
            remaining = max_preview_chars - used_chars - len(header)
            if remaining <= 0:
                break
            snippet = body[:min(len(body), max(1200, remaining))]
            preview_parts.append(header + snippet)
            used_chars += len(header) + len(snippet)
            if used_chars >= max_preview_chars:
                break

        file_inventory_text = "\n".join(
            f"- {item['file_name']} | {item['language']} | {item['total_lines']} lines | symbols: {', '.join(item['symbols'][:6]) or 'none'}"
            for item in inventory[:50]
        )

        all_routes = []
        for item in inventory:
            all_routes.extend(item["routes"])
        routes_text = "\n".join(f"- {route}" for route in sorted(set(all_routes))[:30]) or "- No routes detected."

        if input_type == "pasted_code":
            instruction = (
                "The user pasted a code snippet or function. Explain what the code does, the important logic, parameters, return behavior, edge cases, and improvements. Do not describe it as a full project."
            )
            requested_sections = "Code Purpose, How It Works, Important Logic, Risks or Edge Cases, Suggested Improvements, Clean Usage Notes"
        elif input_type == "single_file":
            instruction = (
                "The user uploaded one file. Explain the file purpose, dependencies, important functions/classes, routes if any, risks, and improvements. Do not describe it as a full project."
            )
            requested_sections = "File Purpose, Technology Detected, Important Functions and Classes, Routes if Any, Important Logic, Risks, Suggested Improvements"
        elif input_type == "multi_file":
            instruction = (
                "The user uploaded multiple files. Explain how the visible files work together. Treat it as a partial project snapshot unless enough structure proves it is a full project."
            )
            requested_sections = "Overview, File Roles, Technology Stack, Important Workflows, Routes if Any, Risks, Improvement Roadmap"
        else:
            instruction = (
                "The user uploaded a full project or project folder. Explain what the project does, its purpose, architecture, framework, important functions, main routes, risks, and improvements."
            )
            requested_sections = "Executive Summary, What This Project Does, Technology Stack, Architecture Overview, Important Files, Main Workflows, API Routes, Risks, Improvement Roadmap, Developer Handover Notes"

        prompt = f"""You are a senior software architect and technical documentation engineer.

Create clean, professional documentation for a SaaS developer workspace.

Strict formatting rules:
- Plain markdown headings only.
- Do not use markdown bold.
- Do not use double asterisks.
- Do not use decorative emojis.
- Do not create messy line-by-line documentation.
- Be practical, concise, and useful for developers.
- Do not invent routes, frameworks, or risks that are not visible.
- Use clear section headings and short paragraphs.
- Focus on important project/file/function information only.

Input type: {input_type}
Source: {source}
Files visible: {len(files)}
Detected languages: {', '.join(languages or tech_stack or ['Unknown'])}
Detected frameworks: {', '.join(frameworks or ['None detected'])}

Documentation instruction:
{instruction}

Required sections:
{requested_sections}

Detected file inventory:
{file_inventory_text}

Detected routes:
{routes_text}

Code:
{chr(10).join(preview_parts)}
"""

        result = call_groq(prompt, max_tokens=3600)
        if result.get("success"):
            return {
                "doc": clean_devflow_text(result.get("text", "")),
                "language": ", ".join(languages or tech_stack or ["Unknown"]),
                "file_count": len(files),
                "input_type": input_type,
                "ai_error": "",
            }

        fallback = build_rule_based_smart_documentation(code, file_name, source)
        fallback["ai_error"] = result.get("error", "AI generation failed.")
        return fallback

    except Exception as e:
        logger.exception("Smart documentation engine failed")
        fallback = build_rule_based_smart_documentation(code, file_name, source)
        fallback["ai_error"] = str(e)
        return fallback


def compact_large_upload_for_generate_docs(code, file_name="", target_chars=None):
    """
    Server-side safety compactor for large project uploads.

    This prevents /generate-doc from failing with "Code is too large" when the
    browser sends a full folder. It keeps the important project files, builds a
    manifest, and sends excerpts from priority files to the documentation engine.
    """
    raw_code = str(code or "")
    target_chars = int(target_chars or min(max(MAX_CODE_CHARS - 5000, 20000), 180000))

    def normalize_path(value):
        return str(value or "").replace("\\", "/").strip()

    def priority_score(path):
        p = normalize_path(path).lower()
        base = p.split("/")[-1]

        score = 10

        high_value_exact = {
            "app.py": 100,
            "main.py": 95,
            "server.py": 95,
            "package.json": 92,
            "requirements.txt": 92,
            "procfile": 90,
            "railway.json": 90,
            "readme.md": 86,
            "readme": 84,
            "app.js": 84,
            "app.jsx": 84,
            "app.tsx": 84,
            "index.js": 78,
            "index.jsx": 78,
            "index.tsx": 78,
        }

        if base in high_value_exact:
            score = max(score, high_value_exact[base])

        important_terms = [
            "auth", "login", "billing", "stripe", "subscription", "webhook",
            "supabase", "github", "workspace", "document", "generate", "health",
            "bug", "task", "api", "route", "service", "config"
        ]

        for term in important_terms:
            if term in p:
                score += 12

        if "/frontend/src/" in p or p.startswith("frontend/src/"):
            score += 14

        if p.endswith((".py", ".js", ".jsx", ".ts", ".tsx", ".json", ".css", ".md", ".txt", ".yml", ".yaml")):
            score += 6

        noisy_terms = [
            "backup", "_backup", "hotfix", "fix_", ".old", ".bak", "screenshot",
            "node_modules", "venv", ".venv", "__pycache__", ".git", "build", "dist",
            "coverage", ".cache", "package-lock.json"
        ]

        for term in noisy_terms:
            if term in p:
                score -= 100

        return score

    def should_skip(path):
        p = normalize_path(path).lower()
        base = p.split("/")[-1]
        skip_parts = [
            "node_modules", "venv", ".venv", "__pycache__", ".git", "dist", "build",
            ".next", "coverage", ".cache", ".pytest_cache", ".mypy_cache"
        ]
        skip_exts = [
            ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".pdf", ".zip", ".rar",
            ".7z", ".exe", ".dll", ".mp4", ".mp3", ".wav", ".woff", ".woff2", ".ttf",
            ".otf", ".map", ".log", ".pyc"
        ]
        if any(part in p for part in skip_parts):
            return True
        if any(p.endswith(ext) for ext in skip_exts):
            return True
        if base in {".env", ".env.local", ".env.production", "package-lock.json", "yarn.lock", "pnpm-lock.yaml"}:
            return True
        if "backup" in base or "hotfix" in base or base.startswith("fix_"):
            return True
        return False

    files = smart_files_from_code(raw_code, file_name)
    filtered = []
    skipped = 0

    for file in files:
        name = normalize_path(file.get("file_name") or file_name or "uploaded-code.txt")
        content = str(file.get("content") or "")
        if should_skip(name):
            skipped += 1
            continue
        filtered.append({
            "file_name": name,
            "content": content,
            "score": priority_score(name),
            "lines": len(content.splitlines()),
        })

    if not filtered:
        filtered = [{
            "file_name": file_name or "uploaded-code.txt",
            "content": raw_code[:target_chars],
            "score": 50,
            "lines": len(raw_code.splitlines()),
        }]

    filtered.sort(key=lambda item: (-item["score"], item["file_name"]))

    manifest = "\n".join(
        f"- {item['file_name']} | {detect_language(item['content'], item['file_name'])} | {item['lines']} lines | priority {item['score']}"
        for item in filtered[:120]
    )

    header = "\n".join([
        "DEVFLOW SMART SERVER COMPACTION",
        "Input type: Full Project or Large Upload",
        f"Original characters received: {len(raw_code)}",
        f"Useful files detected: {len(filtered)}",
        f"Noisy files skipped: {skipped}",
        "",
        "Instruction: Generate one professional project-level documentation report.",
        "Instruction: Explain what the project does, architecture, technology stack, important files, workflows, API routes, risks, and practical improvements.",
        "Instruction: Do not explain every file separately. Focus on important project behavior and developer handover value.",
        "",
        "Project file manifest:",
        manifest,
        "",
        "Important source excerpts:",
    ])

    chunks = [header]
    used = len(header)

    for item in filtered:
        name = item["file_name"]
        content = item["content"]
        language = detect_language(content, name)
        lines = content.splitlines()

        if item["score"] >= 95:
            head_count, tail_count, char_cap = 170, 55, 18000
        elif item["score"] >= 75:
            head_count, tail_count, char_cap = 110, 35, 11000
        else:
            head_count, tail_count, char_cap = 60, 20, 5500

        important_lines = []
        for raw_line in lines:
            line = raw_line.strip()
            if (
                line.startswith("import ") or
                line.startswith("from ") or
                line.startswith("def ") or
                line.startswith("class ") or
                line.startswith("@app.route") or
                line.startswith("function ") or
                line.startswith("const ") or
                line.startswith("export ") or
                "fetch(" in line or
                "stripe" in line.lower() or
                "supabase" in line.lower() or
                "github" in line.lower()
            ):
                important_lines.append(raw_line)
            if len(important_lines) >= 90:
                break

        excerpt_parts = [
            f"--- FILE: {name} ---",
            f"LANGUAGE: {language}",
            f"TOTAL_LINES: {item['lines']}",
            f"PRIORITY: {item['score']}",
            "",
            "IMPORTANT_LINES:",
            "\n".join(important_lines) or "- No important signatures detected.",
            "",
            "FILE_START_EXCERPT:",
            "\n".join(lines[:head_count]),
            "",
            "FILE_END_EXCERPT:",
            "\n".join(lines[-tail_count:]) if len(lines) > tail_count else "",
        ]

        block = "\n".join(excerpt_parts)
        if len(block) > char_cap:
            block = block[:char_cap] + "\n\n[Excerpt trimmed safely for server request size]"

        if used + len(block) + 4 > target_chars:
            continue

        chunks.append(block)
        used += len(block) + 4

    final_code = "\n\n".join(chunks)

    if len(final_code) > target_chars:
        final_code = final_code[:target_chars - 500] + "\n\nSMART_COMPACTION_NOTE\nRemaining content was trimmed safely on the server."

    return final_code


# ── Professional PDF Export ────────────────────────────────────────────────────
@app.route("/export-pdf", methods=["POST"])
@require_auth
def export_pdf():
    """Generate a professional branded PDF from DevFlow documentation content."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.units import mm
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
            HRFlowable, PageBreak, KeepTogether
        )
        from reportlab.platypus.flowables import Flowable
        from reportlab.pdfgen import canvas as rl_canvas
        import io

        data = request.get_json(silent=True) or {}
        content      = str(data.get("content", "")).strip()
        title        = str(data.get("title", "DevFlow Documentation")).strip()
        language     = str(data.get("language", "")).strip()
        file_count   = int(data.get("fileCount", 1))
        mode         = str(data.get("mode", "")).strip()
        workspace    = str(data.get("workspace", "")).strip()

        if not content:
            return jsonify({"ok": False, "error": "No content provided."}), 400

        # ── Color palette ──────────────────────────────────────────────────────
        INK          = colors.HexColor("#0f172a")
        BLUE         = colors.HexColor("#2563eb")
        BLUE_DARK    = colors.HexColor("#1e40af")
        BLUE_LIGHT   = colors.HexColor("#dbeafe")
        SLATE        = colors.HexColor("#334155")
        MUTED        = colors.HexColor("#64748b")
        LINE         = colors.HexColor("#e2e8f0")
        SOFT_BG      = colors.HexColor("#f8fafc")
        WHITE        = colors.white
        ACCENT       = colors.HexColor("#0ea5e9")
        GREEN        = colors.HexColor("#059669")

        buf = io.BytesIO()
        W, H = A4
        margin = 20 * mm

        # ── Canvas callbacks for header/footer ────────────────────────────────
        export_date = datetime.now(timezone.utc).strftime("%B %d, %Y")
        safe_title  = title[:80]

        def on_first_page(canv, doc):
            canv.saveState()
            # Full-width dark header band
            canv.setFillColor(INK)
            canv.rect(0, H - 58 * mm, W, 58 * mm, fill=1, stroke=0)

            # DevFlow logo text
            canv.setFont("Helvetica-Bold", 28)
            canv.setFillColor(WHITE)
            canv.drawString(margin, H - 24 * mm, "DevFlow")

            # Tagline
            canv.setFont("Helvetica", 10)
            canv.setFillColor(colors.HexColor("#94a3b8"))
            canv.drawString(margin, H - 32 * mm, "AI-powered developer documentation")

            # Accent bar
            canv.setFillColor(BLUE)
            canv.rect(0, H - 60 * mm, W, 3 * mm, fill=1, stroke=0)

            # Document title area
            canv.setFont("Helvetica-Bold", 20)
            canv.setFillColor(INK)
            canv.drawString(margin, H - 74 * mm, safe_title[:70])

            # Meta row
            meta_y = H - 83 * mm
            canv.setFillColor(SOFT_BG)
            canv.roundRect(margin, meta_y - 5 * mm, W - 2 * margin, 12 * mm, 3 * mm, fill=1, stroke=0)
            canv.setFont("Helvetica", 9)
            canv.setFillColor(MUTED)
            meta_parts = [f"Exported {export_date}"]
            if language:  meta_parts.append(f"Language: {language}")
            if file_count: meta_parts.append(f"Files: {file_count}")
            if mode:      meta_parts.append(f"Mode: {mode}")
            if workspace: meta_parts.append(f"Workspace: {workspace}")
            canv.drawString(margin + 4 * mm, meta_y + 2 * mm, "   ·   ".join(meta_parts))

            # Footer
            _draw_footer(canv, doc, 1)
            canv.restoreState()

        def on_later_pages(canv, doc):
            canv.saveState()
            # Slim top bar
            canv.setFillColor(INK)
            canv.rect(0, H - 12 * mm, W, 12 * mm, fill=1, stroke=0)
            canv.setFont("Helvetica-Bold", 8)
            canv.setFillColor(WHITE)
            canv.drawString(margin, H - 7 * mm, "DevFlow")
            canv.setFont("Helvetica", 8)
            canv.setFillColor(colors.HexColor("#94a3b8"))
            canv.drawRightString(W - margin, H - 7 * mm, safe_title[:60])
            _draw_footer(canv, doc, doc.page)
            canv.restoreState()

        def _draw_footer(canv, doc, page_num):
            y = 10 * mm
            canv.setStrokeColor(LINE)
            canv.setLineWidth(0.5)
            canv.line(margin, y + 4 * mm, W - margin, y + 4 * mm)
            canv.setFont("Helvetica", 7.5)
            canv.setFillColor(MUTED)
            canv.drawString(margin, y, "Generated by DevFlow · AI-powered developer documentation")
            canv.drawRightString(W - margin, y, f"Page {page_num}")

        # ── Styles ─────────────────────────────────────────────────────────────
        styles = getSampleStyleSheet()

        def make_style(name, **kw):
            return ParagraphStyle(name, **kw)

        style_h1 = make_style("H1",
            fontName="Helvetica-Bold", fontSize=16, textColor=INK,
            spaceAfter=4*mm, spaceBefore=6*mm, leading=22)

        style_h2 = make_style("H2",
            fontName="Helvetica-Bold", fontSize=13, textColor=BLUE_DARK,
            spaceAfter=2*mm, spaceBefore=5*mm, leading=18, borderPad=0)

        style_h3 = make_style("H3",
            fontName="Helvetica-Bold", fontSize=11, textColor=SLATE,
            spaceAfter=1.5*mm, spaceBefore=3*mm, leading=15)

        style_body = make_style("Body",
            fontName="Helvetica", fontSize=9.5, textColor=INK,
            spaceAfter=2*mm, leading=14)

        style_bullet = make_style("Bullet",
            fontName="Helvetica", fontSize=9.5, textColor=INK,
            spaceAfter=1*mm, leading=14, leftIndent=10*mm,
            bulletIndent=3*mm)

        style_meta_key = make_style("MetaKey",
            fontName="Helvetica-Bold", fontSize=9, textColor=SLATE,
            spaceAfter=0, leading=13)

        style_meta_val = make_style("MetaVal",
            fontName="Helvetica", fontSize=9, textColor=INK,
            spaceAfter=0, leading=13)

        style_muted = make_style("Muted",
            fontName="Helvetica-Oblique", fontSize=8.5, textColor=MUTED,
            spaceAfter=1*mm, leading=12)

        SECTION_HEADINGS = {
            "executive summary", "overview", "file purpose", "project purpose",
            "what this project does", "technology stack", "technology detected",
            "architecture overview", "important files", "important functions",
            "important functions and classes", "function and method explanations",
            "main workflows", "api routes", "routes if any", "important logic",
            "dependencies", "security observations", "security risks", "risks",
            "suggested improvements", "improvement roadmap", "developer handover notes",
            "how to run", "how to run / setup", "setup notes", "large file summary",
            "detected functions", "why this score", "detected frameworks",
            "detected routes", "architecture notes", "issues", "priority fixes",
            "testing notes", "what the error means", "why it happened",
            "likely cause", "step-by-step fix", "fixed version", "prevention",
            "summary", "implementation notes", "subtasks", "acceptance criteria",
            "definition of done", "qa notes", "tech stack",
        }

        def clean_line(text):
            return (str(text or "")
                .replace("**", "").replace("__", "")
                .replace("`", "")
                .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .strip())

        def is_section_heading(line):
            v = str(line or "").strip()
            if not v: return False
            norm = v.lstrip("#").rstrip(":：").strip().lower()
            if norm in SECTION_HEADINGS: return True
            if len(v) <= 64 and not v.startswith(("-", "*", "•")) and not v[0].isdigit():
                if not any(c in v for c in ".;"):
                    import re as _re
                    if _re.match(r"^[A-Z][A-Za-z0-9 /&()+-]+$", v):
                        return True
            return False

        def is_meta_line(line):
            import re as _re
            v = str(line or "").strip()
            return bool(_re.match(r"^[A-Za-z][A-Za-z0-9 /&()+-]{1,36}:\s+.+$", v) and len(v) <= 150)

        # ── Parse content into flowables ───────────────────────────────────────
        # Clean markdown artifacts
        import re as _re
        cleaned = (content
            .replace("\r\n", "\n")
            .replace("\r", "\n"))
        # strip code fences
        cleaned = _re.sub(r"```[a-z]*\n?", "", cleaned)
        cleaned = _re.sub(r"\*\*(.*?)\*\*", r"\1", cleaned)
        cleaned = _re.sub(r"`([^`]+)`", r"\1", cleaned)
        cleaned = _re.sub(r"^\s*#{1,6}\s*", "", cleaned, flags=_re.MULTILINE)
        cleaned = _re.sub(r"^\s*[-=]{6,}\s*$", "", cleaned, flags=_re.MULTILINE)
        cleaned = _re.sub(r"\n{3,}", "\n\n", cleaned)

        lines = [l.rstrip() for l in cleaned.split("\n")]

        story = []
        # First page top margin (below the header band)
        story.append(Spacer(1, 32 * mm))

        i = 0
        while i < len(lines):
            raw = lines[i]
            trimmed = raw.strip()
            i += 1

            if not trimmed:
                story.append(Spacer(1, 2 * mm))
                continue

            # Bullet point
            bullet_m = _re.match(r"^[-*•]\s+(.+)$", trimmed)
            if bullet_m:
                story.append(Paragraph(f"• {clean_line(bullet_m.group(1))}", style_bullet))
                continue

            # Numbered list
            num_m = _re.match(r"^\d+\.\s+(.+)$", trimmed)
            if num_m:
                story.append(Paragraph(f"   {clean_line(num_m.group(1))}", style_bullet))
                continue

            # Section heading
            if is_section_heading(trimmed):
                label = trimmed.lstrip("#").rstrip(":：").strip()
                elems = [
                    Spacer(1, 3 * mm),
                    Paragraph(clean_line(label), style_h2),
                    HRFlowable(width="100%", thickness=0.8, color=BLUE_LIGHT, spaceAfter=2*mm),
                ]
                story.append(KeepTogether(elems))
                continue

            # Meta row (Key: value)
            if is_meta_line(trimmed):
                colon = trimmed.index(":")
                key   = trimmed[:colon].strip()
                val   = trimmed[colon + 1:].strip()
                tbl = Table(
                    [[Paragraph(clean_line(key), style_meta_key),
                      Paragraph(clean_line(val),  style_meta_val)]],
                    colWidths=[(W - 2*margin) * 0.28, (W - 2*margin) * 0.72],
                    hAlign="LEFT"
                )
                tbl.setStyle(TableStyle([
                    ("VALIGN", (0,0), (-1,-1), "TOP"),
                    ("BOTTOMPADDING", (0,0), (-1,-1), 2),
                    ("TOPPADDING",    (0,0), (-1,-1), 2),
                ]))
                story.append(tbl)
                story.append(Spacer(1, 1 * mm))
                continue

            # Source file banner
            sf_m = _re.match(r"^(?:#\s*)?Source File:\s*(.+)$", trimmed, _re.IGNORECASE)
            if sf_m:
                fname = sf_m.group(1).strip()[:90]
                tbl = Table(
                    [[Paragraph("📄 " + clean_line(fname), make_style("SF",
                        fontName="Helvetica-Bold", fontSize=10, textColor=INK, leading=14))]],
                    colWidths=[W - 2 * margin],
                    hAlign="LEFT"
                )
                tbl.setStyle(TableStyle([
                    ("BACKGROUND",    (0,0), (0,0), SOFT_BG),
                    ("ROUNDEDCORNERS",(0,0), (0,0), [3]),
                    ("TOPPADDING",    (0,0), (0,0), 5),
                    ("BOTTOMPADDING", (0,0), (0,0), 5),
                    ("LEFTPADDING",   (0,0), (0,0), 8),
                    ("BOX",           (0,0), (0,0), 0.5, LINE),
                ]))
                story.append(Spacer(1, 2 * mm))
                story.append(tbl)
                story.append(Spacer(1, 2 * mm))
                continue

            # Regular paragraph
            story.append(Paragraph(clean_line(trimmed), style_body))

        # Build PDF
        doc = SimpleDocTemplate(
            buf, pagesize=A4,
            leftMargin=margin, rightMargin=margin,
            topMargin=18 * mm, bottomMargin=18 * mm,
            title=safe_title,
            author="DevFlow",
        )
        doc.build(story, onFirstPage=on_first_page, onLaterPages=on_later_pages)

        buf.seek(0)
        from flask import send_file
        safe_fname = _re.sub(r"[^a-z0-9_-]", "-", safe_title.lower())[:48]
        return send_file(
            buf,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"devflow-{safe_fname}.pdf",
        )

    except Exception:
        logger.exception("PDF export failed")
        return jsonify({"ok": False, "error": "PDF export failed on server."}), 500


@app.route("/generate-doc", methods=["POST"])
@require_auth
def generate_doc():
    try:
        data = request.get_json(silent=True) or {}
        code = data.get("code", "")
        file_name = data.get("fileName", "")

        if not isinstance(code, str) or not code.strip():
            return jsonify({"ok": False, "error": "No code provided."}), 400

        if len(code) > MAX_CODE_CHARS:
            logger.info("Large generate-doc upload received. Applying smart server compaction.")
            code = compact_large_upload_for_generate_docs(code, file_name)
            file_name = file_name or "smart-project-upload"

        if len(code) > MAX_CODE_CHARS:
            code = code[: max(20000, MAX_CODE_CHARS - 1000)] + "\n\nSMART_COMPACTION_NOTE\nFinal safety trim applied before documentation generation."

        allowed, usage_summary = ensure_usage_allowed(request.user["id"], "documentation_generations")
        if not allowed:
            return usage_limit_response("documentation_generations", usage_summary)

        smart_result = generate_ai_smart_documentation(code, file_name, source="upload")

        record_usage_event(
            request.user["id"],
            "documentation_generations",
            {
                "source": "upload",
                "file_count": smart_result.get("file_count", 1),
                "input_type": smart_result.get("input_type", "unknown"),
                "ai_fallback": bool(smart_result.get("ai_error")),
            },
        )

        return jsonify({
            "ok": True,
            "doc": smart_result.get("doc", ""),
            "language": smart_result.get("language", "Unknown"),
            "fileCount": smart_result.get("file_count", 1),
            "inputType": smart_result.get("input_type", "unknown"),
            "documentationMode": "smart_documentation_v8",
            "aiEnabled": is_ai_enabled(),
            "usage": build_usage_summary(request.user["id"]),
        })

    except Exception:
        logger.exception("Generate doc failed")
        return jsonify({
            "ok": False,
            "error": "Unexpected server error."
        }), 500


@app.route("/analyze-bug", methods=["POST"])
@require_auth
def analyze_bug():
    data = request.get_json(silent=True) or {}
    error_log = data.get("error_log","").strip()
    if not error_log: return jsonify({"error": "Please provide an error log."}), 400
    allowed, usage_summary = ensure_usage_allowed(request.user["id"], "bug_analyzer")
    if not allowed:
        return usage_limit_response("bug_analyzer", usage_summary)
    if is_ai_enabled():
        result = analyze_bug_with_ai(error_log)
        if result["success"]:
            record_usage_event(request.user["id"], "bug_analyzer", {"input_chars": len(error_log)})
            return jsonify({"success": True, "analysis": result["analysis"], "aiEnabled": True, "usage": build_usage_summary(request.user["id"])})
    record_usage_event(request.user["id"], "bug_analyzer", {"input_chars": len(error_log), "fallback": True})
    return jsonify({"success": True, "analysis": rule_based_bug_analysis(error_log), "aiEnabled": False, "usage": build_usage_summary(request.user["id"])})


@app.route("/project-health", methods=["POST"])
@require_auth
def project_health():
    data = request.get_json(silent=True) or {}
    code = data.get("code","").strip()
    if not code: return jsonify({"error": "Please upload project code first."}), 400
    allowed, usage_summary = ensure_usage_allowed(request.user["id"], "project_health")
    if not allowed:
        return usage_limit_response("project_health", usage_summary)
    if is_ai_enabled():
        result = generate_health_report_with_ai(code)
        if result["success"]:
            record_usage_event(request.user["id"], "project_health", {"code_chars": len(code)})
            return jsonify({"success": True, "report": result["report"], "aiEnabled": True, "usage": build_usage_summary(request.user["id"])})
    record_usage_event(request.user["id"], "project_health", {"code_chars": len(code), "fallback": True})
    return jsonify({"success": True, "report": generate_project_health_report(code), "aiEnabled": False, "usage": build_usage_summary(request.user["id"])})


@app.route("/generate-tasks", methods=["POST"])
@require_auth
def generate_tasks():
    data = request.get_json(silent=True) or {}
    requirements_text = data.get("requirements","").strip()
    if not requirements_text: return jsonify({"error": "Please paste requirements first."}), 400
    allowed, usage_summary = ensure_usage_allowed(request.user["id"], "task_generator")
    if not allowed:
        return usage_limit_response("task_generator", usage_summary)
    if is_ai_enabled():
        result = generate_tasks_with_ai(requirements_text)
        if result["success"]:
            record_usage_event(request.user["id"], "task_generator", {"input_chars": len(requirements_text)})
            return jsonify({"success": True, "tasks": result["tasks"], "aiEnabled": True, "usage": build_usage_summary(request.user["id"])})
    record_usage_event(request.user["id"], "task_generator", {"input_chars": len(requirements_text), "fallback": True})
    return jsonify({"success": True, "tasks": generate_task_plan_fallback(requirements_text), "aiEnabled": False, "usage": build_usage_summary(request.user["id"])})


# ── Static serving ─────────────────────────────────────────────────────────────
@app.errorhandler(413)
def too_large(e):
    return jsonify({"ok": False, "error": "Payload too large."}), 413


@app.errorhandler(404)
def not_found(e):
    """Serve React routes only for frontend paths.

    Important Railway fix:
    If /static/js/main...js is missing, we must not silently return index.html
    as JavaScript because that creates: Unexpected token '<'.
    """
    api_prefixes = (
        "/auth", "/billing", "/stripe", "/workspaces", "/documents",
        "/github", "/health", "/generate-doc", "/analyze-bug",
        "/project-health", "/generate-tasks"
    )

    if request.path.startswith(api_prefixes):
        return jsonify({"success": False, "error": "Route not found"}), 404

    if request.path.startswith("/static/"):
        return jsonify({
            "success": False,
            "error": "React static asset not found. Rebuild frontend and commit frontend/build."
        }), 404

    index_path = os.path.join(app.static_folder, "index.html")
    if os.path.exists(index_path):
        return send_from_directory(app.static_folder, "index.html")

    return jsonify({
        "success": False,
        "error": "Frontend build not found. Run npm run build inside frontend and commit frontend/build."
    }), 500


@app.errorhandler(500)
def server_error(e):
    return jsonify({"ok": False, "error": "Internal server error."}), 500


@app.route("/")
def serve_home():
    index_path = os.path.join(app.static_folder, "index.html")
    if os.path.exists(index_path):
        return send_from_directory(app.static_folder, "index.html")
    return jsonify({
        "success": False,
        "error": "Frontend build not found. Run npm run build inside frontend and commit frontend/build."
    }), 500


@app.route("/<path:path>")
def serve_static(path):
    # React build assets are referenced as /static/js/... and /static/css/...
    # When app.static_folder = frontend/build, these files exist at frontend/build/static/js/...
    requested_file = os.path.join(app.static_folder, path)

    if os.path.isfile(requested_file):
        return send_from_directory(app.static_folder, path)

    # Never return index.html for real asset requests.
    # Returning HTML for JS/CSS causes a blank page and "Unexpected token '<'".
    if path.startswith("static/") or "." in os.path.basename(path):
        return jsonify({
            "success": False,
            "error": f"Static file not found: {path}. Rebuild frontend and commit frontend/build."
        }), 404

    index_path = os.path.join(app.static_folder, "index.html")
    if os.path.exists(index_path):
        return send_from_directory(app.static_folder, "index.html")

    return jsonify({
        "success": False,
        "error": "Frontend build not found. Run npm run build inside frontend and commit frontend/build."
    }), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)
