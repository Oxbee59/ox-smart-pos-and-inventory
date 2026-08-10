from database.db import get_connection, return_connection
from datetime import datetime

# --------------------------- CREATE CLAIM ---------------------------
def create_claim(product_id, batch_id, product_name, brand, category, issue_type, description, quantity):
    """
    Create a new claim for a product batch.
    - Does NOT change remaining_quantity – only increments claimed_quantity.
    - Validates against good stock (remaining_quantity - claimed_quantity).
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        # Get batch with claimed quantity
        cursor.execute("""
            SELECT remaining_quantity, quantity, COALESCE(claimed_quantity, 0) as claimed_qty
            FROM purchase_batches 
            WHERE id = %s AND product_id = %s
        """, (batch_id, product_id))
        batch = cursor.fetchone()
        if not batch:
            raise ValueError("Batch not found")
        
        remaining_qty = batch[0]
        claimed_qty = batch[2] if len(batch) > 2 else 0
        good_stock = remaining_qty - claimed_qty
        
        if good_stock < quantity:
            raise ValueError(f"Insufficient good stock. Available: {good_stock}, Requested: {quantity}")
        
        # Create claim
        cursor.execute("""
            INSERT INTO claims 
            (product_id, batch_id, product_name, brand, category, issue_type, description, quantity, remaining_good, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (product_id, batch_id, product_name, brand, category, issue_type, description, quantity, good_stock - quantity, 'active'))
        
        claim_id = cursor.fetchone()[0]
        
        # Update batch - mark as faulty, increment claimed_quantity ONLY – do NOT change remaining_quantity
        cursor.execute("""
            UPDATE purchase_batches 
            SET claimed_quantity = COALESCE(claimed_quantity, 0) + %s,
                is_faulty = TRUE
            WHERE id = %s
        """, (quantity, batch_id))
        
        # Update product stock (claimed items are removed from available stock)
        cursor.execute("""
            UPDATE products 
            SET stock = stock - %s 
            WHERE id = %s
        """, (quantity, product_id))
        
        conn.commit()
        return claim_id
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


# --------------------------- GET ALL CLAIMS (with sold from sales_items) ---------------------------
def get_all_claims():
    """Get all claims with product, batch, and sold quantity from sales_items"""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT 
                c.id,
                c.product_id,
                c.batch_id,
                c.product_name,
                c.brand,
                c.category,
                c.issue_type,
                c.description,
                c.quantity,
                c.remaining_good,
                c.status,
                c.created_at,
                c.updated_at,
                COALESCE(pb.remaining_quantity, 0) as batch_stock,
                COALESCE(pb.quantity, 0) as batch_original,
                COALESCE(pb.is_faulty, FALSE) as is_faulty,
                COALESCE(pb.claimed_quantity, 0) as batch_claimed,
                COALESCE((
                    SELECT SUM(si.quantity)
                    FROM sales_items si
                    WHERE si.batch_id = pb.id
                ), 0) as sold_from_sales
            FROM claims c
            LEFT JOIN purchase_batches pb ON c.batch_id = pb.id
            ORDER BY c.created_at DESC
        """)
        rows = cursor.fetchall()
        
        result = []
        for r in rows:
            batch_original = r[14] if len(r) > 14 else 0
            batch_stock = r[13] if len(r) > 13 else 0
            claimed_qty = r[8]
            batch_claimed_total = r[16] if len(r) > 16 else 0
            sold = r[17] if len(r) > 17 else 0   # from sales_items
            
            remaining_good_dynamic = batch_stock - claimed_qty
            
            result.append({
                "id": r[0],
                "product_id": r[1],
                "batch_id": r[2],
                "product_name": r[3],
                "brand": r[4] or '',
                "category": r[5] or '',
                "issue_type": r[6],
                "description": r[7] or '',
                "quantity": claimed_qty,
                "remaining_good": remaining_good_dynamic,
                "status": r[10],
                "created_at": r[11].isoformat() if hasattr(r[11], 'isoformat') else str(r[11]),
                "updated_at": r[12].isoformat() if hasattr(r[12], 'isoformat') else str(r[12]) if r[12] else None,
                "batch_stock": int(batch_stock),
                "batch_original": int(batch_original),
                "is_faulty": r[15] if len(r) > 15 else False,
                "batch_claimed": int(batch_claimed_total),
                "sold": int(sold)   # directly from sales
            })
        
        return result
    except Exception as e:
        print(f"❌ Error in get_all_claims: {str(e)}")
        import traceback
        traceback.print_exc()
        return []
    finally:
        conn.close()


# --------------------------- GET CLAIMS BY PRODUCT (with sold from sales_items) ---------------------------
def get_claims_by_product(product_id):
    """Get all claims for a specific product, with sold from sales_items"""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT 
                c.id,
                c.batch_id,
                c.product_name,
                c.brand,
                c.category,
                c.issue_type,
                c.description,
                c.quantity,
                c.remaining_good,
                c.status,
                c.created_at,
                COALESCE(pb.remaining_quantity, 0) as batch_stock,
                COALESCE(pb.quantity, 0) as batch_original,
                COALESCE(pb.claimed_quantity, 0) as batch_claimed,
                COALESCE((
                    SELECT SUM(si.quantity)
                    FROM sales_items si
                    WHERE si.batch_id = pb.id
                ), 0) as sold_from_sales
            FROM claims c
            LEFT JOIN purchase_batches pb ON c.batch_id = pb.id
            WHERE c.product_id = %s
            ORDER BY c.created_at DESC
        """, (product_id,))
        rows = cursor.fetchall()
        
        result = []
        for r in rows:
            batch_original = r[12] if len(r) > 12 else 0
            batch_stock = r[11] if len(r) > 11 else 0
            claimed_qty = r[7]
            batch_claimed_total = r[13] if len(r) > 13 else 0
            sold = r[14] if len(r) > 14 else 0
            
            remaining_good_dynamic = batch_stock - claimed_qty
            
            result.append({
                "id": r[0],
                "batch_id": r[1],
                "product_name": r[2],
                "brand": r[3] or '',
                "category": r[4] or '',
                "issue_type": r[5],
                "description": r[6] or '',
                "quantity": claimed_qty,
                "remaining_good": remaining_good_dynamic,
                "status": r[9],
                "created_at": r[10].isoformat() if hasattr(r[10], 'isoformat') else str(r[10]),
                "batch_stock": int(batch_stock),
                "batch_original": int(batch_original),
                "batch_claimed": int(batch_claimed_total),
                "sold": int(sold)
            })
        
        return result
    except Exception as e:
        print(f"❌ Error in get_claims_by_product: {str(e)}")
        return []
    finally:
        conn.close()


# --------------------------- GET CLAIM BY ID (with sold from sales_items) ---------------------------
def get_claim_by_id(claim_id):
    """Get a single claim by ID, with sold from sales_items"""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT 
                c.id,
                c.product_id,
                c.batch_id,
                c.product_name,
                c.brand,
                c.category,
                c.issue_type,
                c.description,
                c.quantity,
                c.remaining_good,
                c.status,
                c.created_at,
                c.updated_at,
                COALESCE(pb.remaining_quantity, 0) as batch_stock,
                COALESCE(pb.quantity, 0) as batch_original,
                COALESCE(pb.is_faulty, FALSE) as is_faulty,
                COALESCE(pb.claimed_quantity, 0) as batch_claimed,
                COALESCE((
                    SELECT SUM(si.quantity)
                    FROM sales_items si
                    WHERE si.batch_id = pb.id
                ), 0) as sold_from_sales
            FROM claims c
            LEFT JOIN purchase_batches pb ON c.batch_id = pb.id
            WHERE c.id = %s
        """, (claim_id,))
        row = cursor.fetchone()
        if row:
            batch_original = row[14] if len(row) > 14 else 0
            batch_stock = row[13] if len(row) > 13 else 0
            claimed_qty = row[8]
            batch_claimed_total = row[16] if len(row) > 16 else 0
            sold = row[17] if len(row) > 17 else 0
            
            remaining_good_dynamic = batch_stock - claimed_qty
            
            return {
                "id": row[0],
                "product_id": row[1],
                "batch_id": row[2],
                "product_name": row[3],
                "brand": row[4] or '',
                "category": row[5] or '',
                "issue_type": row[6],
                "description": row[7] or '',
                "quantity": claimed_qty,
                "remaining_good": remaining_good_dynamic,
                "status": row[10],
                "created_at": row[11].isoformat() if hasattr(row[11], 'isoformat') else str(row[11]),
                "updated_at": row[12].isoformat() if hasattr(row[12], 'isoformat') else str(row[12]) if row[12] else None,
                "batch_stock": int(batch_stock),
                "batch_original": int(batch_original),
                "is_faulty": row[15] if len(row) > 15 else False,
                "batch_claimed": int(batch_claimed_total),
                "sold": int(sold)
            }
        return None
    except Exception as e:
        print(f"❌ Error in get_claim_by_id: {str(e)}")
        return None
    finally:
        conn.close()


# --------------------------- UPDATE CLAIM ---------------------------
def update_claim(claim_id, issue_type, description, quantity):
    """
    Update an existing claim.
    - Does NOT change remaining_quantity – only adjusts claimed_quantity.
    - Validates that we don't over-claim if quantity increases.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        # Get current claim data
        cursor.execute("""
            SELECT product_id, batch_id, quantity, remaining_good
            FROM claims WHERE id = %s
        """, (claim_id,))
        current = cursor.fetchone()
        if not current:
            raise ValueError("Claim not found")
        
        old_quantity = current[2]
        diff = quantity - old_quantity
        
        # If increasing, verify available good stock
        if diff > 0:
            cursor.execute("""
                SELECT remaining_quantity, COALESCE(claimed_quantity, 0)
                FROM purchase_batches WHERE id = %s
            """, (current[1],))
            batch = cursor.fetchone()
            if batch:
                good_stock = batch[0] - batch[1]
                if good_stock < diff:
                    raise ValueError(f"Not enough good stock. Available: {good_stock}, requested increase: {diff}")
        
        # Update claim
        cursor.execute("""
            UPDATE claims 
            SET issue_type = %s, description = %s, quantity = %s, 
                remaining_good = remaining_good - %s,
                updated_at = %s
            WHERE id = %s
        """, (issue_type, description, quantity, diff, datetime.now(), claim_id))
        
        # Update batch claimed_quantity and product stock if quantity changed
        if diff != 0:
            batch_id = current[1]
            cursor.execute("""
                UPDATE purchase_batches 
                SET claimed_quantity = COALESCE(claimed_quantity, 0) + %s
                WHERE id = %s
            """, (diff, batch_id))
            
            # Adjust product stock (negative diff reduces stock, positive increases)
            cursor.execute("""
                UPDATE products 
                SET stock = stock - %s 
                WHERE id = %s
            """, (diff, current[0]))
        
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


# --------------------------- DELETE CLAIM ---------------------------
def delete_claim(claim_id):
    """
    Delete a claim and restore stock, reset faulty flag if no active claims remain.
    - Does NOT change remaining_quantity – only decrements claimed_quantity.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        # Get claim data
        cursor.execute("""
            SELECT product_id, batch_id, quantity
            FROM claims WHERE id = %s
        """, (claim_id,))
        claim = cursor.fetchone()
        if not claim:
            raise ValueError("Claim not found")
        
        product_id, batch_id, quantity = claim
        
        # Restore stock to batch: only decrease claimed_quantity, do NOT touch remaining_quantity
        cursor.execute("""
            UPDATE purchase_batches 
            SET claimed_quantity = GREATEST(COALESCE(claimed_quantity, 0) - %s, 0)
            WHERE id = %s
        """, (quantity, batch_id))
        
        # Check if any active claims remain for this batch
        cursor.execute("""
            SELECT COUNT(*) FROM claims 
            WHERE batch_id = %s AND status = 'active'
        """, (batch_id,))
        active_claims_count = cursor.fetchone()[0]
        
        # If no active claims, reset faulty flag and set claimed_quantity to 0
        if active_claims_count == 0:
            cursor.execute("""
                UPDATE purchase_batches 
                SET is_faulty = FALSE,
                    claimed_quantity = 0
                WHERE id = %s
            """, (batch_id,))
            print(f"✅ Reset faulty flag for batch {batch_id} - no active claims remain")
        
        # Restore product stock
        cursor.execute("""
            UPDATE products 
            SET stock = stock + %s 
            WHERE id = %s
        """, (quantity, product_id))
        
        # Delete the claim
        cursor.execute("DELETE FROM claims WHERE id = %s", (claim_id,))
        
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


# --------------------------- RESOLVE CLAIM ---------------------------
def resolve_claim(claim_id):
    """
    Mark a claim as resolved (stock stays deducted) and reset faulty flag if no active claims remain.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        # Get the claim details
        cursor.execute("""
            SELECT batch_id, quantity FROM claims WHERE id = %s
        """, (claim_id,))
        claim = cursor.fetchone()
        if not claim:
            raise ValueError("Claim not found")
        
        batch_id, quantity = claim
        
        # Update claim status to resolved
        cursor.execute("""
            UPDATE claims 
            SET status = 'resolved', updated_at = %s
            WHERE id = %s
        """, (datetime.now(), claim_id))
        
        # Check if any active claims remain for this batch
        cursor.execute("""
            SELECT COUNT(*) FROM claims 
            WHERE batch_id = %s AND status = 'active'
        """, (batch_id,))
        active_claims_count = cursor.fetchone()[0]
        
        # If no active claims, reset faulty flag and set claimed_quantity to 0
        if active_claims_count == 0:
            cursor.execute("""
                UPDATE purchase_batches 
                SET is_faulty = FALSE,
                    claimed_quantity = 0
                WHERE id = %s
            """, (batch_id,))
            print(f"✅ Reset faulty flag for batch {batch_id} - no active claims remain")
        
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


# --------------------------- SEARCH PRODUCTS FOR CLAIMS ---------------------------
def search_products_for_claims(keyword):
    """
    Search products by name, brand, or category for claim selection.
    Shows ALL products, including those with 0 stock.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        # First, check if we have any products
        cursor.execute("SELECT COUNT(*) FROM products")
        count = cursor.fetchone()[0]
        print(f"🔍 Total products in database: {count}")
        
        # Build the search query
        if keyword and len(keyword) >= 2:
            cursor.execute("""
                SELECT DISTINCT 
                    p.id,
                    p.name,
                    p.brand,
                    p.category,
                    p.stock,
                    p.selling_price,
                    p.cost_price,
                    COALESCE((
                        SELECT SUM(remaining_quantity) 
                        FROM purchase_batches 
                        WHERE product_id = p.id
                    ), 0) as total_stock
                FROM products p
                WHERE (
                    p.name ILIKE %s OR 
                    p.brand ILIKE %s OR 
                    p.category ILIKE %s
                )
                AND NOT EXISTS (
                    SELECT 1 FROM deleted_products dp 
                    WHERE dp.product_id = p.id 
                    AND dp.action IN ('PERMANENTLY DELETED', 'PRODUCT DELETED')
                    AND dp.source = 'product'
                )
                ORDER BY p.name ASC
                LIMIT 20
            """, (f'%{keyword}%', f'%{keyword}%', f'%{keyword}%'))
        else:
            # If no keyword, return some products
            cursor.execute("""
                SELECT DISTINCT 
                    p.id,
                    p.name,
                    p.brand,
                    p.category,
                    p.stock,
                    p.selling_price,
                    p.cost_price,
                    COALESCE((
                        SELECT SUM(remaining_quantity) 
                        FROM purchase_batches 
                        WHERE product_id = p.id
                    ), 0) as total_stock
                FROM products p
                WHERE NOT EXISTS (
                    SELECT 1 FROM deleted_products dp 
                    WHERE dp.product_id = p.id 
                    AND dp.action IN ('PERMANENTLY DELETED', 'PRODUCT DELETED')
                    AND dp.source = 'product'
                )
                ORDER BY p.name ASC
                LIMIT 20
            """)
        
        rows = cursor.fetchall()
        print(f"🔍 Found {len(rows)} products matching search")
        
        results = []
        for r in rows:
            # Get batches for this product
            cursor.execute("""
                SELECT 
                    id,
                    remaining_quantity,
                    selling_price,
                    cost_price,
                    COALESCE(is_faulty, FALSE) as is_faulty,
                    COALESCE(claimed_quantity, 0) as claimed_quantity
                FROM purchase_batches
                WHERE product_id = %s AND remaining_quantity > 0
                ORDER BY date ASC
            """, (r[0],))
            batches = cursor.fetchall()
            
            batch_list = []
            for b in batches:
                batch_list.append({
                    "batch_id": b[0],
                    "remaining_quantity": b[1],
                    "selling_price": float(b[2] or 0),
                    "cost_price": float(b[3] or 0),
                    "is_faulty": b[4] or False,
                    "claimed_quantity": b[5] or 0
                })
            
            results.append({
                "id": r[0],
                "name": r[1],
                "brand": r[2] or '',
                "category": r[3] or '',
                "stock": r[4] or 0,
                "selling_price": float(r[5] or 0),
                "cost_price": float(r[6] or 0),
                "total_stock": r[7] or 0,
                "batches": batch_list
            })
        
        print(f"✅ Returning {len(results)} products with batches")
        return results
    except Exception as e:
        print(f"❌ Error searching products: {str(e)}")
        import traceback
        traceback.print_exc()
        return []
    finally:
        conn.close()


# --------------------------- GET PRODUCT BATCHES FOR CLAIM ---------------------------
def get_product_batches_for_claim(product_id):
    """Get all batches for a product with claim info (good stock = remaining - claimed)"""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT 
                id,
                quantity,
                remaining_quantity,
                cost_price,
                selling_price,
                discount,
                date,
                COALESCE(is_faulty, FALSE) as is_faulty,
                COALESCE(claimed_quantity, 0) as claimed_quantity,
                COALESCE((
                    SELECT SUM(quantity) 
                    FROM claims 
                    WHERE batch_id = purchase_batches.id AND status = 'active'
                ), 0) as active_claims
            FROM purchase_batches
            WHERE product_id = %s AND remaining_quantity > 0
            ORDER BY date ASC
        """, (product_id,))
        rows = cursor.fetchall()
        
        result = []
        for r in rows:
            remaining_qty = int(r[2] or 0)
            claimed_qty = int(r[8] or 0)
            good_stock = remaining_qty - claimed_qty
            
            result.append({
                "batch_id": r[0],
                "quantity": int(r[1] or 0),
                "remaining_quantity": remaining_qty,
                "cost_price": float(r[3] or 0),
                "selling_price": float(r[4] or 0),
                "discount": float(r[5] or 0),
                "date": r[6].isoformat() if hasattr(r[6], 'isoformat') else str(r[6]) if r[6] else None,
                "is_faulty": r[7] or False,
                "claimed_quantity": claimed_qty,
                "active_claims": int(r[9] or 0),
                "good_stock": good_stock
            })
        
        return result
    except Exception as e:
        print(f"❌ Error in get_product_batches_for_claim: {str(e)}")
        return []
    finally:
        conn.close()