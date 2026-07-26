import os
from database.db import get_connection, get_db_connection, get_param_style
from datetime import datetime
from functools import wraps
import time

# ---------------------------
# DECORATOR FOR TIMEOUT HANDLING
# ---------------------------
def handle_timeout(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        try:
            result = func(*args, **kwargs)
            elapsed = time.time() - start_time
            print(f"{func.__name__} completed in {elapsed:.2f} seconds")
            return result
        except Exception as e:
            elapsed = time.time() - start_time
            print(f"{func.__name__} failed after {elapsed:.2f} seconds: {str(e)}")
            raise
    return wrapper

# ---------------------------
# GET PRODUCTS FOR SALE
# ---------------------------
def get_products_for_sale():
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT p.id, p.name, p.brand, p.selling_price, p.stock, p.category
            FROM products p
            WHERE p.stock > 0
            AND NOT EXISTS (
                SELECT 1 FROM deleted_products dp 
                WHERE dp.product_id = p.id 
                AND dp.action = 'PERMANENTLY DELETED' 
                AND dp.source = 'product'
            )
            ORDER BY p.name ASC
        """)
        rows = cursor.fetchall()
        return [
            {
                "id": r[0],
                "name": r[1],
                "brand": r[2] or "",
                "selling_price": float(r[3] or 0),
                "stock": int(r[4] or 0),
                "category": r[5] or "",
            }
            for r in rows
        ]
    finally:
        from database.db import return_connection
        return_connection(conn)

# ---------------------------
# GET BATCHES FOR PRODUCT (WITH CLAIM INFO - FIXED)
# ---------------------------
def get_batches_for_product(product_id, limit=100):
    """
    Get batches for a product with claim info.
    ✅ FIXED: Shows status based on active claims only.
    - If batch has ACTIVE claims → shows "⚠️ Claimed"
    - If batch has only RESOLVED claims → shows "✅ Good"
    - If batch has no claims → shows "✅ Good"
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT 
                pb.id, 
                pb.quantity, 
                pb.remaining_quantity, 
                pb.cost_price, 
                pb.selling_price, 
                pb.discount, 
                pb.date,
                pb.is_faulty,
                pb.claimed_quantity,
                COALESCE((
                    SELECT SUM(quantity) 
                    FROM claims 
                    WHERE batch_id = pb.id AND status = 'active'
                ), 0) as active_claims_qty,
                COALESCE((
                    SELECT SUM(quantity) 
                    FROM claims 
                    WHERE batch_id = pb.id AND status = 'resolved'
                ), 0) as resolved_claims_qty
            FROM purchase_batches pb
            WHERE pb.product_id = %s AND pb.remaining_quantity > 0
            ORDER BY pb.date ASC
            LIMIT %s
        """, (product_id, limit))
        rows = cursor.fetchall()
        
        result = []
        for r in rows:
            batch_id = r[0]
            quantity = int(r[1] or 0)
            remaining_quantity = int(r[2] or 0)
            cost_price = float(r[3] or 0)
            selling_price = float(r[4] or 0)
            discount = float(r[5] or 0)
            date = r[6]
            is_faulty = r[7] or False
            claimed_quantity = r[8] or 0
            active_claims = r[9] if len(r) > 9 else 0
            resolved_claims = r[10] if len(r) > 10 else 0
            
            # ✅ Determine if batch should show as faulty
            # Only show as faulty if there are ACTIVE claims
            has_active_claims = active_claims > 0
            
            result.append({
                "batch_id": batch_id,
                "quantity": quantity,
                "remaining_quantity": remaining_quantity,
                "cost_price": cost_price,
                "selling_price": selling_price,
                "discount": discount,
                "date": date,
                "is_faulty": has_active_claims,  # ✅ Only active claims make it faulty
                "claimed_quantity": claimed_quantity,
                "active_claims": active_claims,
                "resolved_claims": resolved_claims,
                "has_claims": active_claims > 0 or resolved_claims > 0,
                "has_active_claims": active_claims > 0,
                "has_resolved_claims": resolved_claims > 0,
                # ✅ Status text for display
                "status": "⚠️ Claimed" if active_claims > 0 else "✅ Good"
            })
        
        return result
    finally:
        from database.db import return_connection
        return_connection(conn)

# ---------------------------
# GET SINGLE BATCH BY ID (WITH CLAIM INFO - FIXED)
# ---------------------------
def get_batch_by_id(batch_id):
    """Get a single batch by its ID with claim info"""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT 
                pb.id, 
                pb.product_id, 
                pb.remaining_quantity, 
                pb.cost_price, 
                pb.selling_price,
                pb.is_faulty,
                pb.claimed_quantity,
                COALESCE((
                    SELECT SUM(quantity) 
                    FROM claims 
                    WHERE batch_id = pb.id AND status = 'active'
                ), 0) as active_claims_qty
            FROM purchase_batches pb
            WHERE pb.id = %s
        """, (batch_id,))
        row = cursor.fetchone()
        if row:
            return {
                "batch_id": row[0],
                "product_id": row[1],
                "remaining_quantity": int(row[2] or 0),
                "cost_price": float(row[3] or 0),
                "selling_price": float(row[4] or 0),
                "is_faulty": row[5] or False,
                "claimed_quantity": row[6] or 0,
                "active_claims": row[7] if len(row) > 7 else 0
            }
        return None
    finally:
        from database.db import return_connection
        return_connection(conn)

# ---------------------------
# GET MULTIPLE BATCHES IN SINGLE QUERY (WITH CLAIM INFO - FIXED)
# ---------------------------
def get_batches_by_ids(batch_ids):
    """Fetch multiple batches in one query with claim info"""
    if not batch_ids:
        return {}
    
    conn = get_connection()
    cursor = conn.cursor()
    try:
        result = {}
        for batch_id in batch_ids:
            cursor.execute("""
                SELECT 
                    pb.id, 
                    pb.product_id, 
                    pb.remaining_quantity, 
                    pb.cost_price, 
                    pb.selling_price,
                    pb.is_faulty,
                    pb.claimed_quantity,
                    COALESCE((
                        SELECT SUM(quantity) 
                        FROM claims 
                        WHERE batch_id = pb.id AND status = 'active'
                    ), 0) as active_claims_qty
                FROM purchase_batches pb
                WHERE pb.id = %s
            """, (batch_id,))
            row = cursor.fetchone()
            if row:
                result[row[0]] = {
                    "batch_id": row[0],
                    "product_id": row[1],
                    "remaining_quantity": int(row[2] or 0),
                    "cost_price": float(row[3] or 0),
                    "selling_price": float(row[4] or 0),
                    "is_faulty": row[5] or False,
                    "claimed_quantity": row[6] or 0,
                    "active_claims": row[7] if len(row) > 7 else 0
                }
            else:
                print(f"⚠️ Batch {batch_id} not found in database")
        return result
    finally:
        from database.db import return_connection
        return_connection(conn)

# ---------------------------
# UPDATE PRODUCT STOCK
# ---------------------------
def update_product_stock(cursor, product_id):
    cursor.execute("""
        SELECT COALESCE(SUM(remaining_quantity), 0)
        FROM purchase_batches
        WHERE product_id = %s
    """, (product_id,))
    new_stock = cursor.fetchone()[0]
    cursor.execute("""
        UPDATE products SET stock = %s WHERE id = %s
    """, (int(new_stock), product_id))

# ---------------------------
# BULK UPDATE PRODUCT STOCKS
# ---------------------------
def bulk_update_product_stocks(cursor, product_ids):
    """Update multiple product stocks in bulk"""
    for product_id in product_ids:
        update_product_stock(cursor, product_id)

# ---------------------------
# MAIN SALE CREATION FUNCTION
# ---------------------------
@handle_timeout
def create_multi_sale(cart_items, sale_datetime=None, selected_batches=None, payment_method='cash', cheque_number=None, user_id=None):
    """
    Create a sale with multiple items.
    user_id: optional ID of the user making the sale (for tracking)
    """
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        # ✅ FIXED: Better date handling with multiple formats
        sale_date_str = None
        
        if sale_datetime:
            # Try multiple date formats
            date_formats = [
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M",
                "%m/%d/%Y %H:%M:%S",
                "%m/%d/%Y %H:%M",
                "%d/%m/%Y %H:%M:%S",
                "%d/%m/%Y %H:%M",
            ]
            
            parsed = False
            for fmt in date_formats:
                try:
                    sale_date_obj = datetime.strptime(sale_datetime, fmt)
                    sale_date_str = sale_date_obj.strftime("%Y-%m-%d %H:%M:%S")
                    parsed = True
                    print(f"✅ Date parsed successfully: {sale_datetime} -> {sale_date_str}")
                    break
                except ValueError:
                    continue
            
            if not parsed:
                # If all parsing fails, try to extract date and time separately
                try:
                    # Try to parse as ISO format
                    sale_date_obj = datetime.fromisoformat(sale_datetime.replace('Z', '+00:00'))
                    sale_date_str = sale_date_obj.strftime("%Y-%m-%d %H:%M:%S")
                    parsed = True
                    print(f"✅ ISO date parsed: {sale_datetime} -> {sale_date_str}")
                except:
                    pass
            
            if not parsed:
                # Last resort: use the string as-is if it looks like a date
                if len(sale_datetime) >= 10:
                    # Try to extract date part
                    date_part = sale_datetime[:10]
                    try:
                        sale_date_obj = datetime.strptime(date_part, "%Y-%m-%d")
                        # Use current time but with the selected date
                        now = datetime.now()
                        sale_date_str = sale_date_obj.replace(
                            hour=now.hour,
                            minute=now.minute,
                            second=now.second
                        ).strftime("%Y-%m-%d %H:%M:%S")
                        print(f"✅ Extracted date: {date_part} -> {sale_date_str}")
                    except:
                        sale_date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        print(f"❌ Using current date as fallback: {sale_date_str}")
                else:
                    sale_date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    print(f"❌ Using current date as fallback: {sale_date_str}")
        else:
            sale_date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"✅ No date provided, using current date: {sale_date_str}")
        
        # Ensure we have a valid date string
        if not sale_date_str:
            sale_date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        print(f"📅 Final sale date: {sale_date_str}")
        
        # 2. Insert sale record with payment fields AND user_id
        cursor.execute("""
            INSERT INTO sales (subtotal, discount, total, profit, date, payment_method, cheque_number, user_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (0, 0, 0, 0, sale_date_str, payment_method, cheque_number, user_id))
        sale_id = cursor.fetchone()[0]
        
        # Prepare data structures
        all_sales_items = []
        batch_updates = []
        total_subtotal = 0
        total_discount = 0
        total_gross_profit = 0
        receipt_data = []
        affected_products = set()
        
        # 3. Pre-fetch all needed batches if using selected_batches
        batches_cache = {}
        if selected_batches:
            batch_ids = [sb["batch_id"] for sb in selected_batches]
            batches_cache = get_batches_by_ids(batch_ids)
            print(f"🔍 Selected batches: {selected_batches}")
            print(f"🔍 Batch IDs: {batch_ids}")
            print(f"🔍 Batches cache keys: {list(batches_cache.keys())}")
        
        # 4. Process each cart item
        for item in cart_items:
            product = item["product"]
            product_id = product["id"]
            quantity = int(item["qty"])
            discount = float(item.get("discount", 0))
            selling_price = float(product.get("selling_price", 0))
            
            subtotal = 0
            profit = 0
            batches_used = []
            has_faulty_batch = False
            
            if selected_batches:
                # FIX: Filter selected batches for this specific product
                product_batches = []
                for sb in selected_batches:
                    # Only process batches that belong to this product
                    if sb.get("product_id") == product_id:
                        batch = batches_cache.get(sb["batch_id"])
                        if not batch:
                            print(f"⚠️ Batch {sb['batch_id']} not found in cache for product {product['name']}")
                            continue
                        
                        # Validate stock
                        if batch["remaining_quantity"] < sb["qty"]:
                            raise ValueError(f"Batch {sb['batch_id']} has only {batch['remaining_quantity']} left, requested {sb['qty']} for {product['name']}")
                        
                        # ✅ Check if batch has ACTIVE claims (not resolved)
                        if batch.get("active_claims", 0) > 0 or batch.get("is_faulty", False):
                            has_faulty_batch = True
                        
                        product_batches.append({
                            "batch_id": sb["batch_id"],
                            "qty": sb["qty"],
                            "cost_price": batch["cost_price"],
                            "selling_price": batch.get("selling_price", selling_price),
                            "is_faulty": batch.get("active_claims", 0) > 0,
                            "claimed_quantity": batch.get("claimed_quantity", 0),
                            "active_claims": batch.get("active_claims", 0)
                        })
                
                # Verify quantity
                total_assigned = sum(b["qty"] for b in product_batches)
                if total_assigned != quantity:
                    raise ValueError(f"Quantity mismatch for {product['name']}. Assigned: {total_assigned}, Requested: {quantity}")
                
                for b in product_batches:
                    batch_total = b["selling_price"] * b["qty"]
                    batch_profit = (b["selling_price"] - b["cost_price"]) * b["qty"]
                    
                    subtotal += batch_total
                    profit += batch_profit
                    
                    # Queue updates
                    batch_updates.append((b["qty"], b["batch_id"]))
                    all_sales_items.append((
                        sale_id, product_id, b["batch_id"], b["qty"],
                        b["cost_price"], b["selling_price"], batch_profit
                    ))
                    batches_used.append({
                        "batch_id": b["batch_id"], 
                        "qty": b["qty"],
                        "is_faulty": b.get("is_faulty", False)
                    })
                    affected_products.add(product_id)
            
            else:
                # FIFO: Get batches with claim info
                cursor.execute("""
                    SELECT 
                        id, 
                        remaining_quantity, 
                        cost_price, 
                        selling_price,
                        is_faulty,
                        claimed_quantity
                    FROM purchase_batches
                    WHERE product_id = %s AND remaining_quantity > 0
                    ORDER BY date ASC
                    LIMIT 100
                """, (product_id,))
                batches = cursor.fetchall()
                
                remaining_qty = quantity
                
                for batch in batches:
                    if remaining_qty <= 0:
                        break
                    
                    batch_id = batch[0]
                    batch_remaining = int(batch[1] or 0)
                    batch_cost = float(batch[2] or 0)
                    batch_selling = float(batch[3] or selling_price)
                    is_faulty = batch[4] or False
                    claimed_qty = batch[5] or 0
                    
                    if is_faulty or claimed_qty > 0:
                        has_faulty_batch = True
                    
                    sell_qty = min(batch_remaining, remaining_qty)
                    batch_total = batch_selling * sell_qty
                    batch_profit = (batch_selling - batch_cost) * sell_qty
                    
                    subtotal += batch_total
                    profit += batch_profit
                    remaining_qty -= sell_qty
                    
                    batch_updates.append((sell_qty, batch_id))
                    all_sales_items.append((
                        sale_id, product_id, batch_id, sell_qty,
                        batch_cost, batch_selling, batch_profit
                    ))
                    batches_used.append({
                        "batch_id": batch_id, 
                        "qty": sell_qty,
                        "is_faulty": is_faulty
                    })
                    affected_products.add(product_id)
                
                if remaining_qty > 0:
                    raise ValueError(f"Insufficient stock for {product['name']}. Needed {quantity}, available {quantity - remaining_qty}")
            
            final_total = max(subtotal - discount, 0)
            total_subtotal += subtotal
            total_discount += discount
            total_gross_profit += profit
            
            receipt_data.append({
                "name": product["name"],
                "qty": quantity,
                "batches": batches_used,
                "subtotal": subtotal,
                "discount": discount,
                "total": final_total,
                "has_faulty": has_faulty_batch
            })
        
        # 5. Execute batch updates
        if batch_updates:
            cursor.executemany("""
                UPDATE purchase_batches 
                SET remaining_quantity = remaining_quantity - %s 
                WHERE id = %s
            """, batch_updates)
        
        # 6. Insert all sales items
        if all_sales_items:
            cursor.executemany("""
                INSERT INTO sales_items 
                (sale_id, product_id, batch_id, quantity, cost_price, selling_price, profit)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, all_sales_items)
        
        # 7. Update product stocks
        for product_id in affected_products:
            update_product_stock(cursor, product_id)
        
        # 8. Update sale totals
        net_profit = total_gross_profit - total_discount
        grand_total = max(total_subtotal - total_discount, 0)
        
        cursor.execute("""
            UPDATE sales 
            SET subtotal = %s, discount = %s, total = %s, profit = %s 
            WHERE id = %s
        """, (total_subtotal, total_discount, grand_total, net_profit, sale_id))
        
        # IMPORTANT: Commit the transaction BEFORE returning connection
        conn.commit()
        
        # Prepare the result with payment info and user_id
        result = {
            "sale_id": sale_id,
            "items": receipt_data,
            "subtotal": total_subtotal,
            "discount": total_discount,
            "total": grand_total,
            "profit": net_profit,
            "date": sale_date_str,
            "payment_method": payment_method,
            "cheque_number": cheque_number,
            "user_id": user_id,
            "has_faulty_items": any(item.get("has_faulty", False) for item in receipt_data)
        }
        
        # Return connection to pool AFTER preparing result
        from database.db import return_connection
        return_connection(conn)
        
        return result
        
    except Exception as e:
        conn.rollback()
        from database.db import return_connection
        return_connection(conn)
        print(f"Sale creation failed: {str(e)}")
        raise e

# ---------------------------
# GET SALE DETAILS (WITH PAYMENT INFO AND USERNAME)
# ---------------------------
def get_sale_details(sale_id):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        # Get sale header with payment info and username
        cursor.execute("""
            SELECT s.id, s.subtotal, s.discount, s.total, s.profit, s.date, 
                   s.payment_method, s.cheque_number, u.username
            FROM sales s
            LEFT JOIN users u ON s.user_id = u.id
            WHERE s.id = %s
        """, (sale_id,))
        sale = cursor.fetchone()
        
        if not sale:
            return None
        
        # Get sale items with product names and batch info
        cursor.execute("""
            SELECT 
                si.product_id,
                p.name as product_name,
                si.quantity,
                si.selling_price,
                si.cost_price,
                si.profit,
                si.batch_id,
                pb.is_faulty,
                pb.claimed_quantity
            FROM sales_items si
            JOIN products p ON si.product_id = p.id
            LEFT JOIN purchase_batches pb ON si.batch_id = pb.id
            WHERE si.sale_id = %s
        """, (sale_id,))
        
        items = cursor.fetchall()
        
        result = {
            "id": sale[0],
            "subtotal": float(sale[1] or 0),
            "discount": float(sale[2] or 0),
            "total": float(sale[3] or 0),
            "profit": float(sale[4] or 0),
            "date": sale[5],
            "payment_method": sale[6] if len(sale) > 6 else 'unknown',
            "cheque_number": sale[7] if len(sale) > 7 else None,
            "username": sale[8] if len(sale) > 8 else 'Unknown',
            "items": [
                {
                    "product_id": item[0],
                    "product_name": item[1],
                    "quantity": int(item[2] or 0),
                    "selling_price": float(item[3] or 0),
                    "cost_price": float(item[4] or 0),
                    "profit": float(item[5] or 0),
                    "batch_id": item[6],
                    "is_faulty": item[7] if len(item) > 7 else False,
                    "claimed_quantity": item[8] if len(item) > 8 else 0
                }
                for item in items
            ]
        }
        
        from database.db import return_connection
        return_connection(conn)
        
        return result
        
    except Exception as e:
        from database.db import return_connection
        return_connection(conn)
        print(f"Get sale details failed: {str(e)}")
        raise e

# ---------------------------
# GET TODAY'S SALES (WITH PAYMENT INFO AND USERNAME)
# ---------------------------
def get_todays_sales():
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT s.id, s.subtotal, s.discount, s.total, s.profit, s.date, 
                   s.payment_method, s.cheque_number, u.username
            FROM sales s
            LEFT JOIN users u ON s.user_id = u.id
            WHERE DATE(s.date) = CURRENT_DATE
            ORDER BY s.date DESC
        """)
        rows = cursor.fetchall()
        
        result = [
            {
                "id": r[0],
                "subtotal": float(r[1] or 0),
                "discount": float(r[2] or 0),
                "total": float(r[3] or 0),
                "profit": float(r[4] or 0),
                "date": r[5],
                "payment_method": r[6] if len(r) > 6 else 'unknown',
                "cheque_number": r[7] if len(r) > 7 else None,
                "username": r[8] if len(r) > 8 else 'Unknown'
            }
            for r in rows
        ]
        
        from database.db import return_connection
        return_connection(conn)
        
        return result
        
    except Exception as e:
        from database.db import return_connection
        return_connection(conn)
        print(f"Get today's sales failed: {str(e)}")
        raise e

# ---------------------------
# GET SALES BY DATE RANGE (WITH PAYMENT INFO AND USERNAME)
# ---------------------------
def get_sales_by_date_range(start_date, end_date):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT s.id, s.subtotal, s.discount, s.total, s.profit, s.date, 
                   s.payment_method, s.cheque_number, u.username
            FROM sales s
            LEFT JOIN users u ON s.user_id = u.id
            WHERE DATE(s.date) BETWEEN %s AND %s
            ORDER BY s.date DESC
        """, (start_date, end_date))
        
        rows = cursor.fetchall()
        
        result = [
            {
                "id": r[0],
                "subtotal": float(r[1] or 0),
                "discount": float(r[2] or 0),
                "total": float(r[3] or 0),
                "profit": float(r[4] or 0),
                "date": r[5],
                "payment_method": r[6] if len(r) > 6 else 'unknown',
                "cheque_number": r[7] if len(r) > 7 else None,
                "username": r[8] if len(r) > 8 else 'Unknown'
            }
            for r in rows
        ]
        
        from database.db import return_connection
        return_connection(conn)
        
        return result
        
    except Exception as e:
        from database.db import return_connection
        return_connection(conn)
        print(f"Get sales by date range failed: {str(e)}")
        raise e

# ============================================================================
# IMPORT SALE (BULK, FIFO) WITH USER SUPPORT
# ============================================================================
def import_sale_bulk(product_name, quantity, selling_price, discount, sale_date, 
                     payment_method='cash', cheque_number=None, user_id=None):
    """
    Create a sale for a single product without manual batch selection (FIFO).
    Preserves the given sale_date. Used for bulk importing historical sales.
    Returns sale_id.
    
    user_id: optional ID of the user to associate with the sale.
    """
    from database.db import get_connection, return_connection
    from services.purchase_service import update_product_stock  # reuse stock update

    conn = get_connection()
    try:
        cursor = conn.cursor()
        # 1. Get product
        cursor.execute("SELECT id, stock FROM products WHERE name = %s", (product_name,))
        product = cursor.fetchone()
        if not product:
            raise ValueError(f"Product '{product_name}' not found")
        product_id, stock = product

        if stock < quantity:
            raise ValueError(f"Insufficient stock for '{product_name}'. Stock: {stock}, requested: {quantity}")

        # 2. Get batches with remaining_quantity > 0, ordered by date (oldest first)
        cursor.execute("""
            SELECT id, remaining_quantity, cost_price, is_faulty, claimed_quantity
            FROM purchase_batches
            WHERE product_id = %s AND remaining_quantity > 0
            ORDER BY date ASC
        """, (product_id,))
        batches = cursor.fetchall()

        if not batches:
            raise ValueError(f"No batches available for product '{product_name}'")

        # 3. Allocate quantity from batches (FIFO)
        remaining_to_allocate = quantity
        allocations = []
        for batch_id, batch_qty, cost_price, is_faulty, claimed_qty in batches:
            if remaining_to_allocate <= 0:
                break
            take = min(batch_qty, remaining_to_allocate)
            allocations.append({
                'batch_id': batch_id,
                'qty': take,
                'cost_price': cost_price,
                'is_faulty': is_faulty or False,
                'claimed_quantity': claimed_qty or 0
            })
            remaining_to_allocate -= take

        if remaining_to_allocate > 0:
            raise ValueError(f"Not enough stock across batches for '{product_name}'")

        # 4. Create sale record with the provided date and user_id
        subtotal = quantity * selling_price
        total = subtotal - discount
        cursor.execute("""
            INSERT INTO sales (subtotal, discount, total, profit, date, payment_method, cheque_number, user_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (subtotal, discount, total, 0, sale_date, payment_method, cheque_number, user_id))
        sale_id = cursor.fetchone()[0]

        # 5. Insert sales_items and update batches
        total_profit = 0
        for alloc in allocations:
            batch_id = alloc['batch_id']
            qty = alloc['qty']
            cost = alloc['cost_price']
            item_profit = (selling_price - cost) * qty
            total_profit += item_profit

            cursor.execute("""
                INSERT INTO sales_items (sale_id, product_id, batch_id, quantity, cost_price, selling_price, profit)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (sale_id, product_id, batch_id, qty, cost, selling_price, item_profit))

            cursor.execute("""
                UPDATE purchase_batches
                SET remaining_quantity = remaining_quantity - %s
                WHERE id = %s
            """, (qty, batch_id))

        # 6. Update product stock
        update_product_stock(cursor, product_id)

        # 7. Update sale profit
        cursor.execute("UPDATE sales SET profit = %s WHERE id = %s", (total_profit, sale_id))

        conn.commit()
        return sale_id
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        return_connection(conn)