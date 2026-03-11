import pytest
from unittest.mock import AsyncMock, patch
from backend.agent.schedule import ScheduleLearner
from backend.models.db import HomeSchedule
from datetime import datetime

@pytest.mark.asyncio
async def test_schedule_learner_adjustment():
    learner = ScheduleLearner()
    
    # Mock DB to return a schedule with quiet hours 22-06
    import json
    mock_db = AsyncMock()
    mock_schedule = HomeSchedule(
        site_id="home",
        quiet_hours_start=22,
        quiet_hours_end=6,
        typical_arrivals=json.dumps({"14": 50}) # 14:00 is peak
    )
    
    with patch.object(learner, "_get_schedule", return_value=mock_schedule):
        # Test quiet hours (23:00)
        timestamp_quiet = datetime(2026, 3, 5, 23, 0)
        adj_quiet = await learner.get_schedule_adjustment(mock_db, "home", timestamp_quiet)
        assert adj_quiet.adjustment == learner.QUIET_ADJUSTMENT
        assert not adj_quiet.is_expected
        
        # Test peak hours (14:00)
        timestamp_peak = datetime(2026, 3, 5, 14, 0)
        adj_peak = await learner.get_schedule_adjustment(mock_db, "home", timestamp_peak)
        assert adj_peak.adjustment == learner.PEAK_ADJUSTMENT
        assert adj_peak.is_expected
        
        # Test normal hours (10:00)
        timestamp_normal = datetime(2026, 3, 5, 10, 0)
        adj_normal = await learner.get_schedule_adjustment(mock_db, "home", timestamp_normal)
        assert adj_normal.adjustment == 0.0
        assert adj_normal.is_expected
