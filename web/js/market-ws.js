// ========================================
// market-ws.js - WebSocket + Auto-Refresh + Cleanup
// ========================================

// Import chart functions from market-chart.js (market-chart.js handles window.showChart, etc.)
import { showChart, closeChart, changeChartInterval, getPriceDecimals, updateOHLCVDisplay, setAutoRefreshStatus } from './market-chart.js';

function formatPrice(price) {
    const p = parseFloat(price);
    if (p >= 1000)
        return p.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    if (p >= 1) return p.toFixed(2);
    if (p >= 0.01) return p.toFixed(4);
    if (p >= 0.0001) return p.toFixed(6);
    return p.toFixed(8);
}

// 24h 漲跌幅：以畫面上的兩位小數判斷顏色——0、"0.00"、-0、-0.004 都顯示 0.00% 中性色
// （以前 change >= 0 一律標綠，字串還會讓 toFixed 丟例外）；非數字回 null 不更新
function formatChange24h(change) {
    const n = parseFloat(change);
    if (!Number.isFinite(n)) return null;
    const shown = parseFloat(n.toFixed(2));
    if (shown > 0) return { text: `+${shown.toFixed(2)}%`, cls: 'text-success' };
    if (shown < 0) return { text: `${shown.toFixed(2)}%`, cls: 'text-danger' };
    return { text: '0.00%', cls: 'text-textMuted' };
}

// ========================================
// WebSocket 即時更新功能
// ========================================

function getWebSocketUrl() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    // 0.0.0.0 is a server bind address; browsers should connect to localhost instead
    const host = window.location.host.replace(/^0\.0\.0\.0/, 'localhost');
    return `${protocol}//${host}/ws/klines`;
}

function connectWebSocket() {
    if (window.wsReconnectTimer) {
        clearTimeout(window.wsReconnectTimer);
        window.wsReconnectTimer = null;
    }
    if (window.klineWebSocket && window.klineWebSocket.readyState <= WebSocket.OPEN) {
        return;
    }

    const wsUrl = getWebSocketUrl();
    console.log(window.I18n.t('ws.connecting', { url: wsUrl }) || ('連接 WebSocket: ' + wsUrl));

    try {
        window.klineWebSocket = new WebSocket(wsUrl);

        window.klineWebSocket.onopen = () => {
            console.log(window.I18n.t('ws.connected') || 'WebSocket 連接成功');
            window.wsConnected = true;
            updateWsStatus(true);
            // Reset reconnect attempts on successful connection
            window.reconnectAttempts = 0;

            // 如果已經有訂閱，重新訂閱
            if (window.currentChartSymbol && window.autoRefreshEnabled) {
                subscribeKline(window.currentChartSymbol, window.currentChartInterval);
            }

            // Start live time updates when WebSocket is connected and auto-refresh is enabled
            if (window.autoRefreshEnabled) {
                startLiveTimeUpdates();
            }
        };

        window.klineWebSocket.onmessage = (event) => {
            try {
                const message = JSON.parse(event.data);
                handleWebSocketMessage(message);
            } catch (e) {
                console.error(window.I18n.t('ws.parseError') || 'WebSocket 消息解析錯誤:', e);
            }
        };

        window.klineWebSocket.onclose = (event) => {
            console.log(window.I18n.t('ws.closed') || 'WebSocket 連接關閉:', event.code, event.reason);
            window.wsConnected = false;
            updateWsStatus(false);
            stopLiveTimeUpdates(); // Stop live time updates when disconnected

            // 自動重連 - 限制最大重連次數
            if (window.autoRefreshEnabled && window.reconnectAttempts < window.MAX_RECONNECT_ATTEMPTS) {
                window.reconnectAttempts++;
                console.log(
                    (window.I18n ? window.I18n.t('market.reconnectingWebSocket') : 'Trying to reconnect WebSocket...') + ` (${window.reconnectAttempts}/${window.MAX_RECONNECT_ATTEMPTS})`
                );
                window.wsReconnectTimer = setTimeout(() => {
                    connectWebSocket();
                }, 3000);
            } else if (window.reconnectAttempts >= window.MAX_RECONNECT_ATTEMPTS) {
                console.error(window.I18n.t('ws.reconnectLimitReached') || 'WebSocket 重連次數達到上限，停止重連');
                window.autoRefreshEnabled = false;
                updateAutoRefreshButton();
            }
        };

        window.klineWebSocket.onerror = (error) => {
            console.error(window.I18n.t('ws.error') || 'WebSocket 錯誤:', error);
            window.wsConnected = false;
            updateWsStatus(false);
        };
    } catch (e) {
        console.error(window.I18n.t('ws.connectFailed') || 'WebSocket 連接失敗:', e);
        window.wsConnected = false;
        updateWsStatus(false);
        if (typeof showToast === 'function') showToast(window.I18n.t('market.liveConnectionFailed'), 'warning');
    }
}

function disconnectWebSocket() {
    if (window.wsReconnectTimer) {
        clearTimeout(window.wsReconnectTimer);
        window.wsReconnectTimer = null;
    }

    if (window.klineWebSocket) {
        window.klineWebSocket.close();
        window.klineWebSocket = null;
    }
    window.wsConnected = false;
}

function subscribeKline(symbol, interval) {
    if (!window.klineWebSocket || window.klineWebSocket.readyState !== WebSocket.OPEN) {
        console.warn(window.I18n.t('ws.notConnectedSubscribe') || 'WebSocket 未連接，無法訂閱');
        return;
    }

    window.klineWebSocket.send(
        JSON.stringify({
            action: 'subscribe',
            symbol: symbol,
            interval: interval,
        })
    );
    console.log(window.I18n.t('ws.subscribe', { symbol, interval }) || `訂閱: ${symbol} ${interval}`);
}

function unsubscribeKline() {
    if (!window.klineWebSocket || window.klineWebSocket.readyState !== WebSocket.OPEN) {
        return;
    }

    window.klineWebSocket.send(JSON.stringify({ action: 'unsubscribe' }));
}

function handleWebSocketMessage(message) {
    switch (message.type) {
        case 'kline':
            updateChartWithKline(message.data);
            break;
        case 'subscribed':
            console.log(window.I18n.t('ws.subscribed', { symbol: message.symbol, interval: message.interval }) || `已訂閱: ${message.symbol} ${message.interval}`);
            break;
        case 'unsubscribed':
            console.log(window.I18n.t('ws.unsubscribed') || '已取消訂閱');
            break;
        case 'pong':
            // 心跳回應
            break;
        case 'error':
            console.error(window.I18n.t('ws.error') || 'WebSocket 錯誤:', message.message);
            break;
    }
}

function updateChartWithKline(kline) {
    if (!window.chart || !window.candleSeries) return;

    // 更新或添加 K 線
    const klineData = {
        time: kline.time,
        open: kline.open,
        high: kline.high,
        low: kline.low,
        close: kline.close,
    };

    // 使用 update 方法更新最新 K 線
    window.candleSeries.update(klineData);

    // 更新成交量
    if (window.volumeSeries && kline.volume !== undefined) {
        window.volumeSeries.update({
            time: kline.time,
            value: kline.volume,
            color: kline.close >= kline.open ? 'rgba(34, 197, 94, 0.3)' : 'rgba(239, 68, 68, 0.3)',
        });
    }

    // 更新大尺寸當前價格顯示
    const currentPriceDisplay = document.getElementById('current-price-display');
    if (currentPriceDisplay) {
        const priceDecimals = getPriceDecimals(kline.close);
        const formattedPrice = kline.close.toFixed(priceDecimals);
        const isUp = kline.close >= kline.open;
        currentPriceDisplay.textContent = `$${formattedPrice}`;
        currentPriceDisplay.className = `text-center text-2xl font-bold font-mono ${isUp ? 'text-success' : 'text-danger'}`;
    }

    // 更新時間顯示
    const updatedEl = document.getElementById('chart-updated');
    if (updatedEl) {
        const now = new Date();
        const timeStr = now.toLocaleTimeString(AppUtils.locale(), {
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
        });
        updatedEl.dataset.live = '1';
        updatedEl.textContent = window.I18n.t('common.liveAt', { time: timeStr });
    }

    // 更新 OHLCV 顯示 (僅在圖表未被懸停時)
    if (!window.isChartHovered) {
        const priceDecimals = getPriceDecimals(kline.close);
        updateOHLCVDisplay(
            kline,
            document.getElementById('info-open'),
            document.getElementById('info-high'),
            document.getElementById('info-low'),
            document.getElementById('info-close'),
            document.getElementById('info-volume'),
            priceDecimals
        );
    }

    // 更新存儲的 K 線數據，以便在未懸停時顯示最新數據
    if (window.currentChartKlines && window.currentChartKlines.length > 0) {
        // 更新最後一根 K 線
        const lastIdx = window.currentChartKlines.length - 1;
        if (window.currentChartKlines[lastIdx].time === kline.time) {
            window.currentChartKlines[lastIdx] = kline;
        } else {
            // 如果是新時間的 K 線，添加到末尾
            window.currentChartKlines.push(kline);
            // 保持最多 200 根 K 線
            if (window.currentChartKlines.length > 200) {
                window.currentChartKlines.shift();
            }
        }
    }
}

function updateWsStatus(connected) {
    const btn = document.getElementById('auto-refresh-btn');

    if (connected && window.autoRefreshEnabled) {
        if (btn) {
            btn.classList.add('text-success', 'bg-success/10');
            btn.classList.remove('text-primary', 'bg-primary/10', 'text-textMuted');
        }
        setAutoRefreshStatus('marketStatus.live', 'LIVE');
    } else if (window.autoRefreshEnabled) {
        if (btn) {
            btn.classList.add('text-amber-600', 'bg-amber-500/10');
            btn.classList.remove('text-success', 'bg-success/10', 'text-textMuted');
        }
        setAutoRefreshStatus('marketStatus.connectingDots', '...');
    }
}

function updateAutoRefreshButton() {
    const btn = document.getElementById('auto-refresh-btn');

    if (!window.autoRefreshEnabled) {
        if (btn) {
            btn.classList.remove(
                'text-success',
                'bg-success/10',
                'text-primary',
                'bg-primary/10',
                'text-amber-600',
                'bg-amber-500/10'
            );
            btn.classList.add('text-textMuted');
        }
        setAutoRefreshStatus('marketStatus.off', 'OFF');
    } else if (window.wsConnected) {
        updateWsStatus(true);
    } else {
        updateWsStatus(false);
    }
}

// Timer for updating live time display
let liveTimeUpdateTimer = null;

function startLiveTimeUpdates() {
    if (liveTimeUpdateTimer) {
        clearInterval(liveTimeUpdateTimer);
    }

    liveTimeUpdateTimer = setInterval(() => {
        const updatedEl = document.getElementById('chart-updated');
        if (updatedEl && window.wsConnected && window.autoRefreshEnabled) {
            const now = new Date();
            const timeStr = now.toLocaleTimeString(AppUtils.locale(), {
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit',
            });
            updatedEl.dataset.live = '1';
        updatedEl.textContent = window.I18n.t('common.liveAt', { time: timeStr });
        }
    }, 1000); // Update every second
}

function stopLiveTimeUpdates() {
    if (liveTimeUpdateTimer) {
        clearInterval(liveTimeUpdateTimer);
        liveTimeUpdateTimer = null;
    }
}

// 自動更新功能（使用 WebSocket）
function toggleAutoRefresh() {
    window.autoRefreshEnabled = !window.autoRefreshEnabled;
    const btn = document.getElementById('auto-refresh-btn');

    if (window.autoRefreshEnabled) {
        btn.classList.add('text-primary', 'bg-primary/10');
        btn.classList.remove('text-textMuted');
        setAutoRefreshStatus('chat.connecting', 'Connecting...');
        startAutoRefresh();
    } else {
        btn.classList.remove(
            'text-primary',
            'bg-primary/10',
            'text-success',
            'bg-success/10',
            'text-amber-600',
            'bg-amber-500/10'
        );
        btn.classList.add('text-textMuted');
        setAutoRefreshStatus('marketStatus.off', 'OFF');
        stopAutoRefresh();
    }
}

function startAutoRefresh() {
    // 連接 WebSocket
    connectWebSocket();

    // 訂閱當前幣種
    if (window.currentChartSymbol) {
        // Clear any existing connection check timer
        if (window.wsConnectionCheckTimer) {
            clearInterval(window.wsConnectionCheckTimer);
            window.wsConnectionCheckTimer = null;
        }

        // 等待連接建立後訂閱
        window.wsConnectionCheckTimer = setInterval(() => {
            if (window.wsConnected) {
                subscribeKline(window.currentChartSymbol, window.currentChartInterval);
                clearInterval(window.wsConnectionCheckTimer);
                window.wsConnectionCheckTimer = null;
                // Start live time updates when connection is established
                startLiveTimeUpdates();
            }
        }, 100);

        // 5秒後停止檢查
        setTimeout(() => {
            if (window.wsConnectionCheckTimer) {
                clearInterval(window.wsConnectionCheckTimer);
                window.wsConnectionCheckTimer = null;
            }
        }, 5000);
    }
}

function stopAutoRefresh() {
    unsubscribeKline();
    disconnectWebSocket();
    stopLiveTimeUpdates(); // Stop live time updates when auto-refresh stops

    window.autoRefreshEnabled = false;
    const btn = document.getElementById('auto-refresh-btn');
    if (btn) {
        btn.classList.remove(
            'text-primary',
            'bg-primary/10',
            'text-success',
            'bg-success/10',
            'text-amber-600',
            'bg-amber-500/10'
        );
        btn.classList.add('text-textMuted');
    }
    setAutoRefreshStatus('marketStatus.off', 'OFF');
}

// 保留輪詢作為備用方案
async function refreshChartData() {
    if (!window.currentChartSymbol || !window.chart || !window.candleSeries) return;

    try {
        const data = await AppAPI.post('/api/klines', {
                symbol: window.currentChartSymbol,
                interval: window.currentChartInterval,
                limit: 200,
            });

        window.candleSeries.setData(data.klines);
        window.chartKlinesData = data.klines;

        if (window.volumeSeries) {
            const volumeData = data.klines.map((k) => ({
                time: k.time,
                value: k.volume || 0,
                color: k.close >= k.open ? 'rgba(34, 197, 94, 0.3)' : 'rgba(239, 68, 68, 0.3)',
            }));
            window.volumeSeries.setData(volumeData);
        }

        const updatedEl = document.getElementById('chart-updated');
        if (updatedEl) {
            const now = new Date();
            const timeStr = now.toLocaleTimeString(AppUtils.locale(), {
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit',
            });
            updatedEl.dataset.live = '0';
            updatedEl.textContent = window.I18n.t('common.updatedAt', { time: timeStr });
        }

        const lastKline = data.klines[data.klines.length - 1];
        if (lastKline) {
            const priceDecimals = getPriceDecimals(lastKline.close);
            // 更新 OHLCV 顯示 (僅在圖表未被懸停時)
            if (!window.isChartHovered) {
                updateOHLCVDisplay(
                    lastKline,
                    document.getElementById('info-open'),
                    document.getElementById('info-high'),
                    document.getElementById('info-low'),
                    document.getElementById('info-close'),
                    document.getElementById('info-volume'),
                    priceDecimals
                );

                // 更新大尺寸當前價格顯示 (僅在圖表未被懸停時)
                const currentPriceDisplay = document.getElementById('current-price-display');
                if (currentPriceDisplay) {
                    const formattedPrice = lastKline.close.toFixed(priceDecimals);
                    const isUp = lastKline.close >= lastKline.open;
                    currentPriceDisplay.textContent = `$${formattedPrice}`;
                    currentPriceDisplay.className = `text-center text-2xl font-bold font-mono ${isUp ? 'text-success' : 'text-danger'}`;
                }
            }
        }
    } catch (err) {
        console.error('[Market] refreshChartData failed:', err);
        if (typeof showToast === 'function') showToast(window.I18n.t('market.chartDataUpdateFailed'), 'error');
    }
}

// ========================================
// Market Watch Ticker WebSocket
// ========================================

function getTickerWebSocketUrl() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    // 0.0.0.0 is a server bind address; browsers should connect to localhost instead
    const host = window.location.host.replace(/^0\.0\.0\.0/, 'localhost');
    return `${protocol}//${host}/ws/tickers`;
}

function connectTickerWebSocket() {
    if (window.tickerReconnectTimer) {
        clearTimeout(window.tickerReconnectTimer);
        window.tickerReconnectTimer = null;
    }
    if (window.marketWebSocket && window.marketWebSocket.readyState <= WebSocket.OPEN) {
        return;
    }

    const wsUrl = getTickerWebSocketUrl();
    console.log(window.I18n.t('ws.connectingTicker', { url: wsUrl }) || ('連接 Ticker WebSocket: ' + wsUrl));

    try {
        window.marketWebSocket = new WebSocket(wsUrl);

        window.marketWebSocket.onopen = () => {
            // same lazy guard as subscribeTickerSymbols
            window.pendingTickerSymbols = window.pendingTickerSymbols || new Set();
            window.subscribedTickerSymbols = window.subscribedTickerSymbols || new Set();
            console.log(window.I18n.t('ws.tickerConnected') || 'Ticker WebSocket 連接成功');
            window.marketWsConnected = true;
            updateTickerWsStatus(true);
            // Reset reconnect attempts on successful connection
            window.tickerReconnectAttempts = 0;

            // 訂閱等待中的 symbols
            if (window.pendingTickerSymbols.size > 0) {
                const symbols = Array.from(window.pendingTickerSymbols);
                window.pendingTickerSymbols.clear();
                subscribeTickerSymbols(symbols);
            }

            // 重新訂閱之前的 symbols
            if (window.subscribedTickerSymbols.size > 0) {
                const symbols = Array.from(window.subscribedTickerSymbols);
                window.subscribedTickerSymbols.clear(); // 清空以便重新添加
                subscribeTickerSymbols(symbols);
            }
        };

        window.marketWebSocket.onmessage = (event) => {
            try {
                const message = JSON.parse(event.data);
                handleTickerMessage(message);
            } catch (e) {
                console.error(window.I18n.t('ws.tickerParseError') || 'Ticker WebSocket 消息解析錯誤:', e);
            }
        };

        window.marketWebSocket.onclose = (event) => {
            console.log(window.I18n.t('ws.tickerClosed') || 'Ticker WebSocket 連接關閉:', event.code);
            window.marketWsConnected = false;
            updateTickerWsStatus(false);

            // 自動重連 - 限制最大重連次數
            if (window.tickerReconnectAttempts < window.MAX_TICKET_RECONNECT_ATTEMPTS) {
                window.tickerReconnectAttempts++;
                console.log(
                    (window.I18n ? window.I18n.t('market.reconnectingTickerWebSocket') : 'Trying to reconnect Ticker WebSocket...') + ` (${window.tickerReconnectAttempts}/${window.MAX_TICKET_RECONNECT_ATTEMPTS})`
                );
                window.tickerReconnectTimer = setTimeout(() => {
                    connectTickerWebSocket();
                }, 3000);
            } else {
                console.error(window.I18n.t('ws.tickerReconnectLimitReached') || 'Ticker WebSocket 重連次數達到上限，停止重連');
            }
        };

        window.marketWebSocket.onerror = (error) => {
            console.error(window.I18n.t('ws.tickerError') || 'Ticker WebSocket 錯誤:', error);
            window.marketWsConnected = false;
            updateTickerWsStatus(false);
        };
    } catch (e) {
        console.error(window.I18n.t('ws.tickerConnectFailed') || 'Ticker WebSocket 連接失敗:', e);
        window.marketWsConnected = false;
        updateTickerWsStatus(false);
        if (typeof showToast === 'function') showToast(window.I18n.t('market.tickerConnectionFailed'), 'warning');
    }
}

function disconnectTickerWebSocket() {
    if (window.tickerReconnectTimer) {
        clearTimeout(window.tickerReconnectTimer);
        window.tickerReconnectTimer = null;
    }

    if (window.marketWebSocket) {
        window.marketWebSocket.close();
        window.marketWebSocket = null;
    }
    window.marketWsConnected = false;
    window.subscribedTickerSymbols.clear();
}

function subscribeTickerSymbols(symbols) {
    // SW mixed-cache guard: the declaring module may not have run (2026-09-07)
    window.pendingTickerSymbols = window.pendingTickerSymbols || new Set();
    window.subscribedTickerSymbols = window.subscribedTickerSymbols || new Set();
    // 過濾已訂閱的
    const newSymbols = symbols.filter((s) => !window.subscribedTickerSymbols.has(s.toUpperCase()));
    if (newSymbols.length === 0) return;

    if (!window.marketWebSocket || window.marketWebSocket.readyState !== WebSocket.OPEN) {
        // WebSocket 未連接，加入等待列表
        newSymbols.forEach((s) => window.pendingTickerSymbols.add(s.toUpperCase()));
        console.log(window.I18n.t('ws.tickerQueued', { symbols: newSymbols.join(', ') }) || `Ticker WebSocket 未連接，已排隊等待: ${newSymbols.join(', ')}`);
        return;
    }

    window.marketWebSocket.send(
        JSON.stringify({
            action: 'subscribe',
            symbols: newSymbols,
        })
    );

    newSymbols.forEach((s) => window.subscribedTickerSymbols.add(s.toUpperCase()));
    console.log(window.I18n.t('ws.tickerSubscribe', { symbols: newSymbols.join(', ') }) || `訂閱 Ticker: ${newSymbols.join(', ')}`);
}

function unsubscribeTickerSymbols(symbols) {
    if (!window.marketWebSocket || window.marketWebSocket.readyState !== WebSocket.OPEN) {
        return;
    }

    window.marketWebSocket.send(
        JSON.stringify({
            action: 'unsubscribe',
            symbols: symbols,
        })
    );

    symbols.forEach((s) => window.subscribedTickerSymbols.delete(s.toUpperCase()));
}

function handleTickerMessage(message) {
    switch (message.type) {
        case 'ticker':
            // console.log(`收到 Ticker 更新: ${message.symbol}`, message.data);
            updateMarketWatchItem(message.symbol, message.data);
            break;
        case 'subscribed':
            console.log(window.I18n.t('ws.tickerSubscribed', { symbols: message.symbols.join(', ') }) || `✅ 已訂閱 Ticker: ${message.symbols.join(', ')}`);
            break;
        case 'unsubscribed':
            console.log(window.I18n.t('ws.tickerUnsubscribed') || '已取消訂閱 Ticker');
            break;
        case 'unsubscribed_all':
            console.log(window.I18n.t('ws.tickerUnsubscribedAll') || '已取消所有 Ticker 訂閱');
            break;
        case 'pong':
            break;
        case 'error':
            console.error(window.I18n.t('ws.tickerError') || 'Ticker WebSocket 錯誤:', message.message);
            break;
        default:
            console.log(window.I18n.t('ws.tickerUnknownMessage') || '未知 Ticker 消息:', message);
    }
}

function updateMarketWatchItem(symbol, ticker) {
    // 尋找所有顯示該 symbol 的元素並更新價格
    // symbol 可能是 "BTC-USDT", "BTC", "BTCUSDT" 等格式
    // 統一處理為不帶後綴的格式
    const normalizedSymbol = symbol
        .toUpperCase()
        .replace('-USDT', '')
        .replace('USDT', '')
        .replace('-', '');

    // 更新所有列表中的價格
    const containers = ['top-list', 'oversold-list', 'overbought-list'];
    containers.forEach((containerId) => {
        const container = document.getElementById(containerId);
        if (!container) return;

        // 查找包含該 symbol 的卡片
        const cards = container.querySelectorAll('[data-symbol]');

        cards.forEach((card) => {
            const cardSymbol = (card.dataset.symbol || '')
                .toUpperCase()
                .replace('-USDT', '')
                .replace('USDT', '')
                .replace('-', '');

            if (cardSymbol && cardSymbol === normalizedSymbol) {
                // 更新價格
                const priceEl = card.querySelector('.ticker-price');
                if (priceEl && ticker.last) {
                    const oldPrice = priceEl.textContent;
                    const newPrice = `$${formatPrice(ticker.last)}`;

                    if (oldPrice !== newPrice) {
                        priceEl.textContent = newPrice;
                        // 閃爍效果
                        priceEl.classList.add('price-flash');
                        setTimeout(() => priceEl.classList.remove('price-flash'), 300);
                    }
                }

                // 更新 24h 漲跌幅——只有 top-list 的 .ticker-change 是漲跌幅；
                // oversold／overbought 清單那格放的是 RSI（renderList key='RSI_14'），tick 只更新價格
                const changeEl = containerId === 'top-list' ? card.querySelector('.ticker-change') : null;
                const change = changeEl ? formatChange24h(ticker.change24h) : null;
                if (change) {
                    changeEl.textContent = change.text;
                    // 只換顏色，不重設整個 className（以前會洗掉 renderList 給的 font-mono）
                    changeEl.classList.remove('text-success', 'text-danger', 'text-textMuted');
                    changeEl.classList.add(change.cls);
                }
            }
        });
    });
}

function updateTickerWsStatus(connected) {
    const indicator = document.getElementById('ticker-ws-indicator');
    if (indicator) {
        if (connected) {
            indicator.classList.add('bg-success');
            indicator.classList.remove('bg-gray-500');
            indicator.title = window.I18n.t('market.wsConnected');
        } else {
            indicator.classList.remove('bg-success');
            indicator.classList.add('bg-gray-500');
            indicator.title = window.I18n.t('market.wsDisconnected');
        }
    }
}

// 自動啟動 Ticker WebSocket (當頁面在 Market Watch 標籤時)
function initTickerWebSocket() {
    // 延遲啟動，等待頁面載入
    setTimeout(() => {
        connectTickerWebSocket();
    }, 1000);
}

// Make globally accessible
window.toggleAutoRefresh = toggleAutoRefresh;
window.connectTickerWebSocket = connectTickerWebSocket;
window.disconnectTickerWebSocket = disconnectTickerWebSocket;
window.subscribeTickerSymbols = subscribeTickerSymbols;
// 處理瀏覽器分頁切換 (Visibility Change)
document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
        console.log('[Market] Tab hidden, pausing updates to save resources.');
        // Optionally clear interval if we wanted to be very strict,
        // but app.js handles the main interval.
        // We just note it here.
    } else {
        console.log('[Market] Tab visible, checking for stale data...');
        // 如果當前在 Market Watch 頁面，且數據可能過期，則刷新
        // 檢查是否在前台
        const marketTab = document.getElementById('market-content');
        if (marketTab && !marketTab.classList.contains('hidden')) {
            // 延遲一點點確保網路恢復
            setTimeout(() => {
                console.log('[Market] Resuming updates, forcing refresh...');
                refreshScreener(false, true); // Silent refresh, force update
            }, 500);
        }
    }
});

// ========================================
// Cleanup function for memory leak prevention
// ========================================
function cleanupMarketResources() {
    console.log('[Market] Cleaning up resources...');

    // Clear live time update timer
    if (liveTimeUpdateTimer) {
        clearInterval(liveTimeUpdateTimer);
        liveTimeUpdateTimer = null;
    }

    // Clear WebSocket reconnect timers
    if (window.wsReconnectTimer) {
        clearTimeout(window.wsReconnectTimer);
        window.wsReconnectTimer = null;
    }

    if (window.wsConnectionCheckTimer) {
        clearInterval(window.wsConnectionCheckTimer);
        window.wsConnectionCheckTimer = null;
    }

    if (window.tickerReconnectTimer) {
        clearTimeout(window.tickerReconnectTimer);
        window.tickerReconnectTimer = null;
    }

    // Remove resize event listener
    if (window._marketResizeHandler) {
        window.removeEventListener('resize', window._marketResizeHandler);
        window._marketResizeHandler = null;
    }
    // Disconnect chart ResizeObserver
    if (window._marketResizeObserver) {
        window._marketResizeObserver.disconnect();
        window._marketResizeObserver = null;
    }

    // Close WebSocket connections
    if (window.klineWebSocket) {
        window.klineWebSocket.close();
        window.klineWebSocket = null;
    }

    if (window.marketWebSocket) {
        window.marketWebSocket.close();
        window.marketWebSocket = null;
    }

    // Reset reconnect attempts
    window.reconnectAttempts = 0;
    window.tickerReconnectAttempts = 0;

    console.log('[Market] Resources cleaned up');
}

// Register cleanup on page hide (not beforeunload — pagehide works better
// with BFCache: fires when the page is actually discarded, not just navigated)
window.addEventListener('pagehide', cleanupMarketResources);

// Also cleanup on visibility change when leaving the page
document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') {
        // Optional: cleanup when tab becomes hidden
        // cleanupMarketResources();
    }
});

export { toggleAutoRefresh, connectTickerWebSocket, disconnectTickerWebSocket, subscribeTickerSymbols, cleanupMarketResources, updateMarketWatchItem };

window.startAutoRefresh = startAutoRefresh;
// 以下函式定義於本模組，但 market-chart.js 會跨模組呼叫它們；
// 比照 startAutoRefresh，透過 window 暴露（ES module 頂層函式並非全域，
// 裸呼叫會在另一個模組丟 ReferenceError）。
window.updateWsStatus = updateWsStatus;
window.subscribeKline = subscribeKline;
window.unsubscribeKline = unsubscribeKline;
window.stopAutoRefresh = stopAutoRefresh;
