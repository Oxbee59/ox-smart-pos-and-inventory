from flask import Flask, render_template, request, redirect, url_for, jsonify, send_file, session
from functools import wraps
from services.product_service import get_all_products as get_all_products_service
import os
import uuid
import threading

# ---------- IMPORT PURCHASE SERVICES ----------
from services.purchase_service import (
    add_purchase,
    get_all_purchases,
    get_purchases_by_date_range,
    get_product_suggestions,
    get_category_suggestions,
    update_product,
    get_batch_update_history   # <-- ADD THIS
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
    get_user_logs
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

# ---------- DATE PARSING HELPERS (for sales import) ----------
try:
    from dateutil import parser as date_parser
    HAS_DATEUTIL = True
except ImportError:
    HAS_DATEUTIL = False

def parse_date_cell(value):
    """
    Try to parse a date from an Excel cell.
    Returns a datetime object or None if parsing fails.
    Handles Excel serial numbers, DD/MM/YYYY, MM/DD/YYYY, and other formats.
    """
    if value is None:
        return None
    
    # Case 1: Excel serial number (float or int like 44927)
    if isinstance(value, (int, float)):
        try:
            dt = datetime(1899, 12, 30) + timedelta(days=float(value))
            if dt.year < 2000 or dt.year > 2050:
                pass
            return dt
        except:
            return None
    
    # Case 2: Already a datetime object
    if isinstance(value, datetime):
        return value
    
    # Case 3: Already a date object
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    
    # Case 4: String value
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        
        date_formats = [
            '%d/%m/%Y',      # 28/01/2023
            '%d/%m/%y',      # 28/01/23
            '%d-%m-%Y',      # 28-01-2023
            '%d-%m-%y',      # 28-01-23
            '%d.%m.%Y',      # 28.01.2023
            '%d.%m.%y',      # 28.01.23
            '%d %b %Y',      # 28 Jan 2023
            '%d %B %Y',      # 28 January 2023
            '%d/%m/%Y %H:%M:%S',
            '%d-%m-%Y %H:%M:%S',
            '%m/%d/%Y',
            '%m/%d/%y',
            '%m-%d-%Y',
            '%m-%d-%y',
            '%Y-%m-%d',
            '%Y/%m/%d',
            '%Y%m%d',
            '%b %d, %Y',
            '%B %d, %Y',
        ]
        
        for fmt in date_formats:
            try:
                dt = datetime.strptime(s, fmt)
                if dt.year >= 2000 and dt.year <= 2050:
                    return dt
            except ValueError:
                continue
        
        if HAS_DATEUTIL:
            try:
                dt = date_parser.parse(s, dayfirst=True, fuzzy=False)
                if dt.year >= 2000 and dt.year <= 2050:
                    return dt
            except:
                pass
        
        try:
            date_part = s.split(' ')[0]
            for fmt in ['%d/%m/%Y', '%Y-%m-%d', '%m/%d/%Y', '%d-%m-%Y', '%Y/%m/%d']:
                try:
                    dt = datetime.strptime(date_part, fmt)
                    if dt.year >= 2000 and dt.year <= 2050:
                        return dt
                except:
                    continue
        except:
            pass
        
        return None
    
    return None

# ===================== CREATE APP =====================
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "temporary-dev-key")

# ===================== CLAIM SERVICES (imported after app created) =====================
from services.claim_service import (
    create_claim,
    get_all_claims,
    get_claims_by_product,
    get_claim_by_id,
    update_claim,
    delete_claim,
    resolve_claim,
    search_products_for_claims,
    get_product_batches_for_claim
)

# ===================== LICENSE / TRIAL SYSTEM =====================
import json
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LICENSE_FILE = os.path.join(BASE_DIR, "license.json")
TRIAL_DAYS = 3
MASTER_KEY = "OXSMART-1234-KEY"

# ===================== TRIAL END TIME =====================
def get_trial_end_time():
    now = datetime.now()
    tomorrow = now + timedelta(days=1)
    end_time = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 5, 0, 0)
    return end_time

# ===================== IMPORT JOB TRACKING =====================
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
    except Exception as e:
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
    query = f"""
        UPDATE import_jobs
        SET {', '.join(set_parts)}, updated_at = CURRENT_TIMESTAMP
        WHERE job_id = %s
    """
    cursor.execute(query, params)
    conn.commit()
    conn.close()

init_import_jobs_table()

# ---------- LICENSE FUNCTIONS ----------
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
        return {
            'current_user': {
                'username': session.get('username'),
                'role': session.get('role')
            }
        }
    return {'current_user': None}

# ---------------------- LOGIN REQUIRED DECORATOR ----------------------
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
@app.route("/")
@login_required
def dashboard():
    return render_template("dashboard.html")

@app.route("/products")
@login_required
def products():
    return render_template("products.html")

@app.route("/products/screens")
@login_required
def products_screen():
    return render_template("products_screen.html")

@app.route("/products/add", methods=["POST"])
@login_required
def add_product():
    return redirect(url_for("products"))

@app.route("/sales")
@login_required
def sales():
    return render_template("sales.html")

@app.route("/sales/screens")
@login_required
def sales_screen():
    return render_template("sales_screen.html")

@app.route("/purchases")
@login_required
def purchases():
    return render_template("purchases.html")

@app.route("/purchases/screens")
@login_required
def purchases_screen():
    return render_template("purchases_screen.html")

@app.route("/analytics")
@login_required
def analytics():
    return render_template("analytics.html")

@app.route("/today-sales")
@login_required
def today_sales():
    return render_template("today_sales.html")

@app.route("/today-sales/screens")
@login_required
def today_sales_screen():
    return render_template("today_sales_screen.html")

@app.route("/archive")
@login_required
def archive():
    return render_template("archive.html")

@app.route("/admin/users")
@admin_required
def admin_users():
    return render_template("admin_users.html")

# ===================== CLAIMS ROUTES =====================
@app.route('/claims')
@login_required
def claims_page():
    """Claims management page"""
    return render_template('claims.html')

# ===================== CLAIMS API =====================
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
            product_id=data['product_id'],
            batch_id=data['batch_id'],
            product_name=data['product_name'],
            brand=data.get('brand', ''),
            category=data.get('category', ''),
            issue_type=data['issue_type'],
            description=data.get('description', ''),
            quantity=int(data['quantity'])
        )
        return jsonify({'success': True, 'claim_id': claim_id})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/api/claims/<int:claim_id>', methods=['PUT'])
@login_required
def api_update_claim(claim_id):
    data = request.json
    try:
        success = update_claim(
            claim_id=claim_id,
            issue_type=data['issue_type'],
            description=data.get('description', ''),
            quantity=int(data['quantity'])
        )
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
    remaining_seconds = int(rem.total_seconds()) if rem else 0
    return jsonify({
        "licensed": data.get("licensed", False),
        "trial_active": is_trial_active(),
        "remaining_seconds": remaining_seconds,
        "trial_days": TRIAL_DAYS
    })

@app.route('/api/license/activate', methods=['POST'])
def api_license_activate():
    req = request.json
    key = req.get("key", "").strip()
    if key == MASTER_KEY:
        data = load_license_data()
        data["licensed"] = True
        data["key"] = key
        save_license_data(data)
        return jsonify({"success": True})
    else:
        return jsonify({"success": False, "error": "Invalid license key"}), 400

# ===================== AUTH API =====================
@app.route('/api/auth/login', methods=['POST'])
def api_auth_login():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    
    ip_address = request.headers.get('X-Forwarded-For', request.remote_addr)
    user_agent = request.headers.get('User-Agent')
    
    user = login_user(username, password, ip_address, user_agent)
    if user:
        session['user_id'] = user['id']
        session['username'] = user['username']
        session['role'] = user['role']
        return jsonify({'success': True, 'user': user})
    else:
        return jsonify({'success': False, 'error': 'Invalid credentials'}), 401

@app.route('/api/auth/logout', methods=['POST'])
@login_required
def api_auth_logout():
    user_id = session.get('user_id')
    username = session.get('username')
    
    ip_address = request.headers.get('X-Forwarded-For', request.remote_addr)
    user_agent = request.headers.get('User-Agent')
    logout_user(user_id, username, ip_address, user_agent)
    
    session.clear()
    return jsonify({'success': True})

@app.route('/api/auth/signup', methods=['POST'])
def api_auth_signup():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    requested_role = data.get('role', 'user')
    
    current_admin_count = count_admins()
    
    if current_admin_count >= 2:
        role = 'user'
        print(f"⚠️ Already {current_admin_count} admins (max 2) - forcing role 'user' for {username}")
    else:
        if requested_role == 'admin':
            role = 'admin'
            print(f"✅ Admin {username} created. ({current_admin_count + 1}/2)")
        else:
            role = 'user'
            print(f"✅ Regular user {username} created.")
    
    success = create_user(username, password, role)
    if success:
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': 'Username already exists'}), 400

@app.route('/api/auth/check', methods=['GET'])
def api_auth_check():
    if 'user_id' in session:
        return jsonify({
            'logged_in': True,
            'username': session.get('username'),
            'role': session.get('role')
        })
    else:
        return jsonify({'logged_in': False})

@app.route('/api/auth/admin_exists', methods=['GET'])
def api_auth_admin_exists():
    exists = admin_exists()
    return jsonify({'admin_exists': exists})

@app.route('/api/auth/admin_count', methods=['GET'])
def api_auth_admin_count():
    count = count_admins()
    return jsonify({'admin_count': count})

@app.route("/admin/users/<int:user_id>/edit")
@admin_required
def admin_edit_user(user_id):
    from services.auth_service import get_user_by_id, is_protected_user
    
    user = get_user_by_id(user_id)
    if not user:
        return render_template("error.html", message="User not found"), 404
    
    is_oxbee = is_protected_user(user_id)
    
    return render_template("edit_user.html", user_id=user_id, is_oxbee=is_oxbee)

# ===================== USER LOGS API (OXBEE ONLY) =====================
# ===================== USER LOGS API (UPDATED - Hides oxbee logs from other admins) =====================
@app.route('/api/user/logs', methods=['GET'])
@login_required
def api_user_logs():
    """Get user logs - oxbee sees all, other admins see all except oxbee logs"""
    current_username = session.get('username')
    current_user_role = session.get('role')
    
    # Only admins can view logs
    if current_user_role != 'admin':
        return jsonify({'success': False, 'error': 'Unauthorized - Admin access required'}), 403
    
    conn = get_connection()
    cursor = conn.cursor()
    try:
        # Get the oxbee user ID
        cursor.execute("SELECT id FROM users WHERE username = 'oxbee'")
        oxbee_row = cursor.fetchone()
        OXBEE_USER_ID = oxbee_row[0] if oxbee_row else 1
        
        user_id = request.args.get('user_id', type=int)
        action = request.args.get('action')
        limit = request.args.get('limit', 50, type=int)
        offset = request.args.get('offset', 0, type=int)
        
        # Build the query
        query = """
            SELECT 
                id,
                user_id,
                action,
                ip_address,
                user_agent,
                timestamp
            FROM user_logs
            WHERE 1=1
        """
        params = []
        conditions = []
        
        # ✅ If current user is NOT oxbee, exclude oxbee logs
        if current_username.lower() != 'oxbee':
            conditions.append("user_id != %s")
            params.append(OXBEE_USER_ID)
        
        if user_id:
            # If user is not oxbee and trying to view oxbee's logs, block it
            if current_username.lower() != 'oxbee' and user_id == OXBEE_USER_ID:
                conn.close()
                return jsonify({
                    'success': False, 
                    'error': 'Unauthorized - Cannot view system administrator logs'
                }), 403
            conditions.append("user_id = %s")
            params.append(user_id)
        if action:
            conditions.append("action = %s")
            params.append(action)
        
        if conditions:
            query += " AND " + " AND ".join(conditions)
        
        # Get total count
        count_query = query.replace(
            "SELECT id, user_id, action, ip_address, user_agent, timestamp FROM user_logs WHERE 1=1",
            "SELECT COUNT(*) FROM user_logs WHERE 1=1"
        )
        if conditions:
            # Rebuild count query properly
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
        else:
            cursor.execute(count_query)
        total = cursor.fetchone()[0]
        
        # Get paginated logs
        query += " ORDER BY timestamp DESC LIMIT %s OFFSET %s"
        params.extend([limit, offset])
        cursor.execute(query, params)
        logs = cursor.fetchall()
        
        # Format logs with usernames
        result = []
        for log in logs:
            log_dict = {
                'id': log[0],
                'user_id': log[1],
                'action': log[2],
                'ip_address': log[3],
                'user_agent': log[4],
                'timestamp': log[5].isoformat() if log[5] else None
            }
            # Get username
            if log[1]:
                cursor.execute("SELECT username FROM users WHERE id = %s", (log[1],))
                user_row = cursor.fetchone()
                log_dict['username'] = user_row[0] if user_row else 'Unknown'
            result.append(log_dict)
        
        conn.close()
        
        return jsonify({
            'success': True,
            'logs': result,
            'total': total,
            'limit': limit,
            'offset': offset
        })
    except Exception as e:
        print(f"❌ Error in api_user_logs: {str(e)}")
        conn.close()
        return jsonify({'success': False, 'error': str(e)}), 500


# ===================== LOGS PAGE =====================
# ===================== LOGS PAGE =====================
@app.route("/logs")
@login_required
def logs():
    current_username = session.get('username')
    current_user_role = session.get('role')
    
    # Only admins can access logs page
    if current_user_role != 'admin':
        return render_template("error.html", message="Unauthorized - Admin access required"), 403
    
    # Pass is_oxbee to the template (current_user is injected by context processor)
    return render_template("logs.html", is_oxbee=(current_username.lower() == 'oxbee'))

# ===================== GET SINGLE USER (Admin) =====================
@app.route('/api/admin/users/<int:user_id>', methods=['GET'])
@admin_required
def api_admin_get_user(user_id):
    user = get_user_by_id(user_id)
    if not user:
        return jsonify({'success': False, 'error': 'User not found'}), 404
    
    return jsonify({'success': True, 'user': user})

# ===================== UPDATE USER PASSWORD (Admin) =====================
@app.route('/api/admin/users/<int:user_id>/password', methods=['PUT'])
@admin_required
def api_admin_update_user_password(user_id):
    if is_protected_user(user_id):
        return jsonify({'success': False, 'error': 'Cannot modify the system administrator (oxbee)'}), 403
    
    data = request.json
    new_password = data.get('password')
    
    if not new_password or len(new_password) < 4:
        return jsonify({'success': False, 'error': 'Password must be at least 4 characters'}), 400
    
    success = update_user_password(user_id, new_password)
    if success:
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': 'Update failed'}), 400

# ===================== ADMIN USER MANAGEMENT API =====================
@app.route('/api/admin/users', methods=['GET'])
@admin_required
def api_admin_get_users():
    users = get_all_users()
    return jsonify(users)

@app.route('/api/admin/users', methods=['POST'])
@admin_required
def api_admin_create_user():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    role = data.get('role', 'user')
    if not username or not password:
        return jsonify({'success': False, 'error': 'Username and password required'}), 400
    success = create_user(username, password, role)
    if success:
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': 'Username already exists'}), 400

@app.route('/api/admin/users/<int:user_id>', methods=['DELETE'])
@admin_required
def api_admin_delete_user(user_id):
    if user_id == session.get('user_id'):
        return jsonify({'success': False, 'error': 'Cannot delete your own account'}), 400
    
    if is_protected_user(user_id):
        return jsonify({'success': False, 'error': 'Cannot delete the system administrator (oxbee)'}), 400
    
    success = delete_user(user_id)
    if success:
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': 'Delete failed'}), 400

@app.route('/api/admin/users/<int:user_id>/role', methods=['PUT'])
@admin_required
def api_admin_update_role(user_id):
    if is_protected_user(user_id):
        return jsonify({'success': False, 'error': 'Cannot modify the system administrator (oxbee)'}), 400
    
    data = request.json
    new_role = data.get('role')
    if new_role not in ['admin', 'user']:
        return jsonify({'success': False, 'error': 'Invalid role'}), 400
    success = update_user_role(user_id, new_role)
    if success:
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': 'Update failed'}), 400

# ===================== DASHBOARD API =====================
@app.route('/api/dashboard/summary', methods=['GET'])
@login_required
def api_dashboard_summary():
    selected_date_str = request.args.get('date')
    start_datetime = request.args.get('start_datetime')
    end_datetime = request.args.get('end_datetime')
    
    selected_date = None
    if selected_date_str:
        try:
            selected_date = datetime.strptime(selected_date_str, '%Y-%m-%d').date()
        except:
            selected_date = None
    
    sales = get_today_sales(selected_date, start_datetime, end_datetime)
    profit = get_today_profit(selected_date, start_datetime, end_datetime)
    total_products = get_total_products()
    total_batches = get_total_batches()
    low_stock_products = get_low_stock_products(threshold=10)
    low_stock_count = len(low_stock_products)
    
    return jsonify({
        'sales': sales,
        'profit': profit,
        'total_products': total_products,
        'total_batches': total_batches,
        'low_stock_count': low_stock_count,
        'low_stock_products': low_stock_products
    })

@app.route('/api/dashboard/top_products', methods=['GET'])
@login_required
def api_dashboard_top_products():
    selected_date_str = request.args.get('date')
    start_datetime = request.args.get('start_datetime')
    end_datetime = request.args.get('end_datetime')
    limit = int(request.args.get('limit', 10))
    
    selected_date = None
    if selected_date_str:
        try:
            selected_date = datetime.strptime(selected_date_str, '%Y-%m-%d').date()
        except:
            selected_date = None
    
    top = get_top_products(selected_date, limit, start_datetime, end_datetime)
    result = [{'name': row[0], 'brand': row[1] or '', 'category': row[2] or '', 'qty': int(row[3])} for row in top]
    return jsonify(result)

@app.route('/api/dashboard/sales_history', methods=['GET'])
@login_required
def api_dashboard_sales_history():
    selected_date_str = request.args.get('date')
    start_datetime = request.args.get('start_datetime')
    end_datetime = request.args.get('end_datetime')
    
    selected_date = None
    if selected_date_str:
        try:
            selected_date = datetime.strptime(selected_date_str, '%Y-%m-%d').date()
        except:
            selected_date = None
    
    history = get_sales_history(selected_date, start_datetime, end_datetime)
    result = []
    for row in history:
        date_str = row[0].isoformat() if hasattr(row[0], 'isoformat') else str(row[0])
        result.append({
            'date': date_str,
            'total_sales': float(row[1]),
            'profit': float(row[2]),
            'discount': float(row[3])
        })
    return jsonify(result)

# ===================== PURCHASES API (with category filter) =====================
def serialize_purchase(p):
    p_copy = p.copy()
    if 'date' in p_copy and p_copy['date']:
        if hasattr(p_copy['date'], 'isoformat'):
            p_copy['date'] = p_copy['date'].isoformat()
        else:
            p_copy['date'] = str(p_copy['date'])
    return p_copy

@app.route('/api/purchases', methods=['GET'])
@login_required
def api_get_purchases():
    category = request.args.get('category')
    exclude_category = request.args.get('exclude_category')
    purchases = get_all_purchases()
    
    if category:
        purchases = [p for p in purchases if p.get('category') == category]
    if exclude_category:
        purchases = [p for p in purchases if p.get('category') != exclude_category]
    
    return jsonify([serialize_purchase(p) for p in purchases])

@app.route('/api/purchases/filter', methods=['GET'])
@login_required
def api_filter_purchases():
    start = request.args.get('start_date')
    end = request.args.get('end_date')
    category = request.args.get('category')
    exclude_category = request.args.get('exclude_category')
    date_type = request.args.get('date_type', 'original')               # NEW
    show_updated_only = request.args.get('show_updated_only', 'false').lower() == 'true'   # NEW

    if not start or not end:
        return jsonify([])

    # Pass the extra parameters to the service
    purchases = get_purchases_by_date_range(
        start, end,
        date_type=date_type,
        show_updated_only=show_updated_only
    )

    if category:
        purchases = [p for p in purchases if p.get('category') == category]
    if exclude_category:
        purchases = [p for p in purchases if p.get('category') != exclude_category]

    return jsonify([serialize_purchase(p) for p in purchases])

@app.route('/api/purchases/suggestions/name', methods=['GET'])
@login_required
def api_suggest_name():
    q = request.args.get('q', '')
    category = request.args.get('category')
    exclude_category = request.args.get('exclude_category')
    if not q:
        return jsonify([])
    suggestions = get_product_suggestions(q)
    if category:
        suggestions = [s for s in suggestions if s.get('category') == category]
    if exclude_category:
        suggestions = [s for s in suggestions if s.get('category') != exclude_category]
    return jsonify(suggestions)

@app.route('/api/purchases/suggestions/category', methods=['GET'])
@login_required
def api_suggest_category():
    q = request.args.get('q', '')
    category = request.args.get('category')
    exclude_category = request.args.get('exclude_category')
    if not q:
        return jsonify([])
    suggestions = get_category_suggestions(q)
    if category:
        suggestions = [s for s in suggestions if s.get('category') == category]
    if exclude_category:
        suggestions = [s for s in suggestions if s.get('category') != exclude_category]
    return jsonify(suggestions)

@app.route('/api/purchases/pdf', methods=['POST'])
@login_required
def api_purchases_pdf():
    data = request.json
    purchases = data.get('purchases', [])
    from_date = data.get('from_date', '')
    to_date = data.get('to_date', '')
    totals = data.get('totals', {})
    
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), rightMargin=15, leftMargin=15,
                            topMargin=20, bottomMargin=20)
    styles = getSampleStyleSheet()
    elements = []
    
    elements.append(Paragraph("📦 Purchases Report", styles["Title"]))
    elements.append(Spacer(1, 6))
    elements.append(Paragraph(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", styles["Normal"]))
    if from_date and to_date:
        elements.append(Paragraph(f"Date Range: {from_date} → {to_date}", styles["Normal"]))
    elements.append(Spacer(1, 12))

    # ✅ Updated table with Claimed and Sold columns
    table_data = [["ID", "Name", "Brand", "Qty", "Remaining", "Sold", "Claimed", "Cost", "Discount", "Total", "Selling", "Source", "Date/Time"]]
    total_qty = total_cost = total_discount = total_selling = total_claimed = total_claimed_cost = total_sold_summary = 0
    row_colors = [colors.whitesmoke, colors.lightgrey]

    for p in purchases:
        date_str = p.get('date', '')
        if date_str:
            try:
                dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                date_str = dt.strftime("%Y-%m-%d %H:%M")
            except:
                date_str = str(date_str)[:16]
        
        qty = p.get("quantity", 0)
        remaining = p.get("remaining_quantity", 0)
        claimed = p.get("claimed_quantity", 0)
        
        # ✅ FIXED: Per‑batch sold = total quantity - remaining quantity
        sold = qty - remaining
        
        cost = p.get("cost_price", 0)
        claimed_cost = claimed * cost

        table_data.append([
            p["batch_id"], 
            p["name"], 
            p["brand"],
            qty, 
            remaining,
            sold,
            claimed,
            f"₵{cost:.2f}",
            f"₵{p.get('discount', 0):.2f}",
            f"₵{p.get('total_cost', 0):.2f}",
            f"₵{p.get('selling_price', 0):.2f}",
            p.get('source', 'Unknown'),
            date_str
        ])
        # Aggregate totals for summary
        total_qty += qty
        total_cost += p.get("total_cost", 0)
        total_discount += p.get("discount", 0)
        total_selling += p.get("selling_price", 0) * qty
        total_claimed += claimed
        total_claimed_cost += claimed_cost
        total_sold_summary += sold   # sum of per‑batch sold

    table = Table(table_data, repeatRows=1, hAlign='LEFT',
                  colWidths=[1.5*cm, 4*cm, 3*cm, 1.5*cm, 1.5*cm, 1.5*cm, 1.5*cm, 2*cm, 2*cm, 2.5*cm, 2.5*cm, 2.5*cm, 3*cm])
    style = TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#00CFCF")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.whitesmoke),
        ("ALIGN", (3,1), (-2,-1), "CENTER"),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("GRID", (0,0), (-1,-1), 0.4, colors.black),
        ("FONTSIZE", (0,0), (-1,-1), 7),
    ])
    for i in range(1, len(table_data)):
        style.add("BACKGROUND", (0,i), (-1,i), row_colors[i%2])
    table.setStyle(style)
    elements.append(table)
    elements.append(Spacer(1, 12))

    elements.append(Paragraph("📊 Summary", styles["Heading2"]))
    elements.append(Spacer(1, 6))
    
    # Summary metrics – using the computed aggregates (and fallbacks from totals if provided)
    total_batches = len(purchases)
    total_remaining = total_qty - total_sold_summary
    total_sold = total_sold_summary
    adjusted_capital = totals.get('adjusted_capital', total_cost - total_claimed_cost) if totals else total_cost - total_claimed_cost
    total_profit = totals.get('total_profit', total_selling - (total_cost - total_discount)) if totals else total_selling - (total_cost - total_discount)
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
    
    summary_table = Table(summary_data, colWidths=[6*cm, 6*cm], hAlign='CENTER')
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#1E3A5F")),
        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 9),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('GRID', (0,0), (-1,-1), 0.5, colors.black),
        ('BACKGROUND', (1,1), (1,-1), colors.lightgrey),
        ('TEXTCOLOR', (1,1), (1,-1), colors.black),
        ('BACKGROUND', (0,1), (0,-1), colors.whitesmoke),
    ]))
    elements.append(summary_table)
    
    profit_color = colors.green if total_profit >= 0 else colors.red
    profit_status = "📈 PROFIT" if total_profit >= 0 else "📉 LOSS"
    elements.append(Spacer(1, 6))
    elements.append(Paragraph(
        f"<font color='{profit_color}' size='14'><b>{profit_status}: ₵{total_profit:.2f}</b></font>",
        styles["Normal"]
    ))
    
    doc.build(elements)
    buffer.seek(0)
    return send_file(buffer, as_attachment=True,
                     download_name=f"Purchases_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf",
                     mimetype='application/pdf')

# ===================== PURCHASE HISTORY API =====================
@app.route('/api/purchases/<int:batch_id>/history', methods=['GET'])
@login_required
def api_purchase_history(batch_id):
    """Get purchase history for a batch including update history"""
    try:
        from services.purchase_service import get_purchase_history, get_sold_history, get_batch_update_history
        history = get_purchase_history(batch_id)
        sold_history = get_sold_history(batch_id)
        update_history = get_batch_update_history(batch_id)   # NEW

        if not history:
            return jsonify({'success': False, 'error': 'Batch not found'}), 404

        return jsonify({
            'success': True,
            'purchase': history,
            'sold_items': sold_history,
            'total_sold': sum(item['quantity'] for item in sold_history),
            'update_history': update_history          # <-- INCLUDE IT
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/purchases', methods=['POST'])
@login_required
def api_add_purchase():
    data = request.json
    try:
        batch_id = add_purchase(
            name=data['name'],
            brand=data['brand'],
            category=data['category'],
            quantity=int(data['quantity']),
            cost_price=float(data['cost_price']),
            discount=float(data.get('discount', 0)),
            selling_price=float(data['selling_price']),
            source=data.get('source', 'Unknown')
        )
        return jsonify({'success': True, 'batch_id': batch_id})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/api/purchases/<int:batch_id>', methods=['PUT'])
@login_required
def api_update_purchase(batch_id):
    data = request.json
    try:
        print(f"🔄 Updating batch #{batch_id}: {data}")

        # Extract update_mode (default 'auto')
        update_mode = data.get('update_mode', 'auto')

        new_batch_id = update_product(
            batch_id=batch_id,
            name=data['name'],
            brand=data['brand'],
            category=data['category'],
            quantity=int(data['quantity']),
            cost_price=float(data['cost_price']),
            discount=float(data.get('discount', 0)),
            selling_price=float(data['selling_price']),
            source=data.get('source', 'Unknown'),
            update_mode=update_mode          # <-- PASS IT
        )
        return jsonify({'success': True, 'new_batch_id': new_batch_id})
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        print(f"❌ Error updating batch #{batch_id}: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/purchases/suggestions/source', methods=['GET'])
@login_required
def api_suggest_source():
    q = request.args.get('q', '')
    if not q:
        return jsonify([])
    from services.purchase_service import get_source_suggestions
    suggestions = get_source_suggestions(q)
    return jsonify(suggestions)

# ===================== PRODUCT API =====================
# ===================== PRODUCT API =====================
@app.route('/api/products', methods=['GET'])
@login_required
def api_get_products():
    """Get products grouped by case‑insensitive name+brand, merging all batches."""
    category = request.args.get('category')
    exclude_category = request.args.get('exclude_category')

    conn = get_connection()
    cursor = conn.cursor()
    try:
        # First, get all products that are not deleted, with category filter
        product_query = """
            SELECT p.id, p.name, p.brand, p.category, p.cost_price, p.selling_price, p.discount
            FROM products p
            WHERE NOT EXISTS (
                SELECT 1 FROM deleted_products dp 
                WHERE dp.product_id = p.id 
                AND dp.action IN ('PERMANENTLY DELETED', 'PRODUCT DELETED')
                AND dp.source = 'product'
            )
        """
        params = []
        if category:
            product_query += " AND p.category = %s"
            params.append(category)
        if exclude_category:
            product_query += " AND p.category != %s"
            params.append(exclude_category)

        cursor.execute(product_query, params)
        products = cursor.fetchall()

        # Group by LOWER(name), LOWER(brand)
        groups = {}
        for p in products:
            key = (p[1].lower(), p[2].lower())  # name, brand
            if key not in groups:
                groups[key] = {
                    'product_id': p[0],  # use the first encountered id (could be min)
                    'name': p[1],
                    'brand': p[2],
                    'category': p[3],
                    'cost_price': p[4],
                    'selling_price': p[5],
                    'discount': p[6],
                    'product_ids': [p[0]]
                }
            else:
                # Keep the product with the lowest id as representative
                if p[0] < groups[key]['product_id']:
                    groups[key]['product_id'] = p[0]
                    groups[key]['name'] = p[1]
                    groups[key]['brand'] = p[2]
                    groups[key]['category'] = p[3]
                    groups[key]['cost_price'] = p[4]
                    groups[key]['selling_price'] = p[5]
                    groups[key]['discount'] = p[6]
                groups[key]['product_ids'].append(p[0])

        # Now for each group, fetch all batches from all product_ids in that group
        result_products = []
        total_batches = 0
        for group in groups.values():
            product_ids = group['product_ids']
            placeholders = ','.join(['%s'] * len(product_ids))
            batch_query = f"""
                SELECT pb.id, pb.quantity, pb.remaining_quantity,
                       pb.cost_price, pb.selling_price, pb.discount, pb.date,
                       pb.is_faulty, pb.claimed_quantity,
                       (SELECT SUM(quantity) FROM claims WHERE batch_id = pb.id AND status = 'active') AS active_claims
                FROM purchase_batches pb
                WHERE pb.product_id IN ({placeholders})
            """
            cursor.execute(batch_query, product_ids)
            batch_rows = cursor.fetchall()
            batches = []
            for b in batch_rows:
                batches.append({
                    'batch_id': b[0],
                    'quantity': b[1],
                    'remaining_quantity': b[2],
                    'cost_price': float(b[3]),
                    'selling_price': float(b[4]),
                    'discount': float(b[5]),
                    'date': b[6],
                    'is_faulty': b[7] or False,
                    'claimed_quantity': b[8] or 0,
                    'active_claims': b[9] or 0
                })
                total_batches += 1

            # Compute total stock for the merged product
            total_stock = sum(b['remaining_quantity'] for b in batches)

            result_products.append({
                'product_id': group['product_id'],
                'name': group['name'],
                'brand': group['brand'],
                'category': group['category'],
                'cost_price': float(group['cost_price']),
                'selling_price': float(group['selling_price']),
                'discount': float(group['discount']),
                'stock': total_stock,
                'batches': batches
            })

        return jsonify({
            'products': result_products,
            'total_products': len(result_products),
            'total_batches': total_batches
        })

    except Exception as e:
        print(f"❌ Error in api_get_products: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@app.route('/api/products', methods=['POST'])
@login_required
def api_add_product():
    data = request.json
    try:
        batch_id = add_purchase(
            name=data['name'],
            brand=data['brand'],
            category=data.get('category', ''),
            quantity=int(data['quantity']),
            cost_price=float(data['cost_price']),
            discount=float(data.get('discount', 0)),
            selling_price=float(data['selling_price'])
        )
        return jsonify({'success': True, 'batch_id': batch_id})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/api/products/<int:product_id>', methods=['DELETE'])
@login_required
def api_delete_product(product_id):
    delete_type = request.args.get('type', 'keep')
    try:
        if delete_type == 'clean':
            delete_product_clean_all(product_id)
        else:
            delete_product_keep_history(product_id)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/api/batches/<int:batch_id>', methods=['DELETE'])
@login_required
def api_delete_batch(batch_id):
    delete_type = request.args.get('type', 'keep')
    try:
        if delete_type == 'clean':
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
    products = get_products_for_sale()
    
    if category:
        products = [p for p in products if p.get('category') == category]
    if exclude_category:
        products = [p for p in products if p.get('category') != exclude_category]
    
    grouped = {}
    for p in products:
        key = (p['name'], p['brand'])
        if key not in grouped:
            grouped[key] = {
                'id': p['id'],
                'name': p['name'],
                'brand': p['brand'],
                'category': p.get('category', ''),
                'cost_price': p.get('cost_price', 0),
                'selling_price': p.get('selling_price', 0),
                'discount': p.get('discount', 0),
                'stock': 0,
                'batches': []
            }
        grouped[key]['stock'] += p.get('stock', 0)
        
        if 'batches' in p and p['batches']:
            existing_batch_ids = {b.get('batch_id') for b in grouped[key]['batches']}
            for batch in p['batches']:
                if batch.get('batch_id') not in existing_batch_ids:
                    grouped[key]['batches'].append(batch)
        elif p.get('batch_id'):
            grouped[key]['batches'].append({
                'batch_id': p.get('batch_id'),
                'remaining_quantity': p.get('stock', 0),
                'selling_price': p.get('selling_price', 0),
                'cost_price': p.get('cost_price', 0),
                'batch_quantity': p.get('batch_quantity', p.get('stock', 0))
            })
    
    result = list(grouped.values())
    return jsonify(result)

@app.route('/api/sales/batches/<int:product_id>', methods=['GET'])
@login_required
def api_sales_batches(product_id):
    batches = get_batches_for_product(product_id)
    return jsonify(batches)

@app.route('/api/sales/batches/by_name', methods=['GET'])
@login_required
def api_sales_batches_by_name():
    name = request.args.get('name')
    brand = request.args.get('brand', '')
    
    if not name:
        return jsonify([])
    
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        if brand:
            cursor.execute("""
                SELECT pb.id, pb.product_id, pb.quantity, pb.remaining_quantity, 
                       pb.cost_price, pb.selling_price, pb.discount, pb.date,
                       pb.is_faulty, pb.claimed_quantity,
                       p.name, p.brand
                FROM purchase_batches pb
                JOIN products p ON p.id = pb.product_id
                WHERE p.name = %s AND p.brand = %s
                AND pb.remaining_quantity > 0
                AND NOT EXISTS (
                    SELECT 1 FROM deleted_products dp 
                    WHERE dp.product_id = p.id 
                    AND dp.action IN ('PERMANENTLY DELETED', 'PRODUCT DELETED')
                    AND dp.source = 'product'
                )
                ORDER BY pb.date ASC
            """, (name, brand))
        else:
            cursor.execute("""
                SELECT pb.id, pb.product_id, pb.quantity, pb.remaining_quantity, 
                       pb.cost_price, pb.selling_price, pb.discount, pb.date,
                       pb.is_faulty, pb.claimed_quantity,
                       p.name, p.brand
                FROM purchase_batches pb
                JOIN products p ON p.id = pb.product_id
                WHERE p.name = %s
                AND pb.remaining_quantity > 0
                AND NOT EXISTS (
                    SELECT 1 FROM deleted_products dp 
                    WHERE dp.product_id = p.id 
                    AND dp.action IN ('PERMANENTLY DELETED', 'PRODUCT DELETED')
                    AND dp.source = 'product'
                )
                ORDER BY pb.date ASC
            """, (name,))
        
        rows = cursor.fetchall()
        
        result = []
        for r in rows:
            result.append({
                'batch_id': r[0],
                'product_id': r[1],
                'batch_quantity': r[2],
                'remaining_quantity': r[3],
                'cost_price': float(r[4]),
                'selling_price': float(r[5]),
                'discount': float(r[6]),
                'date': r[7].isoformat() if r[7] else None,
                'is_faulty': r[8] or False,
                'claimed_quantity': r[9] or 0,
                'product_name': r[10],
                'product_brand': r[11]
            })
        
        return jsonify(result)
        
    except Exception as e:
        print(f"❌ Error fetching batches by name: {str(e)}")
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/sales/complete', methods=['POST'])
@login_required
def api_sales_complete():
    data = request.json
    cart_items = data.get('cart_items', [])
    sale_datetime = data.get('sale_datetime')
    selected_batches = data.get('selected_batches', [])
    payment_method = data.get('payment_method', 'cash')
    cheque_number = data.get('cheque_number')
    
    user_id = session.get('user_id')
    
    try:
        result = create_multi_sale(
            cart_items, 
            sale_datetime, 
            selected_batches,
            payment_method,
            cheque_number,
            user_id=user_id
        )
        
        receipt_cart = []
        for idx, item in enumerate(cart_items):
            product_batches = []
            for sb in selected_batches:
                if sb.get('product_id') == item['product']['id']:
                    batch_info = get_batch_by_id(sb['batch_id'])
                    if batch_info:
                        product_batches.append({
                            'batch_id': sb['batch_id'],
                            'qty': sb['qty'],
                            'batch': batch_info
                        })
            receipt_cart.append({
                'product': item['product'],
                'qty': item['qty'],
                'discount': item['discount'],
                'selected_batches': product_batches
            })
        
        receipt_file, receipt_text = generate_receipt_multi(
            receipt_cart, 
            result['total'],
            payment_method,
            cheque_number
        )
        
        return jsonify({
            'success': True,
            'sale_id': result['sale_id'],
            'total': result['total'],
            'date': result['date'],
            'receipt_text': receipt_text,
            'receipt_file': receipt_file,
            'payment_method': payment_method,
            'cheque_number': cheque_number
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

# ===================== REVERSE SALE API =====================
@app.route('/api/sales/reverse/<int:sale_id>', methods=['POST'])
@login_required
def api_reverse_sale(sale_id):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT reversed FROM sales WHERE id = %s", (sale_id,))
        row = cursor.fetchone()
        if not row:
            return jsonify({'success': False, 'error': 'Sale not found'}), 404
        if row[0] == 1:
            return jsonify({'success': False, 'error': 'Sale already reversed'}), 400
        cursor.execute("""
            SELECT product_id, batch_id, quantity, cost_price, selling_price, profit
            FROM sales_items
            WHERE sale_id = %s
        """, (sale_id,))
        items = cursor.fetchall()
        if not items:
            return jsonify({'success': False, 'error': 'No items found for this sale'}), 400
        for item in items:
            product_id, batch_id, qty, cost_price, selling_price, profit = item
            cursor.execute("""
                UPDATE purchase_batches
                SET remaining_quantity = remaining_quantity + %s
                WHERE id = %s
            """, (qty, batch_id))
            update_product_stock(cursor, product_id)
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
    import traceback
    data = request.json
    sale_id = data.get('sale_id')
    items_to_reverse = data.get('items', [])
    if not sale_id or not items_to_reverse:
        return jsonify({'success': False, 'error': 'Missing sale_id or items'}), 400
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT reversed, subtotal, discount, total, profit FROM sales WHERE id = %s", (sale_id,))
        row = cursor.fetchone()
        if not row:
            return jsonify({'success': False, 'error': 'Sale not found'}), 404
        if row[0] == 1:
            return jsonify({'success': False, 'error': 'Sale already fully reversed'}), 400
        original_subtotal = row[1]
        original_discount = row[2]
        original_net_profit = row[4]
        reversed_subtotal = 0
        reversed_profit_gross = 0
        for item in items_to_reverse:
            batch_id = item['batch_id']
            qty = item['quantity']
            cursor.execute("SELECT product_id, selling_price FROM purchase_batches WHERE id = %s", (batch_id,))
            batch_row = cursor.fetchone()
            if not batch_row:
                raise ValueError(f"Batch {batch_id} not found")
            product_id = batch_row[0]
            selling_price = batch_row[1]
            cursor.execute("SELECT cost_price, profit FROM sales_items WHERE sale_id = %s AND batch_id = %s", (sale_id, batch_id))
            item_row = cursor.fetchone()
            if not item_row:
                raise ValueError(f"Sales item for batch {batch_id} not found")
            cost_price = item_row[0]
            item_gross_profit = item_row[1]
            item_subtotal = selling_price * qty
            reversed_subtotal += item_subtotal
            reversed_profit_gross += item_gross_profit
            cursor.execute("UPDATE purchase_batches SET remaining_quantity = remaining_quantity + %s WHERE id = %s", (qty, batch_id))
            cursor.execute("DELETE FROM sales_items WHERE sale_id = %s AND batch_id = %s", (sale_id, batch_id))
            update_product_stock(cursor, product_id)
        new_subtotal = original_subtotal - reversed_subtotal
        new_gross_profit = original_net_profit + original_discount - reversed_profit_gross
        if original_subtotal > 0:
            new_discount = original_discount * (new_subtotal / original_subtotal)
        else:
            new_discount = 0
        new_net_profit = new_gross_profit - new_discount
        new_total = new_subtotal - new_discount
        if new_total < 0:
            new_total = 0
        if new_subtotal == 0:
            cursor.execute("UPDATE sales SET reversed = 1 WHERE id = %s", (sale_id,))
        else:
            cursor.execute("""
                UPDATE sales
                SET subtotal = %s, discount = %s, total = %s, profit = %s
                WHERE id = %s
            """, (new_subtotal, new_discount, new_total, new_net_profit, sale_id))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        print("ERROR in reverse_items:")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()

# ===================== TODAY'S SALES API =====================
@app.route('/api/today_sales', methods=['GET'])
@login_required
def api_today_sales():
    period = request.args.get('period', 'daily')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    category = request.args.get('category')
    exclude_category = request.args.get('exclude_category')
    
    conn = get_connection()
    cursor = conn.cursor()
    
    select_clause = """
        SELECT 
            sales.id, 
            products.name, 
            products.brand, 
            products.category,
            sales_items.quantity, 
            sales_items.selling_price,
            sales_items.quantity * sales_items.selling_price AS subtotal,
            sales.discount, 
            sales.total, 
            sales_items.profit,
            COALESCE(purchase_batches.id, -1) as batch_id,
            COALESCE(purchase_batches.cost_price, 0) as cost_price, 
            sales.date,
            CASE WHEN purchase_batches.id IS NULL THEN 1 ELSE 0 END as is_deleted_batch,
            sales.profit as net_profit,
            COALESCE(sales.payment_method, 'cash') as payment_method,
            sales.cheque_number,
            u.username
    """
    from_clause = """
        FROM sales
        JOIN sales_items ON sales.id = sales_items.sale_id
        JOIN products ON products.id = sales_items.product_id
        LEFT JOIN purchase_batches ON purchase_batches.id = sales_items.batch_id
        LEFT JOIN users u ON sales.user_id = u.id
    """
    
    where_conditions = ["sales.reversed = 0"]
    params = []
    
    if start_date and end_date:
        where_conditions.append("sales.date::date BETWEEN %s AND %s")
        params.extend([start_date, end_date])
    else:
        if period == 'daily':
            where_conditions.append("sales.date::date = CURRENT_DATE")
        elif period == 'weekly':
            where_conditions.append("sales.date >= CURRENT_DATE - INTERVAL '6 days'")
        elif period == 'monthly':
            where_conditions.append("EXTRACT(YEAR FROM sales.date) = EXTRACT(YEAR FROM CURRENT_DATE) AND EXTRACT(MONTH FROM sales.date) = EXTRACT(MONTH FROM CURRENT_DATE)")
        elif period == 'yearly':
            where_conditions.append("EXTRACT(YEAR FROM sales.date) = EXTRACT(YEAR FROM CURRENT_DATE)")
    
    if category:
        where_conditions.append("products.category = %s")
        params.append(category)
    if exclude_category:
        where_conditions.append("products.category != %s")
        params.append(exclude_category)
    
    where_clause = " AND ".join(where_conditions)
    query = f"{select_clause} {from_clause} WHERE {where_clause} ORDER BY sales.date DESC"
    
    try:
        cursor.execute(query, params)
        rows = cursor.fetchall()
        sales_data = []
        for r in rows:
            sales_data.append({
                'sale_id': r[0],
                'name': r[1],
                'brand': r[2] or '',
                'category': r[3] or '',
                'quantity': r[4],
                'selling_price': float(r[5]),
                'subtotal': float(r[6]),
                'discount': float(r[7]),
                'total': float(r[8]),
                'profit': float(r[9]),
                'batch_id': r[10],
                'cost_price': float(r[11]),
                'sale_date': r[12].isoformat() if hasattr(r[12], 'isoformat') else str(r[12]),
                'is_deleted_batch': bool(r[13]),
                'net_profit': float(r[14]),
                'payment_method': r[15] if len(r) > 15 else 'cash',
                'cheque_number': r[16] if len(r) > 16 else None,
                'username': r[17] if len(r) > 17 else 'Unknown'
            })
        return jsonify(sales_data)
    except Exception as e:
        print(f"Error in today_sales API: {str(e)}")
        return jsonify([]), 500
    finally:
        conn.close()

@app.route('/api/today_sales/pdf', methods=['POST'])
@login_required
def api_today_sales_pdf():
    data = request.json
    sales_data = data.get('sales_data', [])
    period_text = data.get('period_text', 'Sales Report')
    summary = data.get('summary', '')
    
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), 
                           rightMargin=20, leftMargin=20, topMargin=20, bottomMargin=20)
    elements = []
    styles = getSampleStyleSheet()
    elements.append(Paragraph("Sales Report", styles['Title']))
    elements.append(Spacer(1, 0.2*cm))
    elements.append(Paragraph(period_text, styles['Normal']))
    elements.append(Spacer(1, 0.2*cm))
    
    seen_sales = set()
    total_sales = 0.0
    total_discount = 0.0
    total_profit = 0.0
    total_items = 0
    total_subtotal = 0.0
    gross_profit = 0.0
    
    # ✅ Initialize payment totals
    cash_total = 0.0
    momo_total = 0.0
    cheque_total = 0.0
    
    for sale in sales_data:
        sale_id = sale['sale_id']
        
        # ✅ Count each sale only once for payment totals
        if sale_id not in seen_sales:
            total_sales += sale['total']
            total_discount += sale['discount']
            seen_sales.add(sale_id)
            
            # ✅ Calculate payment totals per sale (not per item)
            payment_method = sale.get('payment_method', 'cash').lower()
            if payment_method == 'cash' or payment_method == '':
                cash_total += sale['total']
            elif payment_method == 'momo':
                momo_total += sale['total']
            elif payment_method == 'cheque':
                cheque_total += sale['total']
            else:
                # Default to cash if unknown
                cash_total += sale['total']
        
        # ✅ Calculate item profit correctly (always per item)
        item_gross_profit = (sale['selling_price'] - sale['cost_price']) * sale['quantity']
        gross_profit += item_gross_profit
        total_items += sale['quantity']
        total_subtotal += sale['selling_price'] * sale['quantity']
    
    # ✅ Net profit = Gross profit - Total discount
    net_profit = gross_profit - total_discount
    total_profit = net_profit
    
    elements.append(Paragraph(
        f"🧾 Items: {total_items}   |   💰 Sales: ₵{total_sales:.2f}   |   "
        f"📉 Discount: ₵{total_discount:.2f}   |   📈 Profit: ₵{total_profit:.2f}",
        styles['Normal']
    ))
    elements.append(Spacer(1, 0.3*cm))
    
    table_data = [["Name", "Brand", "Category", "Qty", "Price", "Subtotal", 
                   "Discount", "Total", "Profit", "Batch", "Cost", "Sale Date", "Payment", "Status", "User"]]
    
    for s in sales_data:
        status = "Deleted Batch" if s['is_deleted_batch'] else "Active"
        payment_display = s.get('payment_method', 'cash').upper()
        if s.get('cheque_number'):
            payment_display += f" #{s['cheque_number']}"
        
        # ✅ Calculate item profit correctly (gross profit per item)
        item_profit = (s['selling_price'] - s['cost_price']) * s['quantity']
        
        row = [
            s['name'], s['brand'], s['category'],
            str(s['quantity']),
            f"₵{s['selling_price']:.2f}",
            f"₵{s['subtotal']:.2f}",
            f"₵{s['discount']:.2f}",
            f"₵{s['total']:.2f}",
            f"₵{item_profit:.2f}",
            str(s['batch_id']) if s['batch_id'] != -1 else "DELETED",
            f"₵{s['cost_price']:.2f}",
            s['sale_date'],
            payment_display,
            status,
            s.get('username', 'Unknown')
        ]
        table_data.append(row)
    
    table = Table(table_data, repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#1E3A5F")),
        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('GRID', (0,0), (-1,-1), 0.5, colors.black),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('ALIGN', (3,1), (-1,-1), 'CENTER'),
        ('FONTSIZE', (0,0), (-1,-1), 7),
    ]))
    elements.append(table)
    
    # ✅ Add summary section with corrected payment totals
    elements.append(Spacer(1, 0.5*cm))
    elements.append(Paragraph("📊 Summary", styles["Heading2"]))
    elements.append(Spacer(1, 0.2*cm))
    
    summary_data = [
        ["Metric", "Value"],
        ["🧾 Total Sales", f"₵{total_sales:.2f}"],
        ["📦 Total Items", str(total_items)],
        ["📉 Total Discount", f"₵{total_discount:.2f}"],
        ["📈 Net Profit", f"₵{net_profit:.2f}"],
        ["💵 Cash Payments", f"₵{cash_total:.2f}"],
        ["📱 MoMo Payments", f"₵{momo_total:.2f}"],
        ["📝 Cheque Payments", f"₵{cheque_total:.2f}"]
    ]
    
    summary_table = Table(summary_data, colWidths=[6*cm, 6*cm], hAlign='CENTER')
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#1E3A5F")),
        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 9),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('GRID', (0,0), (-1,-1), 0.5, colors.black),
        ('BACKGROUND', (1,1), (1,-1), colors.lightgrey),
        ('TEXTCOLOR', (1,1), (1,-1), colors.black),
        ('BACKGROUND', (0,1), (0,-1), colors.whitesmoke),
    ]))
    elements.append(summary_table)
    
    doc.build(elements)
    buffer.seek(0)
    return send_file(buffer, as_attachment=True,
                     download_name=f"SalesReport_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf",
                     mimetype='application/pdf')
# ===================== ANALYTICS API =====================
@app.route('/api/analytics/summary', methods=['GET'])
@login_required
def api_analytics_summary():
    period = request.args.get('period', 'daily')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    conn = get_connection()
    cursor = conn.cursor()
    
    if start_date and end_date:
        cursor.execute("""
            SELECT 
                COALESCE(SUM(s.total), 0),
                COALESCE(SUM(s.discount), 0),
                COALESCE(SUM(s.profit), 0),
                COALESCE(SUM(si.quantity), 0)
            FROM sales s
            LEFT JOIN sales_items si ON s.id = si.sale_id
            WHERE s.date::date BETWEEN %s AND %s
            AND s.reversed = 0
        """, (start_date, end_date))
    elif period == 'weekly':
        cursor.execute("""
            SELECT 
                COALESCE(SUM(s.total), 0),
                COALESCE(SUM(s.discount), 0),
                COALESCE(SUM(s.profit), 0),
                COALESCE(SUM(si.quantity), 0)
            FROM sales s
            LEFT JOIN sales_items si ON s.id = si.sale_id
            WHERE s.date >= CURRENT_DATE - INTERVAL '6 days'
            AND s.reversed = 0
        """)
    elif period == 'monthly':
        cursor.execute("""
            SELECT 
                COALESCE(SUM(s.total), 0),
                COALESCE(SUM(s.discount), 0),
                COALESCE(SUM(s.profit), 0),
                COALESCE(SUM(si.quantity), 0)
            FROM sales s
            LEFT JOIN sales_items si ON s.id = si.sale_id
            WHERE EXTRACT(YEAR FROM s.date) = EXTRACT(YEAR FROM CURRENT_DATE)
            AND EXTRACT(MONTH FROM s.date) = EXTRACT(MONTH FROM CURRENT_DATE)
            AND s.reversed = 0
        """)
    elif period == 'yearly':
        cursor.execute("""
            SELECT 
                COALESCE(SUM(s.total), 0),
                COALESCE(SUM(s.discount), 0),
                COALESCE(SUM(s.profit), 0),
                COALESCE(SUM(si.quantity), 0)
            FROM sales s
            LEFT JOIN sales_items si ON s.id = si.sale_id
            WHERE EXTRACT(YEAR FROM s.date) = EXTRACT(YEAR FROM CURRENT_DATE)
            AND s.reversed = 0
        """)
    elif period == 'all':
        cursor.execute("""
            SELECT 
                COALESCE(SUM(s.total), 0),
                COALESCE(SUM(s.discount), 0),
                COALESCE(SUM(s.profit), 0),
                COALESCE(SUM(si.quantity), 0)
            FROM sales s
            LEFT JOIN sales_items si ON s.id = si.sale_id
            WHERE s.reversed = 0
        """)
    else:
        cursor.execute("""
            SELECT 
                COALESCE(SUM(s.total), 0),
                COALESCE(SUM(s.discount), 0),
                COALESCE(SUM(s.profit), 0),
                COALESCE(SUM(si.quantity), 0)
            FROM sales s
            LEFT JOIN sales_items si ON s.id = si.sale_id
            WHERE s.date::date = CURRENT_DATE
            AND s.reversed = 0
        """)
    
    result = cursor.fetchone()
    conn.close()
    
    return jsonify({
        'items_sold': int(result[3] or 0),
        'subtotal': float(result[0] or 0),
        'discount': float(result[1] or 0),
        'total': float(result[0] or 0),
        'profit': float(result[2] or 0)
    })

@app.route('/api/analytics/trend', methods=['GET'])
@login_required
def api_analytics_trend():
    period = request.args.get('period', 'daily')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    conn = get_connection()
    cursor = conn.cursor()
    
    if start_date and end_date:
        cursor.execute("""
            SELECT 
                s.date::date as label,
                COALESCE(SUM(s.total), 0) as sales_total,
                COALESCE(SUM(s.profit), 0) as profit_total
            FROM sales s
            WHERE s.date::date BETWEEN %s AND %s
            AND s.reversed = 0
            GROUP BY s.date::date
            ORDER BY label ASC
        """, (start_date, end_date))
    elif period == 'weekly':
        cursor.execute("""
            SELECT 
                s.date::date as label,
                COALESCE(SUM(s.total), 0) as sales_total,
                COALESCE(SUM(s.profit), 0) as profit_total
            FROM sales s
            WHERE s.date >= CURRENT_DATE - INTERVAL '6 days'
            AND s.reversed = 0
            GROUP BY s.date::date
            ORDER BY label ASC
        """)
    elif period == 'monthly':
        cursor.execute("""
            SELECT 
                s.date::date as label,
                COALESCE(SUM(s.total), 0) as sales_total,
                COALESCE(SUM(s.profit), 0) as profit_total
            FROM sales s
            WHERE EXTRACT(YEAR FROM s.date) = EXTRACT(YEAR FROM CURRENT_DATE)
            AND EXTRACT(MONTH FROM s.date) = EXTRACT(MONTH FROM CURRENT_DATE)
            AND s.reversed = 0
            GROUP BY s.date::date
            ORDER BY label ASC
        """)
    elif period == 'yearly':
        cursor.execute("""
            SELECT 
                DATE_TRUNC('month', s.date) as label,
                COALESCE(SUM(s.total), 0) as sales_total,
                COALESCE(SUM(s.profit), 0) as profit_total
            FROM sales s
            WHERE EXTRACT(YEAR FROM s.date) = EXTRACT(YEAR FROM CURRENT_DATE)
            AND s.reversed = 0
            GROUP BY DATE_TRUNC('month', s.date)
            ORDER BY label ASC
        """)
    elif period == 'all':
        cursor.execute("""
            SELECT 
                DATE_TRUNC('month', s.date) as label,
                COALESCE(SUM(s.total), 0) as sales_total,
                COALESCE(SUM(s.profit), 0) as profit_total
            FROM sales s
            WHERE s.reversed = 0
            GROUP BY DATE_TRUNC('month', s.date)
            ORDER BY label ASC
            LIMIT 24
        """)
    else:
        cursor.execute("""
            SELECT 
                s.date::date as label,
                COALESCE(SUM(s.total), 0) as sales_total,
                COALESCE(SUM(s.profit), 0) as profit_total
            FROM sales s
            WHERE s.date::date = CURRENT_DATE
            AND s.reversed = 0
            GROUP BY s.date::date
            ORDER BY label ASC
        """)
    
    rows = cursor.fetchall()
    conn.close()
    
    result = []
    for row in rows:
        label = row[0]
        if period in ['yearly', 'all']:
            if hasattr(label, 'strftime'):
                label = label.strftime('%b %Y')
            else:
                label = str(label)
        else:
            if hasattr(label, 'strftime'):
                label = label.strftime('%Y-%m-%d')
            else:
                label = str(label)
        
        result.append({
            'label': str(label),
            'sales': float(row[1]),
            'profit': float(row[2])
        })
    
    return jsonify(result)

@app.route('/api/analytics/top_products', methods=['GET'])
@login_required
def api_analytics_top_products():
    period = request.args.get('period', 'daily')
    limit = int(request.args.get('limit', 10))
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    conn = get_connection()
    cursor = conn.cursor()
    
    if start_date and end_date:
        cursor.execute("""
            SELECT 
                p.name, 
                COALESCE(p.brand, '') as brand, 
                COALESCE(p.category, '') as category, 
                COALESCE(SUM(si.quantity), 0) as qty
            FROM sales_items si
            JOIN sales s ON si.sale_id = s.id
            JOIN products p ON p.id = si.product_id
            WHERE s.date::date BETWEEN %s AND %s
            AND s.reversed = 0
            AND NOT EXISTS (
                SELECT 1 FROM deleted_products dp 
                WHERE dp.product_id = p.id 
                AND dp.action = 'PERMANENTLY DELETED' 
                AND dp.source = 'product'
            )
            GROUP BY si.product_id, p.name, p.brand, p.category
            ORDER BY qty DESC
            LIMIT %s
        """, (start_date, end_date, limit))
    elif period == 'weekly':
        cursor.execute("""
            SELECT 
                p.name, 
                COALESCE(p.brand, '') as brand, 
                COALESCE(p.category, '') as category, 
                COALESCE(SUM(si.quantity), 0) as qty
            FROM sales_items si
            JOIN sales s ON si.sale_id = s.id
            JOIN products p ON p.id = si.product_id
            WHERE s.date >= CURRENT_DATE - INTERVAL '6 days'
            AND s.reversed = 0
            AND NOT EXISTS (
                SELECT 1 FROM deleted_products dp 
                WHERE dp.product_id = p.id 
                AND dp.action = 'PERMANENTLY DELETED' 
                AND dp.source = 'product'
            )
            GROUP BY si.product_id, p.name, p.brand, p.category
            ORDER BY qty DESC
            LIMIT %s
        """, (limit,))
    elif period == 'monthly':
        cursor.execute("""
            SELECT 
                p.name, 
                COALESCE(p.brand, '') as brand, 
                COALESCE(p.category, '') as category, 
                COALESCE(SUM(si.quantity), 0) as qty
            FROM sales_items si
            JOIN sales s ON si.sale_id = s.id
            JOIN products p ON p.id = si.product_id
            WHERE EXTRACT(YEAR FROM s.date) = EXTRACT(YEAR FROM CURRENT_DATE)
            AND EXTRACT(MONTH FROM s.date) = EXTRACT(MONTH FROM CURRENT_DATE)
            AND s.reversed = 0
            AND NOT EXISTS (
                SELECT 1 FROM deleted_products dp 
                WHERE dp.product_id = p.id 
                AND dp.action = 'PERMANENTLY DELETED' 
                AND dp.source = 'product'
            )
            GROUP BY si.product_id, p.name, p.brand, p.category
            ORDER BY qty DESC
            LIMIT %s
        """, (limit,))
    elif period == 'yearly':
        cursor.execute("""
            SELECT 
                p.name, 
                COALESCE(p.brand, '') as brand, 
                COALESCE(p.category, '') as category, 
                COALESCE(SUM(si.quantity), 0) as qty
            FROM sales_items si
            JOIN sales s ON si.sale_id = s.id
            JOIN products p ON p.id = si.product_id
            WHERE EXTRACT(YEAR FROM s.date) = EXTRACT(YEAR FROM CURRENT_DATE)
            AND s.reversed = 0
            AND NOT EXISTS (
                SELECT 1 FROM deleted_products dp 
                WHERE dp.product_id = p.id 
                AND dp.action = 'PERMANENTLY DELETED' 
                AND dp.source = 'product'
            )
            GROUP BY si.product_id, p.name, p.brand, p.category
            ORDER BY qty DESC
            LIMIT %s
        """, (limit,))
    elif period == 'all':
        cursor.execute("""
            SELECT 
                p.name, 
                COALESCE(p.brand, '') as brand, 
                COALESCE(p.category, '') as category, 
                COALESCE(SUM(si.quantity), 0) as qty
            FROM sales_items si
            JOIN sales s ON si.sale_id = s.id
            JOIN products p ON p.id = si.product_id
            WHERE s.reversed = 0
            AND NOT EXISTS (
                SELECT 1 FROM deleted_products dp 
                WHERE dp.product_id = p.id 
                AND dp.action = 'PERMANENTLY DELETED' 
                AND dp.source = 'product'
            )
            GROUP BY si.product_id, p.name, p.brand, p.category
            ORDER BY qty DESC
            LIMIT %s
        """, (limit,))
    else:
        cursor.execute("""
            SELECT 
                p.name, 
                COALESCE(p.brand, '') as brand, 
                COALESCE(p.category, '') as category, 
                COALESCE(SUM(si.quantity), 0) as qty
            FROM sales_items si
            JOIN sales s ON si.sale_id = s.id
            JOIN products p ON p.id = si.product_id
            WHERE s.date::date = CURRENT_DATE
            AND s.reversed = 0
            AND NOT EXISTS (
                SELECT 1 FROM deleted_products dp 
                WHERE dp.product_id = p.id 
                AND dp.action = 'PERMANENTLY DELETED' 
                AND dp.source = 'product'
            )
            GROUP BY si.product_id, p.name, p.brand, p.category
            ORDER BY qty DESC
            LIMIT %s
        """, (limit,))
    
    rows = cursor.fetchall()
    conn.close()
    
    result = [{'name': r[0], 'brand': r[1] or '', 'category': r[2] or '', 'quantity': int(r[3])} for r in rows]
    return jsonify(result)


   # ===================== SETTINGS API =====================
from services.settings_service import (
    get_user_settings,
    update_user_settings,
    format_currency,
    format_date
)

@app.route('/settings', methods=['GET'])
@login_required
def settings_page():
    return render_template('settings.html')

@app.route('/api/settings', methods=['GET'])
@login_required
def api_get_settings():
    user_id = session.get('user_id')
    try:
        settings = get_user_settings(user_id)
        return jsonify({'success': True, 'settings': settings})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/settings', methods=['POST'])
@login_required
def api_update_settings():
    user_id = session.get('user_id')
    data = request.json
    
    try:
        success = update_user_settings(user_id, data)
        if success:
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'No settings to update'}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500 


@app.context_processor
def inject_settings():
    if 'user_id' in session:
        try:
            from services.settings_service import get_user_settings, format_currency, format_date
            settings = get_user_settings(session.get('user_id'))
            return {
                'settings': settings,
                'format_currency': lambda amount: format_currency(amount, session.get('user_id')),
                'format_date': lambda dt: format_date(dt, session.get('user_id'))
            }
        except:
            return {
                'settings': {'theme': 'light', 'currency_symbol': '₵', 'currency_code': 'GHS', 'language': 'en', 'date_format': 'DD/MM/YYYY'}
            }
    return {'settings': {'theme': 'light', 'currency_symbol': '₵', 'currency_code': 'GHS', 'language': 'en', 'date_format': 'DD/MM/YYYY'}}
# ===================== ARCHIVE API =====================
@app.route('/api/archive', methods=['GET'])
@login_required
def api_archive():
    status_filter = request.args.get('status', 'ALL')
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    search = request.args.get('search', '').strip()
    
    print(f"📁 Archive API called with status: {status_filter}, page: {page}, per_page: {per_page}")
    
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        conn.rollback()
        
        offset = (page - 1) * per_page
        search_pattern = f"%{search}%" if search else None
        
        # -------------------- GET ACTIVE PRODUCTS (only when status_filter is ALL or ACTIVE) --------------------
        active_items = []
        active_count = 0
        
        if status_filter == 'ALL' or status_filter == 'ACTIVE':
            active_query = """
                SELECT 
                    id,
                    name,
                    brand,
                    category,
                    cost_price,
                    selling_price,
                    stock,
                    discount
                FROM products
                WHERE NOT EXISTS (
                    SELECT 1 FROM deleted_products dp 
                    WHERE dp.product_id = products.id 
                    AND dp.action = 'PERMANENTLY DELETED' 
                    AND dp.source = 'product'
                )
            """
            active_params = []
            
            if search:
                active_query += " AND (name ILIKE %s OR brand ILIKE %s)"
                active_params.extend([search_pattern, search_pattern])
            
            # Get count
            count_query = f"SELECT COUNT(*) FROM ({active_query}) sub"
            cursor.execute(count_query, active_params)
            active_count = cursor.fetchone()[0]
            
            # Get paginated active products
            active_query += f" ORDER BY name ASC LIMIT {per_page} OFFSET {offset}"
            cursor.execute(active_query, active_params)
            active_rows = cursor.fetchall()
            
            print(f"✅ Found {active_count} active products, showing {len(active_rows)}")
            
            # Process active items
            for r in active_rows:
                product_id = r[0]
                
                # Get claims for this product (limited to 5 per product for performance)
                claims_data = []
                try:
                    cursor.execute("""
                        SELECT 
                            c.id,
                            c.batch_id,
                            c.issue_type,
                            c.description,
                            c.quantity,
                            c.status,
                            c.created_at
                        FROM claims c
                        WHERE c.product_id = %s AND c.status = 'active'
                        ORDER BY c.created_at DESC
                        LIMIT 5
                    """, (product_id,))
                    claims = cursor.fetchall()
                    for claim in claims:
                        claims_data.append({
                            "claim_id": claim[0],
                            "batch_id": claim[1],
                            "issue_type": claim[2],
                            "description": claim[3],
                            "quantity": claim[4],
                            "status": claim[5],
                            "created_at": claim[6]
                        })
                except Exception as claim_err:
                    print(f"⚠️ Could not fetch claims for product {product_id}: {str(claim_err)}")
                    conn.rollback()
                
                active_items.append({
                    'id': None,
                    'name': r[1],
                    'brand': r[2] or '',
                    'category': r[3] or '',
                    'cost': float(r[4] or 0),
                    'price': float(r[5] or 0),
                    'stock': int(r[6] or 0),
                    'discount': float(r[7] or 0),
                    'action': 'ACTIVE',
                    'date': '',
                    'source': 'active',
                    'is_permanent': False,
                    'batch_id': None,
                    'batch_quantity': None,
                    'batch_remaining': None,
                    'product_id': r[0],
                    'claims': claims_data,
                    'has_claims': len(claims_data) > 0,
                    'total_claimed': sum(c['quantity'] for c in claims_data) if claims_data else 0
                })
        
        # -------------------- GET DELETED RECORDS (only when status_filter is ALL or a specific status) --------------------
        deleted_items = []
        deleted_count = 0
        
        # Show deleted records for ALL status or specific statuses
        show_deleted = status_filter == 'ALL' or status_filter in ['DELETED', 'PRODUCT DELETED', 'BATCH DELETED', 'PERMANENTLY DELETED', 'UPDATED']
        
        if show_deleted:
            deleted_query = """
                SELECT 
                    id, name, brand, cost_price, selling_price, stock, 
                    category, discount, action, deleted_at, batch_id, 
                    batch_quantity, batch_remaining, product_id, source
                FROM deleted_products
                WHERE 1=1
            """
            deleted_params = []
            
            if search:
                deleted_query += " AND (name ILIKE %s OR brand ILIKE %s)"
                deleted_params.extend([search_pattern, search_pattern])
            
            # ✅ Fix: Only apply status filter if not 'ALL'
            if status_filter != 'ALL':
                deleted_query += " AND action = %s"
                deleted_params.append(status_filter)
            
            # Get count
            count_deleted_query = f"SELECT COUNT(*) FROM ({deleted_query}) sub"
            cursor.execute(count_deleted_query, deleted_params)
            deleted_count = cursor.fetchone()[0]
            
            # Get paginated deleted records
            deleted_query += " ORDER BY deleted_at DESC LIMIT %s OFFSET %s"
            deleted_params.extend([per_page, offset])
            cursor.execute(deleted_query, deleted_params)
            deleted_rows = cursor.fetchall()
            
            print(f"✅ Found {deleted_count} deleted records, showing {len(deleted_rows)}")
            
            # Process deleted items
            for r in deleted_rows:
                action = str(r[8]).upper() if len(r) > 8 and r[8] else 'UNKNOWN'
                is_permanent = action == 'PERMANENTLY DELETED'
                
                claims_data = []
                product_id = r[13] if len(r) > 13 else None
                if product_id:
                    try:
                        cursor.execute("""
                            SELECT 
                                c.id,
                                c.batch_id,
                                c.issue_type,
                                c.description,
                                c.quantity,
                                c.status,
                                c.created_at
                            FROM claims c
                            WHERE c.product_id = %s
                            ORDER BY c.created_at DESC
                            LIMIT 5
                        """, (product_id,))
                        claims = cursor.fetchall()
                        for claim in claims:
                            claims_data.append({
                                "claim_id": claim[0],
                                "batch_id": claim[1],
                                "issue_type": claim[2],
                                "description": claim[3],
                                "quantity": claim[4],
                                "status": claim[5],
                                "created_at": claim[6]
                            })
                    except Exception as claim_err:
                        print(f"⚠️ Could not fetch claims for deleted product {product_id}: {str(claim_err)}")
                        conn.rollback()
                
                deleted_items.append({
                    'id': r[0],
                    'name': r[1] if r[1] else '-',
                    'brand': r[2] if r[2] else '-',
                    'category': r[6] if r[6] else '-',
                    'cost': float(r[3] or 0),
                    'price': float(r[4] or 0),
                    'stock': int(r[5] or 0),
                    'discount': float(r[7] or 0),
                    'action': action,
                    'date': r[9].isoformat() if hasattr(r[9], 'isoformat') else str(r[9]) if r[9] else '',
                    'source': r[14] if len(r) > 14 and r[14] else 'product',
                    'is_permanent': is_permanent,
                    'batch_id': r[10] if len(r) > 10 else None,
                    'batch_quantity': r[11] if len(r) > 11 else None,
                    'batch_remaining': r[12] if len(r) > 12 else None,
                    'product_id': r[13] if len(r) > 13 else None,
                    'claims': claims_data,
                    'has_claims': len(claims_data) > 0,
                    'total_claimed': sum(c['quantity'] for c in claims_data) if claims_data else 0
                })
        
        # -------------------- COMBINE RESULTS --------------------
        # Calculate total
        total = active_count + deleted_count
        
        # If no results, return empty
        if total == 0:
            return jsonify({
                'items': [],
                'total': 0,
                'page': page,
                'per_page': per_page,
                'total_pages': 1,
                'has_more': False
            })
        
        # Combine and sort for current page
        combined = active_items + deleted_items
        
        # Sort by date (active items first, then deleted by date)
        def sort_key(item):
            if item['action'] == 'ACTIVE':
                return (datetime.max.replace(tzinfo=timezone.utc), item['name'])
            else:
                try:
                    date_obj = datetime.fromisoformat(str(item['date'])) if item['date'] else datetime.min.replace(tzinfo=timezone.utc)
                    if date_obj.tzinfo is None:
                        date_obj = date_obj.replace(tzinfo=timezone.utc)
                except:
                    date_obj = datetime.min.replace(tzinfo=timezone.utc)
                return (date_obj, item['name'])
        
        combined.sort(key=sort_key, reverse=True)
        
        # Calculate total pages
        total_pages = (total + per_page - 1) // per_page if total > 0 else 1
        has_more = offset + len(combined) < total
        
        print(f"📦 Returning {len(combined)} items of {total} total (page {page}/{total_pages})")
        
        return jsonify({
            'items': combined,
            'total': total,
            'page': page,
            'per_page': per_page,
            'total_pages': total_pages,
            'has_more': has_more
        })
        
    except Exception as e:
        print(f"❌ Error in archive API: {str(e)}")
        import traceback
        traceback.print_exc()
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()
@app.route('/api/archive/restore', methods=['POST'])
@login_required
def api_archive_restore():
    data = request.json
    archive_id = data.get('archive_id')
    if not archive_id:
        return jsonify({'success': False, 'error': 'Missing archive_id'}), 400
    try:
        restore_archive(archive_id)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/api/archive/batches', methods=['GET'])
@login_required
def api_archive_batches():
    name = request.args.get('name')
    brand = request.args.get('brand')
    if not name or not brand:
        return jsonify([])
    purchases = get_all_purchases()
    batches = [b for b in purchases if b['name'] == name and b['brand'] == brand]
    return jsonify(batches)

# ===================== IMPORT ENDPOINTS =====================
import openpyxl
from openpyxl import load_workbook
from werkzeug.utils import secure_filename
import psycopg2
from psycopg2 import sql

ALLOWED_EXTENSIONS = {'xlsx', 'xls'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/inventory/import', methods=['GET'])
@admin_required
def import_inventory_page():
    return render_template('import_inventory.html')

cancel_flags = {}

# ========== INVENTORY IMPORT ==========
def run_inventory_import(job_id, file_stream, target_category, mode='append'):
    conn = None
    try:
        wb = load_workbook(file_stream, data_only=True)
        ws = wb.active
    except Exception as e:
        update_job_progress(job_id, status='error', errors=[f"Unable to read workbook: {str(e)}"])
        return

    header_row_idx = None
    header_row = None
    for i, row in enumerate(ws.iter_rows(min_row=1, max_row=20, values_only=True)):
        if row and any(cell and isinstance(cell, str) and 
                       ('item' in cell.lower() or 'qty' in cell.lower() or 'rate' in cell.lower()) for cell in row):
            header_row_idx = i + 1
            header_row = row
            break

    if header_row_idx is None:
        update_job_progress(job_id, status='error', errors=['Could not find header row'])
        return

    header_map = {}
    for idx, cell in enumerate(header_row):
        if cell:
            cell_lower = str(cell).strip().lower()
            if cell_lower in ['item', 'product', 'name', 'details']:
                header_map['name'] = idx
            elif cell_lower in ['qty', 'quantity']:
                header_map['quantity'] = idx
            elif cell_lower in ['rate', 'cost', 'cost price', 'unit cost']:
                header_map['cost_price'] = idx
            elif cell_lower in ['amount', 'total cost']:
                header_map['total_cost'] = idx
            elif cell_lower in ['selling price', 'price', 'unit price']:
                header_map['selling_price'] = idx
            elif cell_lower in ['date', 'purchase date']:
                header_map['date'] = idx
            elif cell_lower in ['discount']:
                header_map['discount'] = idx

    required = ['name']
    missing = [f for f in required if f not in header_map]
    if missing:
        update_job_progress(job_id, status='error', errors=[f'Missing column: {", ".join(missing)} (only Name is required)'])
        return

    rows_to_process = []
    skipped_rows = []
    warning_rows = []

    for row_idx, row in enumerate(ws.iter_rows(min_row=header_row_idx + 1, values_only=True), start=header_row_idx + 1):
        if not any(row):
            continue
        try:
            name = str(row[header_map['name']]).strip() if row[header_map['name']] else ''
            if not name:
                skipped_rows.append({
                    'row': row_idx,
                    'data': {
                        'name': None,
                        'qty': row[header_map.get('quantity')] if header_map.get('quantity') is not None else None,
                        'rate': row[header_map.get('cost_price')] if header_map.get('cost_price') is not None else None,
                        'amount': row[header_map.get('total_cost')] if header_map.get('total_cost') is not None else None
                    },
                    'reason': 'Product name is empty (row skipped)'
                })
                continue

            try:
                quantity = float(row[header_map['quantity']]) if header_map.get('quantity') is not None and row[header_map['quantity']] is not None else 0
                if quantity < 0:
                    quantity = 0
                    warning_rows.append(f"Row {row_idx}: Quantity was negative, set to 0")
            except:
                quantity = 0
                warning_rows.append(f"Row {row_idx}: Invalid quantity, set to 0")

            cost_price = 0
            if 'cost_price' in header_map and row[header_map['cost_price']] is not None:
                try:
                    cost_price = float(row[header_map['cost_price']])
                    if cost_price < 0:
                        cost_price = 0
                        warning_rows.append(f"Row {row_idx}: Cost price was negative, set to 0")
                except:
                    cost_price = 0
                    warning_rows.append(f"Row {row_idx}: Invalid cost price, set to 0")

            total_cost = 0
            if 'total_cost' in header_map and row[header_map['total_cost']] is not None:
                try:
                    total_cost = float(row[header_map['total_cost']])
                    if total_cost < 0:
                        total_cost = 0
                        warning_rows.append(f"Row {row_idx}: Total cost was negative, set to 0")
                except:
                    total_cost = 0
                    warning_rows.append(f"Row {row_idx}: Invalid total cost, set to 0")
            else:
                total_cost = cost_price * quantity

            if total_cost == 0 and cost_price > 0 and quantity > 0:
                total_cost = cost_price * quantity

            cost_per_unit = total_cost / quantity if quantity > 0 else 0

            selling_price = 0
            if 'selling_price' in header_map and row[header_map['selling_price']] is not None:
                try:
                    selling_price = float(row[header_map['selling_price']])
                    if selling_price < 0:
                        selling_price = 0
                        warning_rows.append(f"Row {row_idx}: Selling price was negative, set to 0")
                except:
                    selling_price = 0
                    warning_rows.append(f"Row {row_idx}: Invalid selling price, set to 0")
            else:
                if quantity > 0 and total_cost > 0:
                    markup = 1.3
                    selling_price = cost_per_unit * markup
                else:
                    selling_price = 0

            try:
                discount = float(row[header_map.get('discount')]) if header_map.get('discount') is not None and row[header_map['discount']] is not None else 0
                if discount < 0:
                    discount = 0
            except:
                discount = 0

            purchase_date = None
            if 'date' in header_map and row[header_map['date']] is not None:
                date_value = row[header_map['date']]
                try:
                    purchase_date = parse_date_cell(date_value)
                    
                    if purchase_date is None:
                        purchase_date = datetime.now()
                        warning_rows.append(f"Row {row_idx}: Could not parse date '{date_value}', using current date")
                    elif purchase_date > datetime.now() + timedelta(days=1):
                        warning_rows.append(f"Row {row_idx}: Date '{purchase_date.strftime('%Y-%m-%d')}' is in the future (original: '{date_value}') - AUTO-CORRECTED to today")
                        purchase_date = datetime.now()
                    elif purchase_date.year < 2000:
                        warning_rows.append(f"Row {row_idx}: Date '{purchase_date.strftime('%Y-%m-%d')}' is before year 2000 (original: '{date_value}') - AUTO-CORRECTED to today")
                        purchase_date = datetime.now()
                except Exception as e:
                    purchase_date = datetime.now()
                    warning_rows.append(f"Row {row_idx}: Date parsing error: {str(e)}, using current date")
            else:
                purchase_date = datetime.now()

            category = target_category

            rows_to_process.append({
                'row_idx': row_idx,
                'name': name,
                'brand': '',
                'quantity': int(quantity),
                'cost_price': cost_per_unit,
                'selling_price': selling_price,
                'discount': discount,
                'category': category,
                'purchase_date': purchase_date,
                'total_cost': total_cost
            })   
        except Exception as e:
            skipped_rows.append({
                'row': row_idx,
                'data': {
                    'name': row[header_map.get('name')] if header_map.get('name') is not None else None,
                    'qty': row[header_map.get('quantity')] if header_map.get('quantity') is not None else None,
                    'rate': row[header_map.get('cost_price')] if header_map.get('cost_price') is not None else None,
                    'amount': row[header_map.get('total_cost')] if header_map.get('total_cost') is not None else None
                },
                'reason': f'Fatal error: {str(e)}'
            })

    total_rows = len(rows_to_process)
    update_job_progress(job_id, 
                        total=total_rows, 
                        processed=0, 
                        status='processing',
                        errors=warning_rows,
                        result={'imported': 0, 'skipped': skipped_rows, 'warnings': warning_rows, 'message': 'Parsing completed'})

    if total_rows == 0:
        update_job_progress(job_id, status='done',
                            result={'imported': 0, 'skipped': skipped_rows, 'warnings': warning_rows,
                                    'message': 'No valid rows to import (all missing product name or had fatal errors)'})
        return

    if not DATABASE_URL:
        update_job_progress(job_id, status='error', errors=['DATABASE_URL not configured'])
        return

    imported_count = 0
    overall_errors = warning_rows.copy()

    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        conn.autocommit = False
        cursor = conn.cursor()

        if mode == 'replace':
            categories_in_file = set(item['category'] for item in rows_to_process)
            for cat in categories_in_file:
                cursor.execute("""
                    DELETE FROM purchase_batches
                    WHERE product_id IN (SELECT id FROM products WHERE category = %s)
                """, (cat,))
                cursor.execute("DELETE FROM purchases WHERE category = %s", (cat,))
                cursor.execute("DELETE FROM products WHERE category = %s", (cat,))
            cursor.execute("SELECT setval('products_id_seq', (SELECT COALESCE(MAX(id), 1) FROM products))")
            cursor.execute("SELECT setval('purchases_id_seq', (SELECT COALESCE(MAX(id), 1) FROM purchases))")
            cursor.execute("SELECT setval('purchase_batches_id_seq', (SELECT COALESCE(MAX(id), 1) FROM purchase_batches))")

        for idx, item in enumerate(rows_to_process, start=1):
            if mode == 'replace':
                cursor.execute("""
                    INSERT INTO products (name, brand, cost_price, selling_price, stock, category)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (item['name'], item['brand'], item['cost_price'], item['selling_price'], 0, item['category']))
                product_id = cursor.fetchone()[0]
            else:
                cursor.execute("""
                    SELECT p.id, p.cost_price, p.selling_price, p.category
                    FROM products p
                    LEFT JOIN deleted_products dp ON dp.product_id = p.id AND dp.action = 'PERMANENTLY DELETED' AND dp.source = 'product'
                    WHERE p.name = %s AND p.brand = %s AND dp.id IS NULL
                """, (item['name'], item['brand']))
                product = cursor.fetchone()

                if product:
                    product_id, existing_cost, existing_selling, existing_category = product
                    if existing_category != item['category']:
                        cursor.execute("""
                            UPDATE products
                            SET category = %s
                            WHERE id = %s
                        """, (item['category'], product_id))
                    if (existing_cost != item['cost_price'] or existing_selling != item['selling_price']):
                        cursor.execute("""
                            UPDATE products
                            SET cost_price = %s, selling_price = %s
                            WHERE id = %s
                        """, (item['cost_price'], item['selling_price'], product_id))
                else:
                    cursor.execute("""
                        INSERT INTO products (name, brand, cost_price, selling_price, stock, category)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        RETURNING id
                    """, (item['name'], item['brand'], item['cost_price'], item['selling_price'], 0, item['category']))
                    product_id = cursor.fetchone()[0]

            total = item['total_cost'] - item['discount']
            cursor.execute("""
                INSERT INTO purchases
                (product_name, brand, category, quantity, cost_price, discount, total, selling_price, date)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (item['name'], item['brand'], item['category'], item['quantity'],
                  item['cost_price'], item['discount'], total, item['selling_price'], item['purchase_date']))

            cursor.execute("""
                INSERT INTO purchase_batches
                (product_id, quantity, remaining_quantity, cost_price, selling_price, discount, date, action)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (product_id, item['quantity'], item['quantity'], item['cost_price'],
                  item['selling_price'], item['discount'], item['purchase_date'], "added"))

            if item['quantity'] != 0:
                cursor.execute("""
                    UPDATE products SET stock = stock + %s WHERE id = %s
                """, (item['quantity'], product_id))

            imported_count += 1
            if imported_count % 100 == 0:
                update_job_progress(job_id, processed=imported_count)
                if cancel_flags.get(job_id):
                    raise Exception("CANCELLED_BY_USER")

        conn.commit()
        cancel_flags.pop(job_id, None)

        verification = None
        try:
            file_stream.seek(0)
            verification = verify_import(job_id, file_stream, target_category)
        except Exception as v_err:
            verification = {'error': str(v_err)}

        update_job_progress(job_id, status='done',
                            result={
                                'imported': imported_count,
                                'skipped': skipped_rows,
                                'warnings': warning_rows,
                                'message': f'Imported {imported_count} records, {len(skipped_rows)} rows skipped, {len(warning_rows)} warnings',
                                'verification': verification
                            })

    except Exception as e:
        if conn:
            conn.rollback()
        if str(e) == "CANCELLED_BY_USER":
            update_job_progress(job_id, status='cancelled',
                                result={'imported': 0, 'skipped': skipped_rows, 'warnings': warning_rows,
                                        'message': 'Import cancelled – no data was committed'})
        else:
            overall_errors.append(str(e))
            update_job_progress(job_id, status='error', errors=overall_errors)
    finally:
        if conn:
            conn.close()
        cancel_flags.pop(job_id, None)

# ========== SALES IMPORT ==========
def run_sales_import(job_id, file_stream, target_category, mode='append', user_id=None):
    try:
        wb = load_workbook(file_stream, data_only=True)
        ws = wb.active
    except Exception as e:
        update_job_progress(job_id, status='error', errors=[f"Unable to read workbook: {str(e)}"])
        return

    header_row_idx = None
    header_row = None
    for i, row in enumerate(ws.iter_rows(min_row=1, max_row=20, values_only=True)):
        if row and any(cell and isinstance(cell, str) and 
                       ('item' in cell.lower() or 'qty' in cell.lower() or 'rate' in cell.lower()) for cell in row):
            header_row_idx = i + 1
            header_row = row
            break

    if header_row_idx is None:
        update_job_progress(job_id, status='error', errors=['Could not find header row'])
        return

    header_map = {}
    for idx, cell in enumerate(header_row):
        if cell:
            cell_lower = str(cell).strip().lower()
            if cell_lower in ['item', 'product', 'name', 'details']:
                header_map['item'] = idx
            elif cell_lower in ['qty', 'quantity']:
                header_map['qty'] = idx
            elif cell_lower in ['rate', 'selling price', 'unit price']:
                header_map['rate'] = idx
            elif cell_lower in ['date', 'sale date']:
                header_map['date'] = idx
            elif cell_lower in ['discount']:
                header_map['discount'] = idx

    required = ['item', 'qty', 'rate']
    missing = [f for f in required if f not in header_map]
    if missing:
        update_job_progress(job_id, status='error', errors=[f'Missing columns: {", ".join(missing)}'])
        return

    rows_to_process = []
    skipped_rows = []
    error_rows = []

    for row_idx, row in enumerate(ws.iter_rows(min_row=header_row_idx + 1, values_only=True), start=header_row_idx + 1):
        if not any(row):
            continue
        try:
            item = str(row[header_map['item']]).strip() if row[header_map['item']] else ''
            if not item:
                skipped_rows.append({
                    'row': row_idx,
                    'data': {
                        'item': None,
                        'qty': row[header_map.get('qty')] if header_map.get('qty') is not None else None,
                        'rate': row[header_map.get('rate')] if header_map.get('rate') is not None else None
                    },
                    'reason': 'Product name is empty'
                })
                continue

            qty = float(row[header_map['qty']]) if row[header_map['qty']] is not None else 0
            if qty <= 0:
                skipped_rows.append({
                    'row': row_idx,
                    'data': {
                        'item': item,
                        'qty': qty,
                        'rate': row[header_map.get('rate')] if header_map.get('rate') is not None else None
                    },
                    'reason': f'Quantity must be positive (got {qty})'
                })
                continue

            rate = float(row[header_map['rate']]) if row[header_map['rate']] is not None else 0.0
            if rate < 0:
                skipped_rows.append({
                    'row': row_idx,
                    'data': {
                        'item': item,
                        'qty': qty,
                        'rate': rate
                    },
                    'reason': 'Selling price cannot be negative'
                })
                continue

            discount = float(row[header_map.get('discount')]) if header_map.get('discount') is not None and row[header_map['discount']] is not None else 0.0

            sale_date = None
            if 'date' in header_map and row[header_map['date']] is not None:
                parsed = parse_date_cell(row[header_map['date']])
                if parsed:
                    if parsed > datetime.now() + timedelta(days=1):
                        error_rows.append(f"Row {row_idx}: Date '{parsed.strftime('%Y-%m-%d')}' is in the future - AUTO-CORRECTED to today")
                        sale_date = datetime.now()
                    elif parsed.year < 2000:
                        error_rows.append(f"Row {row_idx}: Date '{parsed.strftime('%Y-%m-%d')}' is before year 2000 - AUTO-CORRECTED to today")
                        sale_date = datetime.now()
                    else:
                        sale_date = parsed
                else:
                    sale_date = datetime.now()
                    error_rows.append(f"Row {row_idx}: Could not parse date, using current date.")
            else:
                sale_date = datetime.now()

            rows_to_process.append({
                'row_idx': row_idx,
                'item': item,
                'qty': int(qty),
                'rate': rate,
                'discount': discount,
                'sale_date': sale_date
            })
        except Exception as e:
            skipped_rows.append({
                'row': row_idx,
                'data': {
                    'item': row[header_map.get('item')] if header_map.get('item') is not None else None,
                    'qty': row[header_map.get('qty')] if header_map.get('qty') is not None else None,
                    'rate': row[header_map.get('rate')] if header_map.get('rate') is not None else None
                },
                'reason': str(e)
            })

    total_rows = len(rows_to_process)
    update_job_progress(job_id, 
                        total=total_rows, 
                        processed=0, 
                        status='processing',
                        errors=error_rows,
                        result={'imported': 0, 'skipped': skipped_rows, 'message': 'Parsing completed'})

    if total_rows == 0:
        update_job_progress(job_id, status='done',
                            result={'imported': 0, 'skipped': skipped_rows, 'message': 'No valid rows to import'})
        return

    if not DATABASE_URL:
        update_job_progress(job_id, status='error', errors=['DATABASE_URL not configured'])
        return

    imported_count = 0
    overall_errors = error_rows.copy()

    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        conn.autocommit = False
        cursor = conn.cursor()

        if mode == 'replace' and target_category != 'All':
            cursor.execute("""
                DELETE FROM sales_items
                WHERE product_id IN (
                    SELECT id FROM products WHERE category = %s
                )
            """, (target_category,))
            cursor.execute("""
                DELETE FROM sales
                WHERE id NOT IN (SELECT sale_id FROM sales_items)
            """)

        for idx, entry in enumerate(rows_to_process, start=1):
            product = None
            product_id = None
            product_category = None
            product_name = None
            
            cursor.execute(
                "SELECT id, category, name FROM products WHERE name = %s",
                (entry['item'],)
            )
            product = cursor.fetchone()
            
            if not product:
                search_term = entry['item'].strip()
                cursor.execute("""
                    SELECT id, category, name 
                    FROM products 
                    WHERE LOWER(name) LIKE LOWER(%s)
                    AND NOT EXISTS (
                        SELECT 1 FROM deleted_products dp 
                        WHERE dp.product_id = products.id 
                        AND dp.action IN ('PERMANENTLY DELETED', 'PRODUCT DELETED')
                        AND dp.source = 'product'
                    )
                    LIMIT 1
                """, (f'%{search_term}%',))
                product = cursor.fetchone()
                
                if product:
                    overall_errors.append(f"Row {entry['row_idx']}: Product '{entry['item']}' matched to '{product[2]}' (partial match)")
            
            if not product:
                import re
                cleaned_name = re.sub(r'\s+\d+$', '', entry['item'])
                if cleaned_name != entry['item']:
                    cursor.execute("""
                        SELECT id, category, name 
                        FROM products 
                        WHERE LOWER(name) LIKE LOWER(%s)
                        AND NOT EXISTS (
                            SELECT 1 FROM deleted_products dp 
                            WHERE dp.product_id = products.id 
                            AND dp.action IN ('PERMANENTLY DELETED', 'PRODUCT DELETED')
                            AND dp.source = 'product'
                        )
                        LIMIT 1
                    """, (f'%{cleaned_name}%',))
                    product = cursor.fetchone()
                    if product:
                        overall_errors.append(f"Row {entry['row_idx']}: Product '{entry['item']}' matched to '{product[2]}' (cleaned match)")

            if not product:
                overall_errors.append(f"Row {entry['row_idx']}: Product '{entry['item']}' not found in database")
                continue
                
            product_id, product_category, product_name = product[0], product[1], product[2]

            if target_category == 'Screen' and product_category == 'Accessory':
                overall_errors.append(f"Row {entry['row_idx']}: Product '{entry['item']}' is in 'Accessory' category - skipping (only Screens/Others allowed)")
                continue
            
            if target_category == 'Accessory' and product_category != 'Accessory':
                overall_errors.append(f"Row {entry['row_idx']}: Product '{entry['item']}' category '{product_category}' != 'Accessory' - skipping")
                continue

            subtotal = entry['qty'] * entry['rate']
            total = subtotal - entry['discount']

            cursor.execute("""
                INSERT INTO sales (date, subtotal, discount, total, profit, reversed, payment_method, user_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (entry['sale_date'], subtotal, entry['discount'], total, 0, 0, 'cash', user_id))
            sale_id = cursor.fetchone()[0]

            cursor.execute("""
                SELECT id, cost_price, selling_price, remaining_quantity
                FROM purchase_batches
                WHERE product_id = %s AND remaining_quantity > 0
                ORDER BY date ASC
                LIMIT 1
            """, (product_id,))
            batch_info = cursor.fetchone()
            
            if batch_info:
                batch_id, cost_price, selling_price, remaining_qty = batch_info
                
                if remaining_qty >= entry['qty']:
                    selling_price = entry['rate']
                    item_profit = (selling_price - cost_price) * entry['qty'] - entry['discount']
                    
                    cursor.execute("""
                        INSERT INTO sales_items
                        (sale_id, product_id, batch_id, quantity, selling_price, cost_price, profit)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """, (sale_id, product_id, batch_id, entry['qty'], selling_price, cost_price, item_profit))
                    
                    cursor.execute("""
                        UPDATE purchase_batches
                        SET remaining_quantity = remaining_quantity - %s
                        WHERE id = %s
                    """, (entry['qty'], batch_id))
                    
                    cursor.execute("""
                        UPDATE products
                        SET stock = stock - %s
                        WHERE id = %s
                    """, (entry['qty'], product_id))
                    
                    cursor.execute("""
                        UPDATE sales
                        SET profit = %s
                        WHERE id = %s
                    """, (item_profit, sale_id))
                    
                else:
                    available = remaining_qty
                    selling_price = entry['rate']
                    cost_price = float(cost_price) if cost_price else 0
                    item_profit = (selling_price - cost_price) * available - (entry['discount'] * (available / entry['qty']))
                    
                    cursor.execute("""
                        INSERT INTO sales_items
                        (sale_id, product_id, batch_id, quantity, selling_price, cost_price, profit)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """, (sale_id, product_id, batch_id, available, selling_price, cost_price, item_profit))
                    
                    cursor.execute("""
                        UPDATE purchase_batches
                        SET remaining_quantity = 0
                        WHERE id = %s
                    """, (batch_id,))
                    
                    cursor.execute("""
                        UPDATE products
                        SET stock = stock - %s
                        WHERE id = %s
                    """, (available, product_id))
                    
                    cursor.execute("""
                        UPDATE sales
                        SET profit = %s
                        WHERE id = %s
                    """, (item_profit, sale_id))
                    
                    overall_errors.append(f"Row {entry['row_idx']}: Product '{entry['item']}' had only {available} stock (needed {entry['qty']}) - sold {available}, rest recorded without stock")
            else:
                cost_price = 0
                selling_price = entry['rate']
                item_profit = (selling_price - cost_price) * entry['qty'] - entry['discount']
                
                cursor.execute("""
                    INSERT INTO sales_items
                    (sale_id, product_id, batch_id, quantity, selling_price, cost_price, profit)
                    VALUES (%s, %s, NULL, %s, %s, %s, %s)
                """, (sale_id, product_id, entry['qty'], selling_price, cost_price, item_profit))
                
                cursor.execute("""
                    UPDATE sales
                    SET profit = %s
                    WHERE id = %s
                """, (item_profit, sale_id))
                
                overall_errors.append(f"Row {entry['row_idx']}: Product '{entry['item']}' has 0 stock - recorded sale without stock deduction")

            imported_count += 1
            if imported_count % 100 == 0:
                update_job_progress(job_id, processed=imported_count)
                if cancel_flags.get(job_id):
                    raise Exception("CANCELLED_BY_USER")

        conn.commit()
        cancel_flags.pop(job_id, None)
        update_job_progress(job_id, status='done',
                            result={
                                'imported': imported_count,
                                'skipped': skipped_rows,
                                'message': f'Imported {imported_count} sales records, {len(skipped_rows)} rows skipped'
                            })

    except Exception as e:
        if conn:
            conn.rollback()
        if str(e) == "CANCELLED_BY_USER":
            update_job_progress(job_id, status='cancelled',
                                result={'imported': 0, 'skipped': skipped_rows,
                                        'message': 'Import cancelled – no data was committed'})
        else:
            overall_errors.append(str(e))
            update_job_progress(job_id, status='error', errors=overall_errors)
    finally:
        if conn:
            conn.close()
        cancel_flags.pop(job_id, None)

# ----- INVENTORY IMPORT ENDPOINT -----
@app.route('/api/inventory/import', methods=['POST'])
@admin_required
def api_import_inventory():
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'No file uploaded'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'error': 'No file selected'}), 400

    if not allowed_file(file.filename):
        return jsonify({'success': False, 'error': 'File type not allowed'}), 400

    target_category = request.form.get('target_category', 'Accessory')
    if target_category not in ['Accessory', 'Screen']:
        return jsonify({'success': False, 'error': 'Invalid target category'}), 400

    mode = request.form.get('mode', 'append')
    if mode not in ['append', 'replace']:
        return jsonify({'success': False, 'error': 'Invalid mode'}), 400

    file_content = file.read()
    file_stream = io.BytesIO(file_content)

    job_id = str(uuid.uuid4())

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO import_jobs (job_id, status, total, processed, errors, result)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (job_id, 'pending', 0, 0, '[]', None))
    conn.commit()
    conn.close()

    thread = threading.Thread(
        target=run_inventory_import,
        args=(job_id, file_stream, target_category, mode)
    )
    thread.daemon = True
    thread.start()

    return jsonify({'success': True, 'job_id': job_id})

# ----- SALES IMPORT ENDPOINT -----
@app.route('/api/sales/import', methods=['POST'])
@admin_required
def api_import_sales():
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'No file uploaded'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'error': 'No file selected'}), 400

    if not allowed_file(file.filename):
        return jsonify({'success': False, 'error': 'File type not allowed'}), 400

    target_category = request.form.get('target_category', 'All')
    if target_category not in ['Accessory', 'Screen', 'All']:
        return jsonify({'success': False, 'error': 'Invalid target category'}), 400

    mode = request.form.get('mode', 'append')
    if mode not in ['append', 'replace']:
        return jsonify({'success': False, 'error': 'Invalid mode'}), 400

    file_content = file.read()
    file_stream = io.BytesIO(file_content)

    job_id = str(uuid.uuid4())

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO import_jobs (job_id, status, total, processed, errors, result)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (job_id, 'pending', 0, 0, '[]', None))
    conn.commit()
    conn.close()

    user_id = session.get('user_id')

    thread = threading.Thread(
        target=run_sales_import,
        args=(job_id, file_stream, target_category, mode, user_id)
    )
    thread.daemon = True
    thread.start()

    return jsonify({'success': True, 'job_id': job_id})

# ===================== PROGRESS POLLING ENDPOINT =====================
@app.route('/api/import/progress/<job_id>', methods=['GET'])
@login_required
def api_import_progress(job_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT status, total, processed, errors, result, updated_at
        FROM import_jobs
        WHERE job_id = %s
    """, (job_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return jsonify({'success': False, 'error': 'Job not found'}), 404

    result_data = row[4]
    if result_data and isinstance(result_data, str):
        try:
            result_data = json.loads(result_data)
        except:
            pass

    return jsonify({
        'success': True,
        'status': row[0],
        'total': row[1],
        'processed': row[2],
        'errors': row[3],
        'result': result_data,
        'updated_at': row[5].isoformat() if row[5] else None
    })

# ===================== IMPORT CANCELLATION ENDPOINT =====================
@app.route('/api/import/cancel/<job_id>', methods=['POST'])
@login_required
def api_import_cancel(job_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM import_jobs WHERE job_id = %s", (job_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return jsonify({'success': False, 'error': 'Job not found'}), 404
    status = row[0]
    if status in ('done', 'error', 'cancelled'):
        return jsonify({'success': False, 'error': f'Job already {status}'}), 400

    cancel_flags[job_id] = True
    update_job_progress(job_id, status='cancelling')
    return jsonify({'success': True, 'message': 'Cancellation requested'})

# ===================== FAILED REPORT PDF =====================
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
    elements = []

    elements.append(Paragraph("📋 Import Failed Items Report", styles["Title"]))
    elements.append(Spacer(1, 6))
    elements.append(Paragraph(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", styles["Normal"]))
    elements.append(Spacer(1, 12))

    table_data = [["Row", "Name/Item", "Qty", "Rate", "Reason"]]
    for s in skipped:
        table_data.append([
            str(s.get('row', '')),
            s.get('data', {}).get('name') or s.get('data', {}).get('item') or '-',
            str(s.get('data', {}).get('qty', '')),
            str(s.get('data', {}).get('rate', '')),
            s.get('reason', '')
        ])

    table = Table(table_data, repeatRows=1, colWidths=[50, 150, 50, 50, 200])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#1E3A5F")),
        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('GRID', (0,0), (-1,-1), 0.5, colors.black),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('FONTSIZE', (0,0), (-1,-1), 8),
    ]))
    elements.append(table)

    doc.build(elements)
    buffer.seek(0)
    return send_file(buffer, as_attachment=True,
                     download_name=f"failed_import_{job_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf",
                     mimetype='application/pdf')

# ===================== IMPORT VERIFICATION =====================
def verify_import(job_id, file_stream, target_category):
    if not DATABASE_URL:
        return {'error': 'DATABASE_URL not configured'}

    try:
        wb = load_workbook(file_stream, data_only=True)
        ws = wb.active
    except Exception as e:
        return {'error': f"Unable to read workbook: {str(e)}"}

    header_row_idx = None
    header_row = None
    for i, row in enumerate(ws.iter_rows(min_row=1, max_row=20, values_only=True)):
        if row and any(cell and isinstance(cell, str) and
                       ('item' in cell.lower() or 'qty' in cell.lower() or 'rate' in cell.lower()) for cell in row):
            header_row_idx = i + 1
            header_row = row
            break

    if header_row_idx is None:
        return {'error': 'Could not find header row'}

    header_map = {}
    for idx, cell in enumerate(header_row):
        if cell:
            cell_lower = str(cell).strip().lower()
            if cell_lower in ['item', 'product', 'name', 'details']:
                header_map['name'] = idx
            elif cell_lower in ['qty', 'quantity']:
                header_map['quantity'] = idx
            elif cell_lower in ['rate', 'cost', 'cost price', 'unit cost']:
                header_map['cost_price'] = idx
            elif cell_lower in ['amount', 'total cost']:
                header_map['total_cost'] = idx
            elif cell_lower in ['selling price', 'price', 'unit price']:
                header_map['selling_price'] = idx
            elif cell_lower in ['discount']:
                header_map['discount'] = idx

    required = ['name']
    missing = [f for f in required if f not in header_map]
    if missing:
        return {'error': f'Missing column: {", ".join(missing)} (only Name is required)'}

    mismatches = []
    skipped = []
    matched = 0
    total_rows = 0

    conn = None
    cursor = None
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

            cursor.execute("""
                SELECT p.id, p.stock, p.cost_price, p.selling_price, p.discount
                FROM products p
                WHERE p.name = %s AND p.category = %s
                AND NOT EXISTS (
                    SELECT 1 FROM deleted_products dp
                    WHERE dp.product_id = p.id
                    AND dp.action IN ('PERMANENTLY DELETED', 'PRODUCT DELETED')
                    AND dp.source = 'product'
                )
            """, (name, target_category))
            product = cursor.fetchone()

            if not product:
                mismatches.append({
                    'row': row_idx,
                    'reason': 'Product not found in database',
                    'expected': {'name': name, 'qty': quantity, 'cost': cost_price, 'selling': selling_price, 'discount': discount}
                })
                continue

            product_id, db_stock, db_cost, db_selling, db_discount = product

            def compare_float(a, b, tolerance=0.001):
                return abs(a - b) <= tolerance

            issues = []
            if not compare_float(float(db_stock), float(quantity)):
                issues.append(f"stock: expected {quantity}, got {db_stock}")
            if not compare_float(float(db_cost), float(cost_price)):
                issues.append(f"cost: expected {cost_price:.2f}, got {db_cost:.2f}")
            if not compare_float(float(db_selling), float(selling_price)):
                issues.append(f"selling price: expected {selling_price:.2f}, got {db_selling:.2f}")
            if not compare_float(float(db_discount), float(discount)):
                issues.append(f"discount: expected {discount:.2f}, got {db_discount:.2f}")

            if issues:
                mismatches.append({
                    'row': row_idx,
                    'reason': '; '.join(issues),
                    'expected': {'name': name, 'qty': quantity, 'cost': cost_price, 'selling': selling_price, 'discount': discount},
                    'actual': {'stock': db_stock, 'cost': db_cost, 'selling': db_selling, 'discount': db_discount}
                })
            else:
                matched += 1

        return {
            'total_rows': total_rows,
            'matched': matched,
            'mismatches': mismatches,
            'skipped': skipped,
            'message': f"Verified {total_rows} rows: {matched} matched, {len(mismatches)} mismatches, {len(skipped)} skipped"
        }
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
    if file.filename == '':
        return jsonify({'success': False, 'error': 'No file selected'}), 400

    if not allowed_file(file.filename):
        return jsonify({'success': False, 'error': 'File type not allowed'}), 400

    target_category = request.form.get('target_category', 'Accessory')
    if target_category not in ['Accessory', 'Screen']:
        return jsonify({'success': False, 'error': 'Invalid target category'}), 400

    file_content = file.read()
    file_stream = io.BytesIO(file_content)

    result = verify_import(job_id, file_stream, target_category)
    if 'error' in result:
        return jsonify({'success': False, 'error': result['error']}), 400

    return jsonify({'success': True, 'result': result})

# ===================== INITIALIZE CLAIMS TABLE =====================
def init_claims_table():
    """Create the claims table if it doesn't exist"""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS claims (
                id SERIAL PRIMARY KEY,
                product_id INTEGER NOT NULL,
                batch_id INTEGER NOT NULL,
                product_name VARCHAR(255) NOT NULL,
                brand VARCHAR(100) DEFAULT '',
                category VARCHAR(100) DEFAULT '',
                issue_type VARCHAR(100) NOT NULL,
                description TEXT DEFAULT '',
                quantity INTEGER NOT NULL DEFAULT 0,
                remaining_good INTEGER DEFAULT 0,
                status VARCHAR(20) DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_claims_product_id ON claims(product_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_claims_batch_id ON claims(batch_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_claims_status ON claims(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_claims_created_at ON claims(created_at)")
        
        conn.commit()
        print("✅ Claims table initialized successfully!")
    except Exception as e:
        print(f"❌ Error initializing claims table: {str(e)}")
        conn.rollback()
    finally:
        conn.close()

# ===================== CALL THE FUNCTION =====================
init_claims_table()

# ===================== PING =====================
@app.route('/api/ping', methods=['GET'])
def ping():
    return jsonify({"status": "ok", "message": "Deployed version is current"})


# ---------------------- RUN THE APP ----------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)