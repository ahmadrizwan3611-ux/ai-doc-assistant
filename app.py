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
def call_groq(prompt: str, max_tokens: int = 3000) -> dict:
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


def generate_health_report_with_ai(code):
    prompt = (
        "You are a senior DevOps engineer, security reviewer, and software architect.\n\n"
        "Analyze the uploaded project code and return a realistic project health report.\n\n"
        "Return ONLY valid JSON.\n"
        "Do not return markdown.\n"
        "Do not wrap JSON in code fences.\n"
        "Do not invent risks that are not supported by the code.\n"
        "Do not mention SQL injection unless SQL/database query code is visible.\n"
        "Do not mention XSS unless frontend user-rendered content or HTML injection risk is visible.\n\n"
        "Return exactly these keys:\n"
        "- \"score\": string like \"78/100\"\n"
        "- \"score_explanation\": array of 3-6 short reasons explaining the score\n"
        "- \"total_files_detected\": integer\n"
        "- \"tech_stack\": array of detected technologies\n"
        "- \"detected_frameworks\": array of detected frameworks/libraries\n"
        "- \"routes\": array of detected API route strings\n"
        "- \"important_files\": array of important file names or modules\n"
        "- \"architecture_notes\": array of architecture observations\n"
        "- \"issues\": array of concrete issues found\n"
        "- \"security_risks\": array of concrete security risks found\n"
        "- \"production_readiness\": \"Not Ready\", \"Almost Ready\", or \"Production Ready\"\n"
        "- \"priority_fixes\": array of highest priority fixes\n"
        "- \"suggestions\": array of practical improvements\n"
        "- \"testing_notes\": array of testing recommendations\n\n"
        "Scoring guide:\n"
        "- 90-100: production-ready, tests/config/security are visible\n"
        "- 75-89: strong but missing some production hardening\n"
        "- 55-74: functional but not production-ready\n"
        "- 30-54: early prototype\n"
        "- 0-29: broken or insufficient code\n\n"
        f"Code:\n{code[:12000]}\n"
    )
    result = call_groq(prompt, max_tokens=3000)
    if not result["success"]:
        return {"success": False, "report": None, "error": result["error"]}

    raw = result["text"].strip()
    raw = re.sub(r"^```(?:json)?", "", raw).strip()
    raw = re.sub(r"```$", "", raw).strip()

    try:
        report = json.loads(raw)
        if not isinstance(report, dict):
            return {"success": False, "report": None, "error": "AI returned JSON but not an object."}

        report.setdefault("score", "0/100")
        report.setdefault("score_explanation", [])
        report.setdefault("total_files_detected", 0)
        report.setdefault("tech_stack", [])
        report.setdefault("detected_frameworks", [])
        report.setdefault("routes", [])
        report.setdefault("important_files", [])
        report.setdefault("architecture_notes", [])
        report.setdefault("issues", [])
        report.setdefault("security_risks", [])
        report.setdefault("production_readiness", "Not Ready")
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
            return jsonify({"ok": False, "error": "Code is too large."}), 413

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
        logger.exception("Error generating doc")
        return jsonify({"ok": False, "error": "Unexpected server error."}), 500


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
