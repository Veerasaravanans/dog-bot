import asyncio
import logging
import time
from datetime import datetime
from collections import Counter, deque

import httpx

from backend.config import settings
from backend.models.schemas import (
    AIDecision, MotorDirection, Detection, ControlMode, LaneStatus, TrackedObstacle
)
from backend.services.path_planner import PathPlannerEngine

logger = logging.getLogger("dogbot.ai")


class AIDecisionEngine:
    def __init__(self):
        self._mode = ControlMode.MANUAL
        self._last_decision_time = 0.0
        self._last_manual_time = 0.0
        self._motor_state = MotorDirection.STOP
        self._decision_log: deque[AIDecision] = deque(maxlen=50)
        self._running = False
        self._decision_task: asyncio.Task | None = None
        self._on_decision_callback = None
        self._latest_obstacles: list[TrackedObstacle] = []
        self._latest_detections: list[Detection] = []
        self._latest_lane_status: LaneStatus | None = None
        self._path_planner = PathPlannerEngine(
            grid_width_m=settings.planner_grid_width_m,
            grid_depth_m=settings.planner_grid_depth_m,
            cell_size_m=settings.planner_cell_size_m,
            emergency_stop_dist_m=settings.planner_emergency_stop_dist_m,
            estop_lateral_m=settings.planner_estop_lateral_m,
            estop_distance_m=settings.planner_estop_distance_m,
            blocked_threshold=settings.planner_blocked_threshold,
            recovery_speed=settings.planner_recovery_speed,
            recovery_steering=settings.planner_recovery_steering,
            steering_bias=settings.motor_forward_steering_bias,
        )

        # Direction votes accumulated during the ANALYZE phase
        # maxlen=45 covers ~3 seconds at 15 FPS
        self._direction_votes: deque[MotorDirection] = deque(maxlen=45)

        # Forward recovery: track ALL turns (not just same-direction)
        self._total_turns = 0           # total left+right since last forward
        self._consecutive_turns = 0     # same-direction consecutive
        self._last_turn_direction: MotorDirection | None = None

        # SEARCH mode: human following state
        self._target_person: Detection | None = None

        # Non-blocking VIO LLM — background task + cached result
        self._llm_task: asyncio.Task | None = None
        self._llm_cached_decision: AIDecision | None = None
        self._llm_cache_time: float = 0.0

    @property
    def mode(self) -> ControlMode:
        return self._mode

    @property
    def decision_log(self) -> list[AIDecision]:
        return list(self._decision_log)

    def set_mode(self, mode: ControlMode):
        self._mode = mode
        logger.info(f"Mode switched to: {mode.value}")

    def set_motor_state(self, state: MotorDirection):
        self._motor_state = state

    def register_manual_input(self):
        self._last_manual_time = time.time()

    def set_on_decision(self, callback):
        self._on_decision_callback = callback

    def update_scene(self, obstacles: list[TrackedObstacle],
                     detections: list[Detection],
                     lane_status: LaneStatus | None = None):
        self._latest_obstacles = obstacles
        self._latest_detections = detections
        self._latest_lane_status = lane_status

    async def start(self):
        self._running = True
        self._decision_task = asyncio.create_task(self._decision_loop())
        logger.info("AI decision engine started")

    async def stop(self):
        self._running = False
        if self._decision_task:
            self._decision_task.cancel()
            try:
                await self._decision_task
            except asyncio.CancelledError:
                pass

    @property
    def path_planner(self) -> PathPlannerEngine:
        return self._path_planner

    # ------------------------------------------------------------------
    # SEARCH mode: human detection helpers
    # ------------------------------------------------------------------
    def _find_person(self) -> Detection | None:
        """Find the highest-confidence person detection."""
        persons = [d for d in self._latest_detections
                   if d.class_name == "person" and d.confidence > 0.4]
        if not persons:
            return None
        return max(persons, key=lambda d: d.confidence)

    def _person_to_direction(self, person: Detection) -> tuple[MotorDirection, str]:
        """Steer toward a detected person. Stop if close (DANGER zone)."""
        if person.zone == "DANGER":
            return MotorDirection.STOP, f"Person close ({person.zone}) — holding position"

        cx = person.bbox.x + person.bbox.w // 2
        frame_w = 640
        frame_third = frame_w / 3

        if cx < frame_third:
            return MotorDirection.LEFT, f"Approaching person (left, conf={person.confidence:.0%})"
        elif cx > 2 * frame_third:
            return MotorDirection.RIGHT, f"Approaching person (right, conf={person.confidence:.0%})"
        else:
            return MotorDirection.FORWARD, f"Approaching person (ahead, conf={person.confidence:.0%})"

    # ------------------------------------------------------------------
    # Main decision loop
    # ------------------------------------------------------------------
    async def _decision_loop(self):
        """Stop-Analyze-Act cycle with SEARCH mode support.

        SEARCH mode: if person detected, follow them directly.
                     if no person, fall through to normal exploration.
        SEMI_AUTO:   standard analyze-vote-act cycle.
        """
        while self._running:
            try:
                # --- SEARCH mode: human following takes priority ---
                if self._mode == ControlMode.SEARCH:
                    person = self._find_person()
                    if person:
                        direction, reasoning = self._person_to_direction(person)
                        self._target_person = person
                        decision = AIDecision(
                            action=direction, reasoning=reasoning, confidence=0.9
                        )
                        self._record_decision(decision)
                        await asyncio.sleep(settings.ai_act_duration)
                        if direction != MotorDirection.STOP:
                            self._record_decision(AIDecision(
                                action=MotorDirection.STOP,
                                reasoning="Follow pulse complete",
                                confidence=1.0,
                            ))
                        continue
                    else:
                        self._target_person = None
                        # No person — fall through to exploration below

                # Gate: only run in SEMI_AUTO or SEARCH mode
                if self._mode not in (ControlMode.SEMI_AUTO, ControlMode.SEARCH):
                    await asyncio.sleep(0.1)
                    continue

                # Gate: pause after manual input
                if time.time() - self._last_manual_time < settings.ai_manual_pause_seconds:
                    await asyncio.sleep(0.1)
                    continue

                # === PHASE 1: ANALYZE ===
                self._direction_votes.clear()
                analyze_end = time.time() + settings.ai_analyze_duration
                last_gen = self._path_planner.generation
                emergency_stop = False

                logger.debug("Analyze phase started")

                while time.time() < analyze_end and self._running:
                    await asyncio.sleep(settings.ai_decision_interval)

                    # Check mode/manual interrupts
                    if self._mode not in (ControlMode.SEMI_AUTO, ControlMode.SEARCH):
                        break
                    if time.time() - self._last_manual_time < settings.ai_manual_pause_seconds:
                        break

                    # In SEARCH mode, check for person during analysis
                    if self._mode == ControlMode.SEARCH and self._find_person():
                        break  # Exit analysis to handle person follow

                    # Only vote on NEW planner outputs
                    current_gen = self._path_planner.generation
                    if current_gen <= last_gen:
                        continue
                    last_gen = current_gen

                    planner_out = self._path_planner.latest_output
                    if planner_out.latency_ms == 0.0 and planner_out.reasoning == "":
                        continue

                    action = self._path_planner.map_to_direction(planner_out)
                    self._direction_votes.append(action)

                    # STOP is safety-critical — break analysis and act immediately
                    if action == MotorDirection.STOP and planner_out.feasible_count == 0:
                        emergency_stop = True
                        break

                # If mode changed or person appeared in SEARCH, loop back
                if self._mode not in (ControlMode.SEMI_AUTO, ControlMode.SEARCH):
                    continue
                if self._mode == ControlMode.SEARCH and self._find_person():
                    continue  # Will be handled at top of loop

                # Need at least 1 vote to decide
                if not self._direction_votes:
                    continue

                # Determine winning direction from accumulated votes
                vote_counts = Counter(self._direction_votes)
                majority_dir, majority_count = vote_counts.most_common(1)[0]
                total_votes = len(self._direction_votes)
                vote_confidence = majority_count / total_votes

                # Log vote breakdown
                vote_summary = ", ".join(
                    f"{d.value}:{c}" for d, c in vote_counts.most_common()
                )
                logger.info(
                    f"Analyze complete: {total_votes} votes [{vote_summary}] "
                    f"-> {majority_dir.value} ({vote_confidence:.0%})"
                )

                # No consensus → stop (prevents oscillation)
                if vote_confidence < 0.6 and not emergency_stop:
                    majority_dir = MotorDirection.STOP
                    vote_confidence = 1.0

                # --- Forward recovery ---
                # Track ALL turns since last forward. After 3+ total turns
                # (even alternating L-R-L), prefer FORWARD if planner sees
                # ANY viable forward path (>= 1 vote). This prevents the
                # bot from getting stuck in turn loops.
                forward_votes = vote_counts.get(MotorDirection.FORWARD, 0)

                if (majority_dir in (MotorDirection.LEFT, MotorDirection.RIGHT)
                        and self._total_turns >= 3
                        and forward_votes > 0):
                    logger.info(
                        f"Forward recovery: preferring forward "
                        f"({forward_votes}/{total_votes} votes) "
                        f"after {self._total_turns} total turns"
                    )
                    majority_dir = MotorDirection.FORWARD
                    vote_confidence = max(forward_votes / total_votes, 0.5)

                # Softer recovery: after 2+ same-direction turns, lower threshold
                elif (self._consecutive_turns >= 2
                        and majority_dir in (MotorDirection.LEFT, MotorDirection.RIGHT)
                        and total_votes > 0 and forward_votes / total_votes >= 0.2):
                    logger.info(
                        f"Forward recovery (consecutive): preferring forward "
                        f"({forward_votes}/{total_votes} votes) "
                        f"after {self._consecutive_turns} same-dir turns"
                    )
                    majority_dir = MotorDirection.FORWARD
                    vote_confidence = forward_votes / total_votes

                # Update turn tracking
                if majority_dir == MotorDirection.FORWARD:
                    self._total_turns = 0
                    self._consecutive_turns = 0
                elif majority_dir in (MotorDirection.LEFT, MotorDirection.RIGHT):
                    self._total_turns += 1
                    if majority_dir == self._last_turn_direction:
                        self._consecutive_turns += 1
                    else:
                        self._consecutive_turns = 1
                    self._last_turn_direction = majority_dir

                # Fire background LLM query for low-confidence decisions
                if vote_confidence < 0.4 and majority_dir != MotorDirection.STOP:
                    if self._llm_task is None or self._llm_task.done():
                        self._llm_task = asyncio.create_task(self._background_llm_query())
                    if (self._llm_cached_decision
                            and self._llm_cached_decision.confidence > vote_confidence
                            and time.time() - self._llm_cache_time < 10.0):
                        majority_dir = self._llm_cached_decision.action
                        vote_confidence = self._llm_cached_decision.confidence
                        self._llm_cached_decision = None
                        logger.info(f"VIO LLM override: {majority_dir.value}")

                decision = AIDecision(
                    action=majority_dir,
                    reasoning=f"Cycle vote: {majority_dir.value} ({vote_confidence:.0%} of {total_votes} votes)",
                    confidence=vote_confidence,
                )

                # === PHASE 2: ACT ===
                self._record_decision(decision)

                if majority_dir != MotorDirection.STOP:
                    logger.debug(f"Act phase: {majority_dir.value} for {settings.ai_act_duration}s")
                    await asyncio.sleep(settings.ai_act_duration)

                    # Send STOP after acting
                    stop_decision = AIDecision(
                        action=MotorDirection.STOP,
                        reasoning="Act phase complete — returning to analysis",
                        confidence=1.0,
                    )
                    self._record_decision(stop_decision)
                    logger.debug("Act phase complete, stopping motors")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Decision loop error: {e}", exc_info=True)
                await asyncio.sleep(2.0)

    def _record_decision(self, decision: AIDecision):
        """Log decision and send motor command via callback."""
        self._decision_log.append(decision)
        self._last_decision_time = time.time()
        self._motor_state = decision.action
        logger.info(f"AI Decision: {decision.action.value} - {decision.reasoning}")
        if self._on_decision_callback:
            asyncio.create_task(self._on_decision_callback(decision))

    def _build_scene_prompt(self) -> str:
        ls = self._latest_lane_status
        lane_info = "Unknown"
        if ls:
            lane_info = f"LEFT:{ls.left} CENTER:{ls.center} RIGHT:{ls.right} Free:{ls.free_path} Nearest:{ls.nearest_obstacle_m:.1f}m"

        stable = [o for o in self._latest_obstacles if o.frames_seen >= 2]
        obs_details = ""
        for o in stable[:10]:
            obs_details += f"  - #{o.id} at {o.distance_m:.1f}m, lane={o.lane}, zone={o.zone}\n"
        if not obs_details:
            obs_details = "  None\n"

        det_summary = ""
        for d in self._latest_detections:
            det_summary += f"  - {d.class_name} ({d.confidence:.0%}) in {d.zone}\n"
        if not det_summary:
            det_summary = "  None\n"

        return f"""You are the AI controller for a recon dog-robot.

LANE STATUS: {lane_info}

OBSTACLES:
{obs_details}
EVIDENCE:
{det_summary}
MOTOR: {self._motor_state.value}

RULES: Follow free_path. Evidence→approach. All blocked→STOP. Never drive into blocked lane.

Respond:
ACTION: <forward|back|left|right|stop>
REASONING: <brief>
CONFIDENCE: <0-1>"""

    async def _background_llm_query(self):
        """Run VIO LLM query in background — never blocks the decision loop."""
        try:
            result = await self._query_vio_llm()
            if result:
                self._llm_cached_decision = result
                self._llm_cache_time = time.time()
        except Exception as e:
            logger.error(f"Background LLM query failed: {e}")

    async def _query_vio_llm(self) -> AIDecision | None:
        try:
            payload = {
                "username": settings.vio_username,
                "token": settings.vio_api_token,
                "type": "QUESTION",
                "payload": self._build_scene_prompt(),
                "vio_model": "Default",
                "ai_model": settings.vio_primary_model,
                "knowledge": False,
                "webSearch": False,
                "reason": False
            }
            start = time.time()
            async with httpx.AsyncClient(timeout=8.0, verify=settings.vio_ssl_verify) as client:
                response = await client.post(f"{settings.vio_base_url}/message", json=payload)
            latency = (time.time() - start) * 1000

            if response.status_code != 200:
                logger.warning(f"VIO API error: {response.status_code}")
                return None

            response_text = response.text
            try:
                data = response.json()
                if isinstance(data, dict):
                    if "answer" in data:
                        response_text = data["answer"]
                    elif "message" in data:
                        response_text = data["message"]
                    elif "response" in data:
                        response_text = data["response"]
            except:
                pass

            return self._parse_llm_response(response_text, latency)
        except Exception as e:
            logger.error(f"VIO API call failed: {e}")
            return None

    def _parse_llm_response(self, text: str, latency_ms: float) -> AIDecision | None:
        try:
            action = MotorDirection.STOP
            reasoning = "Unable to parse"
            confidence = 0.5

            for line in text.strip().split('\n'):
                line_upper = line.strip().upper()
                if "ACTION:" in line_upper:
                    try:
                        val = line_upper.split("ACTION:", 1)[1].strip().split()[0]
                        val = val.strip('.,*"`')
                        action = MotorDirection(val.lower())
                    except (ValueError, IndexError):
                        pass
                elif "REASONING:" in line_upper:
                    reasoning = line.split(":", 1)[1].strip()
                elif "CONFIDENCE:" in line_upper:
                    try:
                        idx = line_upper.find("CONFIDENCE:")
                        val = line[idx + 11:].strip()
                        confidence = max(0.0, min(1.0, float(val)))
                    except ValueError:
                        pass
            return AIDecision(action=action, reasoning=reasoning, confidence=confidence)
        except Exception as e:
            logger.error(f"LLM parse error: {e}")
            return None
