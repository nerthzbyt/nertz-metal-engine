# Bybit Trading MCP

Official Bybit Trading MCP Server integration for this project.

## Official Source
https://github.com/bybit-exchange/trading-mcp

Provides **206 tools** for:
- Market data (klines, ticker, orderbook, funding rates, OI, long/short ratio, etc.)
- Trading (place/amend/cancel orders, batch)
- Positions management
- Account & assets
- Real-time WebSocket streams (via snapshot model too)

## How to Enable

The server is distributed as npm package. Run via npx (no permanent install needed).

### Config for MCP clients (example for this environment or Claude/Cursor/VSCode)

```json
{
  "mcpServers": {
    "bybit": {
      "command": "npx",
      "args": ["-y", "bybit-official-trading-server@latest"],
      "env": {
        "BYBIT_API_KEY": "${BYBIT_API_KEY}",
        "BYBIT_API_SECRET": "${BYBIT_API_SECRET}",
        "BYBIT_TESTNET": "false"
      }
    }
  }
}
```

For testnet:
Add `"BYBIT_TESTNET": "true"`

Use RSA if you have self-generated keys by setting `BYBIT_API_PRIVATE_KEY_PATH`.

## Integration in NertzMetalEngine

The Python agents (QwenSignalAgent + memory + XGB) can benefit from richer data by calling these tools when available in the MCP environment.

Recommended tools to feed analysis:
- Market: get_ticker, get_kline, get_orderbook, get_funding_rate, get_open_interest, get_long_short_ratio
- Position: get_positions, get_closed_pnl
- Account: get_wallet_balance

See tools/ folder for schema examples.

## Snapshots for Validation

Use the memory MCP (`add_observations`) to store prediction snapshots:

Example entity "BTCUSDT_Prediction_20260622T120000":
- Observation: "XGB prob_up=0.68 | Qwen action=buy conf=0.82 | Features: combined=1.23, funding_rate=0.0001"
- Later: "Outcome: +1.45% realized | TP hit"

This creates a clean dataset for validating and retraining the XGB model over time.

## Adding More Tools

If the full tool list is needed in this workspace, run the server and use discovery, or copy schemas from the repo src/tools or generated.

## Requirements in this project
- pybit (already used for direct WS/REST)
- The MCP gives the *agent layer* (LLM + tools) direct high-level Bybit capabilities without custom code for every endpoint.
