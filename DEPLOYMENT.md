# EchoEar WebSocket Server Deployment

## Changes in this version

This version uses the Python `websockets` library instead of FastAPI/uvicorn WebSocket handling, matching xiaozhi-esp32-server's approach. This fixes the persistent 10-second disconnection issue.

**Architecture:**
- WebSocket server: `websockets.serve()` on port 9001 (device connections)
- HTTP server: FastAPI on port 8000 (admin panel)
- Both run concurrently in the same process

## Deployment Commands

Run these commands on the GCE VM:

```bash
# Navigate to server directory
cd ~/echoear-server

# Pull latest changes
git pull

# Make run_server.py executable
chmod +x run_server.py

# Stop old service
sudo systemctl stop echoear-server

# Copy new service file
sudo cp echoear-server-ws.service /etc/systemd/system/echoear-server.service

# Reload systemd
sudo systemctl daemon-reload

# Enable and start new service
sudo systemctl enable echoear-server
sudo systemctl start echoear-server

# Check status
sudo systemctl status echoear-server

# View logs
sudo journalctl -u echoear-server -f
```

## Verify deployment

1. Check WebSocket server: `ws://136.111.249.161:9001/ws`
2. Check admin panel: `http://136.111.249.161:8000/admin`
3. Monitor logs for device connections

## Key differences from old version

1. **WebSocket ping/pong**: Now handled automatically by websockets library (20s ping interval, 60s timeout)
2. **No more 10-second disconnects**: websockets library is more stable than uvicorn WebSocket handling
3. **Dual server setup**: HTTP admin (port 8000) + WebSocket (port 9001)
4. **Better logging**: More detailed connection and message logs

## Rollback if needed

```bash
sudo systemctl stop echoear-server
git checkout HEAD~1
sudo systemctl start echoear-server
```
