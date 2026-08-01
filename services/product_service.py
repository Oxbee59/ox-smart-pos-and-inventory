from database.db import get_connection
from datetime import datetime

# ---------------- RECALCULATE STOCK ----------------
def recalc_stock(cursor, product_id):
    cursor.execute("""
        SELECT COALESCE(SUM(remaining_quantity), 0)
        FROM purchase_batches
        WHERE product_id = %s AND remaining_quantity > 0
    """, (product_id,))
    stock = cursor.fetchone()[0] or 0
    cursor.execute("UPDATE products SET stock = %s WHERE id = %s", (stock, product_id))
    return stock


# ---------------- DELETE SINGLE BATCH (KEEPS HISTORY) ----------------
def delete_batch(batch_id):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT product_id, quantity, remaining_quantity, cost_price, selling_price, discount FROM purchase_batches WHERE id = %s", (batch_id,))
        batch = cursor.fetchone()
        if not batch:
            conn.close()
            return False
        product_id, batch_qty, batch_rem, cost_price, selling_price, discount = batch

        cursor.execute("SELECT name, brand, category FROM products WHERE id = %s", (product_id,))
        product = cursor.fetchone()
        if product:
            cursor.execute("""
                INSERT INTO deleted_products
                (name, brand, cost_price, selling_price, stock, category, discount, action, source, batch_id, batch_quantity, batch_remaining, product_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (product[0], product[1], cost_price, selling_price, batch_rem, product[2], discount, "BATCH DELETED", "batch", batch_id, batch_qty, batch_rem, product_id))

        cursor.execute("DELETE FROM purchase_batches WHERE id = %s", (batch_id,))
        new_stock = recalc_stock(cursor, product_id)
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        conn.rollback()
        conn.close()
        raise e


# ---------------- DELETE SINGLE BATCH (CLEAN EVERYTHING) ----------------
def delete_batch_clean_all(batch_id):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT product_id, quantity, remaining_quantity, cost_price, selling_price, discount FROM purchase_batches WHERE id = %s", (batch_id,))
        batch = cursor.fetchone()
        if not batch:
            conn.close()
            return False
        product_id, batch_qty, batch_rem, cost_price, selling_price, discount = batch

        cursor.execute("SELECT name, brand, category FROM products WHERE id = %s", (product_id,))
        product = cursor.fetchone()
        if product:
            cursor.execute("""
                INSERT INTO deleted_products
                (name, brand, cost_price, selling_price, stock, category, discount, action, source, batch_id, batch_quantity, batch_remaining, product_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (product[0], product[1], cost_price, selling_price, batch_rem, product[2], discount, "PERMANENTLY DELETED", "batch", batch_id, batch_qty, batch_rem, product_id))

        cursor.execute("DELETE FROM sales_items WHERE batch_id = %s", (batch_id,))
        cursor.execute("DELETE FROM purchase_batches WHERE id = %s", (batch_id,))
        new_stock = recalc_stock(cursor, product_id)
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        conn.rollback()
        conn.close()
        raise e


# ---------------- DELETE FULL PRODUCT (KEEPS HISTORY) ----------------
def delete_product_keep_history(product_id):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT name, brand, cost_price, selling_price, stock, category, discount FROM products WHERE id = %s", (product_id,))
        product = cursor.fetchone()
        if product:
            cursor.execute("SELECT id, quantity, remaining_quantity, cost_price, selling_price, discount FROM purchase_batches WHERE product_id = %s", (product_id,))
            batches = cursor.fetchall()
            for batch in batches:
                batch_id, batch_qty, batch_rem, cost_price, selling_price, discount = batch
                cursor.execute("""
                    INSERT INTO deleted_products
                    (name, brand, cost_price, selling_price, stock, category, discount, action, source, batch_id, batch_quantity, batch_remaining, product_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (product[0], product[1], cost_price, selling_price, batch_rem, product[2], discount, "BATCH DELETED", "product_delete", batch_id, batch_qty, batch_rem, product_id))
            cursor.execute("""
                INSERT INTO deleted_products 
                (name, brand, cost_price, selling_price, stock, category, discount, action, source, product_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (*product, "PRODUCT DELETED", "product", product_id))
        cursor.execute("DELETE FROM purchase_batches WHERE product_id = %s", (product_id,))
        cursor.execute("DELETE FROM products WHERE id = %s", (product_id,))
        conn.commit()
        conn.close()
    except Exception as e:
        conn.rollback()
        conn.close()
        raise e


# ---------------- DELETE PRODUCT (CLEAN EVERYTHING) ----------------
def delete_product_clean_all(product_id):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT name, brand, cost_price, selling_price, stock, category, discount FROM products WHERE id = %s", (product_id,))
        product = cursor.fetchone()
        if product:
            cursor.execute("""
                INSERT INTO deleted_products 
                (name, brand, cost_price, selling_price, stock, category, discount, action, source, product_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (*product, "PERMANENTLY DELETED", "product", product_id))
        cursor.execute("DELETE FROM purchase_batches WHERE product_id = %s", (product_id,))
        cursor.execute("DELETE FROM sales_items WHERE product_id = %s", (product_id,))
        cursor.execute("DELETE FROM products WHERE id = %s", (product_id,))
        conn.commit()
        conn.close()
    except Exception as e:
        conn.rollback()
        conn.close()
        raise e


# ---------------- UPDATE BATCH (WITH SOURCE PARAMETER) ----------------
def update_product(batch_id, name, brand, category, quantity, cost_price, discount, selling_price, source=None):
    quantity = int(quantity)
    cost_price = float(cost_price)
    discount = float(discount or 0)
    selling_price = float(selling_price)
    source = source or 'Unknown'
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT pb.product_id, pb.quantity, pb.remaining_quantity, pb.cost_price, pb.selling_price, pb.discount,
                   p.name, p.brand, p.category, pb.source
            FROM purchase_batches pb
            JOIN products p ON p.id = pb.product_id
            WHERE pb.id = %s
        """, (batch_id,))
        old = cursor.fetchone()
        if not old:
            raise ValueError("Batch not found")
        (product_id, old_qty, old_rem, old_cost, old_selling, old_disc,
         old_name, old_brand, old_category, old_source) = old
        
        # ✅ FIXED: Set source='batch' for batch updates
        cursor.execute("""
            INSERT INTO deleted_products
            (name, brand, cost_price, selling_price, stock, category, discount, action, source, batch_id, batch_quantity, batch_remaining, product_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (old_name, old_brand, old_cost, old_selling, old_rem, old_category, old_disc, "UPDATED", "batch", batch_id, old_qty, old_rem, product_id))
        
        cursor.execute("""
            UPDATE purchase_batches
            SET quantity = %s, remaining_quantity = %s, cost_price = %s, selling_price = %s, discount = %s, date = %s, action = %s, source = %s
            WHERE id = %s
        """, (quantity, quantity, cost_price, selling_price, discount, datetime.now(), "updated", source, batch_id))
        cursor.execute("UPDATE products SET category = %s WHERE id = %s", (category, product_id))
        recalc_stock(cursor, product_id)
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


# ===================== OPTIMIZED GET ALL PRODUCTS (FIXED - Stock from batches) =====================
def get_all_products():
    """
    Get all products with their batches and claim information.
    ✅ FIXED: Stock is calculated from batches, not from products.stock
    ✅ FIXED: Shows products with zero stock (all batches depleted)
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT 
                p.id as product_id,
                p.name,
                p.brand,
                p.cost_price,
                p.selling_price,
                p.category,
                p.discount,
                pb.id as batch_id,
                pb.quantity as batch_quantity,
                pb.remaining_quantity,
                pb.cost_price as batch_cost,
                pb.selling_price as batch_selling,
                pb.discount as batch_discount,
                pb.date as batch_date,
                COALESCE(pb.is_faulty, FALSE) as is_faulty,
                COALESCE(pb.claimed_quantity, 0) as claimed_quantity,
                COALESCE(c.claim_count, 0) as active_claims_qty
            FROM products p
            LEFT JOIN purchase_batches pb ON pb.product_id = p.id
            LEFT JOIN (
                SELECT batch_id, SUM(quantity) as claim_count
                FROM claims 
                WHERE status = 'active'
                GROUP BY batch_id
            ) c ON c.batch_id = pb.id
            WHERE EXISTS (
                SELECT 1 FROM purchase_batches pb2 
                WHERE pb2.product_id = p.id
            )
            AND NOT EXISTS (
                SELECT 1 FROM deleted_products dp 
                WHERE dp.product_id = p.id 
                AND dp.action IN ('PERMANENTLY DELETED', 'PRODUCT DELETED')
                AND dp.source = 'product'
            )
            ORDER BY p.id ASC, pb.date ASC
        """)
        
        rows = cursor.fetchall()
        
        # Group results by product
        products_dict = {}
        total_batches = 0
        
        for r in rows:
            product_id = r[0]
            if product_id not in products_dict:
                products_dict[product_id] = {
                    "product_id": product_id,
                    "name": r[1] or "-",
                    "brand": r[2] or "-",
                    "cost_price": r[3] or 0.0,
                    "selling_price": r[4] or 0.0,
                    "stock": 0,  # ✅ Will be calculated from batches
                    "category": r[5] or "-",
                    "discount": r[6] or 0.0,
                    "batches": [],
                    "total_claimed": 0
                }
            
            # Add batch if it exists
            if r[7] is not None:  # batch_id exists
                total_batches += 1
                active_claims = r[16] or 0
                remaining_qty = int(r[9] or 0)
                claimed_qty = r[15] or 0
                
                batch = {
                    "batch_id": r[7],
                    "quantity": int(r[8] or 0),
                    "remaining_quantity": remaining_qty,
                    "cost_price": float(r[10] or 0),
                    "selling_price": float(r[11] or 0),
                    "discount": float(r[12] or 0),
                    "date": r[13],
                    "is_faulty": r[14] or False,
                    "claimed_quantity": claimed_qty,
                    "active_claims": active_claims,
                    "good_stock": remaining_qty - claimed_qty  # ✅ Good stock per batch
                }
                products_dict[product_id]["batches"].append(batch)
                products_dict[product_id]["stock"] += remaining_qty  # ✅ Calculate stock from batches
                products_dict[product_id]["total_claimed"] += active_claims
        
        # Convert to list
        result = list(products_dict.values())
        
        return {
            "products": result,
            "total_products": len(result),
            "total_batches": total_batches
        }
    except Exception as e:
        print(f"❌ Error in get_all_products: {str(e)}")
        import traceback
        traceback.print_exc()
        return {"products": [], "total_products": 0, "total_batches": 0}
    finally:
        conn.close()


# ---------------- GET DELETED PRODUCTS ----------------
def get_deleted_products():
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT id, name, brand, cost_price, selling_price, stock, category, discount, action, deleted_at,
                   batch_id, batch_quantity, batch_remaining, product_id, source
            FROM deleted_products
            ORDER BY deleted_at DESC
        """)
        data = cursor.fetchall()
        return data
    except Exception as e:
        print(f"❌ Error in get_deleted_products: {str(e)}")
        return []
    finally:
        conn.close()


# ---------------- RESTORE ARCHIVED ----------------
def restore_archive(archive_id):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM deleted_products WHERE id = %s", (archive_id,))
        row = cursor.fetchone()
        if not row:
            raise ValueError("Archive record not found")
        
        columns = [desc[0] for desc in cursor.description]
        record = dict(zip(columns, row))
        
        name = record.get('name')
        brand = record.get('brand')
        category = record.get('category')
        cost_price = record.get('cost_price')
        selling_price = record.get('selling_price')
        stock = record.get('stock')
        discount = record.get('discount')
        source = record.get('source', 'product')
        batch_id = record.get('batch_id')
        batch_qty = record.get('batch_quantity')
        batch_rem = record.get('batch_remaining')
        product_id = record.get('product_id')
        deleted_action = record.get('action')
        
        # ✅ Check for permanent deletion
        if deleted_action == "PERMANENTLY DELETED":
            raise ValueError("This item was permanently deleted and cannot be restored!")
        
        # ✅ Restore logic
        if source == 'batch' and batch_id:
            # Restore a single batch
            cursor.execute("SELECT id FROM products WHERE id = %s", (product_id,))
            prod = cursor.fetchone()
            if not prod:
                cursor.execute("""
                    INSERT INTO products (name, brand, cost_price, selling_price, stock, category, discount)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (name, brand, cost_price, selling_price, 0, category, discount))
                product_id = cursor.fetchone()[0]
            
            cursor.execute("""
                INSERT INTO purchase_batches
                (id, product_id, quantity, remaining_quantity, cost_price, selling_price, discount, date, action)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (batch_id, product_id, batch_qty, batch_rem, cost_price, selling_price, discount, datetime.now(), "restored"))
            recalc_stock(cursor, product_id)
            
        elif source == 'product_delete' and product_id:
            # Restore a product with all its batches
            cursor.execute("SELECT id FROM products WHERE name = %s AND brand = %s", (name, brand))
            prod = cursor.fetchone()
            if prod:
                product_id = prod[0]
            else:
                cursor.execute("""
                    INSERT INTO products (id, name, brand, cost_price, selling_price, stock, category, discount)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (product_id, name, brand, cost_price, selling_price, 0, category, discount))
            
            cursor.execute("""
                SELECT batch_id, batch_quantity, batch_remaining, cost_price, selling_price, discount
                FROM deleted_products
                WHERE product_id = %s AND source = 'product_delete' AND action = 'BATCH DELETED'
            """, (product_id,))
            batches = cursor.fetchall()
            for batch in batches:
                old_batch_id, old_qty, old_rem, old_cost, old_selling, old_disc = batch
                cursor.execute("""
                    INSERT INTO purchase_batches
                    (id, product_id, quantity, remaining_quantity, cost_price, selling_price, discount, date, action)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (old_batch_id, product_id, old_qty, old_rem, old_cost, old_selling, old_disc, datetime.now(), "restored"))
            recalc_stock(cursor, product_id)
            
        elif source == 'batch' and deleted_action == "UPDATED" and batch_id:
            # Restore a batch that was updated
            cursor.execute("SELECT id FROM purchase_batches WHERE id = %s", (batch_id,))
            if cursor.fetchone():
                cursor.execute("""
                    UPDATE purchase_batches
                    SET quantity = %s, remaining_quantity = %s, cost_price = %s, selling_price = %s, discount = %s, date = %s, action = %s
                    WHERE id = %s
                """, (batch_qty, batch_rem, cost_price, selling_price, discount, datetime.now(), "restored", batch_id))
                if product_id:
                    recalc_stock(cursor, product_id)
            else:
                if not product_id:
                    cursor.execute("SELECT id FROM products WHERE name = %s AND brand = %s", (name, brand))
                    prod = cursor.fetchone()
                    if prod:
                        product_id = prod[0]
                    else:
                        cursor.execute("""
                            INSERT INTO products (name, brand, cost_price, selling_price, stock, category, discount)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                            RETURNING id
                        """, (name, brand, cost_price, selling_price, 0, category, discount))
                        product_id = cursor.fetchone()[0]
                
                cursor.execute("""
                    INSERT INTO purchase_batches
                    (id, product_id, quantity, remaining_quantity, cost_price, selling_price, discount, date, action)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (batch_id, product_id, batch_qty, batch_rem, cost_price, selling_price, discount, datetime.now(), "restored"))
                recalc_stock(cursor, product_id)
                
        elif source == 'product' and deleted_action == "PRODUCT DELETED":
            # Restore a product that was deleted
            cursor.execute("SELECT id FROM products WHERE name = %s AND brand = %s", (name, brand))
            prod = cursor.fetchone()
            if prod:
                product_id = prod[0]
                cursor.execute("""
                    INSERT INTO purchase_batches
                    (product_id, quantity, remaining_quantity, cost_price, selling_price, discount, date, action)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (product_id, stock, stock, cost_price, selling_price, discount, datetime.now(), "restored"))
                recalc_stock(cursor, product_id)
            else:
                cursor.execute("""
                    INSERT INTO products
                    (name, brand, cost_price, selling_price, stock, category, discount)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (name, brand, cost_price, selling_price, 0, category, discount))
                product_id = cursor.fetchone()[0]
                cursor.execute("""
                    INSERT INTO purchase_batches
                    (product_id, quantity, remaining_quantity, cost_price, selling_price, discount, date, action)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (product_id, stock, stock, cost_price, selling_price, discount, datetime.now(), "restored"))
                recalc_stock(cursor, product_id)
        
        # ✅ Clean up deleted_products records
        if source == 'product_delete' and product_id:
            cursor.execute("DELETE FROM deleted_products WHERE product_id = %s AND source = 'product_delete'", (product_id,))
        else:
            cursor.execute("DELETE FROM deleted_products WHERE id = %s", (archive_id,))
        
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()