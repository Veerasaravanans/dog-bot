// ═══════════════════════════════════════════════════════════════
// DOG-BOT Tactical Command Interface — Main App
// ═══════════════════════════════════════════════════════════════

const DogBot = {
    videoWs: null,
    controlWs: null,
    reconnectDelay: 2000,
    connected: false,
    startTime: Date.now(),
    currentMode: "manual",

    init() {
        this.connectVideoWs();
        this.connectControlWs();
        Controls.init();
        Panels.init();
        this.startClock();
        this.startUptimeCounter();
        Panels.addLogEntry("act", "System initialized. Connecting to backend...");
        console.log("[DogBot] Tactical Command Interface initialized");
    },

    getWsUrl(path) {
        const proto = location.protocol === "https:" ? "wss:" : "ws:";
        return `${proto}//${location.host}${path}`;
    },

    // ═══════════════════════════════════════════════════════════════
    // CLOCK
    // ═══════════════════════════════════════════════════════════════
    startClock() {
        const update = () => {
            const now = new Date();
            document.getElementById("time").textContent = now.toTimeString().split(" ")[0];
            document.getElementById("date").textContent = now.toISOString().split("T")[0];
        };
        update();
        setInterval(update, 1000);
    },

    startUptimeCounter() {
        setInterval(() => {
            const elapsed = Math.floor((Date.now() - this.startTime) / 1000);
            const h = String(Math.floor(elapsed / 3600)).padStart(2, "0");
            const m = String(Math.floor((elapsed % 3600) / 60)).padStart(2, "0");
            const s = String(elapsed % 60).padStart(2, "0");
            const timeStr = `${h}:${m}:${s}`;
            document.getElementById("recTime").textContent = timeStr;
            document.getElementById("stat-uptime").textContent = timeStr;
            document.getElementById("telem-uptime").textContent = timeStr;
        }, 1000);
    },

    // ═══════════════════════════════════════════════════════════════
    // VIDEO WEBSOCKET
    // ═══════════════════════════════════════════════════════════════
    connectVideoWs() {
        const url = this.getWsUrl("/ws/video");
        this.videoWs = new WebSocket(url);

        this.videoWs.onopen = () => {
            console.log("[Video WS] Connected");
            Panels.addLogEntry("act", "Video stream connected");
        };

        this.videoWs.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                this.handleVideoFrame(data);
            } catch (e) {
                console.error("[Video WS] Parse error:", e);
            }
        };

        this.videoWs.onclose = () => {
            console.log("[Video WS] Disconnected");
            setTimeout(() => this.connectVideoWs(), this.reconnectDelay);
        };

        this.videoWs.onerror = () => {};
    },

    handleVideoFrame(data) {
        const videoFeed = document.getElementById("videoFeed");
        const videoImg = document.getElementById("videoImg");

        if (data.frame_b64) {
            videoImg.src = "data:image/jpeg;base64," + data.frame_b64;

            if (data.telemetry && data.telemetry.connected) {
                videoImg.style.display = "block";
                videoFeed.classList.remove("no-signal");
            } else {
                videoImg.style.display = "none";
                videoFeed.classList.add("no-signal");
            }
        }

        if (data.telemetry) {
            Panels.updateTelemetry(data.telemetry);
        }

        if (data.detections && data.detections.length > 0) {
            Panels.handleDetections(data.detections);
        }
    },

    // ═══════════════════════════════════════════════════════════════
    // CONTROL WEBSOCKET
    // ═══════════════════════════════════════════════════════════════
    connectControlWs() {
        const url = this.getWsUrl("/ws/control");
        this.controlWs = new WebSocket(url);

        this.controlWs.onopen = () => {
            console.log("[Control WS] Connected");
            this.connected = true;
            Panels.setConnectionStatus(true);
            Panels.addLogEntry("act", "Control link established");
        };

        this.controlWs.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                this.handleControlMessage(data);
            } catch (e) {
                console.error("[Control WS] Parse error:", e);
            }
        };

        this.controlWs.onclose = () => {
            this.connected = false;
            Panels.setConnectionStatus(false);
            setTimeout(() => this.connectControlWs(), this.reconnectDelay);
        };

        this.controlWs.onerror = () => {};
    },

    handleControlMessage(data) {
        switch (data.type) {
            case "ai_decision":
                Panels.handleAIDecision(data);
                break;
            case "alert":
                Panels.handleDetectionAlert(data);
                break;
            case "motor_ack":
                Panels.addLogEntry("act", `Motor: ${data.direction.toUpperCase()} ${data.success ? "OK" : "FAILED"}`);
                break;
            case "mode_change":
                Panels.updateMode(data.mode);
                break;
            case "status":
                if (data.mode) Panels.updateMode(data.mode);
                break;
        }
    },

    // ═══════════════════════════════════════════════════════════════
    // SEND COMMANDS
    // ═══════════════════════════════════════════════════════════════
    sendMotorCommand(direction) {
        const speed = parseInt(document.getElementById("speedSlider").value) || 200;
        if (this.controlWs && this.controlWs.readyState === WebSocket.OPEN) {
            this.controlWs.send(JSON.stringify({
                type: "motor",
                direction: direction,
                speed: speed
            }));
        }
    },

    sendModeSwitch(mode) {
        if (this.controlWs && this.controlWs.readyState === WebSocket.OPEN) {
            this.controlWs.send(JSON.stringify({
                type: "mode",
                value: mode
            }));
        }
    }
};

// Start on load
window.addEventListener("DOMContentLoaded", () => DogBot.init());
