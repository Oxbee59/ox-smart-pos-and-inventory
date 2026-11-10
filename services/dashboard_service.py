from database.db import get_connection
from datetime import date, datetime

# ----------------- TOTAL SALES (with optional date or datetime range) -----------------
def get_today_sales(selected_date=None, start_datetime=None, end_datetime=None):
    conn = get_connection()
    cursor = conn.cursor()
    
    if start_datetime and end_datetime:
        cursor.execute("""
            SELECT COALESCE(SUM(s.total), 0)
            FROM sales s
            WHERE s.date BETWEEN ? AND ?
            AND s.reversed = 0
            AND EXISTS (
                SELECT 1 FROM sales_items si
                JOIN products p ON p.id = si.product_id
                WHERE si.sale_id = s.id
                AND NOT EXISTS (
                    SELECT 1 FROM deleted_products dp 
                    WHERE dp.product_id = p.id 
                    AND dp.action IN ('PERMANENTLY DELETED', 'PRODUCT DELETED')
                    AND dp.source = 'product'
                )
            )
        """, (start_datetime, end_datetime))
    elif selected_date:
        dt = selected_date.isoformat() if hasattr(selected_date, 'isoformat') else selected_date
        cursor.execute("""
            SELECT COALESCE(SUM(s.total), 0)
            FROM sales s
            WHERE DATE(s.date) = ?
            AND s.reversed = 0
            AND EXISTS (
                SELECT 1 FROM sales_items si
                JOIN products p ON p.id = si.product_id
                WHERE si.sale_id = s.id
                AND NOT EXISTS (
                    SELECT 1 FROM deleted_products dp 
                    WHERE dp.product_id = p.id 
                    AND dp.action IN ('PERMANENTLY DELETED', 'PRODUCT DELETED')
                    AND dp.source = 'product'
                )
            )
        """, (dt,))
    else:
        cursor.execute("""
            SELECT COALESCE(SUM(s.total), 0)
            FROM sales s
            WHERE DATE(s.date) = DATE('now', 'localtime')
            AND s.reversed = 0
            AND EXISTS (
                SELECT 1 FROM sales_items si
                JOIN products p ON p.id = si.product_id
                WHERE si.sale_id = s.id
                AND NOT EXISTS (
                    SELECT 1 FROM deleted_products dp 
                    WHERE dp.product_id = p.id 
                    AND dp.action IN ('PERMANENTLY DELETED', 'PRODUCT DELETED')
                    AND dp.source = 'product'
                )
            )
        """)
    
    total = cursor.fetchone()[0]
    conn.close()
    return total


# ----------------- TOTAL PROFIT (NET) (with optional date or datetime range) -----------------
def get_today_profit(selected_date=None, start_datetime=None, end_datetime=None):
    conn = get_connection()
    cursor = conn.cursor()
    
    if start_datetime and end_datetime:
        cursor.execute("""
            SELECT COALESCE(SUM(s.profit), 0)
            FROM sales s
            WHERE s.date BETWEEN ? AND ?
            AND s.reversed = 0
            AND EXISTS (
                SELECT 1 FROM sales_items si
                JOIN products p ON p.id = si.product_id
                WHERE si.sale_id = s.id
                AND NOT EXISTS (
                    SELECT 1 FROM deleted_products dp 
                    WHERE dp.product_id = p.id 
                    AND dp.action IN ('PERMANENTLY DELETED', 'PRODUCT DELETED')
                    AND dp.source = 'product'
                )
            )
        """, (start_datetime, end_datetime))
    elif selected_date:
        dt = selected_date.isoformat() if hasattr(selected_date, 'isoformat') else selected_date
        cursor.execute("""
            SELECT COALESCE(SUM(s.profit), 0)
            FROM sales s
            WHERE DATE(s.date) = ?
            AND s.reversed = 0
            AND EXISTS (
                SELECT 1 FROM sales_items si
                JOIN products p ON p.id = si.product_id
                WHERE si.sale_id = s.id
                AND NOT EXISTS (
                    SELECT 1 FROM deleted_products dp 
                    WHERE dp.product_id = p.id 
                    AND dp.action IN ('PERMANENTLY DELETED', 'PRODUCT DELETED')
                    AND dp.source = 'product'
                )
            )
        """, (dt,))
    else:
        cursor.execute("""
            SELECT COALESCE(SUM(s.profit), 0)
            FROM sales s
            WHERE DATE(s.date) = DATE('now', 'localtime')
            AND s.reversed = 0
            AND EXISTS (
                SELECT 1 FROM sales_items si
                JOIN products p ON p.id = si.product_id
                WHERE si.sale_id = s.id
                AND NOT EXISTS (
                    SELECT 1 FROM deleted_products dp 
                    WHERE dp.product_id = p.id 
                    AND dp.action IN ('PERMANENTLY DELETED', 'PRODUCT DELETED')
                    AND dp.source = 'product'
                )
            )
        """)
    
    profit = cursor.fetchone()[0]
    conn.close()
    return profit


# ----------------- TOTAL PRODUCTS (only those with at least one batch) -----------------
def get_total_products():
    """
    Count products that have at least one purchase batch.
    This matches the behaviour of the product pages (Accessories and Screens).
    Excludes permanently deleted products.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT COUNT(DISTINCT p.id) as total_products
        FROM products p
        WHERE EXISTS (
            SELECT 1 FROM purchase_batches pb
            WHERE pb.product_id = p.id
        )
        AND NOT EXISTS (
            SELECT 1 FROM deleted_products dp 
            WHERE dp.product_id = p.id 
            AND dp.action IN ('PERMANENTLY DELETED', 'PRODUCT DELETED')
            AND dp.source = 'product'
        )
    """)
    total = cursor.fetchone()[0] or 0
    conn.close()
    return total


# ----------------- TOTAL BATCHES (all active batches, regardless of stock) -----------------
def get_total_batches():
    """
    Count all purchase batches that belong to non‑deleted products.
    This includes both Accessories and Screens.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT COUNT(*)
        FROM purchase_batches pb
        JOIN products p ON p.id = pb.product_id
        WHERE NOT EXISTS (
            SELECT 1 FROM deleted_products dp 
            WHERE dp.product_id = p.id 
            AND dp.action IN ('PERMANENTLY DELETED', 'PRODUCT DELETED')
            AND dp.source = 'product'
        )
    """)
    count = cursor.fetchone()[0] or 0
    conn.close()
    return count


# ----------------- LOW STOCK PRODUCTS (including zero stock, products without batches, AND ALL batch IDs) -----------------
def get_low_stock_products(threshold=10):
    """
    Return products with stock <= threshold (including 0).
    Includes ALL batch IDs (active & depleted) for traceability.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT p.name, p.brand, p.category, p.stock,
               COALESCE(
                   (SELECT GROUP_CONCAT(id, ',')
                    FROM (SELECT id FROM purchase_batches WHERE product_id = p.id ORDER BY date ASC)),
                   ''
               ) as all_batch_ids,
               COALESCE(
                   (SELECT GROUP_CONCAT(id, ',')
                    FROM (SELECT id FROM purchase_batches WHERE product_id = p.id AND remaining_quantity > 0 ORDER BY date ASC)),
                   ''
               ) as active_batch_ids
        FROM products p
        WHERE p.stock <= ?
        AND p.stock >= 0
        AND NOT EXISTS (
            SELECT 1 FROM deleted_products dp 
            WHERE dp.product_id = p.id 
            AND dp.action IN ('PERMANENTLY DELETED', 'PRODUCT DELETED')
            AND dp.source = 'product'
        )
        ORDER BY p.stock ASC
        LIMIT 1000
    """, (threshold,))
    products = cursor.fetchall()
    conn.close()
    
    result = []
    for p in products:
        name, brand, category, stock, all_batch_str, active_batch_str = p
        
        # Convert comma-separated strings back to lists of integers
        all_batch_ids = [int(x) for x in str(all_batch_str).split(',') if x]
        active_batch_ids = [int(x) for x in str(active_batch_str).split(',') if x]
        
        # Use active batches if available, otherwise show all batches
        batch_ids = active_batch_ids if active_batch_ids else all_batch_ids
        result.append((name, brand, category, stock, batch_ids))
    
    return result


# ----------------- WEEKLY SALES (NET) -----------------
def get_weekly_sales():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT DATE(s.date), COALESCE(SUM(s.total), 0)
        FROM sales s
        WHERE DATE(s.date) >= DATE('now', '-6 days', 'localtime')
        AND s.reversed = 0
        AND EXISTS (
            SELECT 1 FROM sales_items si
            JOIN products p ON p.id = si.product_id
            WHERE si.sale_id = s.id
            AND NOT EXISTS (
                SELECT 1 FROM deleted_products dp 
                WHERE dp.product_id = p.id 
                AND dp.action IN ('PERMANENTLY DELETED', 'PRODUCT DELETED')
                AND dp.source = 'product'
            )
        )
        GROUP BY DATE(s.date)
        ORDER BY DATE(s.date)
    """)
    data = cursor.fetchall()
    conn.close()
    return data


# ----------------- TOP PRODUCTS (with optional date or datetime range) -----------------
def get_top_products(selected_date=None, limit=5, start_datetime=None, end_datetime=None):
    conn = get_connection()
    cursor = conn.cursor()
    params = []
    query = """
        SELECT p.name, p.brand, p.category, COALESCE(SUM(si.quantity), 0) as qty
        FROM sales_items si
        JOIN sales s ON si.sale_id = s.id
        JOIN products p ON si.product_id = p.id
        WHERE s.reversed = 0
        AND NOT EXISTS (
            SELECT 1 FROM deleted_products dp 
            WHERE dp.product_id = p.id 
            AND dp.action IN ('PERMANENTLY DELETED', 'PRODUCT DELETED')
            AND dp.source = 'product'
        )
    """
    
    if start_datetime and end_datetime:
        query += " AND s.date BETWEEN ? AND ?"
        params.extend([start_datetime, end_datetime])
    elif selected_date:
        dt = selected_date.isoformat() if hasattr(selected_date, 'isoformat') else selected_date
        query += " AND DATE(s.date) = ?"
        params.append(dt)
    
    query += " GROUP BY si.product_id, p.name, p.brand, p.category ORDER BY qty DESC LIMIT ?"
    params.append(limit)
    
    cursor.execute(query, params)
    products = cursor.fetchall()
    conn.close()
    return products


# ----------------- SALES HISTORY (NET) (with optional date or datetime range) -----------------
def get_sales_history(selected_date=None, start_datetime=None, end_datetime=None):
    conn = get_connection()
    cursor = conn.cursor()
    params = []
    
    query = """
        SELECT DATE(s.date),
               COALESCE(SUM(s.total), 0) AS total_sales,
               COALESCE(SUM(s.profit), 0) AS total_profit,
               COALESCE(SUM(s.discount), 0) AS total_discount
        FROM sales s
        WHERE s.reversed = 0
        AND EXISTS (
            SELECT 1 FROM sales_items si
            JOIN products p ON p.id = si.product_id
            WHERE si.sale_id = s.id
            AND NOT EXISTS (
                SELECT 1 FROM deleted_products dp 
                WHERE dp.product_id = p.id 
                AND dp.action IN ('PERMANENTLY DELETED', 'PRODUCT DELETED')
                AND dp.source = 'product'
            )
        )
    """
    
    if start_datetime and end_datetime:
        query += " AND s.date BETWEEN ? AND ?"
        params.extend([start_datetime, end_datetime])
    elif selected_date:
        dt = selected_date.isoformat() if hasattr(selected_date, 'isoformat') else selected_date
        query += " AND DATE(s.date) = ?"
        params.append(dt)
    
    query += " GROUP BY DATE(s.date) ORDER BY DATE(s.date) DESC"
    
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    return [(r[0], r[1], r[2], r[3]) for r in rows]


# ----------------- DASHBOARD SUMMARY (Combines all data for the dashboard) -----------------
def get_dashboard_summary(start_datetime=None, end_datetime=None):
    """
    Get all dashboard summary data in one call.
    Returns sales, profit, total_products, total_batches, low_stock_count, and low_stock_products.
    """
    conn = get_connection()
    cursor = conn.cursor()
    
    # Get sales and profit
    if start_datetime and end_datetime:
        cursor.execute("""
            SELECT 
                COALESCE(SUM(s.total), 0) as total_sales,
                COALESCE(SUM(s.profit), 0) as total_profit
            FROM sales s
            WHERE s.date BETWEEN ? AND ?
            AND s.reversed = 0
            AND EXISTS (
                SELECT 1 FROM sales_items si
                JOIN products p ON p.id = si.product_id
                WHERE si.sale_id = s.id
                AND NOT EXISTS (
                    SELECT 1 FROM deleted_products dp 
                    WHERE dp.product_id = p.id 
                    AND dp.action IN ('PERMANENTLY DELETED', 'PRODUCT DELETED')
                    AND dp.source = 'product'
                )
            )
        """, (start_datetime, end_datetime))
    else:
        cursor.execute("""
            SELECT 
                COALESCE(SUM(s.total), 0) as total_sales,
                COALESCE(SUM(s.profit), 0) as total_profit
            FROM sales s
            WHERE DATE(s.date) = DATE('now', 'localtime')
            AND s.reversed = 0
            AND EXISTS (
                SELECT 1 FROM sales_items si
                JOIN products p ON p.id = si.product_id
                WHERE si.sale_id = s.id
                AND NOT EXISTS (
                    SELECT 1 FROM deleted_products dp 
                    WHERE dp.product_id = p.id 
                    AND dp.action IN ('PERMANENTLY DELETED', 'PRODUCT DELETED')
                    AND dp.source = 'product'
                )
            )
        """)
    
    sales_data = cursor.fetchone()
    total_sales = sales_data[0] or 0
    total_profit = sales_data[1] or 0
    
    # Get total products
    total_products = get_total_products()
    
    # Get total batches
    cursor.execute("""
        SELECT COUNT(*)
        FROM purchase_batches pb
        JOIN products p ON p.id = pb.product_id
        WHERE NOT EXISTS (
            SELECT 1 FROM deleted_products dp 
            WHERE dp.product_id = p.id 
            AND dp.action IN ('PERMANENTLY DELETED', 'PRODUCT DELETED')
            AND dp.source = 'product'
        )
    """)
    total_batches = cursor.fetchone()[0] or 0
    
    # Get low stock products
    cursor.execute("""
        SELECT p.name, p.brand, p.category, p.stock,
               COALESCE(
                   (SELECT GROUP_CONCAT(id, ',')
                    FROM (SELECT id FROM purchase_batches WHERE product_id = p.id ORDER BY date ASC)),
                   ''
               ) as all_batch_ids,
               COALESCE(
                   (SELECT GROUP_CONCAT(id, ',')
                    FROM (SELECT id FROM purchase_batches WHERE product_id = p.id AND remaining_quantity > 0 ORDER BY date ASC)),
                   ''
               ) as active_batch_ids
        FROM products p
        WHERE p.stock <= 10
        AND p.stock >= 0
        AND NOT EXISTS (
            SELECT 1 FROM deleted_products dp 
            WHERE dp.product_id = p.id 
            AND dp.action IN ('PERMANENTLY DELETED', 'PRODUCT DELETED')
            AND dp.source = 'product'
        )
        ORDER BY p.stock ASC
        LIMIT 1000
    """)
    low_stock_rows = cursor.fetchall()
    
    conn.close()
    
    processed_rows = []
    for row in low_stock_rows:
        name, brand, category, stock, all_batch_str, active_batch_str = row
        all_batch_ids = [int(x) for x in str(all_batch_str).split(',') if x]
        active_batch_ids = [int(x) for x in str(active_batch_str).split(',') if x]
        batch_ids = active_batch_ids if active_batch_ids else all_batch_ids
        processed_rows.append((name, brand, category, stock, batch_ids))
    
    return {
        'sales': total_sales,
        'profit': total_profit,
        'total_products': total_products,
        'total_batches': total_batches,
        'low_stock_count': len(processed_rows),
        'low_stock_products': processed_rows
    }