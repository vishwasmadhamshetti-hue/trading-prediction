const socket = io();

// DOM Elements
const tickerInput = document.getElementById('tickerInput');
const searchBtn = document.getElementById('searchBtn');
const dashboard = document.getElementById('dashboard');
const loadingState = document.getElementById('loadingState');
const loadingTicker = document.getElementById('loadingTicker');

const connectionDot = document.getElementById('connectionDot');
const connectionText = document.getElementById('connectionText');

const currentPrice = document.getElementById('currentPrice');
const aiSignal = document.getElementById('aiSignal');
const signalContainer = document.getElementById('signalContainer');
const confidenceValue = document.getElementById('confidenceValue');
const confidencePath = document.getElementById('confidencePath');
const riskLevel = document.getElementById('riskLevel');
const reasonsUl = document.getElementById('reasonsUl');
const tickerDisplays = document.querySelectorAll('.ticker-display');

// New Search Elements
const assetType = document.getElementById('assetType');
const countrySelect = document.getElementById('countrySelect');
const searchSuggestions = document.getElementById('searchSuggestions');

let priceChart = null;
let searchTimeout = null;

// Socket Connection Events
socket.on('connect', () => {
    connectionDot.classList.add('active');
    connectionText.textContent = 'Engine Online';
});

socket.on('disconnect', () => {
    connectionDot.classList.remove('active');
    connectionText.textContent = 'Disconnected';
});

socket.on('error', (data) => {
    alert("System Error: " + data.message);
    loadingState.classList.add('hidden');
});

// --- Search Suggestions Logic ---

// Close dropdown when clicking outside
document.addEventListener('click', (e) => {
    if (!tickerInput.contains(e.target) && !searchSuggestions.contains(e.target)) {
        searchSuggestions.classList.add('hidden');
    }
});

// Input debouncing to prevent spamming backend
tickerInput.addEventListener('input', () => {
    clearTimeout(searchTimeout);
    const query = tickerInput.value.trim();
    
    if (query.length < 2) {
        searchSuggestions.classList.add('hidden');
        return;
    }

    searchTimeout = setTimeout(() => {
        fetchSuggestions(query, assetType.value, countrySelect.value);
    }, 300); // 300ms debounce
});

// React to filter changes immediately
assetType.addEventListener('change', () => triggerSearchIfValid());
countrySelect.addEventListener('change', () => triggerSearchIfValid());

function triggerSearchIfValid() {
    const query = tickerInput.value.trim();
    if (query.length >= 2) {
        fetchSuggestions(query, assetType.value, countrySelect.value);
    }
}

async function fetchSuggestions(query, type, country) {
    try {
        const url = `/api/search?q=${encodeURIComponent(query)}&type=${type}&country=${country}`;
        const res = await fetch(url);
        const data = await res.json();
        renderSuggestions(data);
    } catch (e) {
        console.error("Failed to fetch suggestions:", e);
    }
}

function renderSuggestions(suggestions) {
    searchSuggestions.innerHTML = '';
    
    if (suggestions.length === 0) {
        searchSuggestions.classList.add('hidden');
        return;
    }
    
    suggestions.forEach(asset => {
        const li = document.createElement('li');
        li.innerHTML = `
            <span class="suggestion-name">${asset.name}</span>
            <span class="suggestion-symbol">${asset.symbol}</span>
        `;
        li.addEventListener('click', () => {
            // User clicked a suggestion! Run the main engine.
            tickerInput.value = asset.symbol;
            searchSuggestions.classList.add('hidden');
            startEngine(asset.symbol);
        });
        searchSuggestions.appendChild(li);
    });
    
    searchSuggestions.classList.remove('hidden');
}


// --- Main Analysis Engine Logic ---

function startEngine(symbol) {
    if (!symbol) return;
    
    // Hide search suggestions just in case
    searchSuggestions.classList.add('hidden');

    // Show loading
    dashboard.classList.add('hidden');
    loadingState.classList.remove('hidden');
    loadingTicker.textContent = symbol;

    // Send request to server to start socket watching
    socket.emit('request_live_data', { ticker: symbol });
}

searchBtn.addEventListener('click', () => {
    startEngine(tickerInput.value.trim().toUpperCase());
});

// Allow Enter key
tickerInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') {
        e.preventDefault();
        startEngine(tickerInput.value.trim().toUpperCase());
    }
});

// Helper Function for formatting colors based on strings
function applyColorContext(element, valueType, isBorder = false) {
    // Reset classes
    element.classList.remove('color-buy', 'color-sell', 'color-hold');
    element.classList.remove('border-buy', 'border-sell', 'border-hold');
    
    let colorClass = '';
    let borderClass = '';
    
    if (valueType === 'BUY' || valueType === 'Low') {
        colorClass = 'color-buy'; borderClass = 'border-buy';
    } else if (valueType === 'SELL' || valueType === 'High') {
        colorClass = 'color-sell'; borderClass = 'border-sell';
    } else {
        colorClass = 'color-hold'; borderClass = 'border-hold';
    }

    if (isBorder) {
        element.classList.add(borderClass);
    } else {
        element.classList.add(colorClass);
    }
    
    return colorClass.replace('color-', 'var(--') + '-color)'; // returns e.g. var(--buy-color)
}

// Receive Live Updates
socket.on('live_update', (data) => {
    // Hide loading, show dashboard
    loadingState.classList.add('hidden');
    dashboard.classList.remove('hidden');

    // Update Tickers text
    tickerDisplays.forEach(el => el.textContent = data.ticker);

    // Update Price
    currentPrice.textContent = `$${data.price.toFixed(2)}`;

    // Update Signal
    aiSignal.textContent = data.signal;
    applyColorContext(aiSignal, data.signal);
    applyColorContext(signalContainer, data.signal, true);

    // Update Risk
    riskLevel.textContent = data.risk;
    applyColorContext(riskLevel, data.risk);

    // Update Confidence Chart
    confidenceValue.textContent = `${data.confidence}%`;
    confidencePath.setAttribute('stroke-dasharray', `${data.confidence}, 100`);
    
    // Color confidence circle based on the signal
    const confidenceColor = applyColorContext(document.createElement('div'), data.signal);
    confidencePath.style.stroke = confidenceColor;

    // Build the sorted reasons list dynamically
    reasonsUl.innerHTML = '';
    data.reasons.forEach((reason, index) => {
        const li = document.createElement('li');
        // Add a checkmark or alert icon based on string content heuristically for flair
        let icon = '•';
        if (reason.includes("above") || reason.includes("Bullish") || reason.toLowerCase().includes("buy")) icon = '🟢';
        if (reason.includes("below") || reason.includes("Bearish") || reason.toLowerCase().includes("sell")) icon = '🔴';
        
        li.innerHTML = `<strong>${index + 1}.</strong> ${icon} ${reason}`;
        reasonsUl.appendChild(li);
    });

    // Handle Chart.js Update
    const chartCtx = document.getElementById('priceChart').getContext('2d');
    
    if (priceChart) {
        // Just update data if chart exists
        priceChart.data.labels = data.chart.times;
        priceChart.data.datasets[0].data = data.chart.prices;
        
        // Dynamically color line based on trend
        const firstPrice = data.chart.prices[0];
        const lastPrice = data.chart.prices[data.chart.prices.length - 1];
        const strokeColor = lastPrice >= firstPrice ? '#3fb950' : '#f85149';
        
        priceChart.data.datasets[0].borderColor = strokeColor;
        priceChart.data.datasets[0].backgroundColor = strokeColor + '20'; // add transparency
        
        priceChart.update();
    } else {
        // Create chart for the first time
        Chart.defaults.color = '#8b949e';
        Chart.defaults.font.family = 'JetBrains Mono';
        
        priceChart = new Chart(chartCtx, {
            type: 'line',
            data: {
                labels: data.chart.times,
                datasets: [{
                    label: 'Price (1M)',
                    data: data.chart.prices,
                    borderColor: '#58a6ff',
                    backgroundColor: 'rgba(88, 166, 255, 0.1)',
                    borderWidth: 2,
                    fill: true,
                    tension: 0.1, // Smooth curves slightly
                    pointRadius: 0 // Hide individual dots for cleaner look
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false }
                },
                scales: {
                    x: {
                        grid: { color: '#30363d', tickColor: 'transparent' },
                        ticks: { maxTicksLimit: 10 }
                    },
                    y: {
                        grid: { color: '#30363d', tickColor: 'transparent' },
                        ticks: {
                            callback: function(value) {
                                return '$' + value;
                            }
                        }
                    }
                },
                animation: {
                    duration: 0 // Disable animation on continuous updates so it doesn't bounce constantly
                }
            }
        });
    }
});
