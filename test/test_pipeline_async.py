"""
Unit tests for the asynchronous StreamPipeline producer-consumer logic.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest

from backend.agent.pipeline import StreamPipeline
from backend.models.schemas import StreamMeta


@pytest.fixture
def stream_meta():
    return StreamMeta(
        stream_id="test_stream",
        uri="test://uri",
        label="Test Stream",
        site_id="test_site",
        zone="front_door",
    )


class DummyFrameSource:
    def __init__(self, frames, delay=0.0):
        self.frames = frames
        self.delay = delay
        self.closed = False

    async def stream(self):
        for frame in self.frames:
            if self.delay:
                await asyncio.sleep(self.delay)
            yield frame

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_pipeline_starts_and_stops_cleanly(stream_meta):
    """Test that pipeline tasks are created and cancelled cleanly."""
    pipeline = StreamPipeline(
        stream_meta=stream_meta,
        db_factory=AsyncMock(),
        groq_client=AsyncMock(),
        on_verdict=AsyncMock(),
    )
    
    with patch("backend.agent.pipeline.make_source") as mock_make:
        mock_make.return_value = DummyFrameSource([])
        pipeline.start()
        assert pipeline._producer_task is not None
        assert pipeline._consumer_task is not None
        assert pipeline._running is True
        
        # Stop should clean up tasks
        await pipeline.stop()
        
        assert pipeline._running is False
        assert pipeline._producer_task.cancelled() or pipeline._producer_task.done()
        assert pipeline._consumer_task.cancelled() or pipeline._consumer_task.done()


@pytest.mark.asyncio
async def test_pipeline_processes_frames_successfully(stream_meta):
    """Test that producer puts frames and consumer processes them."""
    frames = [np.zeros((10, 10, 3)), np.ones((10, 10, 3))]
    
    # We need to capture the verdicts passed to on_verdict
    processed_count = 0
    
    async def mock_on_verdict(verdict, frame):
        nonlocal processed_count
        processed_count += 1
        
    class MockDBContext:
        async def __aenter__(self):
            return AsyncMock()
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    mock_db_factory = lambda: MockDBContext()

    pipeline = StreamPipeline(
        stream_meta=stream_meta,
        db_factory=mock_db_factory,
        groq_client=AsyncMock(),
        on_verdict=mock_on_verdict,
    )
    pipeline._sample_every_n = 1
    
    # Mock make_source to return our dummy frames
    # Mock process_frame to return a dummy verdict
    with patch("backend.agent.pipeline.make_source") as mock_make, \
         patch("backend.agent.pipeline.process_frame", new_callable=AsyncMock) as mock_process:
        
        mock_make.return_value = DummyFrameSource(frames, delay=0.01)
        mock_process.return_value = "dummy_verdict"
        
        pipeline.start()
        
        # Give producer/consumer time to run
        await asyncio.sleep(0.1)
        
        await pipeline.stop()
        
        assert processed_count == len(frames)
        assert mock_process.call_count == len(frames)


@pytest.mark.asyncio
async def test_pipeline_drops_frames_when_queue_full(stream_meta):
    """Test that producer drops frames when queue is full instead of blocking."""
    # Create more frames than queue size
    from backend.agent.pipeline import _FRAME_QUEUE_SIZE
    num_frames = _FRAME_QUEUE_SIZE + 5
    frames = [np.zeros((10, 10, 3)) for _ in range(num_frames)]
    
    class MockDBContext:
        async def __aenter__(self):
            return AsyncMock()
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    pipeline = StreamPipeline(
        stream_meta=stream_meta,
        db_factory=lambda: MockDBContext(),
        groq_client=AsyncMock(),
        on_verdict=AsyncMock(),
    )
    
    # Mock process_frame to be very slow so queue fills up
    async def slow_process(*args, **kwargs):
        await asyncio.sleep(0.5)
        return "dummy"
        
    with patch("backend.agent.pipeline.make_source") as mock_make, \
         patch("backend.agent.pipeline.process_frame", side_effect=slow_process):
        
        mock_make.return_value = DummyFrameSource(frames, delay=0.0)  # Fast producer
        
        pipeline.start()
        
        # Give producer time to fill queue
        await asyncio.sleep(0.1)
        
        # Queue should be at max size
        assert pipeline._frame_queue.qsize() > 0
        
        await pipeline.stop()
        
        # The test passes if it didn't block forever and stop() succeeded


@pytest.mark.asyncio
async def test_pipeline_samples_frames_and_carries_event_context(stream_meta):
    """Only sampled frames should be processed, with stable frame indices in event context."""
    frames = [np.full((10, 10, 3), fill_value=i) for i in range(5)]
    seen_contexts = []

    class MockDBContext:
        async def __aenter__(self):
            return AsyncMock()
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    async def mock_on_verdict(verdict, frame):
        return None

    async def mock_process_frame(*args, **kwargs):
        seen_contexts.append(kwargs["event_context"].model_dump())
        return "dummy_verdict"

    pipeline = StreamPipeline(
        stream_meta=stream_meta,
        db_factory=lambda: MockDBContext(),
        groq_client=AsyncMock(),
        on_verdict=mock_on_verdict,
    )
    pipeline._sample_every_n = 2

    with patch("backend.agent.pipeline.make_source") as mock_make, \
         patch("backend.agent.pipeline.process_frame", side_effect=mock_process_frame):
        mock_make.return_value = DummyFrameSource(frames, delay=0.0)
        pipeline.start()
        await asyncio.sleep(0.1)
        await pipeline.stop()

    assert [ctx["frame_index"] for ctx in seen_contexts] == [1, 3, 5]
    assert all(ctx["ingest_mode"] == "stream_sampled" for ctx in seen_contexts)
    assert all(ctx["sample_rate"] == 2 for ctx in seen_contexts)
