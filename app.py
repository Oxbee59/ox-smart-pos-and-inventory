from flask import Flask, render_template, request, redirect, url_for, jsonify, send_file, session
from functools import wraps
from services.product_service import get_all_products as get_all_products_service
import os
import uuid
import threading
from flask import send_from_directory
from flask import abort

# ---------- IMPORT PURCHASE SERVICES ----------
from services.purchase_service import (
    add_purchase,
    get_all_purchases,
    get_purchases_by_date_range,
    get_product_suggestions,
    get_category_suggestions,
    update_product,
    get_batch_update_history,
    search_products_by_name_or_brand
)

# ---------- IMPORT DASHBOARD SERVICES ----------
from services.dashboard_service import (
    get_today_sales,
    get_today_profit,
    get_total_products,
    get_low_stock_products,
    get_top_products,
    get_sales_history,
    get_total_batches
)

# ---------- IMPORT PRODUCT DELETION SERVICES ----------
from services.product_service import (
    delete_product_keep_history,
    delete_product_clean_all,
    delete_batch,
    delete_batch_clean_all
)

# ---------- IMPORT SALES SERVICES ----------
from services.sales_service import (
    get_products_for_sale,
    get_batches_for_product,
    get_batch_by_id,
    create_multi_sale,
    update_product_stock,
    import_sale_bulk
)

# ---------- IMPORT RECEIPT SERVICE ----------
from services.receipt_service import generate_receipt_multi

# ---------- IMPORT AUTH SERVICES ----------
from services.auth_service import (
    login_user,
    create_user,
    admin_exists,
    count_admins,
    get_all_users,
    update_user_role,
    delete_user,
    logout_user,
    get_user_by_id,
    is_protected_user,
    get_user_logs,
    update_user_password
)

# ---------- IMPORT ARCHIVE SERVICES ----------
from services.product_service import get_deleted_products, restore_archive

# ---------- DATABASE CONNECTION ----------
from database.db import get_connection
try:
    from database.db import DATABASE_URL
except ImportError:
    DATABASE_URL = os.getenv("DATABASE_URL")

# ---------- PDF GENERATION ----------
import io
from datetime import datetime, date, timedelta, timezone
from reportlab.lib.pagesizes import landscape, A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
from reportlab.lib.units import cm

# ---------- HERBAL CATEGORIES ----------
PERMANENT_CATEGORY = 'Tree Roots'
DEFAULT_CATEGORY = 'Tree Barks'

# The two-page model: every section now has exactly two pages — Oils and Others.
#   * "Oils" page  → Spiritual Oils, Anointing Oils, Essential Oils
#   * "Others" page → Tree Roots, Tree Barks, Leaves, Seeds
OIL_CATEGORIES = ['Spiritual Oils', 'Anointing Oils', 'Essential Oils']
NON_OIL_CATEGORIES = ['Tree Roots', 'Tree Barks', 'Leaves', 'Seeds']
ALL_HERBAL_CATEGORIES = NON_OIL_CATEGORIES + OIL_CATEGORIES


def _group_filter_sql(col, group):
    """
    Return (sql_fragment, params) for a category column given a group string.
      group == 'oils'   → only oil categories
      group == 'others' → everything except oil categories
      otherwise         → no filter
    Uses explicit IN / NOT IN placeholders so it works on psycopg2 and sqlite alike.
    """
    if group == 'oils':
        placeholders = ','.join(['%s'] * len(OIL_CATEGORIES))
        return f" AND {col} IN ({placeholders})", list(OIL_CATEGORIES)
    if group == 'others':
        placeholders = ','.join(['%s'] * len(OIL_CATEGORIES))
        return f" AND {col} NOT IN ({placeholders})", list(OIL_CATEGORIES)
    return "", []


def _group_filter_list(items, group, get_category):
    """Return the subset of ``items`` matching ``group`` ('oils' / 'others' / None)."""
    if group == 'oils':
        return [x for x in items if (get_category(x) or '') in OIL_CATEGORIES]
    if group == 'others':
        return [x for x in items if (get_category(x) or '') not in OIL_CATEGORIES]
    return items


# ---------- DATE PARSING HELPERS ----------
try:
    from dateutil import parser as date_parser
    HAS_DATEUTIL = True
except ImportError:
    HAS_DATEUTIL = False


def parse_date_cell(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime(1899, 12, 30) + timedelta(days=float(value))
        except:
            return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        date_formats = [
            '%d/%m/%Y', '%d/%m/%y', '%d-%m-%Y', '%d-%m-%y', '%d.%m.%Y', '%d.%m.%y',
            '%d %b %Y', '%d %B %Y', '%d/%m/%Y %H:%M:%S', '%d-%m-%Y %H:%M:%S',
            '%m/%d/%Y', '%m/%d/%y', '%m-%d-%Y', '%m-%d-%y', '%Y-%m-%d', '%Y/%m/%d',
            '%Y%m%d', '%b %d, %Y', '%B %d, %Y',
        ]
        for fmt in date_formats:
            try:
                dt = datetime.strptime(s, fmt)
                if 2000 <= dt.year <= 2050:
                    return dt
            except ValueError:
                continue
        if HAS_DATEUTIL:
            try:
                dt = date_parser.parse(s, dayfirst=True, fuzzy=False)
                if 2000 <= dt.year <= 2050:
                    return dt
            except:
                pass
        return None
    return None


# ===================== CREATE APP =====================
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "temporary-dev-key")

app.permanent_session_lifetime = timedelta(days=7)
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)
app.config['SESSION_COOKIE_HTTPONLY'] = True

IS_PRODUCTION = os.getenv("FLASK_ENV") == "production" or os.getenv("RENDER")
app.config['SESSION_COOKIE_SAMESITE'] = 'None' if IS_PRODUCTION else 'Lax'
app.config['SESSION_COOKIE_SECURE'] = True if IS_PRODUCTION else False


@app.route('/sw.js')
def service_worker():
    return send_from_directory('static', 'sw.js')


# ===================== CLAIM SERVICES =====================
from services.claim_service import (
    create_claim, get_all_claims, get_claims_by_product, get_claim_by_id,
    update_claim, delete_claim, resolve_claim, search_products_for_claims,
    get_product_batches_for_claim
)

# ===================== LICENSE / TRIAL SYSTEM =====================
import json
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LICENSE_FILE = os.path.join(BASE_DIR, "license.json")
TRIAL_DAYS = 3
MASTER_KEY = "OXSMART-1234-KEY"


def get_trial_end_time():
    now = datetime.now()
    tomorrow = now + timedelta(days=1)
    return datetime(tomorrow.year, tomorrow.month, tomorrow.day, 5, 0, 0)


def init_auth_tables():
    conn = get_connection()
    cursor = conn.cursor()
    is_pg = bool(os.getenv("DATABASE_URL"))
    id_type = "SERIAL PRIMARY KEY" if is_pg else "INTEGER PRIMARY KEY AUTOINCREMENT"
    try:
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS users (
                id {id_type},
                username VARCHAR(100) UNIQUE NOT NULL,
                password VARCHAR(255) NOT NULL,
                role VARCHAR(20) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS user_logs (
                id {id_type},
                user_id INTEGER,
                username VARCHAR(100),
                action VARCHAR(50),
                ip_address VARCHAR(50),
                user_agent TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        print("✅ Auth tables ensured.")
    except Exception as e:
        print(f"❌ Error creating auth tables: {e}")
        conn.rollback()
    finally:
        conn.close()


def init_import_jobs_table():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS import_jobs (
            job_id UUID PRIMARY KEY,
            status TEXT NOT NULL,
            total INTEGER DEFAULT 0,
            processed INTEGER DEFAULT 0,
            errors JSONB DEFAULT '[]',
            result JSONB,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    try:
        cursor.execute("ALTER TABLE import_jobs ALTER COLUMN result TYPE JSONB USING result::jsonb")
    except Exception:
        pass
    conn.commit()
    conn.close()
    print("✅ import_jobs table ensured.")


def update_job_progress(job_id, **kwargs):
    conn = get_connection()
    cursor = conn.cursor()
    set_parts = []
    params = []
    for key, value in kwargs.items():
        if key in ('errors', 'result'):
            set_parts.append(f"{key} = %s::jsonb")
            params.append(json.dumps(value))
        else:
            set_parts.append(f"{key} = %s")
            params.append(value)
    params.append(job_id)
    query = f"UPDATE import_jobs SET {', '.join(set_parts)}, updated_at = CURRENT_TIMESTAMP WHERE job_id = %s"
    cursor.execute(query, params)
    conn.commit()
    conn.close()


init_auth_tables()
init_import_jobs_table()


def load_license_data():
    try:
        if os.path.exists(LICENSE_FILE):
            with open(LICENSE_FILE, "r") as f:
                return json.load(f)
    except Exception as e:
        print(f"Error loading license file: {e}")
    return {"trial_start": None, "licensed": False, "key": None}


def save_license_data(data):
    try:
        with open(LICENSE_FILE, "w") as f:
            json.dump(data, f, indent=2)
        return True
    except Exception as e:
        print(f"Error saving license file: {e}")
        return False


def get_trial_start():
    data = load_license_data()
    start = data.get("trial_start")
    if start:
        try:
            return datetime.fromisoformat(start)
        except:
            return None
    return None


def get_trial_end():
    return get_trial_end_time()


def get_remaining_trial():
    data = load_license_data()
    if data.get("licensed", False):
        return None
    end = get_trial_end()
    if not end:
        return None
    now = datetime.now()
    if now >= end:
        return timedelta(0)
    return end - now


def is_trial_active():
    if load_license_data().get("licensed", False):
        return True
    rem = get_remaining_trial()
    return rem is not None and rem.total_seconds() > 0


# ---------------------- CONTEXT PROCESSOR ----------------------
@app.context_processor
def inject_user():
    if 'user_id' in session:
        return {'current_user': {'username': session.get('username'), 'role': session.get('role')}}
    return {'current_user': None}


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Unauthorized', 'message': 'Please log in'}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        if session.get('role') != 'admin':
            return render_template("error.html", message="Admin access required"), 403
        return f(*args, **kwargs)
    return decorated_function


# ===================== PUBLIC ROUTES =====================
@app.route("/login")
def login():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return render_template("login.html")


@app.route("/signup")
def signup():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return render_template("signup.html")


# ===================== PROTECTED ROUTES =====================
# Two pages per section — 'others' (default) and 'oils'.
# The template receives a ``group`` variable and passes it to every API call.
@app.route("/")
@login_required
def dashboard():
    return render_template("dashboard.html")


# ---- PRODUCTS ----
@app.route("/products")
@login_required
def products():
    return render_template("products.html", group="others")


@app.route("/products/oils")
@login_required
def products_oils():
    return render_template("products.html", group="oils")


# ---- SALES ----
@app.route("/sales")
@login_required
def sales():
    return render_template("sales.html", group="others")


@app.route("/sales/oils")
@login_required
def sales_oils():
    return render_template("sales.html", group="oils")


# ---- PURCHASES ----
@app.route("/purchases")
@login_required
def purchases():
    return render_template("purchases.html", group="others")


@app.route("/purchases/oils")
@login_required
def purchases_oils():
    return render_template("purchases.html", group="oils")


# ---- TODAY'S SALES ----
@app.route("/today-sales")
@login_required
def today_sales():
    return render_template("today_sales.html", group="others")


@app.route("/today-sales/oils")
@login_required
def today_sales_oils():
    return render_template("today_sales.html", group="oils")


@app.route("/analytics")
@login_required
def analytics():
    return render_template("analytics.html")


@app.route("/archive")
@login_required
def archive():
    return render_template("archive.html")


@app.route("/admin/users")
@admin_required
def admin_users():
    return render_template("admin_users.html")


@app.route('/herbal-shop')
@login_required
def herbal_shop():
    role = session.get('role')
    username = session.get('username', '').lower()
    if role != 'admin' and username != 'oxbee':
        return render_template("error.html", message="Admin access required"), 403
    return render_template('herbal_shop.html')


# ===================== CLAIMS ROUTES =====================
@app.route('/claims')
@login_required
def claims_page():
    return render_template('claims.html')


@app.route('/api/claims', methods=['GET'])
@login_required
def api_get_claims():
    try:
        claims = get_all_claims()
        return jsonify({'success': True, 'claims': claims})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/claims/search', methods=['GET'])
@login_required
def api_search_products_for_claims():
    keyword = request.args.get('q', '')
    if not keyword or len(keyword) < 2:
        return jsonify({'success': True, 'products': []})
    try:
        products = search_products_for_claims(keyword)
        return jsonify({'success': True, 'products': products})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/claims/product/<int:product_id>/batches', methods=['GET'])
@login_required
def api_get_product_batches_for_claim(product_id):
    try:
        batches = get_product_batches_for_claim(product_id)
        return jsonify({'success': True, 'batches': batches})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/claims', methods=['POST'])
@login_required
def api_create_claim():
    data = request.json
    try:
        claim_id = create_claim(
            product_id=data['product_id'], batch_id=data['batch_id'],
            product_name=data['product_name'], brand=data.get('brand', ''),
            category=data.get('category', ''), issue_type=data['issue_type'],
            description=data.get('description', ''), quantity=int(data['quantity'])
        )
        return jsonify({'success': True, 'claim_id': claim_id})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@app.route('/api/claims/<int:claim_id>', methods=['PUT'])
@login_required
def api_update_claim(claim_id):
    data = request.json
    try:
        success = update_claim(claim_id=claim_id, issue_type=data['issue_type'], description=data.get('description', ''), quantity=int(data['quantity']))
        return jsonify({'success': success})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@app.route('/api/claims/<int:claim_id>', methods=['DELETE'])
@login_required
def api_delete_claim(claim_id):
    try:
        success = delete_claim(claim_id)
        return jsonify({'success': success})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@app.route('/api/claims/<int:claim_id>/resolve', methods=['POST'])
@login_required
def api_resolve_claim(claim_id):
    try:
        success = resolve_claim(claim_id)
        return jsonify({'success': success})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


# ===================== LICENSE API =====================
@app.route('/api/license/status', methods=['GET'])
def api_license_status():
    data = load_license_data()
    if not data.get("trial_start"):
        data["trial_start"] = datetime.now().isoformat()
        save_license_data(data)
        data = load_license_data()
    rem = get_remaining_trial()
    return jsonify({
        "licensed": data.get("licensed", False),
        "trial_active": is_trial_active(),
        "remaining_seconds": int(rem.total_seconds()) if rem else 0,
        "trial_days": TRIAL_DAYS
    })


@app.route('/api/license/activate', methods=['POST'])
def api_license_activate():
    req = request.json
    if req.get("key", "").strip() == MASTER_KEY:
        data = load_license_data()
        data["licensed"] = True
        data["key"] = MASTER_KEY
        save_license_data(data)
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Invalid license key"}), 400


# ===================== AUTH API =====================
@app.route('/api/auth/login', methods=['POST'])
def api_auth_login():
    data = request.json
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    ua = request.headers.get('User-Agent')
    session.clear()
    user = login_user(data.get('username'), data.get('password'), ip, ua)
    if user:
        session['user_id'] = user['id']
        session['username'] = user['username']
        session['role'] = user['role']
        session.permanent = True
        return jsonify({'success': True, 'user': user, 'session_token': str(uuid.uuid4())})
    return jsonify({'success': False, 'error': 'Invalid credentials'}), 401


@app.route('/api/auth/logout', methods=['POST'])
@login_required
def api_auth_logout():
    logout_user(session.get('user_id'), session.get('username'), request.headers.get('X-Forwarded-For', request.remote_addr), request.headers.get('User-Agent'))
    session.clear()
    return jsonify({'success': True})


@app.route('/api/auth/signup', methods=['POST'])
def api_auth_signup():
    data = request.json
    count = count_admins()
    role = 'admin' if (count < 2 and data.get('role') == 'admin') else 'user'
    if create_user(data.get('username'), data.get('password'), role):
        return jsonify({'success': True})
    return jsonify({'success': False, 'error': 'Username already exists'}), 400


@app.route('/api/auth/check', methods=['GET'])
def api_auth_check():
    if 'user_id' in session:
        return jsonify({'logged_in': True, 'id': session.get('user_id'), 'username': session.get('username'), 'role': session.get('role')})
    return jsonify({'logged_in': False})


@app.route('/api/auth/admin_exists', methods=['GET'])
def api_auth_admin_exists():
    return jsonify({'admin_exists': admin_exists()})


@app.route('/api/auth/admin_count', methods=['GET'])
def api_auth_admin_count():
    return jsonify({'admin_count': count_admins()})


# ===================== USER MANAGEMENT =====================
@app.route("/admin/users/<int:user_id>/edit")
@admin_required
def admin_edit_user(user_id):
    user = get_user_by_id(user_id)
    if not user:
        return render_template("error.html", message="User not found"), 404
    return render_template("edit_user.html", user_id=user_id, is_oxbee=is_protected_user(user_id), is_self=(session.get('user_id') == user_id))


@app.route('/api/user/logs', methods=['GET'])
@login_required
def api_user_logs():
    current_username = session.get('username')
    current_user_role = session.get('role')
    if current_user_role != 'admin':
        return jsonify({'success': False, 'error': 'Unauthorized - Admin access required'}), 403

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM users WHERE username = 'oxbee'")
        oxbee_row = cursor.fetchone()
        OXBEE_USER_ID = oxbee_row[0] if oxbee_row else 1

        user_id = request.args.get('user_id', type=int)
        action = request.args.get('action')
        limit = request.args.get('limit', 50, type=int)
        offset = request.args.get('offset', 0, type=int)

        query = "SELECT id, user_id, action, ip_address, user_agent, timestamp FROM user_logs WHERE 1=1"
        params = []
        conditions = []

        if current_username.lower() != 'oxbee':
            conditions.append("user_id != %s")
            params.append(OXBEE_USER_ID)

        if user_id:
            if current_username.lower() != 'oxbee' and user_id == OXBEE_USER_ID:
                conn.close()
                return jsonify({'success': False, 'error': 'Unauthorized - Cannot view system administrator logs'}), 403
            conditions.append("user_id = %s")
            params.append(user_id)
        if action:
            conditions.append("action = %s")
            params.append(action)

        if conditions:
            query += " AND " + " AND ".join(conditions)

        count_query = "SELECT COUNT(*) FROM user_logs"
        count_params = []
        count_conditions = []

        if current_username.lower() != 'oxbee':
            count_conditions.append("user_id != %s")
            count_params.append(OXBEE_USER_ID)

        if user_id and not (current_username.lower() != 'oxbee' and user_id == OXBEE_USER_ID):
            count_conditions.append("user_id = %s")
            count_params.append(user_id)
        if action:
            count_conditions.append("action = %s")
            count_params.append(action)

        if count_conditions:
            count_query += " WHERE " + " AND ".join(count_conditions)

        cursor.execute(count_query, count_params)
        total = cursor.fetchone()[0]

        query += " ORDER BY timestamp DESC LIMIT %s OFFSET %s"
        params.extend([limit, offset])
        cursor.execute(query, params)
        logs = cursor.fetchall()

        result = []
        for log in logs:
            log_dict = {'id': log[0], 'user_id': log[1], 'action': log[2], 'ip_address': log[3], 'user_agent': log[4], 'timestamp': log[5].isoformat() if log[5] else None}
            if log[1]:
                cursor.execute("SELECT username FROM users WHERE id = %s", (log[1],))
                user_row = cursor.fetchone()
                log_dict['username'] = user_row[0] if user_row else 'Unknown'
            result.append(log_dict)

        conn.close()
        return jsonify({'success': True, 'logs': result, 'total': total, 'limit': limit, 'offset': offset})
    except Exception as e:
        print(f"❌ Error in api_user_logs: {str(e)}")
        conn.close()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route("/logs")
@login_required
def logs():
    current_username = session.get('username')
    if session.get('role') != 'admin':
        return render_template("error.html", message="Unauthorized - Admin access required"), 403
    return render_template("logs.html", is_oxbee=(current_username.lower() == 'oxbee'))


@app.route('/api/admin/users/<int:user_id>', methods=['GET'])
@admin_required
def api_admin_get_user(user_id):
    user = get_user_by_id(user_id)
    return jsonify({'success': True, 'user': user}) if user else (jsonify({'success': False}), 404)


@app.route('/api/admin/users/<int:user_id>/password', methods=['PUT'])
@admin_required
def api_admin_update_user_password(user_id):
    if is_protected_user(user_id) and session.get('user_id') != user_id:
        return jsonify({'success': False, 'error': 'Cannot modify the system administrator (oxbee)'}), 403
    pwd = request.json.get('password')
    if not pwd or len(pwd) < 4:
        return jsonify({'success': False, 'error': 'Password too short'}), 400
    return jsonify({'success': True}) if update_user_password(user_id, pwd) else (jsonify({'success': False}), 400)


@app.route('/api/admin/users', methods=['GET'])
@admin_required
def api_admin_get_users():
    return jsonify(get_all_users())


@app.route('/api/admin/users', methods=['POST'])
@admin_required
def api_admin_create_user():
    data = request.json
    if create_user(data.get('username'), data.get('password'), data.get('role', 'user')):
        return jsonify({'success': True})
    return jsonify({'success': False, 'error': 'Username exists'}), 400


@app.route('/api/admin/users/<int:user_id>', methods=['DELETE'])
@admin_required
def api_admin_delete_user(user_id):
    if user_id == session.get('user_id') or is_protected_user(user_id):
        return jsonify({'success': False, 'error': 'Cannot delete this user'}), 400
    result = delete_user(user_id)
    return jsonify({'success': True}) if result['success'] else (jsonify({'success': False, 'error': result['error']}), 400)


@app.route('/api/admin/users/<int:user_id>/role', methods=['PUT'])
@admin_required
def api_admin_update_role(user_id):
    if user_id == session.get('user_id') or is_protected_user(user_id):
        return jsonify({'success': False, 'error': 'Cannot modify this user'}), 400
    role = request.json.get('role')
    if role not in ['admin', 'user']:
        return jsonify({'success': False, 'error': 'Invalid role'}), 400
    return jsonify({'success': True}) if update_user_role(user_id, role) else (jsonify({'success': False}), 400)


# ===================== DASHBOARD API =====================
@app.route('/api/dashboard/summary', methods=['GET'])
@login_required
def api_dashboard_summary():
    date_str = request.args.get('date')
    start, end = request.args.get('start_datetime'), request.args.get('end_datetime')
    sel_date = datetime.strptime(date_str, '%Y-%m-%d').date() if date_str else None
    low_stock = get_low_stock_products(threshold=10)
    return jsonify({
        'sales': get_today_sales(sel_date, start, end),
        'profit': get_today_profit(sel_date, start, end),
        'total_products': get_total_products(),
        'total_batches': get_total_batches(),
        'low_stock_count': len(low_stock),
        'low_stock_products': low_stock
    })


@app.route('/api/dashboard/top_products', methods=['GET'])
@login_required
def api_dashboard_top_products():
    date_str = request.args.get('date')
    sel_date = datetime.strptime(date_str, '%Y-%m-%d').date() if date_str else None
    top = get_top_products(sel_date, int(request.args.get('limit', 10)), request.args.get('start_datetime'), request.args.get('end_datetime'))
    return jsonify([{'name': r[0], 'brand': r[1] or '', 'category': r[2] or '', 'qty': int(r[3])} for r in top])


@app.route('/api/dashboard/sales_history', methods=['GET'])
@login_required
def api_dashboard_sales_history():
    date_str = request.args.get('date')
    sel_date = datetime.strptime(date_str, '%Y-%m-%d').date() if date_str else None
    history = get_sales_history(sel_date, request.args.get('start_datetime'), request.args.get('end_datetime'))
    return jsonify([{'date': r[0].isoformat() if hasattr(r[0], 'isoformat') else str(r[0]), 'total_sales': float(r[1]), 'profit': float(r[2]), 'discount': float(r[3])} for r in history])


# ===================== PURCHASES API =====================
def serialize_purchase(p):
    p_copy = p.copy()
    if 'date' in p_copy and p_copy['date']:
        p_copy['date'] = p_copy['date'].isoformat() if hasattr(p_copy['date'], 'isoformat') else str(p_copy['date'])
    return p_copy


@app.route('/api/purchases', methods=['GET'])
@login_required
def api_get_purchases():
    category = request.args.get('category')
    exclude_category = request.args.get('exclude_category')
    group = request.args.get('group')
    show_updated = request.args.get('show_updated_only', 'false').lower() == 'true'

    purchases = get_all_purchases()
    if category:
        purchases = [p for p in purchases if p.get('category') == category]
    if exclude_category:
        purchases = [p for p in purchases if p.get('category') != exclude_category]
    purchases = _group_filter_list(purchases, group, lambda p: p.get('category'))
    if show_updated:
        purchases = [p for p in purchases if p.get('action') != 'added']
    return jsonify([serialize_purchase(p) for p in purchases])


@app.route('/api/purchases/filter', methods=['GET'])
@login_required
def api_filter_purchases():
    start, end = request.args.get('start_date'), request.args.get('end_date')
    group = request.args.get('group')
    if not start or not end:
        return jsonify([])
    purchases = get_purchases_by_date_range(
        start, end,
        date_type=request.args.get('date_type', 'original'),
        show_updated_only=request.args.get('show_updated_only', 'false').lower() == 'true'
    )
    if request.args.get('category'):
        purchases = [p for p in purchases if p.get('category') == request.args.get('category')]
    if request.args.get('exclude_category'):
        purchases = [p for p in purchases if p.get('category') != request.args.get('exclude_category')]
    purchases = _group_filter_list(purchases, group, lambda p: p.get('category'))
    return jsonify([serialize_purchase(p) for p in purchases])


@app.route('/api/purchases/suggestions/name', methods=['GET'])
@login_required
def api_suggest_name():
    q = request.args.get('q', '')
    if not q:
        return jsonify([])
    suggs = get_product_suggestions(q)
    if request.args.get('category'):
        suggs = [s for s in suggs if s.get('category') == request.args.get('category')]
    return jsonify(suggs)


@app.route('/api/purchases/suggestions/category', methods=['GET'])
@login_required
def api_suggest_category():
    q = request.args.get('q', '')
    if not q:
        return jsonify([])
    return jsonify(get_category_suggestions(q))


@app.route('/api/purchases/suggestions/source', methods=['GET'])
@login_required
def api_suggest_source():
    q = request.args.get('q', '')
    if not q:
        return jsonify([])
    from services.purchase_service import get_source_suggestions
    return jsonify(get_source_suggestions(q))


@app.route('/api/purchases/suggestions/product', methods=['GET'])
@login_required
def api_suggest_product():
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify([])
    from services.purchase_service import search_products_by_name_or_brand
    return jsonify(search_products_by_name_or_brand(keyword=q, category=request.args.get('category'), exclude_category=request.args.get('exclude_category')))


@app.route('/api/purchases/<int:batch_id>/history', methods=['GET'])
@login_required
def api_purchase_history(batch_id):
    from services.purchase_service import get_purchase_history, get_sold_history, get_batch_update_history
    history = get_purchase_history(batch_id)
    if not history:
        return jsonify({'success': False, 'error': 'Batch not found'}), 404
    sold = get_sold_history(batch_id)
    return jsonify({'success': True, 'purchase': history, 'sold_items': sold, 'total_sold': sum(i['quantity'] for i in sold), 'update_history': get_batch_update_history(batch_id)})


@app.route('/api/purchases/<int:batch_id>', methods=['PUT'])
@login_required
def api_update_purchase(batch_id):
    data = request.json or {}
    try:
        new_id = update_product(
            batch_id=batch_id, name=data['name'], brand=data['brand'], category=data.get('category', DEFAULT_CATEGORY),
            quantity=int(data['quantity']), cost_price=float(data['cost_price']), discount=float(data.get('discount', 0) or 0),
            selling_price=float(data['selling_price']), source=data.get('source', 'Unknown'),
            update_mode=data.get('update_mode', 'auto'), keep_sold_with_old=data.get('keep_sold_with_old', True)
        )
        return jsonify({'success': True, 'new_batch_id': new_id})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@app.route('/api/purchases', methods=['POST'])
@login_required
def api_add_purchase():
    data = request.json
    incoming_id = data.get('batch_id')
    if incoming_id not in (None, '', 0, '0'):
        try:
            incoming_id = int(incoming_id)
        except:
            incoming_id = None
        if incoming_id:
            conn = get_connection()
            try:
                cur = conn.cursor()
                cur.execute("SELECT 1 FROM purchase_batches WHERE id = %s", (incoming_id,))
                if cur.fetchone():
                    data['update_mode'] = 'update'
                    return api_update_purchase(incoming_id)
            finally:
                conn.close()
    try:
        batch_id = add_purchase(name=data['name'], brand=data['brand'], category=data.get('category', DEFAULT_CATEGORY), quantity=int(data['quantity']), cost_price=float(data['cost_price']), discount=float(data.get('discount', 0)), selling_price=float(data['selling_price']), source=data.get('source', 'Unknown'))
        return jsonify({'success': True, 'batch_id': batch_id})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@app.route('/api/purchases/pdf', methods=['POST'])
@login_required
def api_purchases_pdf():
    try:
        data = request.json
        purchases = data.get('purchases', [])
        from_date = data.get('from_date', '')
        to_date = data.get('to_date', '')
        totals = data.get('totals', {})

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), rightMargin=12, leftMargin=12, topMargin=20, bottomMargin=20)
        styles = getSampleStyleSheet()
        elements = []

        elements.append(Paragraph("📦 Purchases Report", styles["Title"]))
        elements.append(Spacer(1, 6))
        elements.append(Paragraph(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", styles["Normal"]))
        if from_date and to_date:
            elements.append(Paragraph(f"Date Range: {from_date} → {to_date}", styles["Normal"]))
        elements.append(Spacer(1, 12))

        col_widths = [0.8 * cm, 4.5 * cm, 2.8 * cm, 0.8 * cm, 0.8 * cm, 0.8 * cm, 0.8 * cm, 1.2 * cm, 1.2 * cm, 1.8 * cm, 1.8 * cm, 1.8 * cm, 1.8 * cm]

        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.enums import TA_LEFT

        cell_style = ParagraphStyle('CellStyle', fontName='Helvetica', fontSize=6, leading=7, alignment=TA_LEFT, wordWrap='LTR')
        header_style = ParagraphStyle('HeaderStyle', fontName='Helvetica-Bold', fontSize=7, leading=8, alignment=TA_LEFT, wordWrap='LTR')

        headers = ["ID", "Name", "Brand", "Qty", "Remaining", "Sold", "Claimed", "Cost", "Discount", "Total", "Selling", "Source", "Date/Time"]
        header_cells = [Paragraph(h, header_style) for h in headers]

        table_data = [header_cells]
        total_qty = total_cost = total_discount = total_selling = total_claimed = total_claimed_cost = total_sold_summary = 0

        for p in purchases:
            date_str = p.get('date', '')
            if date_str:
                try:
                    if isinstance(date_str, str):
                        dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                        date_str = dt.strftime("%Y-%m-%d %H:%M")
                    else:
                        date_str = str(date_str)[:16]
                except:
                    date_str = str(date_str)[:16]

            qty = p.get("quantity", 0)
            remaining = p.get("remaining_quantity", 0)
            claimed = p.get("claimed_quantity", 0)
            sold = qty - remaining
            cost = p.get("cost_price", 0)

            row = [
                Paragraph(str(p.get("batch_id", '') or ''), cell_style),
                Paragraph(str(p.get("name", '') or ''), cell_style),
                Paragraph(str(p.get("brand", '') or ''), cell_style),
                Paragraph(str(qty), cell_style),
                Paragraph(str(remaining), cell_style),
                Paragraph(str(sold), cell_style),
                Paragraph(str(claimed), cell_style),
                Paragraph(f"₵{cost:.2f}", cell_style),
                Paragraph(f"₵{p.get('discount', 0):.2f}", cell_style),
                Paragraph(f"₵{p.get('total_cost', 0):.2f}", cell_style),
                Paragraph(f"₵{p.get('selling_price', 0):.2f}", cell_style),
                Paragraph(str(p.get('source', 'Unknown') or ''), cell_style),
                Paragraph(date_str, cell_style)
            ]
            table_data.append(row)

            total_qty += qty
            total_cost += p.get("total_cost", 0)
            total_discount += p.get("discount", 0)
            total_selling += p.get("selling_price", 0) * qty
            total_claimed += claimed
            total_claimed_cost += claimed * cost
            total_sold_summary += sold

        table = Table(table_data, repeatRows=1, splitByRow=1, hAlign='LEFT', colWidths=col_widths)
        style = TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#00CFCF")),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (3, 1), (-2, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.3, colors.black),
            ('FONTSIZE', (0, 0), (-1, -1), 6),
            ('LEFTPADDING', (0, 0), (-1, -1), 1),
            ('RIGHTPADDING', (0, 0), (-1, -1), 1),
        ])
        for i in range(1, len(table_data)):
            style.add('BACKGROUND', (0, i), (-1, i), colors.whitesmoke if i % 2 == 0 else colors.lightgrey)
        table.setStyle(style)
        elements.append(table)
        elements.append(Spacer(1, 12))

        elements.append(Paragraph("📊 Summary", styles["Heading2"]))
        elements.append(Spacer(1, 6))

        total_batches = len(purchases)
        total_remaining = total_qty - total_sold_summary
        total_sold = total_sold_summary

        adjusted_capital = totals.get('adjusted_capital')
        if adjusted_capital is None:
            adjusted_capital = total_cost - total_claimed_cost
        else:
            adjusted_capital = float(adjusted_capital)

        total_profit = totals.get('total_profit')
        if total_profit is None:
            total_profit = total_selling - (total_cost - total_discount)
        else:
            total_profit = float(total_profit)

        total_good = total_remaining - total_claimed
        sold_percentage = (total_sold / total_qty * 100) if total_qty > 0 else 0

        summary_data = [
            ["Metric", "Value"],
            ["📦 Total Batches", str(total_batches)],
            ["🧾 Total Quantity", str(total_qty)],
            ["📦 Remaining Stock", str(total_remaining)],
            ["📊 Sold", str(total_sold)],
            ["⚠️ Claimed", str(total_claimed)],
            ["✅ Good Stock", str(max(total_good, 0))],
            ["💰 Total Cost", f"₵{total_cost:.2f}"],
            ["📉 Claimed Cost", f"₵{total_claimed_cost:.2f}"],
            ["💰 Capital (Adjusted)", f"₵{adjusted_capital:.2f}"],
            ["📈 Total Selling Price", f"₵{total_selling:.2f}"],
            ["📉 Total Discount", f"₵{total_discount:.2f}"],
            ["💎 Total Profit", f"₵{total_profit:.2f}"],
            ["📊 Sold %", f"{sold_percentage:.1f}%"]
        ]

        summary_table = Table(summary_data, colWidths=[6 * cm, 6 * cm], hAlign='CENTER')
        summary_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#1E3A5F")),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
            ('BACKGROUND', (1, 1), (1, -1), colors.lightgrey),
            ('TEXTCOLOR', (1, 1), (1, -1), colors.black),
            ('BACKGROUND', (0, 1), (0, -1), colors.whitesmoke),
        ]))
        elements.append(summary_table)

        profit_color = colors.green if total_profit >= 0 else colors.red
        profit_status = "📈 PROFIT" if total_profit >= 0 else "📉 LOSS"
        elements.append(Spacer(1, 6))
        elements.append(Paragraph(f"<font color='{profit_color}' size='14'><b>{profit_status}: ₵{total_profit:.2f}</b></font>", styles["Normal"]))

        doc.build(elements)
        buffer.seek(0)
        return send_file(buffer, as_attachment=True, download_name=f"Purchases_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf", mimetype='application/pdf')
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ===================== PRODUCT API =====================
@app.route('/api/low_stock/split', methods=['GET'])
@login_required
def api_low_stock_split():
    from services.low_stock_service import get_low_stock_split
    try:
        return jsonify({'success': True, **get_low_stock_split(request.args.get('category'))})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/products', methods=['GET'])
@login_required
def api_get_products():
    data = get_all_products_service()
    products = data.get('products', [])
    category = request.args.get('category')
    exclude_category = request.args.get('exclude_category')
    group = request.args.get('group')

    filtered = []
    for p in products:
        p_cat = p.get('category', '') or ''
        if category and p_cat.lower() != category.lower():
            continue
        if exclude_category and p_cat.lower() == exclude_category.lower():
            continue
        if group == 'oils' and p_cat not in OIL_CATEGORIES:
            continue
        if group == 'others' and p_cat in OIL_CATEGORIES:
            continue
        filtered.append(p)

    groups = {}
    for p in filtered:
        key = (p['name'].lower(), p['brand'].lower())
        if key not in groups:
            groups[key] = {'product_id': p['product_id'], 'name': p['name'], 'brand': p['brand'], 'category': p['category'], 'cost_price': p['cost_price'], 'selling_price': p['selling_price'], 'discount': p['discount'], 'stock': 0, 'batches': []}
        if p.get('batches'):
            groups[key]['batches'].extend(p['batches'])
        groups[key]['stock'] += p.get('stock', 0)

    result, total_batches = [], 0
    for group_obj in groups.values():
        seen = set()
        unique = []
        for b in group_obj['batches']:
            if b.get('batch_id') not in seen:
                seen.add(b.get('batch_id'))
                unique.append(b)
        group_obj['batches'] = unique
        group_obj['stock'] = sum(b.get('remaining_quantity', 0) for b in unique)
        total_batches += len(unique)
        result.append(group_obj)
    return jsonify({'products': result, 'total_products': len(result), 'total_batches': total_batches})


@app.route('/api/products', methods=['POST'])
@login_required
def api_add_product():
    data = request.json
    incoming_id = data.get('batch_id')
    if incoming_id not in (None, '', 0, '0'):
        try:
            incoming_id = int(incoming_id)
        except:
            incoming_id = None
        if incoming_id:
            conn = get_connection()
            try:
                cur = conn.cursor()
                cur.execute("SELECT 1 FROM purchase_batches WHERE id = %s", (incoming_id,))
                if cur.fetchone():
                    data['update_mode'] = 'update'
                    data.setdefault('category', DEFAULT_CATEGORY)
                    return api_update_purchase(incoming_id)
            finally:
                conn.close()
    try:
        batch_id = add_purchase(name=data['name'], brand=data['brand'], category=data.get('category', DEFAULT_CATEGORY), quantity=int(data['quantity']), cost_price=float(data['cost_price']), discount=float(data.get('discount', 0)), selling_price=float(data['selling_price']))
        return jsonify({'success': True, 'batch_id': batch_id})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@app.route('/api/products/<int:product_id>', methods=['DELETE'])
@login_required
def api_delete_product(product_id):
    try:
        if request.args.get('type') == 'clean':
            delete_product_clean_all(product_id)
        else:
            delete_product_keep_history(product_id)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@app.route('/api/products/low_stock', methods=['GET'])
@login_required
def api_low_stock():
    category = request.args.get('category')
    exclude_category = request.args.get('exclude_category')
    group = request.args.get('group')
    threshold = int(request.args.get('threshold', 10))
    low = get_low_stock_products(threshold)
    filtered = []
    for p in low:
        cat = p[2] or ''
        if category and cat.lower() != category.lower():
            continue
        if exclude_category and cat.lower() == exclude_category.lower():
            continue
        if group == 'oils' and cat not in OIL_CATEGORIES:
            continue
        if group == 'others' and cat in OIL_CATEGORIES:
            continue
        filtered.append(p)
    return jsonify([{'name': p[0], 'brand': p[1], 'category': p[2] or '', 'stock': p[3], 'batch_ids': p[4]} for p in filtered])


@app.route('/api/batches/<int:batch_id>', methods=['DELETE'])
@login_required
def api_delete_batch(batch_id):
    try:
        if request.args.get('type') == 'clean':
            delete_batch_clean_all(batch_id)
        else:
            delete_batch(batch_id)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


# ===================== SALES API =====================
@app.route('/api/sales/products', methods=['GET'])
@login_required
def api_sales_products():
    category = request.args.get('category')
    exclude_category = request.args.get('exclude_category')
    group = request.args.get('group')

    conn = get_connection()
    cursor = conn.cursor()
    try:
        query = """
            SELECT p.id, p.name, p.brand, p.category, p.cost_price, p.selling_price, p.discount, p.stock,
                   pb.id, pb.remaining_quantity, pb.cost_price, pb.selling_price, pb.discount, pb.date, pb.claimed_quantity, pb.is_faulty, pb.quantity
            FROM products p
            LEFT JOIN purchase_batches pb ON p.id = pb.product_id AND pb.remaining_quantity > 0
            WHERE p.stock > 0 AND NOT EXISTS (SELECT 1 FROM deleted_products dp WHERE dp.product_id = p.id AND dp.action = 'PERMANENTLY DELETED' AND dp.source = 'product')
        """
        params = []
        if category:
            query += " AND p.category = %s"
            params.append(category)
        if exclude_category:
            query += " AND p.category != %s"
            params.append(exclude_category)
        frag, gp = _group_filter_sql("p.category", group)
        if frag:
            query += frag
            params.extend(gp)
        query += " ORDER BY p.name ASC, pb.date ASC"
        cursor.execute(query, params)
        rows = cursor.fetchall()

        pmap = {}
        for r in rows:
            pid = r[0]
            if pid not in pmap:
                pmap[pid] = {'product_id': pid, 'name': r[1], 'brand': r[2] or '', 'category': r[3] or '', 'cost_price': float(r[4] or 0), 'selling_price': float(r[5] or 0), 'discount': float(r[6] or 0), 'stock': 0, 'batches': []}
            if r[8] is not None:
                pmap[pid]['batches'].append({'batch_id': r[8], 'remaining_quantity': int(r[9] or 0), 'cost_price': float(r[10] or 0), 'selling_price': float(r[11] or 0), 'discount': float(r[12] or 0), 'date': r[13].isoformat() if r[13] else None, 'claimed_quantity': int(r[14] or 0), 'is_faulty': r[15] or False, 'batch_quantity': int(r[16] or 0)})
                pmap[pid]['stock'] += int(r[9] or 0)
        return jsonify(list(pmap.values()))
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()


@app.route('/api/sales/batches/<int:product_id>', methods=['GET'])
@login_required
def api_sales_batches(product_id):
    return jsonify(get_batches_for_product(product_id))


@app.route('/api/sales/batches/by_name', methods=['GET'])
@login_required
def api_sales_batches_by_name():
    name, brand = request.args.get('name'), request.args.get('brand', '')
    if not name:
        return jsonify([])
    conn = get_connection()
    cursor = conn.cursor()
    try:
        if brand:
            cursor.execute("""SELECT pb.id, pb.product_id, pb.quantity, pb.remaining_quantity, pb.cost_price, pb.selling_price, pb.discount, pb.date, pb.is_faulty, pb.claimed_quantity, p.name, p.brand FROM purchase_batches pb JOIN products p ON p.id = pb.product_id WHERE p.name = %s AND p.brand = %s AND pb.remaining_quantity > 0 AND NOT EXISTS (SELECT 1 FROM deleted_products dp WHERE dp.product_id = p.id AND dp.action IN ('PERMANENTLY DELETED', 'PRODUCT DELETED') AND dp.source = 'product') ORDER BY pb.date ASC""", (name, brand))
        else:
            cursor.execute("""SELECT pb.id, pb.product_id, pb.quantity, pb.remaining_quantity, pb.cost_price, pb.selling_price, pb.discount, pb.date, pb.is_faulty, pb.claimed_quantity, p.name, p.brand FROM purchase_batches pb JOIN products p ON p.id = pb.product_id WHERE p.name = %s AND pb.remaining_quantity > 0 AND NOT EXISTS (SELECT 1 FROM deleted_products dp WHERE dp.product_id = p.id AND dp.action IN ('PERMANENTLY DELETED', 'PRODUCT DELETED') AND dp.source = 'product') ORDER BY pb.date ASC""", (name,))
        return jsonify([{'batch_id': r[0], 'product_id': r[1], 'batch_quantity': r[2], 'remaining_quantity': r[3], 'cost_price': float(r[4]), 'selling_price': float(r[5]), 'discount': float(r[6]), 'date': r[7].isoformat() if r[7] else None, 'is_faulty': r[8] or False, 'claimed_quantity': r[9] or 0, 'product_name': r[10], 'product_brand': r[11]} for r in cursor.fetchall()])
    finally:
        conn.close()


@app.route('/api/sales/complete', methods=['POST'])
@login_required
def api_sales_complete():
    data = request.json
    try:
        result = create_multi_sale(data.get('cart_items', []), data.get('sale_datetime'), data.get('selected_batches', []), data.get('payment_method', 'cash'), data.get('cheque_number'), user_id=session.get('user_id'))
        return jsonify({'success': True, 'sale_id': result['sale_id'], 'total': result['total'], 'date': result['date']})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@app.route('/api/sales/reverse/<int:sale_id>', methods=['POST'])
@login_required
def api_reverse_sale(sale_id):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT reversed FROM sales WHERE id = %s", (sale_id,))
        row = cursor.fetchone()
        if not row or row[0] == 1:
            return jsonify({'success': False, 'error': 'Not found or already reversed'}), 400
        cursor.execute("SELECT product_id, batch_id, quantity FROM sales_items WHERE sale_id = %s", (sale_id,))
        for pid, bid, qty in cursor.fetchall():
            cursor.execute("UPDATE purchase_batches SET remaining_quantity = remaining_quantity + %s WHERE id = %s", (qty, bid))
            update_product_stock(cursor, pid)
        cursor.execute("UPDATE sales SET reversed = 1 WHERE id = %s", (sale_id,))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()


@app.route('/api/sales/reverse_items', methods=['POST'])
@login_required
def api_reverse_sale_items():
    data = request.json
    sale_id, items = data.get('sale_id'), data.get('items', [])
    if not sale_id or not items:
        return jsonify({'success': False}), 400
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT reversed, subtotal, discount, profit FROM sales WHERE id = %s", (sale_id,))
        row = cursor.fetchone()
        if not row or row[0] == 1:
            return jsonify({'success': False}), 400
        sub, disc, prof = row[1], row[2], row[3]
        r_sub, r_prof = 0, 0
        for item in items:
            cursor.execute("SELECT product_id, selling_price FROM purchase_batches WHERE id = %s", (item['batch_id'],))
            pid, sp = cursor.fetchone()
            cursor.execute("SELECT cost_price, profit FROM sales_items WHERE sale_id = %s AND batch_id = %s", (sale_id, item['batch_id']))
            cp, ip = cursor.fetchone()
            r_sub += sp * item['quantity']
            r_prof += ip
            cursor.execute("UPDATE purchase_batches SET remaining_quantity = remaining_quantity + %s WHERE id = %s", (item['quantity'], item['batch_id']))
            cursor.execute("DELETE FROM sales_items WHERE sale_id = %s AND batch_id = %s", (sale_id, item['batch_id']))
            update_product_stock(cursor, pid)
        n_sub = sub - r_sub
        n_gross = prof + disc - r_prof
        n_disc = disc * (n_sub / sub) if sub > 0 else 0
        n_prof = n_gross - n_disc
        n_total = max(0, n_sub - n_disc)
        if n_sub == 0:
            cursor.execute("UPDATE sales SET reversed = 1 WHERE id = %s", (sale_id,))
        else:
            cursor.execute("UPDATE sales SET subtotal = %s, discount = %s, total = %s, profit = %s WHERE id = %s", (n_sub, n_disc, n_total, n_prof, sale_id))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()


# ===================== TODAY'S SALES API =====================
@app.route('/api/today_sales', methods=['GET'])
@login_required
def api_today_sales():
    period = request.args.get('period', 'daily')
    start = request.args.get('start_date')
    end = request.args.get('end_date')
    category = request.args.get('category')
    exclude_category = request.args.get('exclude_category')
    group = request.args.get('group')
    batch_id = request.args.get('batch_id', type=int)

    conn = get_connection()
    cursor = conn.cursor()
    try:
        q = """SELECT s.id, p.name, p.brand, p.category, si.quantity, si.selling_price, si.quantity * si.selling_price, s.discount, s.total, COALESCE(si.profit, 0), COALESCE(pb.id, -1), COALESCE(pb.cost_price, 0), s.date, CASE WHEN pb.id IS NULL THEN 1 ELSE 0 END, COALESCE(s.profit, 0), COALESCE(s.payment_method, 'cash'), s.cheque_number, u.username FROM sales s JOIN sales_items si ON s.id = si.sale_id JOIN products p ON p.id = si.product_id LEFT JOIN purchase_batches pb ON pb.id = si.batch_id LEFT JOIN users u ON s.user_id = u.id WHERE s.reversed = 0"""
        params, conds = [], []
        if start and end:
            conds.append("s.date::date BETWEEN %s AND %s")
            params.extend([start, end])
        elif period == 'daily':
            conds.append("s.date::date = CURRENT_DATE")
        elif period == 'weekly':
            conds.append("s.date >= CURRENT_DATE - INTERVAL '6 days'")
        elif period == 'monthly':
            conds.append("EXTRACT(YEAR FROM s.date) = EXTRACT(YEAR FROM CURRENT_DATE) AND EXTRACT(MONTH FROM s.date) = EXTRACT(MONTH FROM CURRENT_DATE)")
        elif period == 'yearly':
            conds.append("EXTRACT(YEAR FROM s.date) = EXTRACT(YEAR FROM CURRENT_DATE)")

        if category:
            conds.append("p.category = %s")
            params.append(category)
        if exclude_category:
            conds.append("p.category != %s")
            params.append(exclude_category)
        frag, gp = _group_filter_sql("p.category", group)
        if frag:
            conds.append(frag.strip()[4:])   # drop leading " AND "
            params.extend(gp)
        if batch_id:
            conds.append("si.batch_id = %s")
            params.append(batch_id)

        if conds:
            q += " AND " + " AND ".join(conds)
        q += " ORDER BY s.date DESC"
        cursor.execute(q, params)
        return jsonify([{
            'sale_id': r[0], 'name': r[1], 'brand': r[2] or '', 'category': r[3] or '',
            'quantity': r[4], 'selling_price': float(r[5]), 'subtotal': float(r[6]),
            'discount': float(r[7]), 'total': float(r[8]), 'profit': float(r[9]),
            'batch_id': r[10], 'cost_price': float(r[11]),
            'sale_date': r[12].isoformat() if hasattr(r[12], 'isoformat') else str(r[12]),
            'is_deleted_batch': bool(r[13]), 'net_profit': float(r[14]),
            'payment_method': r[15], 'cheque_number': r[16],
            'username': r[17] or 'Unknown'
        } for r in cursor.fetchall()])
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()


@app.route('/api/today_sales/pdf', methods=['POST'])
@login_required
def api_today_sales_pdf():
    data = request.json
    sales_data = data.get('sales_data', [])
    period_text = data.get('period_text', 'Sales Report')
    is_admin = data.get('is_admin', False)
    if not sales_data:
        return jsonify({'error': 'No data to export'}), 400

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), rightMargin=12, leftMargin=12, topMargin=20, bottomMargin=20)
    styles = getSampleStyleSheet()
    elements = [Paragraph("Sales Report", styles['Title']), Spacer(1, 0.2 * cm), Paragraph(period_text, styles['Normal']), Spacer(1, 0.2 * cm)]

    seen_sales = set()
    total_sales = total_discount = total_profit = total_items = 0.0

    for sale in sales_data:
        sale_id = sale.get('sale_id')
        if sale_id not in seen_sales:
            total_sales += float(sale.get('total', 0) or 0)
            total_discount += float(sale.get('discount', 0) or 0)
            total_profit += float(sale.get('net_profit', 0) or 0)
            seen_sales.add(sale_id)
        total_items += int(sale.get('quantity', 0) or 0)

    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_LEFT
    cell_style = ParagraphStyle('CellStyle', fontName='Helvetica', fontSize=6, leading=7, alignment=TA_LEFT, wordWrap='LTR')
    header_style = ParagraphStyle('HeaderStyle', fontName='Helvetica-Bold', fontSize=7, leading=8, alignment=TA_LEFT, wordWrap='LTR')

    headers = ["Name", "Brand", "Category", "Qty", "Price", "Subtotal", "Discount", "Total", "Batch", "Sale Date", "Payment"]
    if is_admin:
        headers.extend(["Profit", "Cost", "User"])

    col_widths = [3.5 * cm, 2.5 * cm, 2.0 * cm, 1.0 * cm, 1.5 * cm, 2.0 * cm, 1.5 * cm, 2.0 * cm, 1.5 * cm, 3.0 * cm, 2.0 * cm]
    if is_admin:
        col_widths.extend([1.5 * cm, 1.5 * cm, 2.0 * cm])

    table_data = [[Paragraph(h, header_style) for h in headers]]
    for sale in sales_data:
        sp, cp, qty = float(sale.get('selling_price', 0) or 0), float(sale.get('cost_price', 0) or 0), int(sale.get('quantity', 0) or 0)
        row = [
            Paragraph(str(sale.get('name', '') or ''), cell_style),
            Paragraph(str(sale.get('brand', '') or ''), cell_style),
            Paragraph(str(sale.get('category', '') or ''), cell_style),
            Paragraph(str(qty), cell_style),
            Paragraph(f"₵{sp:.2f}", cell_style),
            Paragraph(f"₵{sale.get('subtotal', 0):.2f}", cell_style),
            Paragraph(f"₵{sale.get('discount', 0):.2f}", cell_style),
            Paragraph(f"₵{sale.get('total', 0):.2f}", cell_style),
            Paragraph(str(sale.get('batch_id', -1) if sale.get('batch_id', -1) != -1 else 'DEL'), cell_style),
            Paragraph(str(sale.get('sale_date', '') or ''), cell_style),
            Paragraph(sale.get('payment_method', 'cash').upper(), cell_style)
        ]
        if is_admin:
            row.extend([Paragraph(f"₵{(sp - cp) * qty:.2f}", cell_style), Paragraph(f"₵{cp:.2f}", cell_style), Paragraph(str(sale.get('username', 'Unknown') or ''), cell_style)])
        table_data.append(row)

    table = Table(table_data, repeatRows=1, splitByRow=1, hAlign='LEFT', colWidths=col_widths)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#1E3A5F")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('GRID', (0, 0), (-1, -1), 0.4, colors.black),
        ('FONTSIZE', (0, 0), (-1, -1), 6),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (3, 1), (-1, -1), 'CENTER'),
        ('LEFTPADDING', (0, 0), (-1, -1), 1),
        ('RIGHTPADDING', (0, 0), (-1, -1), 1),
    ]))
    elements.append(table)
    elements.append(Spacer(1, 0.5 * cm))

    summary_data = [["Metric", "Value"], ["🧾 Total Sales", f"₵{total_sales:.2f}"], ["📦 Total Items", str(total_items)], ["📉 Total Discount", f"₵{total_discount:.2f}"]]
    if is_admin:
        summary_data.insert(2, ["📈 Net Profit", f"₵{total_profit:.2f}"])

    summary_table = Table(summary_data, colWidths=[6 * cm, 6 * cm], hAlign='CENTER')
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#1E3A5F")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
    ]))
    elements.append(summary_table)
    doc.build(elements)
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name=f"SalesReport_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf", mimetype='application/pdf')


# ===================== ANALYTICS API =====================
@app.route('/api/analytics/summary', methods=['GET'])
@login_required
def api_analytics_summary():
    period, start, end = request.args.get('period', 'daily'), request.args.get('start_date'), request.args.get('end_date')
    conn = get_connection()
    cursor = conn.cursor()
    q = "SELECT COALESCE(SUM(s.total), 0), COALESCE(SUM(s.discount), 0), COALESCE(SUM(s.profit), 0), COALESCE(SUM(si.quantity), 0) FROM sales s LEFT JOIN sales_items si ON s.id = si.sale_id WHERE s.reversed = 0"
    params = []
    if start and end:
        q += " AND s.date::date BETWEEN %s AND %s"
        params = [start, end]
    elif period == 'weekly':
        q += " AND s.date >= CURRENT_DATE - INTERVAL '6 days'"
    elif period == 'monthly':
        q += " AND EXTRACT(YEAR FROM s.date) = EXTRACT(YEAR FROM CURRENT_DATE) AND EXTRACT(MONTH FROM s.date) = EXTRACT(MONTH FROM CURRENT_DATE)"
    elif period == 'yearly':
        q += " AND EXTRACT(YEAR FROM s.date) = EXTRACT(YEAR FROM CURRENT_DATE)"
    elif period != 'all':
        q += " AND s.date::date = CURRENT_DATE"
    cursor.execute(q, params)
    r = cursor.fetchone()
    conn.close()
    return jsonify({'items_sold': int(r[3]), 'subtotal': float(r[0]), 'discount': float(r[1]), 'total': float(r[0]), 'profit': float(r[2])})


@app.route('/api/analytics/trend', methods=['GET'])
@login_required
def api_analytics_trend():
    period, start, end = request.args.get('period', 'daily'), request.args.get('start_date'), request.args.get('end_date')
    conn = get_connection()
    cursor = conn.cursor()
    q = "SELECT s.date::date, COALESCE(SUM(s.total), 0), COALESCE(SUM(s.profit), 0) FROM sales s WHERE s.reversed = 0"
    params = []
    if start and end:
        q += " AND s.date::date BETWEEN %s AND %s"
        params = [start, end]
    elif period == 'weekly':
        q += " AND s.date >= CURRENT_DATE - INTERVAL '6 days'"
    elif period == 'monthly':
        q += " AND EXTRACT(YEAR FROM s.date) = EXTRACT(YEAR FROM CURRENT_DATE) AND EXTRACT(MONTH FROM s.date) = EXTRACT(MONTH FROM CURRENT_DATE)"
    elif period == 'yearly':
        q += " AND EXTRACT(YEAR FROM s.date) = EXTRACT(YEAR FROM CURRENT_DATE) GROUP BY DATE_TRUNC('month', s.date) ORDER BY 1 ASC"
    elif period == 'all':
        q += " GROUP BY DATE_TRUNC('month', s.date) ORDER BY 1 ASC LIMIT 24"
    else:
        q += " AND s.date::date = CURRENT_DATE"
    if period not in ['yearly', 'all']:
        q += " GROUP BY s.date::date ORDER BY 1 ASC"
    cursor.execute(q, params)
    rows = cursor.fetchall()
    conn.close()
    res = []
    for r in rows:
        lbl = r[0].strftime('%b %Y') if hasattr(r[0], 'strftime') and period in ['yearly', 'all'] else (r[0].strftime('%Y-%m-%d') if hasattr(r[0], 'strftime') else str(r[0]))
        res.append({'label': lbl, 'sales': float(r[1]), 'profit': float(r[2])})
    return jsonify(res)


@app.route('/api/analytics/top_products', methods=['GET'])
@login_required
def api_analytics_top_products():
    period, limit = request.args.get('period', 'daily'), int(request.args.get('limit', 10))
    start, end = request.args.get('start_date'), request.args.get('end_date')
    conn = get_connection()
    cursor = conn.cursor()
    q = """SELECT p.name, COALESCE(p.brand, ''), COALESCE(p.category, ''), COALESCE(SUM(si.quantity), 0) FROM sales_items si JOIN sales s ON si.sale_id = s.id JOIN products p ON p.id = si.product_id WHERE s.reversed = 0 AND NOT EXISTS (SELECT 1 FROM deleted_products dp WHERE dp.product_id = p.id AND dp.action = 'PERMANENTLY DELETED' AND dp.source = 'product')"""
    params = []
    if start and end:
        q += " AND s.date::date BETWEEN %s AND %s"
        params = [start, end]
    elif period == 'weekly':
        q += " AND s.date >= CURRENT_DATE - INTERVAL '6 days'"
    elif period == 'monthly':
        q += " AND EXTRACT(YEAR FROM s.date) = EXTRACT(YEAR FROM CURRENT_DATE) AND EXTRACT(MONTH FROM s.date) = EXTRACT(MONTH FROM CURRENT_DATE)"
    elif period == 'yearly':
        q += " AND EXTRACT(YEAR FROM s.date) = EXTRACT(YEAR FROM CURRENT_DATE)"
    elif period != 'all':
        q += " AND s.date::date = CURRENT_DATE"
    q += " GROUP BY si.product_id, p.name, p.brand, p.category ORDER BY 4 DESC LIMIT %s"
    params.append(limit)
    cursor.execute(q, params)
    res = [{'name': r[0], 'brand': r[1], 'category': r[2], 'quantity': int(r[3])} for r in cursor.fetchall()]
    conn.close()
    return jsonify(res)


# ===================== SETTINGS API =====================
from services.settings_service import get_user_settings, update_user_settings, format_currency, format_date


@app.route('/settings', methods=['GET'])
@login_required
def settings_page():
    return render_template('settings.html')


@app.route('/api/settings', methods=['GET'])
@login_required
def api_get_settings():
    try:
        return jsonify({'success': True, 'settings': get_user_settings(session.get('user_id'))})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/settings', methods=['POST'])
@login_required
def api_update_settings():
    try:
        if update_user_settings(session.get('user_id'), request.json):
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': 'No settings'}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.context_processor
def inject_settings():
    if 'user_id' in session:
        try:
            s = get_user_settings(session.get('user_id'))
            return {'settings': s, 'format_currency': lambda a: format_currency(a, session.get('user_id')), 'format_date': lambda d: format_date(d, session.get('user_id'))}
        except:
            pass
    return {'settings': {'theme': 'light', 'currency_symbol': '₵', 'currency_code': 'GHS', 'language': 'en', 'date_format': 'DD/MM/YYYY'}}


# ===================== ARCHIVE API =====================
@app.route('/api/archive', methods=['GET'])
@login_required
def api_archive():
    status, page, per_page, search = request.args.get('status', 'ALL'), request.args.get('page', 1, type=int), request.args.get('per_page', 50, type=int), request.args.get('search', '').strip()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        conn.rollback()
        offset = (page - 1) * per_page
        pattern = f"%{search}%" if search else None
        active_items, deleted_items = [], []
        ac, dc = 0, 0
        if status in ['ALL', 'ACTIVE']:
            aq = "SELECT id, name, brand, category, cost_price, selling_price, stock, discount FROM products WHERE NOT EXISTS (SELECT 1 FROM deleted_products dp WHERE dp.product_id = products.id AND dp.action = 'PERMANENTLY DELETED' AND dp.source = 'product')"
            ap = []
            if search:
                aq += " AND (name ILIKE %s OR brand ILIKE %s)"
                ap.extend([pattern, pattern])
            cursor.execute(f"SELECT COUNT(*) FROM ({aq}) sub", ap)
            ac = cursor.fetchone()[0]
            cursor.execute(aq + f" ORDER BY name ASC LIMIT {per_page} OFFSET {offset}", ap)
            for r in cursor.fetchall():
                claims = []
                try:
                    cursor.execute("SELECT id, batch_id, issue_type, description, quantity, status, created_at FROM claims WHERE product_id = %s AND status = 'active' ORDER BY created_at DESC LIMIT 5", (r[0],))
                    claims = [{'claim_id': c[0], 'batch_id': c[1], 'issue_type': c[2], 'description': c[3], 'quantity': c[4], 'status': c[5], 'created_at': c[6]} for c in cursor.fetchall()]
                except:
                    conn.rollback()
                active_items.append({'id': None, 'name': r[1], 'brand': r[2] or '', 'category': r[3] or '', 'cost': float(r[4] or 0), 'price': float(r[5] or 0), 'stock': int(r[6] or 0), 'discount': float(r[7] or 0), 'action': 'ACTIVE', 'date': '', 'source': 'active', 'is_permanent': False, 'batch_id': None, 'batch_quantity': None, 'batch_remaining': None, 'product_id': r[0], 'claims': claims, 'has_claims': len(claims) > 0, 'total_claimed': sum(c['quantity'] for c in claims)})

        if status == 'ALL' or status in ['DELETED', 'PRODUCT DELETED', 'BATCH DELETED', 'PERMANENTLY DELETED', 'UPDATED']:
            dq = "SELECT id, name, brand, cost_price, selling_price, stock, category, discount, action, deleted_at, batch_id, batch_quantity, batch_remaining, product_id, source FROM deleted_products WHERE 1=1"
            dp = []
            if search:
                dq += " AND (name ILIKE %s OR brand ILIKE %s)"
                dp.extend([pattern, pattern])
            if status != 'ALL':
                dq += " AND action = %s"
                dp.append(status)
            cursor.execute(f"SELECT COUNT(*) FROM ({dq}) sub", dp)
            dc = cursor.fetchone()[0]
            cursor.execute(dq + " ORDER BY deleted_at DESC LIMIT %s OFFSET %s", dp + [per_page, offset])
            for r in cursor.fetchall():
                claims = []
                if r[13]:
                    try:
                        cursor.execute("SELECT id, batch_id, issue_type, description, quantity, status, created_at FROM claims WHERE product_id = %s ORDER BY created_at DESC LIMIT 5", (r[13],))
                        claims = [{'claim_id': c[0], 'batch_id': c[1], 'issue_type': c[2], 'description': c[3], 'quantity': c[4], 'status': c[5], 'created_at': c[6]} for c in cursor.fetchall()]
                    except:
                        conn.rollback()
                deleted_items.append({'id': r[0], 'name': r[1] or '-', 'brand': r[2] or '-', 'category': r[6] or '-', 'cost': float(r[3] or 0), 'price': float(r[4] or 0), 'stock': int(r[5] or 0), 'discount': float(r[7] or 0), 'action': str(r[8]).upper() if r[8] else 'UNKNOWN', 'date': r[9].isoformat() if hasattr(r[9], 'isoformat') else str(r[9]), 'source': r[14] or 'product', 'is_permanent': str(r[8]).upper() == 'PERMANENTLY DELETED', 'batch_id': r[10], 'batch_quantity': r[11], 'batch_remaining': r[12], 'product_id': r[13], 'claims': claims, 'has_claims': len(claims) > 0, 'total_claimed': sum(c['quantity'] for c in claims)})

        total = ac + dc
        if total == 0:
            return jsonify({'items': [], 'total': 0, 'page': page, 'per_page': per_page, 'total_pages': 1, 'has_more': False})
        combined = active_items + deleted_items
        combined.sort(key=lambda x: (datetime.max.replace(tzinfo=timezone.utc) if x['action'] == 'ACTIVE' else (datetime.fromisoformat(x['date']) if x['date'] else datetime.min.replace(tzinfo=timezone.utc)), x['name']), reverse=True)
        total_pages = (total + per_page - 1) // per_page
        return jsonify({'items': combined, 'total': total, 'page': page, 'per_page': per_page, 'total_pages': total_pages, 'has_more': offset + len(combined) < total})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()


@app.route('/api/archive/restore', methods=['POST'])
@login_required
def api_archive_restore():
    if not request.json.get('archive_id'):
        return jsonify({'success': False}), 400
    try:
        restore_archive(request.json['archive_id'])
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@app.route('/api/archive/batches', methods=['GET'])
@login_required
def api_archive_batches():
    name, brand = request.args.get('name'), request.args.get('brand')
    if not name or not brand:
        return jsonify([])
    purchases = get_all_purchases()
    return jsonify([b for b in purchases if b['name'] == name and b['brand'] == brand])


# ===================== SYNC API =====================
@app.route('/api/sync/all', methods=['GET'])
@login_required
def api_sync_all():
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id, name, brand, category, cost_price, selling_price, stock, discount FROM products WHERE NOT EXISTS (SELECT 1 FROM deleted_products dp WHERE dp.product_id = products.id AND dp.action = 'PERMANENTLY DELETED' AND dp.source = 'product')")
        prods = [{'id': r[0], 'name': r[1], 'brand': r[2] or '', 'category': r[3] or '', 'cost_price': float(r[4]), 'selling_price': float(r[5]), 'stock': int(r[6]), 'discount': float(r[7])} for r in cursor.fetchall()]

        cursor.execute("SELECT id, product_id, quantity, remaining_quantity, cost_price, selling_price, discount, date, action, source, original_quantity, original_date, original_cost_price, original_selling_price, original_discount, claimed_quantity FROM purchase_batches ORDER BY id")
        batches = [{'id': r[0], 'product_id': r[1], 'quantity': r[2], 'remaining_quantity': r[3], 'cost_price': float(r[4]), 'selling_price': float(r[5]), 'discount': float(r[6]), 'date': r[7].isoformat() if r[7] else None, 'action': r[8] or '', 'source': r[9] or '', 'original_quantity': r[10], 'original_date': r[11].isoformat() if r[11] else None, 'original_cost_price': float(r[12] or 0), 'original_selling_price': float(r[13] or 0), 'original_discount': float(r[14] or 0), 'claimed_quantity': r[15] or 0} for r in cursor.fetchall()]

        cursor.execute("SELECT id, date, subtotal, discount, total, profit, reversed, payment_method, cheque_number, user_id FROM sales WHERE reversed = 0")
        sales = [{'id': r[0], 'date': r[1].isoformat() if r[1] else None, 'subtotal': float(r[2]), 'discount': float(r[3]), 'total': float(r[4]), 'profit': float(r[5]), 'reversed': r[6], 'payment_method': r[7], 'cheque_number': r[8], 'user_id': r[9]} for r in cursor.fetchall()]

        cursor.execute("SELECT id, sale_id, product_id, batch_id, quantity, selling_price, cost_price, profit FROM sales_items")
        items = [{'id': r[0], 'sale_id': r[1], 'product_id': r[2], 'batch_id': r[3], 'quantity': r[4], 'selling_price': float(r[5]), 'cost_price': float(r[6]), 'profit': float(r[7])} for r in cursor.fetchall()]

        cursor.execute("SELECT id, product_id, batch_id, product_name, brand, category, issue_type, description, quantity, status, created_at, updated_at FROM claims")
        claims = [{'id': r[0], 'product_id': r[1], 'batch_id': r[2], 'product_name': r[3], 'brand': r[4], 'category': r[5], 'issue_type': r[6], 'description': r[7], 'quantity': r[8], 'status': r[9], 'created_at': r[10].isoformat() if r[10] else None, 'updated_at': r[11].isoformat() if r[11] else None} for r in cursor.fetchall()]

        cursor.execute("SELECT id, name, brand, category, cost_price, selling_price, stock, discount, action, deleted_at, batch_id, batch_quantity, batch_remaining, product_id, source FROM deleted_products ORDER BY deleted_at DESC")
        deleted = [{'id': r[0], 'name': r[1], 'brand': r[2], 'category': r[3], 'cost_price': float(r[4]), 'selling_price': float(r[5]), 'stock': r[6], 'discount': float(r[7]), 'action': r[8], 'deleted_at': r[9].isoformat() if r[9] else None, 'batch_id': r[10], 'batch_quantity': r[11], 'batch_remaining': r[12], 'product_id': r[13], 'source': r[14]} for r in cursor.fetchall()]

        return jsonify({'products': prods, 'batches': batches, 'sales': sales, 'sales_items': items, 'claims': claims, 'deleted_products': deleted})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()


# ===================== IMPORT ENDPOINTS =====================
import openpyxl
from openpyxl import load_workbook
from werkzeug.utils import secure_filename
import psycopg2

ALLOWED_EXTENSIONS = {'xlsx', 'xls'}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route('/inventory/import', methods=['GET'])
@admin_required
def import_inventory_page():
    return render_template('import_inventory.html')


cancel_flags = {}


def run_inventory_import(job_id, file_stream, target_category, mode='append'):
    conn = None
    try:
        wb = load_workbook(file_stream, data_only=True)
        ws = wb.active
    except Exception as e:
        update_job_progress(job_id, status='error', errors=[f"Unable to read workbook: {str(e)}"])
        return

    header_row_idx, header_row = None, None
    for i, row in enumerate(ws.iter_rows(min_row=1, max_row=20, values_only=True)):
        if row and any(cell and isinstance(cell, str) and ('item' in cell.lower() or 'qty' in cell.lower() or 'rate' in cell.lower()) for cell in row):
            header_row_idx = i + 1
            header_row = row
            break

    if header_row_idx is None:
        update_job_progress(job_id, status='error', errors=['Could not find header row'])
        return

    header_map = {}
    for idx, cell in enumerate(header_row):
        if cell:
            cl = str(cell).strip().lower()
            if cl in ['item', 'product', 'name', 'details']:
                header_map['name'] = idx
            elif cl in ['qty', 'quantity']:
                header_map['quantity'] = idx
            elif cl in ['rate', 'cost', 'cost price', 'unit cost']:
                header_map['cost_price'] = idx
            elif cl in ['amount', 'total cost']:
                header_map['total_cost'] = idx
            elif cl in ['selling price', 'price', 'unit price']:
                header_map['selling_price'] = idx
            elif cl in ['date', 'purchase date']:
                header_map['date'] = idx
            elif cl in ['discount']:
                header_map['discount'] = idx

    if 'name' not in header_map:
        update_job_progress(job_id, status='error', errors=['Missing column: Name'])
        return

    rows_to_process, skipped_rows, warning_rows = [], [], []

    for row_idx, row in enumerate(ws.iter_rows(min_row=header_row_idx + 1, values_only=True), start=header_row_idx + 1):
        if not any(row):
            continue
        try:
            name = str(row[header_map['name']]).strip() if row[header_map['name']] else ''
            if not name:
                skipped_rows.append({'row': row_idx, 'data': {'name': None}, 'reason': 'Product name is empty'})
                continue
            try:
                quantity = float(row[header_map['quantity']]) if header_map.get('quantity') is not None and row[header_map['quantity']] is not None else 0
            except:
                quantity = 0
            if quantity < 0:
                quantity = 0
            try:
                cost_price = float(row[header_map['cost_price']]) if header_map.get('cost_price') is not None and row[header_map['cost_price']] is not None else 0
            except:
                cost_price = 0
            if cost_price < 0:
                cost_price = 0
            try:
                total_cost = float(row[header_map['total_cost']]) if header_map.get('total_cost') is not None and row[header_map['total_cost']] is not None else 0
            except:
                total_cost = 0
            if total_cost < 0:
                total_cost = 0
            if total_cost == 0 and cost_price > 0 and quantity > 0:
                total_cost = cost_price * quantity
            cost_per_unit = total_cost / quantity if quantity > 0 else 0

            try:
                selling_price = float(row[header_map['selling_price']]) if header_map.get('selling_price') is not None and row[header_map['selling_price']] is not None else 0
            except:
                selling_price = 0
            if selling_price < 0:
                selling_price = 0
            if selling_price == 0 and quantity > 0 and total_cost > 0:
                selling_price = cost_per_unit * 1.3

            try:
                discount = float(row[header_map['discount']]) if header_map.get('discount') is not None and row[header_map['discount']] is not None else 0
            except:
                discount = 0
            if discount < 0:
                discount = 0

            purchase_date = None
            if 'date' in header_map and row[header_map['date']] is not None:
                purchase_date = parse_date_cell(row[header_map['date']])
                if purchase_date is None or purchase_date.year < 2000 or purchase_date > datetime.now() + timedelta(days=1):
                    purchase_date = datetime.now()
            else:
                purchase_date = datetime.now()

            rows_to_process.append({'row_idx': row_idx, 'name': name, 'brand': '', 'quantity': int(quantity), 'cost_price': cost_per_unit, 'selling_price': selling_price, 'discount': discount, 'category': target_category, 'purchase_date': purchase_date, 'total_cost': total_cost})
        except Exception as e:
            skipped_rows.append({'row': row_idx, 'data': {'name': row[header_map.get('name')]}, 'reason': f'Fatal error: {str(e)}'})

    total_rows = len(rows_to_process)
    update_job_progress(job_id, total=total_rows, processed=0, status='processing', errors=warning_rows, result={'imported': 0, 'skipped': skipped_rows, 'warnings': warning_rows, 'message': 'Parsing completed'})

    if total_rows == 0:
        update_job_progress(job_id, status='done', result={'imported': 0, 'skipped': skipped_rows, 'warnings': warning_rows, 'message': 'No valid rows'})
        return

    if not DATABASE_URL:
        update_job_progress(job_id, status='error', errors=['DATABASE_URL not configured'])
        return

    imported_count = 0
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        conn.autocommit = False
        cursor = conn.cursor()

        if mode == 'replace':
            for cat in set(i['category'] for i in rows_to_process):
                cursor.execute("DELETE FROM purchase_batches WHERE product_id IN (SELECT id FROM products WHERE category = %s)", (cat,))
                cursor.execute("DELETE FROM purchases WHERE category = %s", (cat,))
                cursor.execute("DELETE FROM products WHERE category = %s", (cat,))
            cursor.execute("SELECT setval('products_id_seq', (SELECT COALESCE(MAX(id), 1) FROM products))")
            cursor.execute("SELECT setval('purchases_id_seq', (SELECT COALESCE(MAX(id), 1) FROM purchases))")
            cursor.execute("SELECT setval('purchase_batches_id_seq', (SELECT COALESCE(MAX(id), 1) FROM purchase_batches))")

        for item in rows_to_process:
            if mode == 'replace':
                cursor.execute("INSERT INTO products (name, brand, cost_price, selling_price, stock, category) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id", (item['name'], item['brand'], item['cost_price'], item['selling_price'], 0, item['category']))
                product_id = cursor.fetchone()[0]
            else:
                cursor.execute("SELECT p.id, p.cost_price, p.selling_price, p.category FROM products p LEFT JOIN deleted_products dp ON dp.product_id = p.id AND dp.action = 'PERMANENTLY DELETED' AND dp.source = 'product' WHERE p.name = %s AND p.brand = %s AND dp.id IS NULL", (item['name'], item['brand']))
                prod = cursor.fetchone()
                if prod:
                    product_id, ec, es, ecat = prod
                    if ecat != item['category']:
                        cursor.execute("UPDATE products SET category = %s WHERE id = %s", (item['category'], product_id))
                    if ec != item['cost_price'] or es != item['selling_price']:
                        cursor.execute("UPDATE products SET cost_price = %s, selling_price = %s WHERE id = %s", (item['cost_price'], item['selling_price'], product_id))
                else:
                    cursor.execute("INSERT INTO products (name, brand, cost_price, selling_price, stock, category) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id", (item['name'], item['brand'], item['cost_price'], item['selling_price'], 0, item['category']))
                    product_id = cursor.fetchone()[0]

            total = item['total_cost'] - item['discount']
            cursor.execute("INSERT INTO purchases (product_name, brand, category, quantity, cost_price, discount, total, selling_price, date) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)", (item['name'], item['brand'], item['category'], item['quantity'], item['cost_price'], item['discount'], total, item['selling_price'], item['purchase_date']))
            cursor.execute("INSERT INTO purchase_batches (product_id, quantity, remaining_quantity, cost_price, selling_price, discount, date, action) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)", (product_id, item['quantity'], item['quantity'], item['cost_price'], item['selling_price'], item['discount'], item['purchase_date'], "added"))

            if item['quantity'] != 0:
                cursor.execute("UPDATE products SET stock = stock + %s WHERE id = %s", (item['quantity'], product_id))

            imported_count += 1
            if imported_count % 100 == 0:
                update_job_progress(job_id, processed=imported_count)
                if cancel_flags.get(job_id):
                    raise Exception("CANCELLED_BY_USER")

        conn.commit()
        update_job_progress(job_id, status='done', result={'imported': imported_count, 'skipped': skipped_rows, 'warnings': warning_rows, 'message': f'Imported {imported_count} records.'})
    except Exception as e:
        if conn:
            conn.rollback()
        update_job_progress(job_id, status='error', errors=[str(e)])
    finally:
        if conn:
            conn.close()
        cancel_flags.pop(job_id, None)


def run_sales_import(job_id, file_stream, target_category, mode='append', user_id=None):
    try:
        wb = load_workbook(file_stream, data_only=True)
        ws = wb.active
    except Exception as e:
        update_job_progress(job_id, status='error', errors=[f"Unable to read workbook: {str(e)}"])
        return

    header_row_idx, header_row = None, None
    for i, row in enumerate(ws.iter_rows(min_row=1, max_row=20, values_only=True)):
        if row and any(cell and isinstance(cell, str) and ('item' in cell.lower() or 'qty' in cell.lower() or 'rate' in cell.lower()) for cell in row):
            header_row_idx = i + 1
            header_row = row
            break

    if header_row_idx is None:
        update_job_progress(job_id, status='error', errors=['Could not find header row'])
        return

    header_map = {}
    for idx, cell in enumerate(header_row):
        if cell:
            cl = str(cell).strip().lower()
            if cl in ['item', 'product', 'name', 'details']:
                header_map['item'] = idx
            elif cl in ['qty', 'quantity']:
                header_map['qty'] = idx
            elif cl in ['rate', 'selling price', 'unit price']:
                header_map['rate'] = idx
            elif cl in ['date', 'sale date']:
                header_map['date'] = idx
            elif cl in ['discount']:
                header_map['discount'] = idx

    missing = [f for f in ['item', 'qty', 'rate'] if f not in header_map]
    if missing:
        update_job_progress(job_id, status='error', errors=[f'Missing columns: {", ".join(missing)}'])
        return

    rows_to_process, skipped_rows, error_rows = [], [], []
    for row_idx, row in enumerate(ws.iter_rows(min_row=header_row_idx + 1, values_only=True), start=header_row_idx + 1):
        if not any(row):
            continue
        try:
            item = str(row[header_map['item']]).strip() if row[header_map['item']] else ''
            if not item:
                skipped_rows.append({'row': row_idx, 'data': {'item': None}, 'reason': 'Empty name'})
                continue
            qty = float(row[header_map['qty']]) if row[header_map['qty']] is not None else 0
            if qty <= 0:
                skipped_rows.append({'row': row_idx, 'data': {'item': item}, 'reason': f'Invalid qty: {qty}'})
                continue
            rate = float(row[header_map['rate']]) if row[header_map['rate']] is not None else 0
            if rate < 0:
                skipped_rows.append({'row': row_idx, 'data': {'item': item}, 'reason': 'Negative rate'})
                continue
            discount = float(row[header_map.get('discount')]) if header_map.get('discount') is not None and row[header_map['discount']] is not None else 0

            sale_date = datetime.now()
            if 'date' in header_map and row[header_map['date']] is not None:
                parsed = parse_date_cell(row[header_map['date']])
                if parsed and 2000 <= parsed.year <= 2050 and parsed <= datetime.now() + timedelta(days=1):
                    sale_date = parsed

            rows_to_process.append({'row_idx': row_idx, 'item': item, 'qty': int(qty), 'rate': rate, 'discount': discount, 'sale_date': sale_date})
        except Exception as e:
            skipped_rows.append({'row': row_idx, 'data': {'item': row[header_map.get('item')]}, 'reason': str(e)})

    total_rows = len(rows_to_process)
    update_job_progress(job_id, total=total_rows, processed=0, status='processing', errors=error_rows, result={'imported': 0, 'skipped': skipped_rows, 'message': 'Parsing completed'})

    if total_rows == 0:
        update_job_progress(job_id, status='done', result={'imported': 0, 'skipped': skipped_rows, 'message': 'No valid rows'})
        return

    if not DATABASE_URL:
        update_job_progress(job_id, status='error', errors=['DATABASE_URL not configured'])
        return

    imported_count = 0
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        conn.autocommit = False
        cursor = conn.cursor()

        if mode == 'replace' and target_category != 'All':
            cursor.execute("DELETE FROM sales_items WHERE product_id IN (SELECT id FROM products WHERE category = %s)", (target_category,))
            cursor.execute("DELETE FROM sales WHERE id NOT IN (SELECT sale_id FROM sales_items)")

        for entry in rows_to_process:
            cursor.execute("SELECT id, category, name FROM products WHERE name = %s", (entry['item'],))
            prod = cursor.fetchone()
            if not prod:
                cursor.execute("SELECT id, category, name FROM products WHERE LOWER(name) LIKE LOWER(%s) AND NOT EXISTS (SELECT 1 FROM deleted_products dp WHERE dp.product_id = products.id AND dp.action IN ('PERMANENTLY DELETED', 'PRODUCT DELETED') AND dp.source = 'product') LIMIT 1", (f'%{entry["item"].strip()}%',))
                prod = cursor.fetchone()
            if not prod:
                error_rows.append(f"Row {entry['row_idx']}: '{entry['item']}' not found")
                continue

            product_id, product_category, product_name = prod[0], prod[1], prod[2]

            if target_category == PERMANENT_CATEGORY and product_category not in [PERMANENT_CATEGORY, 'Others']:
                error_rows.append(f"Row {entry['row_idx']}: Skipping {product_category} for {target_category} import")
                continue
            if target_category == DEFAULT_CATEGORY and product_category != DEFAULT_CATEGORY:
                error_rows.append(f"Row {entry['row_idx']}: Skipping {product_category} for {target_category} import")
                continue

            subtotal = entry['qty'] * entry['rate']
            total = subtotal - entry['discount']
            cursor.execute("INSERT INTO sales (date, subtotal, discount, total, profit, reversed, payment_method, user_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id", (entry['sale_date'], subtotal, entry['discount'], total, 0, 0, 'cash', user_id))
            sale_id = cursor.fetchone()[0]

            cursor.execute("SELECT id, cost_price, selling_price, remaining_quantity FROM purchase_batches WHERE product_id = %s AND remaining_quantity > 0 ORDER BY date ASC LIMIT 1", (product_id,))
            batch_info = cursor.fetchone()

            if batch_info:
                batch_id, cost_price, selling_price, remaining_qty = batch_info
                if remaining_qty >= entry['qty']:
                    sp = entry['rate']
                    ip = (sp - cost_price) * entry['qty'] - entry['discount']
                    cursor.execute("INSERT INTO sales_items (sale_id, product_id, batch_id, quantity, selling_price, cost_price, profit) VALUES (%s, %s, %s, %s, %s, %s, %s)", (sale_id, product_id, batch_id, entry['qty'], sp, cost_price, ip))
                    cursor.execute("UPDATE purchase_batches SET remaining_quantity = remaining_quantity - %s WHERE id = %s", (entry['qty'], batch_id))
                    cursor.execute("UPDATE products SET stock = stock - %s WHERE id = %s", (entry['qty'], product_id))
                    cursor.execute("UPDATE sales SET profit = %s WHERE id = %s", (ip, sale_id))
                else:
                    avail = remaining_qty
                    sp = entry['rate']
                    ip = (sp - cost_price) * avail - (entry['discount'] * (avail / entry['qty']))
                    cursor.execute("INSERT INTO sales_items (sale_id, product_id, batch_id, quantity, selling_price, cost_price, profit) VALUES (%s, %s, %s, %s, %s, %s, %s)", (sale_id, product_id, batch_id, avail, sp, cost_price, ip))
                    cursor.execute("UPDATE purchase_batches SET remaining_quantity = 0 WHERE id = %s", (batch_id,))
                    cursor.execute("UPDATE products SET stock = stock - %s WHERE id = %s", (avail, product_id))
                    cursor.execute("UPDATE sales SET profit = %s WHERE id = %s", (ip, sale_id))
                    error_rows.append(f"Row {entry['row_idx']}: Partial stock for '{entry['item']}'")
            else:
                sp = entry['rate']
                ip = sp * entry['qty'] - entry['discount']
                cursor.execute("INSERT INTO sales_items (sale_id, product_id, batch_id, quantity, selling_price, cost_price, profit) VALUES (%s, %s, NULL, %s, %s, 0, %s)", (sale_id, product_id, entry['qty'], sp, ip))
                cursor.execute("UPDATE sales SET profit = %s WHERE id = %s", (ip, sale_id))
                error_rows.append(f"Row {entry['row_idx']}: No stock for '{entry['item']}'")

            imported_count += 1
            if imported_count % 100 == 0:
                update_job_progress(job_id, processed=imported_count)
                if cancel_flags.get(job_id):
                    raise Exception("CANCELLED_BY_USER")

        conn.commit()
        update_job_progress(job_id, status='done', result={'imported': imported_count, 'skipped': skipped_rows, 'message': f'Imported {imported_count} records'})
    except Exception as e:
        if conn:
            conn.rollback()
        update_job_progress(job_id, status='error', errors=[str(e)])
    finally:
        if conn:
            conn.close()
        cancel_flags.pop(job_id, None)


@app.route('/api/inventory/import', methods=['POST'])
@admin_required
def api_import_inventory():
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'No file uploaded'}), 400
    file = request.files['file']
    if file.filename == '' or not allowed_file(file.filename):
        return jsonify({'success': False, 'error': 'Invalid file'}), 400

    target_category = request.form.get('target_category', DEFAULT_CATEGORY)
    if target_category not in ALL_HERBAL_CATEGORIES:
        return jsonify({'success': False, 'error': 'Invalid target category'}), 400

    mode = request.form.get('mode', 'append')
    if mode not in ['append', 'replace']:
        return jsonify({'success': False, 'error': 'Invalid mode'}), 400

    job_id = str(uuid.uuid4())
    conn = get_connection()
    conn.cursor().execute("INSERT INTO import_jobs (job_id, status, total, processed, errors, result) VALUES (%s, 'pending', 0, 0, '[]', NULL)", (job_id,))
    conn.commit()
    conn.close()

    threading.Thread(target=run_inventory_import, args=(job_id, io.BytesIO(file.read()), target_category, mode), daemon=True).start()
    return jsonify({'success': True, 'job_id': job_id})


@app.route('/api/sales/import', methods=['POST'])
@admin_required
def api_import_sales():
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'No file uploaded'}), 400
    file = request.files['file']
    if file.filename == '' or not allowed_file(file.filename):
        return jsonify({'success': False, 'error': 'Invalid file'}), 400

    target_category = request.form.get('target_category', 'All')
    if target_category not in ALL_HERBAL_CATEGORIES + ['All']:
        return jsonify({'success': False, 'error': 'Invalid target category'}), 400

    mode = request.form.get('mode', 'append')
    if mode not in ['append', 'replace']:
        return jsonify({'success': False, 'error': 'Invalid mode'}), 400

    job_id = str(uuid.uuid4())
    conn = get_connection()
    conn.cursor().execute("INSERT INTO import_jobs (job_id, status, total, processed, errors, result) VALUES (%s, 'pending', 0, 0, '[]', NULL)", (job_id,))
    conn.commit()
    conn.close()

    threading.Thread(target=run_sales_import, args=(job_id, io.BytesIO(file.read()), target_category, mode, session.get('user_id')), daemon=True).start()
    return jsonify({'success': True, 'job_id': job_id})


@app.route('/api/import/progress/<job_id>', methods=['GET'])
@login_required
def api_import_progress(job_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status, total, processed, errors, result, updated_at FROM import_jobs WHERE job_id = %s", (job_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return jsonify({'success': False, 'error': 'Job not found'}), 404
    return jsonify({'success': True, 'status': row[0], 'total': row[1], 'processed': row[2], 'errors': row[3], 'result': json.loads(row[4]) if isinstance(row[4], str) else row[4], 'updated_at': row[5].isoformat() if row[5] else None})


@app.route('/api/import/cancel/<job_id>', methods=['POST'])
@login_required
def api_import_cancel(job_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM import_jobs WHERE job_id = %s", (job_id,))
    row = cursor.fetchone()
    conn.close()
    if not row or row[0] in ('done', 'error', 'cancelled'):
        return jsonify({'success': False, 'error': 'Invalid job'}), 400
    cancel_flags[job_id] = True
    update_job_progress(job_id, status='cancelling')
    return jsonify({'success': True, 'message': 'Cancelling...'})


@app.route('/api/import/failed-report/<job_id>', methods=['GET'])
@login_required
def api_import_failed_report(job_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT result FROM import_jobs WHERE job_id = %s", (job_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return jsonify({'success': False, 'error': 'Job not found'}), 404

    result = row[0]
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except:
            pass
    if not result or not result.get('skipped'):
        return jsonify({'success': False, 'error': 'No skipped rows to report'}), 400

    skipped = result['skipped']
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30)
    styles = getSampleStyleSheet()
    elements = [Paragraph("📋 Import Failed Items Report", styles["Title"]), Spacer(1, 6), Paragraph(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", styles["Normal"]), Spacer(1, 12)]

    table_data = [["Row", "Name/Item", "Qty", "Rate", "Reason"]]
    for s in skipped:
        table_data.append([str(s.get('row', '')), s.get('data', {}).get('name') or s.get('data', {}).get('item') or '-', str(s.get('data', {}).get('qty', '')), str(s.get('data', {}).get('rate', '')), s.get('reason', '')])

    table = Table(table_data, repeatRows=1, colWidths=[50, 150, 50, 50, 200])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#1E3A5F")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
    ]))
    elements.append(table)
    doc.build(elements)
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name=f"failed_import_{job_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf", mimetype='application/pdf')


def verify_import(job_id, file_stream, target_category):
    if not DATABASE_URL:
        return {'error': 'DATABASE_URL not configured'}
    try:
        wb = load_workbook(file_stream, data_only=True)
        ws = wb.active
    except Exception as e:
        return {'error': f"Unable to read workbook: {str(e)}"}

    header_row_idx, header_row = None, None
    for i, row in enumerate(ws.iter_rows(min_row=1, max_row=20, values_only=True)):
        if row and any(cell and isinstance(cell, str) and ('item' in cell.lower() or 'qty' in cell.lower() or 'rate' in cell.lower()) for cell in row):
            header_row_idx = i + 1
            header_row = row
            break
    if header_row_idx is None:
        return {'error': 'Could not find header row'}

    header_map = {}
    for idx, cell in enumerate(header_row):
        if cell:
            cl = str(cell).strip().lower()
            if cl in ['item', 'product', 'name', 'details']:
                header_map['name'] = idx
            elif cl in ['qty', 'quantity']:
                header_map['quantity'] = idx
            elif cl in ['rate', 'cost', 'cost price', 'unit cost']:
                header_map['cost_price'] = idx
            elif cl in ['amount', 'total cost']:
                header_map['total_cost'] = idx
            elif cl in ['selling price', 'price', 'unit price']:
                header_map['selling_price'] = idx
            elif cl in ['discount']:
                header_map['discount'] = idx

    if 'name' not in header_map:
        return {'error': 'Missing column: Name'}

    mismatches, skipped, matched, total_rows = [], [], 0, 0
    conn, cursor = None, None
    try:
        import time
        time.sleep(0.2)
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        cursor = conn.cursor()

        for row_idx, row in enumerate(ws.iter_rows(min_row=header_row_idx + 1, values_only=True), start=header_row_idx + 1):
            if not any(row):
                continue
            total_rows += 1
            name = str(row[header_map['name']]).strip() if row[header_map['name']] else ''
            if not name:
                skipped.append({'row': row_idx, 'reason': 'Product name is empty'})
                continue
            try:
                quantity = float(row[header_map['quantity']]) if header_map.get('quantity') is not None and row[header_map['quantity']] is not None else 0
            except:
                quantity = 0
            try:
                cost_price = float(row[header_map['cost_price']]) if header_map.get('cost_price') is not None and row[header_map['cost_price']] is not None else 0
            except:
                cost_price = 0
            try:
                total_cost = float(row[header_map['total_cost']]) if header_map.get('total_cost') is not None and row[header_map['total_cost']] is not None else 0
            except:
                total_cost = 0
            if total_cost == 0 and cost_price > 0 and quantity > 0:
                total_cost = cost_price * quantity
            selling_price = total_cost / quantity if quantity > 0 else 0
            try:
                discount = float(row[header_map.get('discount')]) if header_map.get('discount') is not None and row[header_map['discount']] is not None else 0
            except:
                discount = 0

            cursor.execute("SELECT p.id, p.stock, p.cost_price, p.selling_price, p.discount FROM products p WHERE p.name = %s AND p.category = %s AND NOT EXISTS (SELECT 1 FROM deleted_products dp WHERE dp.product_id = p.id AND dp.action IN ('PERMANENTLY DELETED', 'PRODUCT DELETED') AND dp.source = 'product')", (name, target_category))
            product = cursor.fetchone()

            if not product:
                mismatches.append({'row': row_idx, 'reason': 'Product not found', 'expected': {'name': name, 'qty': quantity, 'cost': cost_price, 'selling': selling_price, 'discount': discount}})
                continue

            product_id, db_stock, db_cost, db_selling, db_discount = product
            issues = []
            if abs(float(db_stock) - float(quantity)) > 0.001:
                issues.append(f"stock: expected {quantity}, got {db_stock}")
            if abs(float(db_cost) - float(cost_price)) > 0.001:
                issues.append(f"cost: expected {cost_price:.2f}, got {db_cost:.2f}")
            if abs(float(db_selling) - float(selling_price)) > 0.001:
                issues.append(f"selling: expected {selling_price:.2f}, got {db_selling:.2f}")
            if abs(float(db_discount) - float(discount)) > 0.001:
                issues.append(f"discount: expected {discount:.2f}, got {db_discount:.2f}")

            if issues:
                mismatches.append({'row': row_idx, 'reason': '; '.join(issues), 'expected': {'name': name, 'qty': quantity, 'cost': cost_price, 'selling': selling_price, 'discount': discount}, 'actual': {'stock': db_stock, 'cost': db_cost, 'selling': db_selling, 'discount': db_discount}})
            else:
                matched += 1

        return {'total_rows': total_rows, 'matched': matched, 'mismatches': mismatches, 'skipped': skipped, 'message': f"Verified {total_rows} rows: {matched} matched."}
    except Exception as e:
        return {'error': f"Verification failed: {str(e)}"}
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@app.route('/api/import/verify/<job_id>', methods=['POST'])
@admin_required
def api_import_verify(job_id):
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'No file uploaded'}), 400
    file = request.files['file']
    if file.filename == '' or not allowed_file(file.filename):
        return jsonify({'success': False, 'error': 'Invalid file'}), 400

    target_category = request.form.get('target_category', DEFAULT_CATEGORY)
    if target_category not in ALL_HERBAL_CATEGORIES:
        return jsonify({'success': False, 'error': 'Invalid target category'}), 400

    result = verify_import(job_id, io.BytesIO(file.read()), target_category)
    if 'error' in result:
        return jsonify({'success': False, 'error': result['error']}), 400
    return jsonify({'success': True, 'result': result})


# ===================== ACTIVITIES =====================
@app.route('/activities')
@login_required
def activities():
    if session.get('role') != 'admin':
        return render_template("error.html", message="Admin access required"), 403
    return render_template('activities.html')


@app.route('/api/user/activity-summary')
@login_required
def api_user_activity_summary():
    if session.get('role') != 'admin':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users WHERE username = 'oxbee'")
    oxbee_row = cursor.fetchone()
    oxbee_id = oxbee_row[0] if oxbee_row else 1

    q = "SELECT u.id, u.username, u.role, COUNT(l.id), SUM(CASE WHEN l.action = 'login' THEN 1 ELSE 0 END), SUM(CASE WHEN l.action = 'logout' THEN 1 ELSE 0 END), SUM(CASE WHEN l.action = 'login_failed' THEN 1 ELSE 0 END), MAX(l.timestamp) FROM users u LEFT JOIN user_logs l ON u.id = l.user_id WHERE 1=1"
    params = []
    if session.get('username', '').lower() != 'oxbee':
        q += " AND u.id != %s"
        params.append(oxbee_id)
    q += " GROUP BY u.id, u.username, u.role ORDER BY 8 DESC NULLS LAST"
    cursor.execute(q, params)
    res = [{'user_id': r[0], 'username': r[1], 'role': r[2], 'total_logs': r[3], 'login_count': r[4], 'logout_count': r[5], 'failed_login_count': r[6], 'last_activity': r[7].isoformat() if r[7] else None} for r in cursor.fetchall()]
    conn.close()
    return jsonify({'success': True, 'data': res})


def init_claims_table():
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""CREATE TABLE IF NOT EXISTS claims (id SERIAL PRIMARY KEY, product_id INTEGER NOT NULL, batch_id INTEGER NOT NULL, product_name VARCHAR(255) NOT NULL, brand VARCHAR(100) DEFAULT '', category VARCHAR(100) DEFAULT '', issue_type VARCHAR(100) NOT NULL, description TEXT DEFAULT '', quantity INTEGER NOT NULL DEFAULT 0, remaining_good INTEGER DEFAULT 0, status VARCHAR(20) DEFAULT 'active', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_claims_product_id ON claims(product_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_claims_batch_id ON claims(batch_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_claims_status ON claims(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_claims_created_at ON claims(created_at)")
        conn.commit()
    except Exception as e:
        conn.rollback()
    finally:
        conn.close()


init_claims_table()


@app.route('/api/ping', methods=['GET'])
def ping():
    return jsonify({"status": "ok", "message": "Deployed version is current"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)