from database.db import get_connection, return_connection
import hashlib
from datetime import datetime

# ---------------- HASH PASSWORD ----------------
def hash_password(password: str) -> str:
    """Return a SHA-256 hash of the password."""
    return hashlib.sha256(password.encode()).hexdigest()


# ---------------- LOG USER ACTION ----------------
def log_user_action(user_id, username, action, ip_address=None, user_agent=None):
    """Log user actions for audit"""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO user_logs (user_id, username, action, ip_address, user_agent, timestamp)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (user_id, username, action, ip_address, user_agent, datetime.now()))
        conn.commit()
    except Exception as e:
        print(f"❌ Error logging user action: {e}")
    finally:
        return_connection(conn)


# ---------------- CREATE USER ----------------
def create_user(username: str, password: str, role: str = "user") -> bool:
    """Create a new user in PostgreSQL. Returns True if successful, False if username exists."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO users (username, password, role) VALUES (%s, %s, %s)",
            (username, hash_password(password), role)
        )
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        print(f"Create user error: {e}")
        return False
    finally:
        return_connection(conn)


# ---------------- LOGIN USER ----------------
def login_user(username: str, password: str, ip_address=None, user_agent=None):
    """Return user dict if credentials are correct, else None."""
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        # Case-insensitive username lookup
        cursor.execute(
            "SELECT id, username, role, password FROM users WHERE LOWER(username) = LOWER(%s)",
            (username,)
        )
        row = cursor.fetchone()
        
        if row and row[3] == hash_password(password):
            user = {"id": row[0], "username": row[1], "role": row[2]}
            # Log successful login
            log_user_action(row[0], row[1], 'login', ip_address, user_agent)
            return user
        
        # Log failed login attempt (if user exists)
        if row:
            log_user_action(row[0], row[1], 'login_failed', ip_address, user_agent)
        
        return None
    finally:
        return_connection(conn)


# ---------------- LOGOUT USER ----------------
def logout_user(user_id, username, ip_address=None, user_agent=None):
    """Log a user logout event"""
    log_user_action(user_id, username, 'logout', ip_address, user_agent)


# ---------------- GET USER LOGS (ADMIN ONLY) ----------------
def get_user_logs(user_id=None, limit=100, offset=0, action_filter=None):
    """Get user logs for admin viewing"""
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        query = """
            SELECT 
                ul.id,
                ul.user_id,
                ul.username,
                ul.action,
                ul.ip_address,
                ul.user_agent,
                ul.timestamp
            FROM user_logs ul
        """
        params = []
        conditions = []
        
        if user_id:
            conditions.append("ul.user_id = %s")
            params.append(user_id)
        
        if action_filter:
            conditions.append("ul.action = %s")
            params.append(action_filter)
        
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        
        query += " ORDER BY ul.timestamp DESC LIMIT %s OFFSET %s"
        params.extend([limit, offset])
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        
        return [
            {
                "id": r[0],
                "user_id": r[1],
                "username": r[2],
                "action": r[3],
                "ip_address": r[4],
                "user_agent": r[5],
                "timestamp": r[6].isoformat() if r[6] else None
            }
            for r in rows
        ]
    finally:
        return_connection(conn)


# ---------------- CHECK ADMIN EXISTS ----------------
def admin_exists() -> bool:
    """Return True if an admin user exists."""
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("SELECT 1 FROM users WHERE role = 'admin' LIMIT 1")
        return cursor.fetchone() is not None
    finally:
        return_connection(conn)


# ---------------- COUNT ADMINS ----------------
def count_admins() -> int:
    """Return the total number of admin users in the system."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'")
        count = cursor.fetchone()[0]
        return count
    finally:
        return_connection(conn)


# ---------------- GET ALL USERS ----------------
def get_all_users():
    """Get all users (for admin management)."""
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("SELECT id, username, role, created_at FROM users ORDER BY created_at DESC")
        rows = cursor.fetchall()
        return [{"id": r[0], "username": r[1], "role": r[2], "created_at": r[3]} for r in rows]
    finally:
        return_connection(conn)


# ---------------- GET USER BY ID ----------------
def get_user_by_id(user_id: int):
    """Get a single user by ID."""
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("SELECT id, username, role, created_at FROM users WHERE id = %s", (user_id,))
        row = cursor.fetchone()
        if row:
            return {"id": row[0], "username": row[1], "role": row[2], "created_at": row[3]}
        return None
    finally:
        return_connection(conn)


# ---------------- UPDATE USER ROLE ----------------
def update_user_role(user_id: int, new_role: str) -> bool:
    """Update a user's role."""
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("UPDATE users SET role = %s WHERE id = %s", (new_role, user_id))
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        print(f"Error updating user role: {e}")
        return False
    finally:
        return_connection(conn)


# ---------------- UPDATE USER PASSWORD ----------------
def update_user_password(user_id: int, new_password: str) -> bool:
    """Update a user's password (admin action)."""
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute(
            "UPDATE users SET password = %s WHERE id = %s",
            (hash_password(new_password), user_id)
        )
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        print(f"Error updating user password: {e}")
        return False
    finally:
        return_connection(conn)


# ---------------- DELETE USER (IMPROVED WITH DEPENDENCY CHECK) ----------------
def delete_user(user_id: int):
    """
    Delete a user. Returns dict with success and error message.
    Checks for dependent records (sales, purchases) to give a clear reason if deletion fails.
    """
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        # Check for dependent records (sales, purchases)
        cursor.execute("""
            SELECT 
                (SELECT COUNT(*) FROM sales WHERE user_id = %s) as sales_count,
                (SELECT COUNT(*) FROM purchases WHERE user_id = %s) as purchases_count
        """, (user_id, user_id))
        counts = cursor.fetchone()
        sales_count, purchases_count = counts[0], counts[1]
        
        if sales_count > 0 or purchases_count > 0:
            error_msg = "Cannot delete user. They have "
            if sales_count > 0:
                error_msg += f"{sales_count} sale(s) "
            if purchases_count > 0:
                error_msg += f"{purchases_count} purchase(s) "
            error_msg += "associated. Please reassign or delete those records first."
            return {"success": False, "error": error_msg}
        
        # Also check for any other references (e.g., user_logs – we can delete them)
        # Delete user logs first
        cursor.execute("DELETE FROM user_logs WHERE user_id = %s", (user_id,))
        
        # Now delete the user
        cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
        conn.commit()
        return {"success": True}
    except Exception as e:
        conn.rollback()
        return {"success": False, "error": f"Database error: {str(e)}"}
    finally:
        return_connection(conn)


# ---------------- CHANGE PASSWORD (Self) ----------------
def change_password(user_id: int, old_password: str, new_password: str) -> bool:
    """Change user's own password."""
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        # Verify old password
        cursor.execute("SELECT password FROM users WHERE id = %s", (user_id,))
        row = cursor.fetchone()
        
        if not row or row[0] != hash_password(old_password):
            return False
        
        # Update to new password
        cursor.execute("UPDATE users SET password = %s WHERE id = %s", (hash_password(new_password), user_id))
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        print(f"Error changing password: {e}")
        return False
    finally:
        return_connection(conn)


# ---------------- IS PROTECTED USER ----------------
def is_protected_user(user_id: int) -> bool:
    """Check if user is the protected admin (oxbee)"""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT username FROM users WHERE id = %s", (user_id,))
        row = cursor.fetchone()
        if row and row[0].lower() == 'oxbee':
            return True
        return False
    finally:
        return_connection(conn)