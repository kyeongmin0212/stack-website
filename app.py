import os
from flask import Flask, jsonify, request
from flask_cors import CORS
from pybit.unified_trading import HTTP
from dotenv import load_dotenv
import pandas as pd
import ta
import time
import logging
import threading
from datetime import datetime
import numpy as np
from flask_socketio import SocketIO, emit
import websocket
import json

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler('bybit_trading.log', encoding='utf-8'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# 환경 변수 로드
load_dotenv()
BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET")

# Bybit 클라이언트 초기화 (테스트넷 사용)
bybit = HTTP(
    testnet=True,
    api_key=BYBIT_API_KEY,
    api_secret=BYBIT_API_SECRET
)

# Flask 앱 설정
app = Flask(__name__)
CORS(app)  # 프론트엔드와의 CORS 문제 해결

# 가상 잔고 및 포지션 관리
virtual_balance = 1000  # 초기 가상 자금 (USD)
positions = {}  # 포지션: {"BTCUSDT": {"side": "Buy", "amount": 0.1, "entry_price": 50000}}
trade_history = []

# 실시간 데이터 저장
price_data = {"BTCUSDT": []}

socketio = SocketIO(app, cors_allowed_origins="*")

def on_message(ws, message):
    data = json.loads(message)
    if 'data' in data:
        price_data["BTCUSDT"].append({
            "timestamp": data['data']['timestamp'],
            "open": float(data['data']['open']),
            "high": float(data['data']['high']),
            "low": float(data['data']['low']),
            "close": float(data['data']['close']),
            "volume": float(data['data']['volume'])
        })
        # 최근 100개의 데이터만 유지
        if len(price_data["BTCUSDT"]) > 100:
            price_data["BTCUSDT"] = price_data["BTCUSDT"][-100:]
        socketio.emit('price_update', price_data["BTCUSDT"][-1])

def on_error(ws, error):
    logger.error(f"WebSocket error: {error}")

def on_close(ws, close_status_code, close_msg):
    logger.info("WebSocket connection closed")

def on_open(ws):
    logger.info("WebSocket connection established")
    ws.send(json.dumps({
        "op": "subscribe",
        "args": ["kline.1.BTCUSDT"]
    }))

def start_websocket():
    websocket.enableTrace(True)
    ws = websocket.WebSocketApp(
        "wss://stream.bybit.com/v5/public/linear",
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
        on_open=on_open
    )
    ws.run_forever()

# WebSocket 연결 시작
threading.Thread(target=start_websocket, daemon=True).start()

# 기술적 분석 지표 계산
def calculate_indicators(df):
    df = df.copy()
    df['rsi'] = ta.momentum.RSIIndicator(df['close']).rsi()
    df['ema_9'] = ta.trend.EMAIndicator(df['close'], window=9).ema_indicator()
    df['ema_21'] = ta.trend.EMAIndicator(df['close'], window=21).ema_indicator()
    return df

# 자동매매 로직
def trading_strategy():
    global virtual_balance, positions
    while True:
        try:
            # 데이터 가져오기
            df = pd.DataFrame(price_data["BTCUSDT"])
            if len(df) < 21:
                time.sleep(60)
                continue

            # 지표 계산
            df = calculate_indicators(df)
            latest = df.iloc[-1]
            previous = df.iloc[-2]

            # 롱/숏 진입 조건
            if latest['ema_9'] > latest['ema_21'] and previous['ema_9'] <= previous['ema_21'] and latest['rsi'] < 70:
                # 롱 진입
                if "BTCUSDT" not in positions or positions["BTCUSDT"]["side"] != "Buy":
                    amount = (virtual_balance * 0.1) / latest['close']  # 10% 자금으로 매수
                    positions["BTCUSDT"] = {
                        "side": "Buy",
                        "amount": amount,
                        "entry_price": latest['close']
                    }
                    virtual_balance -= amount * latest['close']
                    trade_history.append({
                        "timestamp": datetime.now().isoformat(),
                        "side": "Buy",
                        "price": latest['close'],
                        "amount": amount
                    })
                    logger.info(f"롱 진입: {amount} BTC @ {latest['close']}")

            elif latest['ema_9'] < latest['ema_21'] and previous['ema_9'] >= previous['ema_21'] and latest['rsi'] > 30:
                # 숏 진입
                if "BTCUSDT" not in positions or positions["BTCUSDT"]["side"] != "Sell":
                    amount = (virtual_balance * 0.1) / latest['close']
                    positions["BTCUSDT"] = {
                        "side": "Sell",
                        "amount": amount,
                        "entry_price": latest['close']
                    }
                    virtual_balance += amount * latest['close']
                    trade_history.append({
                        "timestamp": datetime.now().isoformat(),
                        "side": "Sell",
                        "price": latest['close'],
                        "amount": amount
                    })
                    logger.info(f"숏 진입: {amount} BTC @ {latest['close']}")

            # 청산 조건 (5% 손절, 10% 익절)
            if "BTCUSDT" in positions:
                position = positions["BTCUSDT"]
                profit_percent = ((latest['close'] - position["entry_price"]) / position["entry_price"]) * 100
                if position["side"] == "Sell":
                    profit_percent = -profit_percent

                if profit_percent <= -5:  # 손절
                    if position["side"] == "Buy":
                        virtual_balance += position["amount"] * latest['close']
                    else:
                        virtual_balance -= position["amount"] * (latest['close'] - position["entry_price"])
                    trade_history.append({
                        "timestamp": datetime.now().isoformat(),
                        "side": "Close",
                        "price": latest['close'],
                        "amount": position["amount"],
                        "reason": "Stop Loss"
                    })
                    logger.info(f"손절 청산: {position['amount']} BTC @ {latest['close']}")
                    del positions["BTCUSDT"]

                elif profit_percent >= 10:  # 익절
                    if position["side"] == "Buy":
                        virtual_balance += position["amount"] * latest['close']
                    else:
                        virtual_balance -= position["amount"] * (latest['close'] - position["entry_price"])
                    trade_history.append({
                        "timestamp": datetime.now().isoformat(),
                        "side": "Close",
                        "price": latest['close'],
                        "amount": position["amount"],
                        "reason": "Take Profit"
                    })
                    logger.info(f"익절 청산: {position['amount']} BTC @ {latest['close']}")
                    del positions["BTCUSDT"]

            time.sleep(60)  # 1분 대기
        except Exception as e:
            logger.error(f"Trading strategy error: {e}")
            time.sleep(60)

# 실시간 데이터 수집
def fetch_data():
    while True:
        try:
            # Bybit에서 1분 캔들 데이터 가져오기
            klines = bybit.get_kline(
                category="linear",
                symbol="BTCUSDT",
                interval="1",
                limit=200
            )["result"]["list"]
            df = pd.DataFrame(klines, columns=["timestamp", "open", "high", "low", "close", "volume", "turnover"])
            df['timestamp'] = pd.to_datetime(df['timestamp'].astype(float), unit='ms')
            df[['open', 'high', 'low', 'close', 'volume']] = df[['open', 'high', 'low', 'close', 'volume']].astype(float)
            price_data["BTCUSDT"] = df.to_dict('records')
            time.sleep(60)  # 1분 대기
        except Exception as e:
            logger.error(f"Data fetch error: {e}")
            time.sleep(60)

# API 엔드포인트
@app.route('/api/klines', methods=['GET'])
def get_klines():
    return jsonify(price_data["BTCUSDT"])

@app.route('/api/balance', methods=['GET'])
def get_balance():
    return jsonify({"balance": virtual_balance, "positions": positions})

@app.route('/api/trade', methods=['POST'])
def manual_trade():
    global virtual_balance, positions
    data = request.json
    side = data['side']  # "Buy" or "Sell"
    amount = float(data['amount'])
    latest_price = price_data["BTCUSDT"][-1]["close"]

    if side == "Buy" or side == "Sell":
        if virtual_balance < amount * latest_price and side == "Buy":
            return jsonify({"error": "Insufficient balance"}), 400
        positions["BTCUSDT"] = {
            "side": side,
            "amount": amount,
            "entry_price": latest_price
        }
        if side == "Buy":
            virtual_balance -= amount * latest_price
        else:
            virtual_balance += amount * latest_price
        trade_history.append({
            "timestamp": datetime.now().isoformat(),
            "side": side,
            "price": latest_price,
            "amount": amount
        })
        return jsonify({"message": f"{side} order placed: {amount} BTC @ {latest_price}"})
    elif side == "Close" and "BTCUSDT" in positions:
        position = positions["BTCUSDT"]
        if position["side"] == "Buy":
            virtual_balance += position["amount"] * latest_price
        else:
            virtual_balance -= position["amount"] * (latest_price - position["entry_price"])
        trade_history.append({
            "timestamp": datetime.now().isoformat(),
            "side": "Close",
            "price": latest_price,
            "amount": position["amount"],
            "reason": "Manual Close"
        })
        del positions["BTCUSDT"]
        return jsonify({"message": f"Position closed: {position['amount']} BTC @ {latest_price}"})
    return jsonify({"error": "Invalid trade"}), 400

# 서버 실행
if __name__ == "__main__":
    threading.Thread(target=fetch_data, daemon=True).start()
    threading.Thread(target=trading_strategy, daemon=True).start()
    app.run(host="0.0.0.0", port=5000)