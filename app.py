import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO
import yfinance as yf
import pandas as pd
import numpy as np
import ta
import time
import math

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, async_mode='eventlet', cors_allowed_origins="*")

# Store active active watching threads per client
active_watchers = {}

# Internal database of popular assets for instantaneous search UX
POPULAR_ASSETS = [
    # Crypto
    {"symbol": "BTC-USD", "name": "Bitcoin", "type": "crypto", "country": "global"},
    {"symbol": "ETH-USD", "name": "Ethereum", "type": "crypto", "country": "global"},
    {"symbol": "SOL-USD", "name": "Solana", "type": "crypto", "country": "global"},
    {"symbol": "DOGE-USD", "name": "Dogecoin", "type": "crypto", "country": "global"},
    {"symbol": "XRP-USD", "name": "XRP", "type": "crypto", "country": "global"},
    {"symbol": "ADA-USD", "name": "Cardano", "type": "crypto", "country": "global"},
    
    # US Stocks
    {"symbol": "AAPL", "name": "Apple Inc.", "type": "stocks", "country": "us"},
    {"symbol": "MSFT", "name": "Microsoft Corp.", "type": "stocks", "country": "us"},
    {"symbol": "TSLA", "name": "Tesla Inc.", "type": "stocks", "country": "us"},
    {"symbol": "NVDA", "name": "NVIDIA Corp.", "type": "stocks", "country": "us"},
    {"symbol": "AMZN", "name": "Amazon.com Inc.", "type": "stocks", "country": "us"},
    {"symbol": "META", "name": "Meta Platforms Inc.", "type": "stocks", "country": "us"},
    {"symbol": "GOOGL", "name": "Alphabet Inc.", "type": "stocks", "country": "us"},
    
    # Indian Stocks (NSE/BSE symbols on Yahoo end with .NS or .BO)
    {"symbol": "RELIANCE.NS", "name": "Reliance Industries", "type": "stocks", "country": "in"},
    {"symbol": "TCS.NS", "name": "Tata Consultancy Services", "type": "stocks", "country": "in"},
    {"symbol": "HDFCBANK.NS", "name": "HDFC Bank", "type": "stocks", "country": "in"},
    {"symbol": "INFY.NS", "name": "Infosys", "type": "stocks", "country": "in"},
    {"symbol": "ICICIBANK.NS", "name": "ICICI Bank", "type": "stocks", "country": "in"},
    {"symbol": "SBIN.NS", "name": "State Bank of India", "type": "stocks", "country": "in"},
    {"symbol": "BHARTIARTL.NS", "name": "Bharti Airtel", "type": "stocks", "country": "in"},
    {"symbol": "TATAMOTORS.NS", "name": "Tata Motors", "type": "stocks", "country": "in"},
]

def fetch_data(ticker):
    """Fetches the last 5 days of 1-minute interval data."""
    
    def try_fetch(symbol):
        stock = yf.Ticker(symbol)
        df = stock.history(period="5d", interval="1m")
        if df.empty or len(df) < 50:
            return None
        return df

    try:
        # First try the exact ticker requested
        df = try_fetch(ticker)
        
        # If it failed, and it doesn't already have a suffix, try adding .NS (NSE India fallback)
        if df is None and "." not in ticker and "-" not in ticker:
            print(f"No valid data for {ticker}. Trying {ticker}.NS...")
            df = try_fetch(f"{ticker}.NS")
            
        return df
    except Exception as e:
        print(f"Error fetching data for {ticker}: {e}")
        return None

def calculate_indicators(df):
    """Calculates MA20, MA50, RSI, MACD, and Volume Trends and drops NaN rows."""
    if len(df) < 50: # Need at least 50 points for MA50
        return None
    
    # Moving Averages
    df['MA20'] = ta.trend.sma_indicator(df['Close'], window=20)
    df['MA50'] = ta.trend.sma_indicator(df['Close'], window=50)
    
    # RSI
    df['RSI'] = ta.momentum.rsi(df['Close'], window=14)
    
    # MACD
    macd = ta.trend.MACD(df['Close'])
    df['MACD'] = macd.macd()
    df['MACD_Signal'] = macd.macd_signal()
    df['MACD_Histogram'] = macd.macd_diff()
    
    # Volume Trend (Simple moving average of volume)
    df['Volume_MA20'] = ta.trend.sma_indicator(df['Volume'], window=20)
    
    # Drop initial rows with NaNs caused by the moving window calculations
    df = df.dropna()
    return df

def generate_decision_and_reasons(df):
    """
    Evaluates the latest data point against multiple conditions to produce
    a Buy/Hold/Sell signal, Confidence Level, Risk Level, and a list of sorted reasons.
    """
    latest = df.iloc[-1]
    prev = df.iloc[-2]
    
    price = latest['Close']
    ma20 = latest['MA20']
    ma50 = latest['MA50']
    rsi = latest['RSI']
    macd = latest['MACD']
    macd_signal = latest['MACD_Signal']
    volume = latest['Volume']
    volume_ma20 = latest['Volume_MA20']
    
    reasons = [] # Tuple list: (Reason String, Impact Score)
    
    # --- Trend Indicators (High Impact) ---
    if ma20 > ma50:
        reasons.append(("Short-term trend (MA20) is above long-term trend (MA50)", 25))
    else:
        reasons.append(("Short-term trend (MA20) is below long-term trend (MA50)", -25))
        
    if price > ma20:
        reasons.append(("Current price is holding above the 20-minute moving average", 15))
    else:
        reasons.append(("Current price has fallen below the 20-minute moving average", -15))
        
    # --- Momentum Indicators (RSI) ---
    if rsi < 30:
        reasons.append((f"RSI is {rsi:.1f} (Oversold), suggesting potential bounce", 20))
    elif rsi > 70:
        reasons.append((f"RSI is {rsi:.1f} (Overbought), high risk of pullback", -20))
    elif 40 <= rsi <= 60:
        reasons.append((f"RSI is neutral at {rsi:.1f}. Minimal momentum.", 5))
    else:
        # 30-40 (bullish leaning), 60-70 (bearish leaning)
        impact = 10 if rsi < 50 else -10
        direction = "bullish" if rsi < 50 else "bearish"
        reasons.append((f"RSI is {rsi:.1f}, showing mild {direction} momentum", impact))
        
    # --- MACD Crossovers ---
    if macd > macd_signal and prev['MACD'] <= prev['MACD_Signal']:
        reasons.append(("Fresh Bullish MACD Crossover detected", 30))
    elif macd < macd_signal and prev['MACD'] >= prev['MACD_Signal']:
        reasons.append(("Fresh Bearish MACD Crossover detected", -30))
    elif macd > macd_signal:
        reasons.append(("MACD is positive (Bullish momentum)", 10))
    else:
        reasons.append(("MACD is negative (Bearish momentum)", -10))
        
    # --- Volume ---
    if volume > (volume_ma20 * 1.5):
        # Spiking volume amplifies the current short term trend
        if price > df.iloc[-2]['Close']:
            reasons.append(("High buying volume detected (>1.5x average)", 15))
        else:
             reasons.append(("High selling volume detected (>1.5x average)", -15))   
    
    # Calculate Total Confidence Engine Score
    total_score = sum(impact for reason, impact in reasons)
    
    # Normalize a score between -100 and 100 to a 0-100 Confidence %
    # An ideal strong buy is around a score of +100.
    # We will use an absolute based confidence.
    raw_confidence = min(max(abs(total_score), 0), 100) 
    
    if total_score >= 25:
        signal = "BUY"
    elif total_score <= -25:
        signal = "SELL"
    else:
        signal = "HOLD"
        
    # Risk Level heuristic (simplistic based on volatility/RSI extremes)
    volatility = df['Close'].pct_change().std() * math.sqrt(252*390) # roughly annualized
    if rsi > 80 or rsi < 20 or volatility > 0.4:
        risk = "High"
    elif volatility < 0.15:
        risk = "Low"
    else:
        risk = "Medium"
        
    # Sort reasons by their absolute impact score descending (most important first)
    sorted_reasons = sorted(reasons, key=lambda x: abs(x[1]), reverse=True)
    # Return just the text strings now that they are sorted
    reason_strings = [r[0] for r in sorted_reasons]
    
    return {
        "signal": signal,
        "confidence": raw_confidence,
        "risk": risk,
        "reasons": reason_strings,
        "latest_price": price
    }

def background_watch_loop(ticker, sid):
    """Continuously fetches data and emits to a specific client via websocket."""
    # Run loop manually since we are using eventlet directly
    print(f"Started watcher logic for {ticker} on sid {sid}")
    while active_watchers.get(sid) == ticker:
        df = fetch_data(ticker)
        if df is not None:
             df_technical = calculate_indicators(df)
             if df_technical is not None:
                 decision = generate_decision_and_reasons(df_technical)
                 
                 # Prepare the last 100 candles for the chart
                 recent_df = df_technical.tail(100)
                 chart_data = {
                     "times": recent_df.index.strftime('%H:%M').tolist(),
                     "prices": recent_df['Close'].tolist()
                 }
                 
                 payload = {
                     "ticker": ticker.upper(),
                     "price": round(decision["latest_price"], 2),
                     "signal": decision["signal"],
                     "confidence": int(decision["confidence"]),
                     "risk": decision["risk"],
                     "reasons": decision["reasons"],
                     "chart": chart_data
                 }
                 
                 socketio.emit('live_update', payload, to=sid)
             else:
                  socketio.emit('error', {'message': f"Not enough data to calculate indicators for {ticker}."}, to=sid)
        else:
             socketio.emit('error', {'message': f"Failed to fetch data for ticker: {ticker}. Check symbol."}, to=sid)
        
        # yfinance minute data updates roughly every 60 seconds
        eventlet.sleep(30) # Poll every 30 seconds to catch new updates
        
    print(f"Stopped watching {ticker} for sid {sid}")


@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/search')
def search():
    query = request.args.get('q', '').lower()
    asset_type = request.args.get('type', 'all').lower()
    country = request.args.get('country', 'all').lower()
    
    if not query:
        return jsonify([])
        
    results = []
    
    for asset in POPULAR_ASSETS:
        # Filter by type
        if asset_type != 'all' and asset['type'] != asset_type:
            continue
            
        # Filter by country (ignore country filter if it's crypto)
        if asset['type'] == 'stocks' and country != 'all' and asset['country'] != country:
            continue
            
        # Match query against name or symbol
        if query in asset['name'].lower() or query in asset['symbol'].lower():
            results.append(asset)
            
    # If the user typed an exact symbol not in our dictionary, add it as a raw option at the bottom
    # (Checking if no results or if the query doesn't match an exact symbol)
    exact_match = any(a['symbol'].lower() == query for a in results)
    if not exact_match and len(query) >= 2:
        results.append({
            "symbol": query.upper(),
            "name": f"Search Live: {query.upper()}",
            "type": asset_type if asset_type != 'all' else "unknown",
            "country": "unknown"
        })
        
    return jsonify(results[:10]) # limit to 10 suggestions


@socketio.on('connect')
def handle_connect():
    print(f"Client connected: {request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    print(f"Client disconnected: {request.sid}")
    # Stop their watcher thread
    if request.sid in active_watchers:
        del active_watchers[request.sid]

@socketio.on('request_live_data')
def handle_request(data):
    ticker = data.get('ticker')
    if not ticker:
        return
    
    sid = request.sid
    # Update state so any existing thread stops
    active_watchers[sid] = ticker
    
    # Start a new watcher thread for this client
    eventlet.spawn(background_watch_loop, ticker, sid)


if __name__ == '__main__':
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)
