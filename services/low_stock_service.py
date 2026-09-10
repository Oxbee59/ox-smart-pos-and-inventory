"""
Split low-stock / out-of-stock data at both batch level and product level.
- Batch level: each purchase_batch row is evaluated independently.
- Product level: total remaining across all batches of a product is evaluated.

Query param `category`:
  - 'Screen'    → only screens
  - 'Accessory' → only accessories
  - 'all' or omitted → both
"""
from database.db import get_connection, return_connection

LOW_STOCK_THRESHOLD = 10


def get_low_stock_split(category=None):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        # Normalise category filter
        cat = (category or 'all').strip()
        if cat.lower() in ('', 'all', 'none'):
            cat_filter = None
        elif cat.lower() == 'screen':
            cat_filter = 'Screen'
        elif cat.lower() in ('accessory', 'accessories'):
            cat_filter = 'Accessory'
        else:
            cat_filter = cat  # pass through as-is

        # ---------- BATCH LEVEL ----------
        batch_query = """
            SELECT
                pb.id                AS batch_id,
                pb.product_id        AS product_id,
                p.name               AS name,
                COALESCE(p.brand,'') AS brand,
                COALESCE(p.category,'') AS category,
                pb.remaining_quantity,
                pb.quantity,
                pb.claimed_quantity
            FROM purchase_batches pb
            JOIN products p ON p.id = pb.product_id
            WHERE NOT EXISTS (
                SELECT 1 FROM deleted_products dp
                WHERE dp.product_id = p.id
                  AND dp.action IN ('PERMANENTLY DELETED', 'PRODUCT DELETED')
                  AND dp.source = 'product'
            )
        """
        batch_params = []
        if cat_filter:
            batch_query += " AND p.category = %s"
            batch_params.append(cat_filter)
        batch_query += " AND pb.remaining_quantity BETWEEN 0 AND %s"
        batch_params.append(LOW_STOCK_THRESHOLD)

        cursor.execute(batch_query, batch_params)
        batch_rows = cursor.fetchall()

        batch_low = []
        batch_out = []
        for r in batch_rows:
            item = {
                'batch_id': r[0],
                'product_id': r[1],
                'name': r[2],
                'brand': r[3],
                'category': r[4],
                'remaining_quantity': int(r[5] or 0),
                'batch_quantity': int(r[6] or 0),
                'claimed_quantity': int(r[7] or 0),
            }
            if item['remaining_quantity'] > 0:
                batch_low.append(item)
            else:
                batch_out.append(item)

        # ---------- PRODUCT LEVEL ----------
        product_query = """
            SELECT
                p.id                  AS product_id,
                p.name,
                COALESCE(p.brand,'')  AS brand,
                COALESCE(p.category,'') AS category,
                COALESCE(SUM(pb.remaining_quantity), 0) AS total_remaining,
                COUNT(pb.id)          AS batch_count
            FROM products p
            LEFT JOIN purchase_batches pb ON pb.product_id = p.id
            WHERE NOT EXISTS (
                SELECT 1 FROM deleted_products dp
                WHERE dp.product_id = p.id
                  AND dp.action IN ('PERMANENTLY DELETED', 'PRODUCT DELETED')
                  AND dp.source = 'product'
            )
        """
        product_params = []
        if cat_filter:
            product_query += " AND p.category = %s"
            product_params.append(cat_filter)
        product_query += """
            GROUP BY p.id, p.name, p.brand, p.category
            HAVING COALESCE(SUM(pb.remaining_quantity), 0) <= %s
               AND COUNT(pb.id) > 0
        """
        product_params.append(LOW_STOCK_THRESHOLD)

        cursor.execute(product_query, product_params)
        product_rows = cursor.fetchall()

        product_low = []
        product_out = []
        for r in product_rows:
            item = {
                'product_id': r[0],
                'name': r[1],
                'brand': r[2],
                'category': r[3],
                'total_remaining': int(r[4] or 0),
                'batch_count': int(r[5] or 0),
            }
            if item['total_remaining'] > 0:
                product_low.append(item)
            else:
                product_out.append(item)

        # Sort for predictable UI
        batch_low.sort(key=lambda x: x['remaining_quantity'])
        batch_out.sort(key=lambda x: (x['name'] or '').lower())
        product_low.sort(key=lambda x: x['total_remaining'])
        product_out.sort(key=lambda x: (x['name'] or '').lower())

        return {
            'category': cat_filter or 'all',
            'threshold': LOW_STOCK_THRESHOLD,
            'batch_level': {
                'low_stock': batch_low,
                'out_of_stock': batch_out,
                'counts': {
                    'low_stock': len(batch_low),
                    'out_of_stock': len(batch_out),
                }
            },
            'product_level': {
                'low_stock': product_low,
                'out_of_stock': product_out,
                'counts': {
                    'low_stock': len(product_low),
                    'out_of_stock': len(product_out),
                }
            }
        }
    finally:
        return_connection(conn)