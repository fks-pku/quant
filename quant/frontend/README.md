# Quant Trading System Dashboard

## Quick Start

### 1. Install Dependencies

```bash
# Install Flask API server dependencies
pip install flask flask-cors

# Install React frontend dependencies
cd frontend
npm install
```

### 2. Start the API Server

```bash
# From the project root directory
python api_server.py
```

The API server will run at `http://localhost:5000`

### 3. Start the React Frontend

```bash
cd frontend
npm start
```

The frontend will open at `http://localhost:3000`

## Features

- **Start/Stop System**: Click the prominent button to start or stop the quant trading system
- **Portfolio Dashboard**: View NAV, realized P&L, and unrealized P&L
- **Strategy Management**: See active strategies and their symbols
- **Positions Tracking**: Monitor open positions in real-time
- **System Status**: Visual indicator shows if the system is running

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   React UI      │────▶│   Flask API     │────▶│  Quant System   │
│   (Port 3000)   │◀────│   (Port 5000)   │◀────│  (Python CLI)   │
└─────────────────┘     └─────────────────┘     └─────────────────┘
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/status` | Get system status, portfolio, strategies, positions |
| POST | `/api/start` | Start the quant system |
| POST | `/api/stop` | Stop the quant system |
| GET | `/api/portfolio` | Get portfolio data |
| GET | `/api/logs` | Get system logs |
