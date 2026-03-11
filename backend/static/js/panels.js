// ═══════════════════════════════════════════════════════════════
// DOG-BOT — Dashboard Panels (Telemetry, AI Log, Alerts, Gauges)
// ═══════════════════════════════════════════════════════════════

const Panels = {
    maxLogEntries: 50,
    evidenceAlertTimeout: null,
    // Nav2-style trail: tracks all bot movement on minimap
    trailPoints: [],
    trailCanvas: null,
    trailCtx: null,
    trailW: 120,
    trailH: 120,
    _lastTrailTime: 0,       // throttle: ms timestamp of last trail point
    _trailInterval: 300,     // add point at most every 300ms

    init() {
        this.trailCanvas = document.getElementById("minimapTrail");
        if (this.trailCanvas) {
            this.trailW = this.trailCanvas.width;
            this.trailH = this.trailCanvas.height;
            this.trailCtx = this.trailCanvas.getContext("2d");
            // Start at bottom-center (bot starts looking forward/up)
            this.trailPoints = [{x: this.trailW / 2, y: this.trailH - 15, dir: "start"}];
            this.drawTrail();
        }
    },

    // ═══════════════════════════════════════════════════════════════
    // CONNECTION STATUS
    // ═══════════════════════════════════════════════════════════════
    setConnectionStatus(connected) {
        const cameraDot = document.getElementById("status-camera");
        const cameraText = document.getElementById("status-camera-text");
        const motorDot = document.getElementById("status-motor");
        const motorText = document.getElementById("status-motor-text");

        if (connected) {
            motorDot.className = "status-dot";
            motorText.textContent = "CONNECTED";
        } else {
            cameraDot.className = "status-dot offline";
            cameraText.textContent = "OFFLINE";
            motorDot.className = "status-dot offline";
            motorText.textContent = "OFFLINE";
        }
    },

    // ═══════════════════════════════════════════════════════════════
    // TELEMETRY UPDATE
    // ═══════════════════════════════════════════════════════════════
    updateTelemetry(telem) {
        // Camera status
        const cameraDot = document.getElementById("status-camera");
        const cameraText = document.getElementById("status-camera-text");
        if (telem.connected) {
            cameraDot.className = "status-dot";
            cameraText.textContent = "ONLINE";
        } else {
            cameraDot.className = "status-dot offline";
            cameraText.textContent = "OFFLINE";
        }

        // AI engine status — use DogBot.currentMode as source of truth
        // (avoids telemetry race condition overwriting user's mode switch)
        const aiDot = document.getElementById("status-ai");
        const aiText = document.getElementById("status-ai-text");
        const activeMode = DogBot.currentMode || "manual";
        if (activeMode === "semi_auto" || activeMode === "search") {
            aiDot.className = "status-dot";
            aiText.textContent = "ACTIVE";
        } else {
            aiDot.className = "status-dot offline";
            aiText.textContent = "STANDBY";
        }

        // HUD values
        const fps = telem.fps != null ? telem.fps.toFixed(1) : "0.0";
        document.getElementById("hud-fps").textContent = fps;
        document.getElementById("hud-mode").textContent = activeMode.toUpperCase().replace("_", "-");

        const obsCnt = telem.obstacle_count || 0;
        const detCnt = telem.detection_count || 0;
        const hudObs = document.getElementById("hud-obstacles");
        hudObs.textContent = obsCnt;
        hudObs.className = obsCnt > 0 ? "hud-value warning" : "hud-value";

        const hudEvd = document.getElementById("hud-evidence");
        hudEvd.textContent = detCnt;
        hudEvd.className = detCnt > 0 ? "hud-value danger" : "hud-value";

        // Bottom telemetry bar
        const rssi = telem.esp32_rssi || 0;
        document.getElementById("telem-rssi").innerHTML = `${rssi}<span class="telemetry-unit">dBm</span>`;
        this.updateSignalBars(rssi);

        const dir = (telem.motor_state || "stop").toUpperCase();
        document.getElementById("telem-direction").textContent = dir;

        // Real-time trail update from motor state (throttled)
        const motorDir = (telem.motor_state || "stop").toLowerCase();
        if (motorDir !== "stop") {
            const now = Date.now();
            if (now - this._lastTrailTime >= this._trailInterval) {
                this._lastTrailTime = now;
                this.addTrailPoint(motorDir);
            }
        }

        const telemObs = document.getElementById("telem-obstacles");
        telemObs.textContent = obsCnt;
        telemObs.className = obsCnt > 0 ? "telemetry-value warning" : "telemetry-value";

        const telemEvd = document.getElementById("telem-evidence");
        telemEvd.textContent = detCnt;
        telemEvd.className = detCnt > 0 ? "telemetry-value danger" : "telemetry-value";

        // Minimap indicators
        document.getElementById("miniObs1").style.display = obsCnt > 0 ? "block" : "none";
        document.getElementById("miniObs2").style.display = obsCnt > 1 ? "block" : "none";
        document.getElementById("miniEvd1").style.display = detCnt > 0 ? "block" : "none";

        // Lane status from CV pipeline
        const ls = telem.lane_status;
        if (ls) {
            this.updateLaneStatusFromCV(ls);
        }
    },

    updateLaneStatusFromCV(ls) {
        const lanes = { laneLeft: ls.left, laneCenter: ls.center, laneRight: ls.right };
        const icons = { clear: "&#10003;", caution: "&#9888;", blocked: "&#10007;" };
        for (const [id, status] of Object.entries(lanes)) {
            const el = document.getElementById(id);
            if (!el) continue;
            el.className = `lane ${status}`;
            el.querySelector(".lane-icon").innerHTML = icons[status] || "&#10003;";
        }
    },

    updateSignalBars(rssi) {
        const bars = document.getElementById("signalBars").children;
        let activeBars = 0;
        if (rssi === 0) activeBars = 0;
        else if (rssi > -50) activeBars = 5;
        else if (rssi > -60) activeBars = 4;
        else if (rssi > -70) activeBars = 3;
        else if (rssi > -80) activeBars = 2;
        else activeBars = 1;

        for (let i = 0; i < 5; i++) {
            bars[i].className = i < activeBars ? "signal-bar" : "signal-bar inactive";
        }
    },

    // ═══════════════════════════════════════════════════════════════
    // MODE UPDATE
    // ═══════════════════════════════════════════════════════════════
    updateMode(mode) {
        DogBot.currentMode = mode;
        const manualBtn = document.getElementById("manualBtn");
        const autoBtn = document.getElementById("autoBtn");
        const searchBtn = document.getElementById("searchBtn");

        manualBtn.classList.remove("active");
        manualBtn.classList.remove("auto");
        autoBtn.classList.remove("active", "auto");
        searchBtn.classList.remove("active", "search");

        if (mode === "semi_auto") {
            autoBtn.classList.add("active", "auto");
        } else if (mode === "search") {
            searchBtn.classList.add("active", "search");
        } else {
            manualBtn.classList.add("active");
        }

        document.getElementById("hud-mode").textContent = mode.toUpperCase().replace("_", "-");
    },

    // ═══════════════════════════════════════════════════════════════
    // AI DECISION HANDLING
    // ═══════════════════════════════════════════════════════════════
    handleAIDecision(decision) {
        const action = decision.action || "stop";
        const reasoning = decision.reasoning || "";
        const confidence = decision.confidence || 0;

        // Add to AI reasoning log
        this.addLogEntry("dec", `${action.toUpperCase()} - ${reasoning}`);

        // Show nav recommendation on video feed
        const navEl = document.getElementById("navRecommendation");
        const arrows = { forward: "\u2191", back: "\u2193", left: "\u2190", right: "\u2192", stop: "\u25A0" };
        document.getElementById("navArrow").textContent = arrows[action] || "\u25A0";
        document.getElementById("navText").textContent = action.toUpperCase();
        document.getElementById("navConf").textContent = `CONF: ${confidence.toFixed(2)}`;
        navEl.classList.remove("hidden");

        // Auto-hide after 5s
        setTimeout(() => navEl.classList.add("hidden"), 5000);

        if (decision.overridden) {
            this.addLogEntry("evd", `OVERRIDE: ${decision.override_reason || "CV collision avoidance"}`);
        }
    },

    // ═══════════════════════════════════════════════════════════════
    // MINIMAP TRAIL (Nav2-style path visualization)
    // ═══════════════════════════════════════════════════════════════
    addTrailPoint(direction) {
        if (!this.trailCtx) return;
        if (direction === "stop") return;  // don't add points for stop

        const last = this.trailPoints[this.trailPoints.length - 1];
        const step = 3;
        let nx = last.x, ny = last.y;
        if (direction === "forward") ny -= step;
        else if (direction === "back") ny += step;
        else if (direction === "left") nx -= step;
        else if (direction === "right") nx += step;
        else return;

        // Clamp to canvas with margin
        nx = Math.max(4, Math.min(this.trailW - 4, nx));
        ny = Math.max(4, Math.min(this.trailH - 4, ny));

        this.trailPoints.push({x: nx, y: ny, dir: direction});
        if (this.trailPoints.length > 200) this.trailPoints.shift();
        this.drawTrail();
    },

    drawTrail() {
        const ctx = this.trailCtx;
        const w = this.trailW, h = this.trailH;
        ctx.clearRect(0, 0, w, h);

        if (this.trailPoints.length < 1) return;

        // --- Draw trail path (nav2 style: thick line with color coding) ---
        const len = this.trailPoints.length;
        const dirColors = {
            forward: "#00ff88",  // green
            back: "#ff4444",     // red
            left: "#44aaff",     // blue
            right: "#ffaa00",    // orange
            start: "#00ff88"
        };

        if (len >= 2) {
            // Draw shadow/glow under the path
            ctx.lineCap = "round";
            ctx.lineJoin = "round";
            ctx.strokeStyle = "rgba(0, 255, 136, 0.15)";
            ctx.lineWidth = 6;
            ctx.beginPath();
            ctx.moveTo(this.trailPoints[0].x, this.trailPoints[0].y);
            for (let i = 1; i < len; i++) {
                ctx.lineTo(this.trailPoints[i].x, this.trailPoints[i].y);
            }
            ctx.stroke();

            // Draw colored segments
            for (let i = 1; i < len; i++) {
                const p0 = this.trailPoints[i - 1];
                const p1 = this.trailPoints[i];
                const age = i / len;  // 0=oldest, 1=newest
                const alpha = 0.3 + 0.7 * age;
                const color = dirColors[p1.dir] || "#00ff88";

                ctx.strokeStyle = color;
                ctx.globalAlpha = alpha;
                ctx.lineWidth = 2.5;
                ctx.lineCap = "round";
                ctx.beginPath();
                ctx.moveTo(p0.x, p0.y);
                ctx.lineTo(p1.x, p1.y);
                ctx.stroke();
            }
            ctx.globalAlpha = 1.0;
        }

        // --- Draw start marker (small circle) ---
        const start = this.trailPoints[0];
        ctx.fillStyle = "rgba(0, 255, 136, 0.3)";
        ctx.beginPath();
        ctx.arc(start.x, start.y, 3, 0, Math.PI * 2);
        ctx.fill();

        // --- Draw current position (bot icon: filled dot + direction arrow) ---
        const cur = this.trailPoints[len - 1];

        // Glow ring
        ctx.strokeStyle = "rgba(0, 255, 136, 0.3)";
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.arc(cur.x, cur.y, 7, 0, Math.PI * 2);
        ctx.stroke();

        // Solid dot
        ctx.fillStyle = "#00ff88";
        ctx.shadowColor = "#00ff88";
        ctx.shadowBlur = 8;
        ctx.beginPath();
        ctx.arc(cur.x, cur.y, 3.5, 0, Math.PI * 2);
        ctx.fill();
        ctx.shadowBlur = 0;

        // Direction arrow on current position
        if (cur.dir && cur.dir !== "start") {
            ctx.strokeStyle = "#ffffff";
            ctx.lineWidth = 1.5;
            ctx.lineCap = "round";
            const aLen = 6;
            let ax = 0, ay = 0;
            if (cur.dir === "forward") ay = -aLen;
            else if (cur.dir === "back") ay = aLen;
            else if (cur.dir === "left") ax = -aLen;
            else if (cur.dir === "right") ax = aLen;
            ctx.beginPath();
            ctx.moveTo(cur.x, cur.y);
            ctx.lineTo(cur.x + ax, cur.y + ay);
            ctx.stroke();
        }
    },

    // ═══════════════════════════════════════════════════════════════
    // DETECTION HANDLING
    // ═══════════════════════════════════════════════════════════════
    handleDetections(detections) {
        for (const det of detections) {
            if (det.confidence > 0.6) {
                this.handleDetectionAlert(det);
            }
        }
    },

    handleDetectionAlert(alert) {
        const className = (alert.class_name || "Unknown").toUpperCase();
        const conf = ((alert.confidence || 0) * 100).toFixed(0);
        const zone = alert.zone || "?";

        this.addLogEntry("evd", `${className} detected at zone ${zone} (${conf}%)`);

        // Show evidence alert on video
        const alertEl = document.getElementById("evidenceAlert");
        document.getElementById("evidenceAlertText").textContent =
            `${className} DETECTED - ${conf}% CONFIDENCE`;
        alertEl.classList.remove("hidden");

        if (this.evidenceAlertTimeout) clearTimeout(this.evidenceAlertTimeout);
        this.evidenceAlertTimeout = setTimeout(() => {
            alertEl.classList.add("hidden");
        }, 5000);

        // Update lane status based on zone
        this.updateLaneFromZone(zone);
    },

    updateLaneFromZone(zone) {
        // Reset to clear
        ["laneLeft", "laneCenter", "laneRight"].forEach(id => {
            const el = document.getElementById(id);
            el.className = "lane clear";
            el.querySelector(".lane-icon").innerHTML = "&#10003;";
        });

        // Mark detection zone
        if (zone === "NEAR") {
            const center = document.getElementById("laneCenter");
            center.className = "lane blocked";
            center.querySelector(".lane-icon").innerHTML = "&#10007;";
        } else if (zone === "MID") {
            const center = document.getElementById("laneCenter");
            center.className = "lane caution";
            center.querySelector(".lane-icon").innerHTML = "&#9888;";
        }
    },

    // ═══════════════════════════════════════════════════════════════
    // AI REASONING LOG
    // ═══════════════════════════════════════════════════════════════
    addLogEntry(type, message) {
        const logContainer = document.getElementById("logContainer");
        const now = new Date();
        const time = now.toTimeString().split(" ")[0];

        const typeLabels = {
            obs: "[OBS]",
            ana: "[ANA]",
            dec: "[DEC]",
            act: "[ACT]",
            evd: "[EVD]"
        };

        const entry = document.createElement("div");
        entry.className = `log-entry ${type}`;
        entry.innerHTML = `
            <span class="log-time">${time}</span>
            <span class="log-type ${type}">${typeLabels[type] || "[LOG]"}</span>
            <span class="log-message">${message}</span>
        `;

        logContainer.appendChild(entry);

        // Trim old entries
        while (logContainer.children.length > this.maxLogEntries) {
            logContainer.removeChild(logContainer.firstChild);
        }

        // Auto-scroll
        logContainer.scrollTop = logContainer.scrollHeight;
    },

    // ═══════════════════════════════════════════════════════════════
    // HELPERS
    // ═══════════════════════════════════════════════════════════════
    formatTime(isoString) {
        try {
            const d = isoString ? new Date(isoString) : new Date();
            return d.toLocaleTimeString("en-US", { hour12: false });
        } catch {
            return new Date().toLocaleTimeString("en-US", { hour12: false });
        }
    }
};
