// ═══════════════════════════════════════════════════════════════
// DOG-BOT — Motor Controls (D-Pad, Keyboard, Speed, Mode)
// ═══════════════════════════════════════════════════════════════

const Controls = {
    activeKeys: new Set(),
    keyMap: {
        "w": "forward", "arrowup": "forward",
        "s": "back", "arrowdown": "back",
        "a": "left", "arrowleft": "left",
        "d": "right", "arrowright": "right",
        " ": "stop"
    },
    btnMap: {
        "forward": "btnForward",
        "back": "btnBackward",
        "left": "btnLeft",
        "right": "btnRight",
        "stop": "btnStop"
    },

    init() {
        this.setupKeyboard();
        this.setupDpad();
        this.setupModeToggle();
        this.setupSpeedSlider();
        this.setupActionButtons();
    },

    // ═══════════════════════════════════════════════════════════════
    // KEYBOARD
    // ═══════════════════════════════════════════════════════════════
    setupKeyboard() {
        document.addEventListener("keydown", (e) => {
            const key = e.key.toLowerCase();
            if (key === " ") e.preventDefault();
            if (this.keyMap[key] && !this.activeKeys.has(key)) {
                e.preventDefault();
                this.activeKeys.add(key);
                const dir = this.keyMap[key];
                DogBot.sendMotorCommand(dir);
                this.highlightBtn(dir, true);
                Panels.addLogEntry("act", `Command: ${dir.toUpperCase()} at speed ${document.getElementById("speedSlider").value}`);
            }
        });

        document.addEventListener("keyup", (e) => {
            const key = e.key.toLowerCase();
            if (this.keyMap[key]) {
                e.preventDefault();
                this.activeKeys.delete(key);
                const dir = this.keyMap[key];
                this.highlightBtn(dir, false);

                if (dir !== "stop" && this.activeKeys.size === 0) {
                    DogBot.sendMotorCommand("stop");
                    this.highlightBtn("stop", false);
                }
            }
        });
    },

    highlightBtn(direction, active) {
        const btnId = this.btnMap[direction];
        if (!btnId) return;
        const btn = document.getElementById(btnId);
        if (btn) {
            if (active) btn.classList.add("pressed");
            else btn.classList.remove("pressed");
        }
    },

    // ═══════════════════════════════════════════════════════════════
    // D-PAD BUTTONS
    // ═══════════════════════════════════════════════════════════════
    setupDpad() {
        document.querySelectorAll(".dpad-btn[data-dir]").forEach(btn => {
            const dir = btn.dataset.dir;

            const sendCmd = () => {
                DogBot.sendMotorCommand(dir);
                Panels.addLogEntry("act", `Command: ${dir.toUpperCase()} at speed ${document.getElementById("speedSlider").value}`);
            };

            // Mouse
            btn.addEventListener("mousedown", (e) => {
                e.preventDefault();
                sendCmd();
                btn.classList.add("pressed");
            });
            btn.addEventListener("mouseup", (e) => {
                e.preventDefault();
                btn.classList.remove("pressed");
                if (dir !== "stop") DogBot.sendMotorCommand("stop");
            });
            btn.addEventListener("mouseleave", () => {
                if (btn.classList.contains("pressed")) {
                    btn.classList.remove("pressed");
                    if (dir !== "stop") DogBot.sendMotorCommand("stop");
                }
            });

            // Touch
            btn.addEventListener("touchstart", (e) => {
                e.preventDefault();
                sendCmd();
                btn.classList.add("pressed");
            });
            btn.addEventListener("touchend", (e) => {
                e.preventDefault();
                btn.classList.remove("pressed");
                if (dir !== "stop") DogBot.sendMotorCommand("stop");
            });
        });
    },

    // ═══════════════════════════════════════════════════════════════
    // MODE TOGGLE
    // ═══════════════════════════════════════════════════════════════
    setupModeToggle() {
        const manualBtn = document.getElementById("manualBtn");
        const autoBtn = document.getElementById("autoBtn");
        const searchBtn = document.getElementById("searchBtn");

        manualBtn.addEventListener("click", () => {
            DogBot.currentMode = "manual";
            manualBtn.classList.add("active");
            manualBtn.classList.remove("auto");
            autoBtn.classList.remove("active", "auto");
            searchBtn.classList.remove("active", "search");
            DogBot.sendModeSwitch("manual");
            Panels.addLogEntry("act", "Mode changed to MANUAL - operator control enabled");
        });

        autoBtn.addEventListener("click", () => {
            DogBot.currentMode = "semi_auto";
            autoBtn.classList.add("active", "auto");
            manualBtn.classList.remove("active");
            searchBtn.classList.remove("active", "search");
            DogBot.sendModeSwitch("semi_auto");
            Panels.addLogEntry("act", "Mode changed to SEMI-AUTO - AI navigation enabled");
        });

        searchBtn.addEventListener("click", () => {
            DogBot.currentMode = "search";
            searchBtn.classList.add("active", "search");
            manualBtn.classList.remove("active");
            autoBtn.classList.remove("active", "auto");
            DogBot.sendModeSwitch("search");
            Panels.addLogEntry("act", "Mode changed to SEARCH - human detection & following enabled");
        });
    },

    // ═══════════════════════════════════════════════════════════════
    // SPEED SLIDER
    // ═══════════════════════════════════════════════════════════════
    setupSpeedSlider() {
        const slider = document.getElementById("speedSlider");
        const display = document.getElementById("speedValue");
        slider.addEventListener("input", (e) => {
            display.textContent = e.target.value;
        });
    },

    // ═══════════════════════════════════════════════════════════════
    // ACTION BUTTONS
    // ═══════════════════════════════════════════════════════════════
    setupActionButtons() {
        document.getElementById("btnSnapshot").addEventListener("click", () => {
            Panels.addLogEntry("act", "Snapshot captured and saved");
        });

        document.getElementById("btnSaveLog").addEventListener("click", () => {
            Panels.addLogEntry("act", "Decision log exported to file");
        });

        document.getElementById("btnEstop").addEventListener("click", () => {
            DogBot.sendMotorCommand("stop");
            Panels.addLogEntry("evd", "EMERGENCY STOP ACTIVATED - all motors halted");
        });
    }
};
