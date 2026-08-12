// static/js/db.js
const db = new Dexie('OxSmartOfflineDB');

db.version(1).stores({
    // Products (active)
    products: 'id, name, brand, category, stock, cost_price, selling_price, discount, last_sync',

    // Purchase batches (all)
    batches: 'id, product_id, quantity, remaining_quantity, cost_price, selling_price, discount, claimed_quantity, date, action, source, original_quantity, original_date, original_cost_price, original_selling_price, original_discount, last_sync',

    // Sales (non‑reversed)
    sales: 'id, date, subtotal, discount, total, profit, reversed, payment_method, cheque_number, user_id, last_sync',

    // Sales items
    sales_items: 'id, sale_id, product_id, batch_id, quantity, selling_price, cost_price, profit, last_sync',

    // Claims
    claims: 'id, product_id, batch_id, product_name, brand, category, issue_type, description, quantity, status, created_at, updated_at, last_sync',

    // Pending operations (queue for offline writes)
    pending_ops: '++id, operation, table, record_id, payload, timestamp, attempts, synced',

    // Deleted products archive (mirrors the server's deleted_products)
    deleted_products: 'id, name, brand, category, cost_price, selling_price, stock, discount, action, deleted_at, batch_id, batch_quantity, batch_remaining, product_id, source, last_sync'
});

// Export db for use in other scripts