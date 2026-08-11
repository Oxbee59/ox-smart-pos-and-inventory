// static/js/offline.js

// ==================== SALES ====================

function savePendingSale(saleData) {
    let pending = JSON.parse(localStorage.getItem('pendingSales') || '[]');
    pending.push({
        id: Date.now() + '_' + Math.random().toString(36).substr(2, 5),
        data: saleData,
        timestamp: new Date().toISOString()
    });
    localStorage.setItem('pendingSales', JSON.stringify(pending));
    updatePendingBadge();
}

function getPendingSales() {
    return JSON.parse(localStorage.getItem('pendingSales') || '[]');
}

function removePendingSale(localId) {
    let pending = getPendingSales();
    pending = pending.filter(s => s.id !== localId);
    localStorage.setItem('pendingSales', JSON.stringify(pending));
    updatePendingBadge();
}

async function syncPendingSales() {
    const pending = getPendingSales();
    if (pending.length === 0) return;

    let synced = 0;
    for (const sale of pending) {
        try {
            const response = await fetch('/api/sales/complete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(sale.data)
            });
            if (response.ok) {
                removePendingSale(sale.id);
                synced++;
            } else {
                console.warn(`Failed to sync sale ${sale.id} – status: ${response.status}`);
            }
        } catch (err) {
            console.warn(`Sync error for sale ${sale.id}:`, err);
            break; // stop if network fails
        }
    }
    if (synced > 0) {
        console.log(`✅ ${synced} offline sale(s) synced.`);
    }
    updatePendingBadge();
}

// ==================== PURCHASES ====================

function savePendingPurchase(payload, actionType, batchId = null) {
    let pending = JSON.parse(localStorage.getItem('pendingPurchases') || '[]');
    pending.push({
        id: Date.now() + '_' + Math.random().toString(36).substr(2, 5),
        type: actionType,        // 'add' or 'update'
        batchId: batchId,        // only for updates
        data: payload,
        timestamp: new Date().toISOString()
    });
    localStorage.setItem('pendingPurchases', JSON.stringify(pending));
    updatePendingBadge();
}

function getPendingPurchases() {
    return JSON.parse(localStorage.getItem('pendingPurchases') || '[]');
}

function removePendingPurchase(localId) {
    let pending = getPendingPurchases();
    pending = pending.filter(p => p.id !== localId);
    localStorage.setItem('pendingPurchases', JSON.stringify(pending));
    updatePendingBadge();
}

async function syncPendingPurchases() {
    const pending = getPendingPurchases();
    if (pending.length === 0) return;

    let synced = 0;
    for (const entry of pending) {
        try {
            let url = '/api/purchases';
            let method = 'POST';
            if (entry.type === 'update' && entry.batchId) {
                url = `/api/purchases/${entry.batchId}`;
                method = 'PUT';
            }
            const response = await fetch(url, {
                method: method,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(entry.data)
            });
            if (response.ok) {
                removePendingPurchase(entry.id);
                synced++;
            } else {
                console.warn(`Failed to sync purchase ${entry.id} – status: ${response.status}`);
            }
        } catch (err) {
            console.warn(`Sync error for purchase ${entry.id}:`, err);
            break; // stop if network fails
        }
    }
    if (synced > 0) {
        console.log(`✅ ${synced} offline purchase(s) synced.`);
    }
    updatePendingBadge();
}

// ==================== PRODUCT DELETIONS ====================

function savePendingProductDeletion(productId, deleteType) {
    let pending = JSON.parse(localStorage.getItem('pendingProductDeletions') || '[]');
    pending.push({
        id: Date.now() + '_' + Math.random().toString(36).substr(2, 5),
        productId: productId,
        deleteType: deleteType, // 'keep' or 'clean'
        timestamp: new Date().toISOString()
    });
    localStorage.setItem('pendingProductDeletions', JSON.stringify(pending));
    updatePendingBadge();
}

function getPendingProductDeletions() {
    return JSON.parse(localStorage.getItem('pendingProductDeletions') || '[]');
}

function removePendingProductDeletion(localId) {
    let pending = getPendingProductDeletions();
    pending = pending.filter(item => item.id !== localId);
    localStorage.setItem('pendingProductDeletions', JSON.stringify(pending));
    updatePendingBadge();
}

async function syncPendingProductDeletions() {
    const pending = getPendingProductDeletions();
    if (pending.length === 0) return;

    let synced = 0;
    for (const item of pending) {
        try {
            const response = await fetch(`/api/products/${item.productId}?type=${item.deleteType}`, {
                method: 'DELETE'
            });
            if (response.ok) {
                removePendingProductDeletion(item.id);
                synced++;
            } else {
                console.warn(`Failed to sync product deletion ${item.id} – status: ${response.status}`);
            }
        } catch (err) {
            console.warn(`Sync error for product deletion ${item.id}:`, err);
            break;
        }
    }
    if (synced > 0) {
        console.log(`✅ ${synced} offline product deletion(s) synced.`);
    }
    updatePendingBadge();
}

// ==================== BATCH DELETIONS ====================

function savePendingBatchDeletion(batchId, deleteType) {
    let pending = JSON.parse(localStorage.getItem('pendingBatchDeletions') || '[]');
    pending.push({
        id: Date.now() + '_' + Math.random().toString(36).substr(2, 5),
        batchId: batchId,
        deleteType: deleteType, // 'keep' or 'clean'
        timestamp: new Date().toISOString()
    });
    localStorage.setItem('pendingBatchDeletions', JSON.stringify(pending));
    updatePendingBadge();
}

function getPendingBatchDeletions() {
    return JSON.parse(localStorage.getItem('pendingBatchDeletions') || '[]');
}

function removePendingBatchDeletion(localId) {
    let pending = getPendingBatchDeletions();
    pending = pending.filter(item => item.id !== localId);
    localStorage.setItem('pendingBatchDeletions', JSON.stringify(pending));
    updatePendingBadge();
}

async function syncPendingBatchDeletions() {
    const pending = getPendingBatchDeletions();
    if (pending.length === 0) return;

    let synced = 0;
    for (const item of pending) {
        try {
            const response = await fetch(`/api/batches/${item.batchId}?type=${item.deleteType}`, {
                method: 'DELETE'
            });
            if (response.ok) {
                removePendingBatchDeletion(item.id);
                synced++;
            } else {
                console.warn(`Failed to sync batch deletion ${item.id} – status: ${response.status}`);
            }
        } catch (err) {
            console.warn(`Sync error for batch deletion ${item.id}:`, err);
            break;
        }
    }
    if (synced > 0) {
        console.log(`✅ ${synced} offline batch deletion(s) synced.`);
    }
    updatePendingBadge();
}

// ==================== UNIFIED BADGE ====================

function updatePendingBadge() {
    const salesCount = getPendingSales().length;
    const purchasesCount = getPendingPurchases().length;
    const productDeletionsCount = getPendingProductDeletions().length;
    const batchDeletionsCount = getPendingBatchDeletions().length;
    const total = salesCount + purchasesCount + productDeletionsCount + batchDeletionsCount;

    const badge = document.getElementById('pending-badge');
    if (badge) {
        if (total > 0) {
            badge.textContent = `${total} pending`;
            badge.style.display = 'inline';
            badge.title = 
                `${salesCount} sale(s), ${purchasesCount} purchase(s), ` +
                `${productDeletionsCount} product deletion(s), ${batchDeletionsCount} batch deletion(s) pending sync`;
        } else {
            badge.textContent = '';
            badge.style.display = 'none';
        }
    }
}

// ==================== AUTO-SYNC ON RECONNECT ====================

window.addEventListener('online', function() {
    console.log('🌐 Connection restored – syncing all pending items...');
    syncAllPending();
});

// ==================== SYNC ALL (manual) ====================

async function syncAllPending() {
    if (!navigator.onLine) {
        console.warn('⛔ Offline – skipping sync');
        return;
    }
    console.log('🔄 Syncing all pending data...');
    await syncPendingSales();
    await syncPendingPurchases();
    await syncPendingProductDeletions();
    await syncPendingBatchDeletions();
    updatePendingBadge();
}

// ==================== BADGE UPDATE ON PAGE LOAD ====================

document.addEventListener('DOMContentLoaded', function() {
    updatePendingBadge();
    // If online, sync automatically
    if (navigator.onLine) {
        syncAllPending();
    }
});