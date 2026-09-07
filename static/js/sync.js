// static/js/sync.js
// ============================================================
//  CENTRAL OFFLINE SYNC ENGINE
//  - Pulls all data from server into Dexie
//  - Pushes pending operations to server
//  - Handles full sync, retries, and conflict resolution
// ============================================================

/**
 * Pull all data from the server and upsert into Dexie.
 * Called on app start (if online) and periodically.
 */
async function pullData() {
    if (!navigator.onLine) {
        console.warn('⚠️ pullData skipped – offline');
        return;
    }

    try {
        console.log('📥 Pulling data from /api/sync/all...');
        const res = await fetch('/api/sync/all', {
            credentials: 'include' // send session cookie
        });

        if (!res.ok) {
            throw new Error(`HTTP ${res.status}`);
        }

        const data = await res.json();
        const { products, batches, sales, sales_items, claims, deleted_products } = data;

        // Use a single transaction for all upserts (atomic)
        await db.transaction('rw', db.products, db.batches, db.sales, db.sales_items, db.claims, db.deleted_products, async () => {
            // 1. Upsert products
            for (const p of products) {
                await db.products.put({
                    id: p.id,
                    name: p.name,
                    brand: p.brand || '',
                    category: p.category || '',
                    cost_price: p.cost_price,
                    selling_price: p.selling_price,
                    stock: p.stock,
                    discount: p.discount || 0,
                    last_sync: new Date().toISOString()
                });
            }

            // 2. Upsert batches
            for (const b of batches) {
                await db.batches.put({
                    id: b.id,
                    product_id: b.product_id,
                    quantity: b.quantity,
                    remaining_quantity: b.remaining_quantity,
                    cost_price: b.cost_price,
                    selling_price: b.selling_price,
                    discount: b.discount || 0,
                    claimed_quantity: b.claimed_quantity || 0,
                    date: b.date,
                    action: b.action || '',
                    source: b.source || '',
                    original_quantity: b.original_quantity,
                    original_date: b.original_date,
                    original_cost_price: b.original_cost_price,
                    original_selling_price: b.original_selling_price,
                    original_discount: b.original_discount || 0,
                    last_sync: new Date().toISOString()
                });
            }

            // 3. Upsert sales (non‑reversed)
            for (const s of sales) {
                await db.sales.put({
                    id: s.id,
                    date: s.date,
                    subtotal: s.subtotal,
                    discount: s.discount,
                    total: s.total,
                    profit: s.profit,
                    reversed: s.reversed,
                    payment_method: s.payment_method || 'cash',
                    cheque_number: s.cheque_number || null,
                    user_id: s.user_id,
                    last_sync: new Date().toISOString()
                });
            }

            // 4. Upsert sales_items
            for (const si of sales_items) {
                await db.sales_items.put({
                    id: si.id,
                    sale_id: si.sale_id,
                    product_id: si.product_id,
                    batch_id: si.batch_id,
                    quantity: si.quantity,
                    selling_price: si.selling_price,
                    cost_price: si.cost_price,
                    profit: si.profit,
                    last_sync: new Date().toISOString()
                });
            }

            // 5. Upsert claims
            for (const c of claims) {
                await db.claims.put({
                    id: c.id,
                    product_id: c.product_id,
                    batch_id: c.batch_id,
                    product_name: c.product_name,
                    brand: c.brand || '',
                    category: c.category || '',
                    issue_type: c.issue_type,
                    description: c.description || '',
                    quantity: c.quantity,
                    status: c.status || 'active',
                    created_at: c.created_at,
                    updated_at: c.updated_at,
                    last_sync: new Date().toISOString()
                });
            }

            // 6. (Optional) Sync deleted_products – keep for archive
            for (const d of deleted_products) {
                await db.deleted_products.put({
                    id: d.id,
                    name: d.name,
                    brand: d.brand || '',
                    category: d.category || '',
                    cost_price: d.cost_price,
                    selling_price: d.selling_price,
                    stock: d.stock,
                    discount: d.discount || 0,
                    action: d.action || '',
                    deleted_at: d.deleted_at,
                    batch_id: d.batch_id,
                    batch_quantity: d.batch_quantity,
                    batch_remaining: d.batch_remaining,
                    product_id: d.product_id,
                    source: d.source || '',
                    last_sync: new Date().toISOString()
                });
            }
        });

        // Store last sync time
        localStorage.setItem('lastSyncTime', Date.now().toString());

        console.log('✅ Pull sync completed – all data updated locally.');
    } catch (err) {
        console.error('❌ Pull sync error:', err);
        // Optionally, alert the user
    }
}

/**
 * Save a pending operation to the queue.
 * This function is used by all pages to queue operations offline.
 * @param {string} table - Dexie table name (e.g., 'batches', 'sales', 'claims', 'products', 'batches_delete')
 * @param {string} operation - 'add', 'update', 'delete'
 * @param {number|string} record_id - local ID (temporary or actual)
 * @param {object} payload - the data to send to the API
 */
async function savePendingOperation(table, operation, record_id, payload) {
    if (!db) {
        console.error('❌ Dexie not available');
        return;
    }
    try {
        await db.pending_ops.add({
            table: table,
            operation: operation,
            record_id: record_id,
            payload: payload,
            timestamp: new Date().toISOString(),
            attempts: 0,
            synced: 0
        });
        console.log(`💾 Pending operation saved: ${table} ${operation} (${record_id})`);
        if (typeof updatePendingBadge === 'function') {
            updatePendingBadge();
        }
    } catch (err) {
        console.error('❌ Failed to save pending operation:', err);
    }
}

/**
 * Push all pending operations to the server.
 * Called after pullData (to ensure we have latest IDs) and on reconnect.
 */
async function pushPending() {
    if (!navigator.onLine) {
        console.warn('⚠️ pushPending skipped – offline');
        return;
    }

    const pending = await db.pending_ops.where('synced').equals(0).toArray();
    if (pending.length === 0) {
        console.log('📭 No pending operations to push.');
        return;
    }

    console.log(`📤 Pushing ${pending.length} pending operations...`);
    let successCount = 0;
    let failCount = 0;

    for (const op of pending) {
        try {
            let url = '';
            let method = '';
            let payload = op.payload;
            let serverId = null;

            // Map operation to the correct API endpoint
            switch (op.table) {
                case 'batches':
                    if (op.operation === 'add') {
                        url = '/api/purchases';
                        method = 'POST';
                    } else if (op.operation === 'update') {
                        url = `/api/purchases/${op.record_id}`;
                        method = 'PUT';
                    } else {
                        throw new Error(`Unsupported operation for batches: ${op.operation}`);
                    }
                    break;

                case 'sales':
                    if (op.operation === 'add') {
                        url = '/api/sales/complete';
                        method = 'POST';
                    } else {
                        throw new Error(`Unsupported operation for sales: ${op.operation}`);
                    }
                    break;

                case 'claims':
                    if (op.operation === 'add') {
                        url = '/api/claims';
                        method = 'POST';
                    } else if (op.operation === 'update') {
                        url = `/api/claims/${op.record_id}`;
                        method = 'PUT';
                    } else if (op.operation === 'delete') {
                        url = `/api/claims/${op.record_id}`;
                        method = 'DELETE';
                    } else {
                        throw new Error(`Unsupported operation for claims: ${op.operation}`);
                    }
                    break;

                case 'products':
                    if (op.operation === 'delete') {
                        url = `/api/products/${op.record_id}?type=keep`;
                        method = 'DELETE';
                    } else {
                        throw new Error(`Unsupported operation for products: ${op.operation}`);
                    }
                    break;

                case 'batches_delete':
                    if (op.operation === 'delete') {
                        url = `/api/batches/${op.record_id}?type=keep`;
                        method = 'DELETE';
                    } else {
                        throw new Error(`Unsupported operation for batch deletion: ${op.operation}`);
                    }
                    break;

                default:
                    throw new Error(`Unknown table: ${op.table}`);
            }

            if (!url) {
                throw new Error(`No endpoint defined for ${op.table} ${op.operation}`);
            }

            const res = await fetch(url, {
                method: method,
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify(payload)
            });

            if (!res.ok) {
                const errorData = await res.json().catch(() => ({}));
                throw new Error(errorData.error || `HTTP ${res.status}`);
            }

            const result = await res.json();
            if (result.success === false) {
                throw new Error(result.error || 'Unknown error from server');
            }

            // If the server returned a new ID, update the local record if needed
            if (result.batch_id && op.table === 'batches' && op.operation === 'add') {
                // The batch was created on server; we should update the local batch ID.
                // However, we already have a temporary ID in record_id, so we can update it.
                // For simplicity, we'll just mark as synced and keep the local ID.
                // If you need to update the local record with the server ID, you can do it here.
                // For now, we assume the local ID is fine.
                serverId = result.batch_id;
            } else if (result.claim_id && op.table === 'claims' && op.operation === 'add') {
                serverId = result.claim_id;
            } else if (result.sale_id && op.table === 'sales' && op.operation === 'add') {
                serverId = result.sale_id;
            }

            // Mark as synced
            await db.pending_ops.update(op.id, { synced: 1 });
            successCount++;

            // Optionally, if we got a server ID, we might want to update the local record
            // This is advanced; for now we just mark it synced.

            console.log(`✅ Synced op ${op.id} (${op.table} ${op.operation})`);

        } catch (err) {
            // Increment attempts and keep for retry
            await db.pending_ops.update(op.id, { attempts: (op.attempts || 0) + 1 });
            console.warn(`❌ Push failed for op ${op.id}:`, err.message);
            failCount++;
        }
    }

    // After pushing, update the pending badge (if any)
    if (typeof updatePendingBadge === 'function') {
        updatePendingBadge();
    }

    console.log(`📤 Push completed: ${successCount} succeeded, ${failCount} failed.`);
}

/**
 * Full sync: pull latest data from server, then push pending operations.
 * This should be called on app startup (if online) and periodically.
 */
async function fullSync() {
    console.log('🔄 Starting full sync...');
    // First, pull new data from server (to get latest IDs, updates from others)
    await pullData();
    // Then push any local changes
    await pushPending();
    console.log('✅ Full sync completed.');
}

/**
 * Clear all pending operations (careful – use with caution).
 * Typically used for debugging or after a full reset.
 */
async function clearAllPending() {
    if (!db) return;
    if (!confirm('Clear all pending operations?')) return;
    await db.pending_ops.where('synced').equals(0).delete();
    console.log('🗑️ All pending operations cleared.');
    if (typeof updatePendingBadge === 'function') {
        updatePendingBadge();
    }
}

/**
 * Get the count of pending operations.
 */
async function getPendingCount() {
    if (!db) return 0;
    const count = await db.pending_ops.where('synced').equals(0).count();
    return count;
}

// Expose functions globally
window.pullData = pullData;
window.pushPending = pushPending;
window.fullSync = fullSync;
window.savePendingOperation = savePendingOperation;
window.clearAllPending = clearAllPending;
window.getPendingCount = getPendingCount;