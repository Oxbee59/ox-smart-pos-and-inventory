/* global fetch, sessionStorage, window, document */
(function () {
    const LOW_STOCK_SPLIT = {};

    function escapeHtml(str) {
        if (str === null || str === undefined) return '';
        return String(str).replace(/[&<>]/g, function (m) {
            if (m === '&') return '&amp;';
            if (m === '<') return '&lt;';
            if (m === '>') return '&gt;';
            return m;
        });
    }

    function categoryTargetPage(category) {
        return (category && category.toLowerCase() === 'screen')
            ? '/products/screens'
            : '/products';
    }

    function batchRow(b) {
        const page = categoryTargetPage(b.category);
        return `
            <div class="low-stock-row border-b pb-1 hover:bg-blue-50 p-1 rounded cursor-pointer text-xs"
                 data-nav="${page}"
                 data-product="${escapeHtml(b.name)}"
                 data-category="${escapeHtml(b.category)}"
                 data-batch-id="${b.batch_id}">
                <div class="flex justify-between items-start">
                    <span class="truncate">
                        <strong class="text-indigo-700">${escapeHtml(b.name)}</strong>
                        <span class="text-gray-500">(${escapeHtml(b.brand)})</span>
                        <span class="block text-gray-500">
                            Batch #${b.batch_id} · ${b.remaining_quantity} left · ${escapeHtml(b.category)}
                        </span>
                    </span>
                    <span class="font-semibold text-red-600 whitespace-nowrap">
                        ${b.remaining_quantity}
                    </span>
                </div>
            </div>
        `;
    }

    function productRow(p) {
        const page = categoryTargetPage(p.category);
        return `
            <div class="low-stock-row border-b pb-1 hover:bg-blue-50 p-1 rounded cursor-pointer text-xs"
                 data-nav="${page}"
                 data-product="${escapeHtml(p.name)}"
                 data-category="${escapeHtml(p.category)}">
                <div class="flex justify-between items-start">
                    <span class="truncate">
                        <strong class="text-indigo-700">${escapeHtml(p.name)}</strong>
                        <span class="text-gray-500">(${escapeHtml(p.brand)})</span>
                        <span class="block text-gray-500">
                            ${p.total_remaining} total · ${p.batch_count} batch${p.batch_count !== 1 ? 'es' : ''} · ${escapeHtml(p.category)}
                        </span>
                    </span>
                    <span class="font-semibold text-red-600 whitespace-nowrap">
                        ${p.total_remaining}
                    </span>
                </div>
            </div>
        `;
    }

    function renderSection(title, items, renderer, emptyText) {
        return `
            <div class="mb-3">
                <p class="text-xs font-semibold text-gray-600 mb-1">
                    ${title} (${items.length})
                </p>
                <div class="space-y-1 max-h-48 overflow-y-auto pr-1">
                    ${items.length
                        ? items.map(renderer).join('')
                        : `<div class="text-xs text-gray-400 py-1">${emptyText}</div>`}
                </div>
            </div>
        `;
    }

    function renderSplit(data) {
        const bl = data.batch_level || { low_stock: [], out_of_stock: [] };
        const pl = data.product_level || { low_stock: [], out_of_stock: [] };

        return `
            <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
                <div class="bg-white rounded-lg border border-gray-100 p-3">
                    <h3 class="text-sm font-bold text-indigo-700 mb-2">📦 Batch Level</h3>
                    ${renderSection('⚠️ Low Stock Batches', bl.low_stock, batchRow, 'None')}
                    ${renderSection('📭 Out of Stock Batches', bl.out_of_stock, batchRow, 'None')}
                </div>
                <div class="bg-white rounded-lg border border-gray-100 p-3">
                    <h3 class="text-sm font-bold text-indigo-700 mb-2">📦 Product Level</h3>
                    ${renderSection('⚠️ Low Stock Products', pl.low_stock, productRow, 'None')}
                    ${renderSection('📭 Out of Stock Products', pl.out_of_stock, productRow, 'None')}
                </div>
            </div>
        `;
    }

    function attachClickHandlers(container) {
        container.querySelectorAll('.low-stock-row').forEach(el => {
            el.addEventListener('click', () => {
                const page = el.dataset.nav;
                const product = el.dataset.product;
                const batchId = el.dataset.batchId;

                sessionStorage.setItem('autoOpenProduct', product);
                sessionStorage.setItem('autoOpenBatches', 'true');

                if (batchId) {
                    sessionStorage.setItem('autoHighlightBatch', batchId);
                } else {
                    sessionStorage.removeItem('autoHighlightBatch');
                }

                // Same page → just trigger inline re-open
                if (window.location.pathname === page) {
                    if (typeof window.triggerAutoOpenInline === 'function') {
                        window.triggerAutoOpenInline();
                    } else {
                        window.location.reload();
                    }
                } else {
                    window.location.href = page;
                }
            });
        });
    }

    async function fetchSplit(category) {
        const url = `/api/low_stock/split?category=${encodeURIComponent(category || 'all')}`;
        const res = await fetch(url, { credentials: 'include' });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const json = await res.json();
        if (!json.success) throw new Error(json.error || 'Unknown error');
        return json;
    }

    LOW_STOCK_SPLIT.init = async function (opts) {
        const root = document.getElementById(opts.rootId);
        if (!root) return;

        const body = document.getElementById('lowStockSplitBody');
        const subtitle = document.getElementById('lowStockSplitSubtitle');
        const category = opts.category || root.dataset.category || 'all';

        try {
            const data = await fetchSplit(category);

            if (body) body.innerHTML = renderSplit(data);

            if (subtitle) {
                const catLabel = category === 'all'
                    ? 'Screens + Accessories'
                    : category;
                subtitle.textContent = `${catLabel} · threshold ≤ ${data.threshold}`;
            }

            attachClickHandlers(root);
        } catch (e) {
            console.warn('Low stock split failed:', e);
            if (body) {
                body.innerHTML = `
                    <div class="text-xs text-red-500 text-center py-4">
                        ⚠️ Could not load low stock data. ${escapeHtml(e.message || '')}
                    </div>`;
            }
        }
    };

    window.LowStockSplit = LOW_STOCK_SPLIT;
})();