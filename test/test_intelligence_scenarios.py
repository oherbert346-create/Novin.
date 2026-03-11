import pytest
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock
from backend.ingest.processor import process_canonical

# Path to scenarios
SCENARIOS_DIR = Path("test/fixtures/scenarios")

def load_scenarios():
    scenarios = []
    for path in SCENARIOS_DIR.rglob("*.json"):
        with open(path, "r") as f:
            scenarios.append(json.load(f))
    return scenarios

@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", load_scenarios(), ids=lambda s: s["scenario_id"])
async def test_intelligence_scenario(scenario):
    """
    Generic test runner for intelligence scenarios.
    Loads scenario, runs through pipeline, validates result.
    """
    from backend.ingest.schemas import CanonicalIngestPayload
    
    # Prepare payload from scenario
    payload = CanonicalIngestPayload(
        home_id=scenario["camera_config"]["site_id"],
        cam_id="test_cam",
        image_url="test_url",  # Mocked
        zone=scenario["camera_config"]["zone"],
    )

    # Mock DB
    mock_db = AsyncMock()

    # Configure the db_factory to return an async context manager
    class AsyncContextManager:
        def __init__(self, obj):
            self.obj = obj
        async def __aenter__(self):
            return self.obj
        async def __aexit__(self, exc_type, exc, tb):
            pass

    # Create the factory as a synchronous function that returns the async context manager
    mock_db_factory = MagicMock(return_value=AsyncContextManager(mock_db))
    
    import numpy as np
    dummy_frame = np.zeros((100, 100, 3), dtype=np.uint8)
    
    # Mocking external dependencies
    with patch("backend.ingest.processor.fetch_frame_from_url", new_callable=AsyncMock) as mock_fetch, \
         patch("backend.agent.history.query_history", new_callable=AsyncMock) as mock_history, \
         patch("backend.agent.vision.analyse_frame", new_callable=AsyncMock) as mock_vision, \
         patch("backend.agent.sequence.SequenceDetector", new_callable=MagicMock) as mock_seq_detector, \
         patch("backend.agent.schedule.ScheduleLearner", new_callable=MagicMock) as mock_sch_learner, \
         patch("backend.hub._persist_verdict", new_callable=AsyncMock) as mock_persist, \
         patch("backend.agent.reasoning.arbiter.compute_home_thresholds", new_callable=AsyncMock) as mock_thresholds:
        
        mock_fetch.return_value = dummy_frame
        mock_persist.return_value = AsyncMock()
        mock_thresholds.return_value = {
            "vote_confidence_threshold": 0.55,
            "strong_vote_threshold": 0.70,
            "min_alert_confidence": 0.35,
        }
        
        from backend.models.schemas import HistoryContext, VisionResult
        mock_history.return_value = HistoryContext()
        mock_vision.return_value = VisionResult(
            threat=False,
            severity="low",
            categories=["pet"],
            description="pet detected",
            confidence=0.8
        )
        
        # Mock SequenceDetector methods
        seq_detector_instance = mock_seq_detector.return_value
        seq_detector_instance.get_recent_events = AsyncMock(return_value=[])
        
        # Mock ScheduleLearner methods
        sch_learner_instance = mock_sch_learner.return_value
        sch_learner_instance.get_schedule_adjustment = AsyncMock(return_value=MagicMock(adjustment=0, reason="none"))
        
        # Run pipeline using actual function
        result = await process_canonical(
            payload,
            db_factory=mock_db_factory,
            groq_client=AsyncMock(),
            on_verdict=None
        )

        # Assert results
        assert result["action"] == scenario["expected"]["verdict"]
        print(f"Running scenario: {scenario['scenario_id']} passed")
