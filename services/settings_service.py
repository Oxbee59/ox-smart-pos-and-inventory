from database.db import get_connection
from datetime import datetime
import json

def get_user_settings(user_id):
    """Get user settings from database"""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT theme, currency_symbol, currency_code, language, date_format, timezone
        FROM user_settings
        WHERE user_id = %s
    """, (user_id,))
    
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return {
            'theme': row[0] or 'light',
            'currency_symbol': row[1] or '₵',
            'currency_code': row[2] or 'GHS',
            'language': row[3] or 'en',
            'date_format': row[4] or 'DD/MM/YYYY',
            'timezone': row[5] or 'UTC'
        }
    
    # Create default settings if not exists
    create_default_settings(user_id)
    return get_user_settings(user_id)

def create_default_settings(user_id):
    """Create default settings for a new user"""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO user_settings (user_id, theme, currency_symbol, currency_code, language, date_format, timezone)
        VALUES (%s, 'light', '₵', 'GHS', 'en', 'DD/MM/YYYY', 'UTC')
    """, (user_id,))
    
    conn.commit()
    conn.close()

def update_user_settings(user_id, settings):
    """Update user settings"""
    conn = get_connection()
    cursor = conn.cursor()
    
    allowed_fields = ['theme', 'currency_symbol', 'currency_code', 'language', 'date_format', 'timezone']
    set_clauses = []
    params = []
    
    for key in allowed_fields:
        if key in settings:
            set_clauses.append(f"{key} = %s")
            params.append(settings[key])
    
    if not set_clauses:
        return False
    
    params.append(user_id)
    query = f"""
        UPDATE user_settings
        SET {', '.join(set_clauses)}, updated_at = CURRENT_TIMESTAMP
        WHERE user_id = %s
    """
    
    cursor.execute(query, params)
    conn.commit()
    conn.close()
    return True

def get_currency_symbol(user_id):
    """Get user's currency symbol"""
    settings = get_user_settings(user_id)
    return settings.get('currency_symbol', '₵')

def get_currency_code(user_id):
    """Get user's currency code"""
    settings = get_user_settings(user_id)
    return settings.get('currency_code', 'GHS')

def get_theme(user_id):
    """Get user's theme preference"""
    settings = get_user_settings(user_id)
    return settings.get('theme', 'light')

def get_date_format(user_id):
    """Get user's date format"""
    settings = get_user_settings(user_id)
    return settings.get('date_format', 'DD/MM/YYYY')

def get_language(user_id):
    """Get user's language preference"""
    settings = get_user_settings(user_id)
    return settings.get('language', 'en')

def format_currency(amount, user_id, symbol=True):
    """Format currency based on user settings"""
    settings = get_user_settings(user_id)
    symbol_str = settings.get('currency_symbol', '₵')
    if symbol:
        return f"{symbol_str}{amount:,.2f}"
    return f"{amount:,.2f}"

def format_date(date_obj, user_id):
    """Format date based on user settings"""
    if not date_obj:
        return ''
    settings = get_user_settings(user_id)
    date_format = settings.get('date_format', 'DD/MM/YYYY')
    
    # Convert date format string to Python format
    format_map = {
        'DD/MM/YYYY': '%d/%m/%Y',
        'MM/DD/YYYY': '%m/%d/%Y',
        'YYYY-MM-DD': '%Y-%m-%d',
        'DD-MM-YYYY': '%d-%m-%Y',
        'MM-DD-YYYY': '%m-%d-%Y'
    }
    py_format = format_map.get(date_format, '%d/%m/%Y')
    
    return date_obj.strftime(py_format)