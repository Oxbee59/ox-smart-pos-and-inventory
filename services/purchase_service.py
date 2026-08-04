from database.db import get_connection
from datetime import datetime

# --------------------------- UPDATE PRODUCT STOCK ---------------------------
def update_product_stock(cursor, product_id):
    """Recalculate stock for a product based on all batches."""
    cursor.execute("""
        SELECT COALESCE(SUM(remaining_quantity), 0)
        FROM purchase_batches
        WHERE product_id = %s
    """, (product_id,))
    new_stock = cursor.fetchone()[0] or 0
    cursor.execute(
        "UPDATE products SET stock = %s WHERE id = %s",
        (new_stock, product_id)
    )


# --------------------------- ADD PURCHASE (BATCH-AWARE) ---------------------------
def add_purchase(name, brand, category, quantity, cost_price, discount, selling_price, purchase_date=None, source=None):
    quantity = int(quantity)
    cost_price = float(cost_price)
    discount = float(discount or 0)
    selling_price = float(selling_price)
    source = source or 'Unknown'

    # Use provided date or current time
    if purchase_date is None:
        purchase_date = datetime.now()

    conn = get_connection()
    try:
        cursor = conn.cursor()

        total = (cost_price * quantity) - discount

        # Save purchase record
        cursor.execute("""
            INSERT INTO purchases
            (product_name, brand, category, quantity, cost_price, discount, total, selling_price, date, source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (name, brand, category, quantity, cost_price, discount, total, selling_price, purchase_date, source))

        # Get or create product (check if not permanently deleted)
        cursor.execute("""
            SELECT p.id 
            FROM products p
            LEFT JOIN deleted_products dp ON dp.product_id = p.id AND dp.action = 'PERMANENTLY DELETED' AND dp.source = 'product'
            WHERE p.name = %s AND p.brand = %s AND dp.id IS NULL
        """, (name, brand))
        product = cursor.fetchone()

        if product:
            product_id = product[0]
            # Update category if different (keep other fields as-is)
            cursor.execute("""
                UPDATE products
                SET category = %s
                WHERE id = %s
            """, (category, product_id))
        else:
            cursor.execute("""
                INSERT INTO products (name, brand, cost_price, selling_price, stock, category)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (name, brand, cost_price, selling_price, 0, category))
            product_id = cursor.fetchone()[0]

        # Create batch with the provided date
        cursor.execute("""
            INSERT INTO purchase_batches
            (product_id, quantity, remaining_quantity, cost_price, selling_price, discount, date, action, source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (product_id, quantity, quantity, cost_price, selling_price, discount, purchase_date, "added", source))

        batch_id = cursor.fetchone()[0]

        # Recalculate stock
        update_product_stock(cursor, product_id)

        conn.commit()
        return batch_id

    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


# --------------------------- UPDATE BATCH (FIXED - PRESERVES SALES HISTORY) ---------------------------
def update_product(batch_id, name, brand, category, quantity, cost_price, discount, selling_price, source=None):
    """
    Update a batch and its associated product.
    ✅ FIXED: Preserves sales history - remaining_quantity is calculated from sales
    ✅ FIXED: Updates ONLY the specific batch, not other batches
    ✅ FIXED: Product-level prices are NOT updated (they come from batches)
    ✅ FIXED: Handles name/brand changes without creating duplicates
    ✅ FIXED: Batch number search support
    """
    quantity = int(quantity)
    cost_price = float(cost_price)
    discount = float(discount or 0)
    selling_price = float(selling_price)
    source = source or 'Unknown'

    conn = get_connection()
    try:
        cursor = conn.cursor()

        # Get linked product_id and current product data
        cursor.execute("""
            SELECT pb.product_id, p.name, p.brand, p.category, p.cost_price, p.selling_price, p.discount, 
                   pb.source, pb.remaining_quantity, pb.quantity
            FROM purchase_batches pb
            JOIN products p ON p.id = pb.product_id
            WHERE pb.id = %s
        """, (batch_id,))
        result = cursor.fetchone()

        if not result:
            raise ValueError("Batch not found")

        product_id = result[0]
        old_product_name = result[1]
        old_product_brand = result[2]
        old_category = result[3]
        old_cost_price = result[4]
        old_selling_price = result[5]
        old_discount = result[6]
        old_source = result[7] if len(result) > 7 else 'Unknown'
        current_remaining = result[8] if len(result) > 8 else 0
        current_total = result[9] if len(result) > 9 else 0

        # ✅ Calculate total sold from sales_items
        cursor.execute("""
            SELECT COALESCE(SUM(quantity), 0) 
            FROM sales_items 
            WHERE batch_id = %s
        """, (batch_id,))
        total_sold = cursor.fetchone()[0]

        # ✅ Calculate correct remaining quantity
        # Remaining = New Total Quantity - Total Sold
        correct_remaining = max(quantity - total_sold, 0)

        print(f"📊 Batch #{batch_id}: Total={quantity}, Sold={total_sold}, Remaining={correct_remaining}")

        # ✅ Check if we're changing the product name/brand
        name_changed = (old_product_name.lower() != name.lower() or old_product_brand.lower() != brand.lower())

        # Archive old batch data
        cursor.execute("""
            SELECT quantity, remaining_quantity, cost_price, selling_price, discount, date, action, source
            FROM purchase_batches
            WHERE id = %s
        """, (batch_id,))
        old_batch = cursor.fetchone()
        
        if old_batch:
            cursor.execute("""
                INSERT INTO deleted_products
                (name, brand, cost_price, selling_price, stock, category, discount, action, product_id, source, batch_id, batch_quantity, batch_remaining)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (old_product_name, old_product_brand, old_batch[2], old_batch[3], old_batch[1], 
                  old_category, old_batch[4], "updated", product_id, old_source, batch_id, old_batch[0], old_batch[1]))

        # ✅ If name/brand changed, check if target product already exists
        if name_changed:
            cursor.execute("""
                SELECT p.id 
                FROM products p
                LEFT JOIN deleted_products dp ON dp.product_id = p.id AND dp.action = 'PERMANENTLY DELETED' AND dp.source = 'product'
                WHERE p.name = %s AND p.brand = %s AND p.id != %s AND dp.id IS NULL
            """, (name, brand, product_id))
            existing_product = cursor.fetchone()
            
            if existing_product:
                # ✅ Target product exists - move this batch to it
                new_product_id = existing_product[0]
                
                # Update the batch to point to the existing product
                cursor.execute("""
                    UPDATE purchase_batches
                    SET product_id = %s, quantity = %s, remaining_quantity = %s, 
                        cost_price = %s, selling_price = %s, discount = %s, 
                        date = %s, action = %s, source = %s
                    WHERE id = %s
                """, (new_product_id, quantity, correct_remaining, cost_price, selling_price, 
                      discount, datetime.now(), "updated", source, batch_id))
                
                # Update the existing product's category
                cursor.execute("""
                    UPDATE products
                    SET category = %s
                    WHERE id = %s
                """, (category, new_product_id))
                
                # Recalculate stock for BOTH products
                update_product_stock(cursor, new_product_id)
                update_product_stock(cursor, product_id)
                
                # Check if old product has any other batches
                cursor.execute("""
                    SELECT COUNT(*) FROM purchase_batches WHERE product_id = %s
                """, (product_id,))
                remaining_batches = cursor.fetchone()[0]
                
                if remaining_batches == 0:
                    # Delete the old product if it has no more batches
                    cursor.execute("DELETE FROM products WHERE id = %s", (product_id,))
                
                conn.commit()
                return batch_id
            
            else:
                # ✅ Target product doesn't exist - update the existing product
                cursor.execute("""
                    UPDATE products
                    SET name = %s, brand = %s, category = %s
                    WHERE id = %s
                """, (name, brand, category, product_id))

        else:
            # ✅ Name/brand didn't change - only update category
            cursor.execute("""
                UPDATE products
                SET category = %s
                WHERE id = %s
            """, (category, product_id))

        # ✅ Update ONLY the batch record with the new values
        # ✅ CRITICAL: remaining_quantity is calculated from sales, not just set to quantity
        cursor.execute("""
            UPDATE purchase_batches
            SET quantity = %s, remaining_quantity = %s, cost_price = %s, selling_price = %s, 
                discount = %s, date = %s, action = %s, source = %s
            WHERE id = %s
        """, (quantity, correct_remaining, cost_price, selling_price, discount, 
              datetime.now(), "updated", source, batch_id))

        # Recalculate stock for the product
        update_product_stock(cursor, product_id)

        conn.commit()
        print(f"✅ Batch #{batch_id} updated successfully!")
        return batch_id
        
    except Exception as e:
        conn.rollback()
        print(f"❌ Error updating batch #{batch_id}: {str(e)}")
        raise e
    finally:
        conn.close()


# --------------------------- GET ALL PURCHASES (FIXED - Added claimed_quantity) ---------------------------
def get_all_purchases():
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT b.id, p.name, p.brand, p.category,
                   b.quantity, b.remaining_quantity,
                   b.cost_price, b.discount, b.selling_price,
                   (b.cost_price * b.quantity - b.discount) AS total,
                   b.date, b.action, b.source,
                   COALESCE(b.claimed_quantity, 0) as claimed_quantity
            FROM purchase_batches b
            JOIN products p ON p.id = b.product_id
            WHERE NOT EXISTS (
                SELECT 1 FROM deleted_products dp 
                WHERE dp.product_id = p.id 
                AND dp.action = 'PERMANENTLY DELETED' 
                AND dp.source = 'product'
            )
            ORDER BY b.date DESC
        """)
        rows = cursor.fetchall()
        return [
            {
                "batch_id": r[0],
                "name": r[1],
                "brand": r[2],
                "category": r[3],
                "quantity": r[4],
                "remaining_quantity": r[5],
                "cost_price": r[6],
                "discount": r[7],
                "selling_price": r[8],
                "total_cost": r[9],
                "date": r[10],
                "action": r[11],
                "source": r[12] if len(r) > 12 else 'Unknown',
                "claimed_quantity": r[13] if len(r) > 13 else 0
            }
            for r in rows
        ]
    except Exception as e:
        print(f"❌ Error in get_all_purchases: {str(e)}")
        return []
    finally:
        conn.close()


# --------------------------- GET PURCHASES BY DATE RANGE (FIXED - Added claimed_quantity) ---------------------------
def get_purchases_by_date_range(start_date, end_date):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT b.id, p.name, p.brand, p.category,
                   b.quantity, b.remaining_quantity,
                   b.cost_price, b.discount, b.selling_price,
                   (b.cost_price * b.quantity - b.discount) AS total,
                   b.date, b.action, b.source,
                   COALESCE(b.claimed_quantity, 0) as claimed_quantity
            FROM purchase_batches b
            JOIN products p ON p.id = b.product_id
            WHERE b.date::date BETWEEN %s AND %s
            AND NOT EXISTS (
                SELECT 1 FROM deleted_products dp 
                WHERE dp.product_id = p.id 
                AND dp.action = 'PERMANENTLY DELETED' 
                AND dp.source = 'product'
            )
            ORDER BY b.date DESC
        """, (start_date, end_date))
        rows = cursor.fetchall()
        return [
            {
                "batch_id": r[0],
                "name": r[1],
                "brand": r[2],
                "category": r[3],
                "quantity": r[4],
                "remaining_quantity": r[5],
                "cost_price": r[6],
                "discount": r[7],
                "selling_price": r[8],
                "total_cost": r[9],
                "date": r[10],
                "action": r[11],
                "source": r[12] if len(r) > 12 else 'Unknown',
                "claimed_quantity": r[13] if len(r) > 13 else 0
            }
            for r in rows
        ]
    except Exception as e:
        print(f"❌ Error in get_purchases_by_date_range: {str(e)}")
        return []
    finally:
        conn.close()


# --------------------------- AUTOCOMPLETE SUGGESTIONS (with batch ID support) ---------------------------
def get_product_suggestions(keyword):
    """
    Get product suggestions with batch ID support.
    If keyword is numeric, search by batch ID as well.
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        
        # Try to parse as batch ID if it's numeric
        is_batch_id = False
        try:
            batch_id = int(keyword)
            is_batch_id = True
        except:
            pass
        
        if is_batch_id:
            # Search by batch ID
            cursor.execute("""
                SELECT DISTINCT p.name, p.brand, p.category, pb.id as batch_id
                FROM products p
                JOIN purchase_batches pb ON pb.product_id = p.id
                WHERE pb.id = %s 
                AND NOT EXISTS (
                    SELECT 1 FROM deleted_products dp 
                    WHERE dp.product_id = p.id 
                    AND dp.action = 'PERMANENTLY DELETED' 
                    AND dp.source = 'product'
                )
                LIMIT 5
            """, (batch_id,))
        else:
            # Search by name
            cursor.execute("""
                SELECT DISTINCT p.name, p.brand, p.category, NULL as batch_id
                FROM products p
                WHERE p.name ILIKE %s 
                AND NOT EXISTS (
                    SELECT 1 FROM deleted_products dp 
                    WHERE dp.product_id = p.id 
                    AND dp.action = 'PERMANENTLY DELETED' 
                    AND dp.source = 'product'
                )
                ORDER BY p.name ASC
                LIMIT 5
            """, (f"%{keyword}%",))
            
        results = cursor.fetchall()
        return [
            {"name": r[0], "brand": r[1], "category": r[2] or "", "batch_id": r[3] if len(r) > 3 else None}
            for r in results
        ]
    except Exception as e:
        print(f"❌ Error in get_product_suggestions: {str(e)}")
        return []
    finally:
        conn.close()


def get_category_suggestions(keyword):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT p.category
            FROM products p
            WHERE p.category ILIKE %s 
            AND NOT EXISTS (
                SELECT 1 FROM deleted_products dp 
                WHERE dp.product_id = p.id 
                AND dp.action = 'PERMANENTLY DELETED' 
                AND dp.source = 'product'
            )
            ORDER BY p.category ASC
            LIMIT 5
        """, (f"%{keyword}%",))
        results = cursor.fetchall()
        return [
            {"category": r[0]} for r in results if r[0]
        ]
    except Exception as e:
        print(f"❌ Error in get_category_suggestions: {str(e)}")
        return []
    finally:
        conn.close()


# --------------------------- GET SOURCE SUGGESTIONS ---------------------------
def get_source_suggestions(keyword):
    """Get distinct source suggestions for autocomplete"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT source
            FROM purchase_batches
            WHERE source ILIKE %s 
            AND source IS NOT NULL
            AND source != ''
            ORDER BY source ASC
            LIMIT 5
        """, (f"%{keyword}%",))
        results = cursor.fetchall()
        return [
            {"source": r[0]} for r in results if r[0]
        ]
    except Exception as e:
        print(f"❌ Error in get_source_suggestions: {str(e)}")
        return []
    finally:
        conn.close()


# --------------------------- GET BATCH BY ID (for reference) ---------------------------
def get_batch_by_id(batch_id):
    """Get a single batch by its ID"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT b.id, p.name, p.brand, p.category,
                   b.quantity, b.remaining_quantity,
                   b.cost_price, b.discount, b.selling_price,
                   (b.cost_price * b.quantity - b.discount) AS total,
                   b.date, b.action, b.source,
                   COALESCE(b.claimed_quantity, 0) as claimed_quantity
            FROM purchase_batches b
            JOIN products p ON p.id = b.product_id
            WHERE b.id = %s
            AND NOT EXISTS (
                SELECT 1 FROM deleted_products dp 
                WHERE dp.product_id = p.id 
                AND dp.action = 'PERMANENTLY DELETED' 
                AND dp.source = 'product'
            )
        """, (batch_id,))
        row = cursor.fetchone()
        if row:
            return {
                "batch_id": row[0],
                "name": row[1],
                "brand": row[2],
                "category": row[3],
                "quantity": row[4],
                "remaining_quantity": row[5],
                "cost_price": row[6],
                "discount": row[7],
                "selling_price": row[8],
                "total_cost": row[9],
                "date": row[10],
                "action": row[11],
                "source": row[12] if len(row) > 12 else 'Unknown',
                "claimed_quantity": row[13] if len(row) > 13 else 0
            }
        return None
    except Exception as e:
        print(f"❌ Error in get_batch_by_id: {str(e)}")
        return None
    finally:
        conn.close()


# --------------------------- GET TOTAL SOLD FOR BATCH ---------------------------
def get_batch_sold_quantity(batch_id):
    """Get total sold quantity for a specific batch"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COALESCE(SUM(quantity), 0) 
            FROM sales_items 
            WHERE batch_id = %s
        """, (batch_id,))
        return cursor.fetchone()[0]
    except Exception as e:
        print(f"❌ Error in get_batch_sold_quantity: {str(e)}")
        return 0
    finally:
        conn.close()