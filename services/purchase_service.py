from database.db import get_connection
from datetime import datetime
import json

# ============================================================
#  HELPERS
# ============================================================

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


def log_batch_update(cursor, batch_id, old_data, new_data):
    """Log changes to batch_update_history."""
    diff = {}
    for key in old_data:
        if old_data[key] != new_data[key]:
            diff[key] = {"old": old_data[key], "new": new_data[key]}
    if diff:
        cursor.execute("""
            INSERT INTO batch_update_history (batch_id, changed_fields, updated_at)
            VALUES (%s, %s, %s)
        """, (batch_id, json.dumps(diff), datetime.now()))


# ============================================================
#  ADD PURCHASE
# ============================================================

def add_purchase(name, brand, category, quantity, cost_price, discount, selling_price, purchase_date=None, source=None):
    quantity = int(quantity)
    cost_price = float(cost_price)
    discount = float(discount or 0)
    selling_price = float(selling_price)
    source = source or 'Unknown'

    if purchase_date is None:
        purchase_date = datetime.now()

    conn = get_connection()
    try:
        cursor = conn.cursor()

        total = (cost_price * quantity) - discount

        # Save purchase record (legacy)
        cursor.execute("""
            INSERT INTO purchases
            (product_name, brand, category, quantity, cost_price, discount, total, selling_price, date, source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (name, brand, category, quantity, cost_price, discount, total, selling_price, purchase_date, source))

        # Get or create product
        cursor.execute("""
            SELECT p.id 
            FROM products p
            LEFT JOIN deleted_products dp ON dp.product_id = p.id AND dp.action = 'PERMANENTLY DELETED' AND dp.source = 'product'
            WHERE p.name = %s AND p.brand = %s AND dp.id IS NULL
        """, (name, brand))
        product = cursor.fetchone()

        if product:
            product_id = product[0]
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

        # Create batch
        cursor.execute("""
            INSERT INTO purchase_batches
            (product_id, quantity, remaining_quantity, cost_price, selling_price, discount, date, action, source,
             original_quantity, original_cost_price, original_selling_price, original_discount, original_date)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (product_id, quantity, quantity, cost_price, selling_price, discount, purchase_date, "added", source,
              quantity, cost_price, selling_price, discount, purchase_date))

        batch_id = cursor.fetchone()[0]

        update_product_stock(cursor, product_id)

        conn.commit()
        return batch_id

    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


# ============================================================
#  UPDATE BATCH (WITH MODE, HISTORY, AND KEEP_SOLD_WITH_OLD)
# ============================================================

def update_product(
    batch_id,
    name,
    brand,
    category,
    quantity,
    cost_price,
    discount,
    selling_price,
    source=None,
    update_mode='auto',
    keep_sold_with_old=True
):
    """
    Smart batch update with explicit mode and sold‑item handling.

    Modes:
      - 'auto': original behaviour (create new batch on price/identity change)
      - 'create': force creation of a new batch if price/identity changes
      - 'update': force update of the same batch, even if price/identity changes

    keep_sold_with_old:
      - If True (default): the new batch inherits the remaining stock from the old batch.
        The old batch's remaining_quantity is zeroed out since that stock now lives
        on the new batch — otherwise it gets counted twice in stock/capital totals.
      - If False: the new batch gets the full new quantity (fresh stock), and the old batch is depleted.
    """
    quantity = int(quantity)
    cost_price = float(cost_price)
    discount = float(discount or 0)
    selling_price = float(selling_price)
    source = source or 'Unknown'

    conn = get_connection()
    try:
        cursor = conn.cursor()

        # Fetch current batch and product
        cursor.execute("""
            SELECT pb.product_id, p.name, p.brand, p.category, p.cost_price, p.selling_price, p.discount,
                   pb.source, pb.remaining_quantity, pb.quantity, pb.original_quantity, pb.original_date,
                   pb.original_cost_price, pb.original_selling_price, pb.original_discount
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
        original_quantity = result[10] if len(result) > 10 else current_total
        original_date = result[11] if len(result) > 11 else datetime.now()
        original_cost = result[12] if len(result) > 12 else old_cost_price
        original_selling = result[13] if len(result) > 13 else old_selling_price
        original_discount = result[14] if len(result) > 14 else old_discount

        # Calculate total sold
        cursor.execute("""
            SELECT COALESCE(SUM(quantity), 0)
            FROM sales_items
            WHERE batch_id = %s
        """, (batch_id,))
        total_sold = cursor.fetchone()[0]

        # Determine changes – now case‑sensitive so even case changes trigger the modal
        identity_changed = (
            old_product_name != name or
            old_product_brand != brand or
            old_category != category
        )

        price_changed = (
            abs(old_cost_price - cost_price) > 0.001 or
            abs(old_selling_price - selling_price) > 0.001 or
            abs(old_discount - discount) > 0.001
        )

        print(f"📊 Batch #{batch_id}: Identity: {identity_changed}, Price: {price_changed}, Mode: {update_mode}")

        # ============ DECIDE ACTION ============
        if update_mode == 'update':
            # Force update of this batch – preserve original data
            if identity_changed:
                # Check if another product with new identity exists
                cursor.execute("""
                    SELECT p.id
                    FROM products p
                    LEFT JOIN deleted_products dp ON dp.product_id = p.id AND dp.action = 'PERMANENTLY DELETED' AND dp.source = 'product'
                    WHERE p.name = %s AND p.brand = %s AND p.category = %s AND p.id != %s AND dp.id IS NULL
                """, (name, brand, category, product_id))
                existing = cursor.fetchone()
                if existing:
                    new_product_id = existing[0]
                    cursor.execute("""
                        UPDATE purchase_batches
                        SET product_id = %s, quantity = %s, remaining_quantity = %s,
                            cost_price = %s, selling_price = %s, discount = %s,
                            date = %s, action = %s, source = %s
                        WHERE id = %s
                    """, (new_product_id, quantity, max(quantity - total_sold, 0), cost_price, selling_price,
                          discount, datetime.now(), "moved_forced", source, batch_id))
                    update_product_stock(cursor, new_product_id)
                    update_product_stock(cursor, product_id)
                    cursor.execute("SELECT COUNT(*) FROM purchase_batches WHERE product_id = %s", (product_id,))
                    if cursor.fetchone()[0] == 0:
                        cursor.execute("DELETE FROM products WHERE id = %s", (product_id,))
                    product_id = new_product_id
                else:
                    cursor.execute("""
                        UPDATE products
                        SET name = %s, brand = %s, category = %s
                        WHERE id = %s
                    """, (name, brand, category, product_id))
                    cursor.execute("""
                        UPDATE purchase_batches
                        SET quantity = %s, remaining_quantity = %s,
                            cost_price = %s, selling_price = %s, discount = %s,
                            date = %s, action = %s, source = %s
                        WHERE id = %s
                    """, (quantity, max(quantity - total_sold, 0), cost_price, selling_price, discount,
                          datetime.now(), "updated_forced", source, batch_id))
            else:
                cursor.execute("""
                    UPDATE purchase_batches
                    SET quantity = %s, remaining_quantity = %s,
                        cost_price = %s, selling_price = %s, discount = %s,
                        date = %s, action = %s, source = %s
                    WHERE id = %s
                """, (quantity, max(quantity - total_sold, 0), cost_price, selling_price, discount,
                      datetime.now(), "updated_forced", source, batch_id))

            old_data = {
                "quantity": current_total,
                "remaining": current_remaining,
                "cost_price": old_cost_price,
                "selling_price": old_selling_price,
                "discount": old_discount,
                "source": old_source,
                "product_name": old_product_name,
                "brand": old_product_brand,
                "category": old_category
            }
            new_data = {
                "quantity": quantity,
                "remaining": max(quantity - total_sold, 0),
                "cost_price": cost_price,
                "selling_price": selling_price,
                "discount": discount,
                "source": source,
                "product_name": name,
                "brand": brand,
                "category": category
            }
            log_batch_update(cursor, batch_id, old_data, new_data)
            update_product_stock(cursor, product_id)
            conn.commit()
            return batch_id

        # ============ MODE == 'create' OR 'auto' ============
        # Archive the old batch (for history) – common for both create and auto
        cursor.execute("""
            SELECT quantity, remaining_quantity, cost_price, selling_price, discount, date, action, source
            FROM purchase_batches
            WHERE id = %s
        """, (batch_id,))
        old_batch = cursor.fetchone()
        if old_batch:
            cursor.execute("""
                INSERT INTO deleted_products
                (name, brand, cost_price, selling_price, stock, category, discount, action, product_id, source,
                 batch_id, batch_quantity, batch_remaining)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (old_product_name, old_product_brand, old_batch[2], old_batch[3], old_batch[1],
                  old_category, old_batch[4], "updated", product_id, old_source,
                  batch_id, old_batch[0], old_batch[1]))

        # ============ CASE 1: Identity changed ============
        if identity_changed:
            if update_mode == 'create' or update_mode == 'auto':
                cursor.execute("""
                    SELECT p.id
                    FROM products p
                    LEFT JOIN deleted_products dp ON dp.product_id = p.id AND dp.action = 'PERMANENTLY DELETED' AND dp.source = 'product'
                    WHERE p.name = %s AND p.brand = %s AND p.category = %s AND p.id != %s AND dp.id IS NULL
                """, (name, brand, category, product_id))
                existing_product = cursor.fetchone()

                if existing_product:
                    new_product_id = existing_product[0]
                    # Determine new batch quantity and remaining based on keep_sold_with_old
                    if keep_sold_with_old:
                        new_batch_qty = current_remaining
                        new_batch_remaining = current_remaining
                        # FIX: the unsold stock is moving to the new batch — zero the old
                        # batch's remaining_quantity so it isn't counted on both rows.
                        cursor.execute("""
                            UPDATE purchase_batches
                            SET remaining_quantity = 0, action = 'remaining_moved_to_new_batch'
                            WHERE id = %s
                        """, (batch_id,))
                    else:
                        new_batch_qty = quantity
                        new_batch_remaining = quantity
                        # Deplete old batch
                        cursor.execute("""
                            UPDATE purchase_batches
                            SET remaining_quantity = 0, action = 'depleted_by_update'
                            WHERE id = %s
                        """, (batch_id,))

                    cursor.execute("""
                        INSERT INTO purchase_batches
                        (product_id, quantity, remaining_quantity, cost_price, selling_price, discount,
                         date, action, source,
                         original_quantity, original_cost_price, original_selling_price, original_discount, original_date)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING id
                    """, (new_product_id, new_batch_qty, new_batch_remaining, cost_price, selling_price, discount,
                          datetime.now(), "moved_new", source,
                          new_batch_qty, cost_price, selling_price, discount, datetime.now()))
                    new_batch_id = cursor.fetchone()[0]
                    update_product_stock(cursor, new_product_id)
                    update_product_stock(cursor, product_id)
                    cursor.execute("SELECT COUNT(*) FROM purchase_batches WHERE product_id = %s", (product_id,))
                    if cursor.fetchone()[0] == 0:
                        cursor.execute("DELETE FROM products WHERE id = %s", (product_id,))
                    conn.commit()
                    return new_batch_id
                else:
                    # Rename existing product and create new batch on it
                    cursor.execute("""
                        UPDATE products
                        SET name = %s, brand = %s, category = %s
                        WHERE id = %s
                    """, (name, brand, category, product_id))
                    if keep_sold_with_old:
                        new_batch_qty = current_remaining
                        new_batch_remaining = current_remaining
                        # FIX: same double-counting guard as above
                        cursor.execute("""
                            UPDATE purchase_batches
                            SET remaining_quantity = 0, action = 'remaining_moved_to_new_batch'
                            WHERE id = %s
                        """, (batch_id,))
                    else:
                        new_batch_qty = quantity
                        new_batch_remaining = quantity
                        cursor.execute("""
                            UPDATE purchase_batches
                            SET remaining_quantity = 0, action = 'depleted_by_update'
                            WHERE id = %s
                        """, (batch_id,))
                    cursor.execute("""
                        INSERT INTO purchase_batches
                        (product_id, quantity, remaining_quantity, cost_price, selling_price, discount,
                         date, action, source,
                         original_quantity, original_cost_price, original_selling_price, original_discount, original_date)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING id
                    """, (product_id, new_batch_qty, new_batch_remaining, cost_price, selling_price, discount,
                          datetime.now(), "identity_changed", source,
                          new_batch_qty, cost_price, selling_price, discount, datetime.now()))
                    new_batch_id = cursor.fetchone()[0]
                    update_product_stock(cursor, product_id)
                    conn.commit()
                    return new_batch_id

        # ============ CASE 2: Price changed, but identity same ============
        elif price_changed and not identity_changed:
            if update_mode == 'create' or update_mode == 'auto':
                if keep_sold_with_old:
                    new_batch_qty = current_remaining
                    new_batch_remaining = current_remaining
                    # FIX: same double-counting guard as above
                    cursor.execute("""
                        UPDATE purchase_batches
                        SET remaining_quantity = 0, action = 'remaining_moved_to_new_batch'
                        WHERE id = %s
                    """, (batch_id,))
                else:
                    new_batch_qty = quantity
                    new_batch_remaining = quantity
                    cursor.execute("""
                        UPDATE purchase_batches
                        SET remaining_quantity = 0, action = 'depleted_by_update'
                        WHERE id = %s
                    """, (batch_id,))
                cursor.execute("""
                    INSERT INTO purchase_batches
                    (product_id, quantity, remaining_quantity, cost_price, selling_price, discount,
                     date, action, source,
                     original_quantity, original_cost_price, original_selling_price, original_discount, original_date)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (product_id, new_batch_qty, new_batch_remaining, cost_price, selling_price, discount,
                      datetime.now(), "price_changed", source,
                      new_batch_qty, cost_price, selling_price, discount, datetime.now()))
                new_batch_id = cursor.fetchone()[0]
                cursor.execute("""
                    UPDATE purchase_batches
                    SET action = %s
                    WHERE id = %s
                """, ("price_updated_original", batch_id))
                update_product_stock(cursor, product_id)
                conn.commit()
                return new_batch_id

        # ============ CASE 3: Quantity/Source only (or no change) ============
        # If we reach here, either mode is 'auto' and no price/identity change, or mode is 'create' but no price/identity change.
        # In both cases, we update the same batch.
        cursor.execute("""
            UPDATE purchase_batches
            SET quantity = %s, remaining_quantity = %s,
                cost_price = %s, selling_price = %s, discount = %s,
                date = %s, action = %s, source = %s
            WHERE id = %s
        """, (quantity, max(quantity - total_sold, 0), cost_price, selling_price, discount,
              datetime.now(), "updated_qty", source, batch_id))

        update_product_stock(cursor, product_id)
        conn.commit()
        return batch_id

    except Exception as e:
        conn.rollback()
        print(f"❌ Error updating batch #{batch_id}: {str(e)}")
        raise e
    finally:
        conn.close()


# ============================================================
#  BATCH UPDATE HISTORY RETRIEVAL
# ============================================================

def get_batch_update_history(batch_id):
    """Get all logged updates for a batch."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT changed_fields, updated_at
            FROM batch_update_history
            WHERE batch_id = %s
            ORDER BY updated_at DESC
        """, (batch_id,))
        rows = cursor.fetchall()
        return [{"changes": r[0], "date": r[1]} for r in rows]
    except Exception as e:
        print(f"❌ Error getting batch update history: {str(e)}")
        return []
    finally:
        conn.close()


# ============================================================
#  GET PURCHASE HISTORY (original + current + updates)
# ============================================================

def get_purchase_history(batch_id):
    """Get the original purchase record for a batch (original + current state)."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT pb.id, p.name, p.brand, p.category,
                   pb.original_quantity, pb.quantity, pb.remaining_quantity,
                   pb.original_cost_price, pb.cost_price, pb.original_selling_price, pb.selling_price,
                   pb.original_discount, pb.discount,
                   pb.original_date, pb.date, pb.source, pb.action
            FROM purchase_batches pb
            JOIN products p ON p.id = pb.product_id
            WHERE pb.id = %s
        """, (batch_id,))
        row = cursor.fetchone()
        if row:
            return {
                "batch_id": row[0],
                "name": row[1],
                "brand": row[2],
                "category": row[3],
                "original_quantity": row[4],
                "current_quantity": row[5],
                "remaining_quantity": row[6],
                "original_cost_price": row[7],
                "current_cost_price": row[8],
                "original_selling_price": row[9],
                "current_selling_price": row[10],
                "original_discount": row[11],
                "current_discount": row[12],
                "original_date": row[13],
                "last_updated": row[14],
                "source": row[15],
                "action": row[16]
            }
        return None
    except Exception as e:
        print(f"❌ Error getting purchase history: {str(e)}")
        return None
    finally:
        conn.close()


# ============================================================
#  GET SOLD HISTORY
# ============================================================

def get_sold_history(batch_id):
    """Get all sales records for a batch with dates and quantities."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                si.id,
                si.quantity,
                si.selling_price,
                si.cost_price,
                si.profit,
                s.date as sale_date,
                s.id as sale_id,
                s.total as sale_total,
                u.username
            FROM sales_items si
            JOIN sales s ON s.id = si.sale_id
            LEFT JOIN users u ON s.user_id = u.id
            WHERE si.batch_id = %s
            ORDER BY s.date DESC
        """, (batch_id,))
        rows = cursor.fetchall()
        return [
            {
                "sale_item_id": r[0],
                "quantity": r[1],
                "selling_price": r[2],
                "cost_price": r[3],
                "profit": r[4],
                "sale_date": r[5],
                "sale_id": r[6],
                "sale_total": r[7],
                "username": r[8] if len(r) > 8 else 'Unknown'
            }
            for r in rows
        ]
    except Exception as e:
        print(f"❌ Error getting sold history: {str(e)}")
        return []
    finally:
        conn.close()


# ============================================================
#  GET ALL PURCHASES (WITH ORIGINAL DATA)
# ============================================================

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
                   COALESCE(b.claimed_quantity, 0) as claimed_quantity,
                   b.original_quantity, b.original_date,
                   b.original_cost_price, b.original_selling_price
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
                "claimed_quantity": r[13] if len(r) > 13 else 0,
                "original_quantity": r[14] if len(r) > 14 else r[4],
                "original_date": r[15] if len(r) > 15 else r[10],
                "original_cost_price": r[16] if len(r) > 16 else r[6],
                "original_selling_price": r[17] if len(r) > 17 else r[8]
            }
            for r in rows
        ]
    except Exception as e:
        print(f"❌ Error in get_all_purchases: {str(e)}")
        return []
    finally:
        conn.close()


# ============================================================
#  GET PURCHASES BY DATE RANGE (WITH DATE TYPE & UPDATED-ONLY FILTER)
# ============================================================

def get_purchases_by_date_range(
    start_date,
    end_date,
    date_type='original',
    show_updated_only=False
):
    """
    Fetch purchases filtered by date range.

    date_type:
        'original' -> uses original_date
        'updated'  -> uses date (last update)
    show_updated_only: if True, only include batches where action != 'added'
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()

        date_column = 'b.original_date' if date_type == 'original' else 'b.date'

        query = f"""
            SELECT b.id, p.name, p.brand, p.category,
                   b.quantity, b.remaining_quantity,
                   b.cost_price, b.discount, b.selling_price,
                   (b.cost_price * b.quantity - b.discount) AS total,
                   b.date, b.action, b.source,
                   COALESCE(b.claimed_quantity, 0) as claimed_quantity,
                   b.original_quantity, b.original_date,
                   b.original_cost_price, b.original_selling_price
            FROM purchase_batches b
            JOIN products p ON p.id = b.product_id
            WHERE {date_column}::date BETWEEN %s AND %s
            AND NOT EXISTS (
                SELECT 1 FROM deleted_products dp 
                WHERE dp.product_id = p.id 
                AND dp.action = 'PERMANENTLY DELETED' 
                AND dp.source = 'product'
            )
        """
        params = [start_date, end_date]

        if show_updated_only:
            query += " AND b.action != 'added'"

        query += " ORDER BY b.date DESC"

        cursor.execute(query, params)
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
                "claimed_quantity": r[13] if len(r) > 13 else 0,
                "original_quantity": r[14] if len(r) > 14 else r[4],
                "original_date": r[15] if len(r) > 15 else r[10],
                "original_cost_price": r[16] if len(r) > 16 else r[6],
                "original_selling_price": r[17] if len(r) > 17 else r[8]
            }
            for r in rows
        ]
    except Exception as e:
        print(f"❌ Error in get_purchases_by_date_range: {str(e)}")
        return []
    finally:
        conn.close()


# ============================================================
#  SUGGESTIONS (UPDATED: HIGHER LIMIT FOR "ALL")
# ============================================================

def get_product_suggestions(keyword):
    """
    Search products by name, brand, or batch ID.
    Returns a limited set (1000) – practically all matching results.
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        is_batch_id = False
        try:
            batch_id = int(keyword)
            is_batch_id = True
        except:
            pass

        if is_batch_id:
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
                LIMIT 1000
            """, (batch_id,))
        else:
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
                LIMIT 1000
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
            LIMIT 1000
        """, (f"%{keyword}%",))
        results = cursor.fetchall()
        return [{"category": r[0]} for r in results if r[0]]
    except Exception as e:
        print(f"❌ Error in get_category_suggestions: {str(e)}")
        return []
    finally:
        conn.close()


def get_source_suggestions(keyword):
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
            LIMIT 1000
        """, (f"%{keyword}%",))
        results = cursor.fetchall()
        return [{"source": r[0]} for r in results if r[0]]
    except Exception as e:
        print(f"❌ Error in get_source_suggestions: {str(e)}")
        return []
    finally:
        conn.close()


# ============================================================
#  NEW: UNIFIED SEARCH BY NAME OR BRAND (NO LIMIT – ALL RESULTS)
# ============================================================

def search_products_by_name_or_brand(keyword):
    """
    Search products by name or brand (case-insensitive) – returns ALL matching results.
    Used for the product name / brand autocomplete.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT DISTINCT p.name, p.brand, p.category
            FROM products p
            WHERE (p.name ILIKE %s OR p.brand ILIKE %s)
            AND NOT EXISTS (
                SELECT 1 FROM deleted_products dp
                WHERE dp.product_id = p.id
                AND dp.action IN ('PERMANENTLY DELETED', 'PRODUCT DELETED')
                AND dp.source = 'product'
            )
            ORDER BY p.name ASC
        """, (f'%{keyword}%', f'%{keyword}%'))
        rows = cursor.fetchall()
        return [
            {"name": r[0], "brand": r[1] or "", "category": r[2] or ""}
            for r in rows
        ]
    except Exception as e:
        print(f"❌ Error searching products by name/brand: {str(e)}")
        return []
    finally:
        conn.close()


# ============================================================
#  BATCH FETCH
# ============================================================

def get_batch_by_id(batch_id):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT b.id, p.name, p.brand, p.category,
                   b.quantity, b.remaining_quantity,
                   b.cost_price, b.discount, b.selling_price,
                   (b.cost_price * b.quantity - b.discount) AS total,
                   b.date, b.action, b.source,
                   COALESCE(b.claimed_quantity, 0) as claimed_quantity,
                   b.original_quantity, b.original_date,
                   b.original_cost_price, b.original_selling_price
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
                "claimed_quantity": row[13] if len(row) > 13 else 0,
                "original_quantity": row[14] if len(row) > 14 else row[4],
                "original_date": row[15] if len(row) > 15 else row[10],
                "original_cost_price": row[16] if len(row) > 16 else row[6],
                "original_selling_price": row[17] if len(row) > 17 else row[8]
            }
        return None
    except Exception as e:
        print(f"❌ Error in get_batch_by_id: {str(e)}")
        return None
    finally:
        conn.close()


def get_batch_sold_quantity(batch_id):
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