"""Schedule learning - learns household patterns to reduce false positives."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.db import Event, HomeSchedule

logger = logging.getLogger(__name__)


@dataclass
class ScheduleAdjustment:
    """Result of schedule-based confidence adjustment."""
    adjustment: float  # Confidence adjustment to apply
    is_expected: bool  # True if event is during typical activity
    reason: str


class ScheduleLearner:
    """Learns household patterns from event history to reduce false positives.
    
    Learns:
    - Typical activity hours (when is the home active?)
    - Quiet hours (when should it be quiet?)
    - Peak hours (when is most activity?)
    
    Uses learned patterns to adjust alert confidence:
    - During quiet hours: increase alert confidence (suspicious)
    - During peak hours: decrease alert confidence (expected)
    """
    
    # Thresholds
    MIN_EVENTS_FOR_LEARNING = 50
    QUIET_HOUR_THRESHOLD = 5  # Events < 5% of total = quiet
    PEAK_HOUR_THRESHOLD = 30  # Events > 30% = peak
    QUIET_ADJUSTMENT = 0.15  # Add to alert confidence during quiet hours
    PEAK_ADJUSTMENT = -0.20  # Subtract from alert confidence during peak hours
    REFRESH_MIN_NEW_EVENTS = 25
    REFRESH_MAX_AGE = timedelta(hours=24)
    
    async def learn_schedule(
        self, 
        db: AsyncSession, 
        site_id: str
    ) -> HomeSchedule | None:
        """Analyze event history and build/update schedule for a home.
        
        Args:
            db: Database session
            site_id: The home/site to learn schedule for
            
        Returns:
            HomeSchedule if enough data, None otherwise
        """
        
        # Get all events for this site
        events = await self._get_all_events(db, site_id)
        
        if len(events) < self.MIN_EVENTS_FOR_LEARNING:
            logger.info(
                "Not enough events for schedule learning: %d < %d",
                len(events), self.MIN_EVENTS_FOR_LEARNING
            )
            return None
        
        # Compute distributions
        hourly_dist = self._compute_hourly_distribution(events)
        daily_dist = self._compute_daily_distribution(events)
        quiet_hours = self._find_quiet_hours(hourly_dist)
        peak_hours = self._find_peak_hours(hourly_dist)
        
        # Get or create schedule
        schedule = await self._get_or_create_schedule(db, site_id)
        
        # Update schedule
        schedule.typical_arrivals = json.dumps(hourly_dist)
        schedule.typical_departures = json.dumps(daily_dist)
        schedule.events_analyzed = len(events)
        
        if quiet_hours:
            schedule.quiet_hours_start = quiet_hours[0]
            schedule.quiet_hours_end = quiet_hours[1]
        
        await db.commit()
        
        logger.info(
            "Learned schedule for %s: quiet=%s, peak=%s, events=%d",
            site_id, quiet_hours, peak_hours, len(events)
        )
        
        return schedule
    
    async def get_schedule_adjustment(
        self, 
        db: AsyncSession, 
        site_id: str, 
        event_timestamp: datetime
    ) -> ScheduleAdjustment:
        """Get confidence adjustment based on learned schedule.
        
        Args:
            db: Database session
            site_id: The home/site
            event_timestamp: When the event occurred
            
        Returns:
            ScheduleAdjustment with confidence change and reason
        """
        
        schedule = await self._get_schedule(db, site_id)
        
        if not schedule:
            return ScheduleAdjustment(
                adjustment=0.0,
                is_expected=True,
                reason="No schedule learned yet"
            )
        
        hour = event_timestamp.hour
        
        # Check quiet hours first
        if schedule.quiet_hours_start is not None and schedule.quiet_hours_end is not None:
            if self._in_hour_range(
                hour, 
                schedule.quiet_hours_start, 
                schedule.quiet_hours_end
            ):
                return ScheduleAdjustment(
                    adjustment=self.QUIET_ADJUSTMENT,
                    is_expected=False,
                    reason=f"Event during learned quiet hours ({schedule.quiet_hours_start:02d}-{schedule.quiet_hours_end:02d})"
                )
        
        # Check peak hours
        typical_arrivals = json.loads(schedule.typical_arrivals)
        hour_activity = typical_arrivals.get(str(hour), 0)
        
        if hour_activity >= self.PEAK_HOUR_THRESHOLD:
            return ScheduleAdjustment(
                adjustment=self.PEAK_ADJUSTMENT,
                is_expected=True,
                reason=f"Event during typical high-activity hour ({hour}:00)"
            )
        
        # Default: normal activity time
        return ScheduleAdjustment(
            adjustment=0.0,
            is_expected=True,
            reason="Event within normal activity parameters"
        )

    async def refresh_schedule_if_due(self, db: AsyncSession, site_id: str) -> HomeSchedule | None:
        schedule = await self._get_schedule(db, site_id)
        if schedule and not await self._should_refresh_schedule(db, site_id, schedule):
            return schedule
        return await self.learn_schedule(db, site_id)
    
    async def _get_all_events(
        self, 
        db: AsyncSession, 
        site_id: str,
        days_back: int = 30
    ) -> list[Event]:
        """Get all events for a site within time window."""
        
        since = datetime.utcnow() - timedelta(days=days_back)
        
        result = await db.execute(
            select(Event)
            .join(Event.stream)
            .where(Event.stream.has(site_id=site_id))
            .where(Event.timestamp >= since)
        )
        
        return list(result.scalars().all())
    
    def _compute_hourly_distribution(
        self, 
        events: list[Event]
    ) -> dict[str, float]:
        """Compute events per hour (0-23) as percentage."""
        
        hourly = {h: 0 for h in range(24)}
        total = len(events)
        
        for e in events:
            hour = e.timestamp.hour
            hourly[hour] += 1
        
        # Convert to percentages
        return {
            str(h): (count / total * 100) if total > 0 else 0.0
            for h, count in hourly.items()
        }
    
    def _compute_daily_distribution(
        self, 
        events: list[Event]
    ) -> dict[str, float]:
        """Compute events per day of week as percentage."""
        
        daily = {day: 0 for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]}
        total = len(events)
        
        for e in events:
            day = e.timestamp.strftime("%A")
            daily[day] += 1
        
        # Convert to percentages
        return {
            day: (count / total * 100) if total > 0 else 0.0
            for day, count in daily.items()
        }
    
    def _find_quiet_hours(
        self, 
        hourly_dist: dict[str, float]
    ) -> tuple[int, int] | None:
        """Find continuous quiet hours (typically night time)."""

        quiet_hours = [
            int(h) for h, pct in hourly_dist.items()
            if pct < self.QUIET_HOUR_THRESHOLD
        ]

        if not quiet_hours:
            return None

        quiet_set = set(quiet_hours)
        best_block: tuple[int, bool, int, int] | None = None

        for start in sorted(quiet_set):
            if (start - 1) % 24 in quiet_set:
                continue

            length = 1
            end = start
            while length < 24 and (end + 1) % 24 in quiet_set:
                end = (end + 1) % 24
                length += 1

            contains_midnight = start == 0 or start > end or length == 24
            candidate = (length, contains_midnight, start, end)
            if best_block is None:
                best_block = candidate
                continue
            if candidate[0] > best_block[0]:
                best_block = candidate
                continue
            if candidate[0] == best_block[0]:
                if candidate[1] and not best_block[1]:
                    best_block = candidate
                    continue
                if candidate[1] == best_block[1] and candidate[2] < best_block[2]:
                    best_block = candidate

        if best_block is None:
            return None
        return best_block[2], best_block[3]
    
    def _find_peak_hours(
        self, 
        hourly_dist: dict[str, float]
    ) -> list[int]:
        """Find peak activity hours."""
        
        return [
            int(h) for h, pct in hourly_dist.items()
            if pct >= self.PEAK_HOUR_THRESHOLD
        ]
    
    def _in_hour_range(
        self, 
        hour: int, 
        start: int, 
        end: int
    ) -> bool:
        """Check if hour is in range (handles overnight ranges like 22-6)."""
        
        if start <= end:
            # Normal range: e.g., 9-17
            return start <= hour <= end
        else:
            # Overnight range: e.g., 22-6
            return hour >= start or hour <= end
    
    async def _get_schedule(
        self, 
        db: AsyncSession, 
        site_id: str
    ) -> HomeSchedule | None:
        """Get existing schedule for a site."""
        
        result = await db.execute(
            select(HomeSchedule).where(HomeSchedule.site_id == site_id)
        )
        
        return result.scalar_one_or_none()
    
    async def _get_or_create_schedule(
        self, 
        db: AsyncSession, 
        site_id: str
    ) -> HomeSchedule:
        """Get or create schedule for a site."""
        
        schedule = await self._get_schedule(db, site_id)
        
        if not schedule:
            schedule = HomeSchedule(site_id=site_id)
            db.add(schedule)
            await db.flush()
        
        return schedule

    async def _should_refresh_schedule(
        self,
        db: AsyncSession,
        site_id: str,
        schedule: HomeSchedule,
    ) -> bool:
        total_events = len(await self._get_all_events(db, site_id))
        if total_events < self.MIN_EVENTS_FOR_LEARNING:
            return False
        if schedule.events_analyzed == 0:
            return True
        if total_events - schedule.events_analyzed >= self.REFRESH_MIN_NEW_EVENTS:
            return True
        if schedule.last_updated is None:
            return True
        return datetime.utcnow() - schedule.last_updated >= self.REFRESH_MAX_AGE
    
    async def has_sufficient_data(
        self, 
        db: AsyncSession, 
        site_id: str
    ) -> bool:
        """Check if there's enough data to learn a schedule."""
        
        events = await self._get_all_events(db, site_id)
        return len(events) >= self.MIN_EVENTS_FOR_LEARNING

    async def refresh_schedule_if_due(
        self,
        db: AsyncSession,
        site_id: str,
    ) -> HomeSchedule | None:
        """Refresh learned schedule if missing or stale."""
        schedule = await self._get_schedule(db, site_id)
        if schedule and not await self._should_refresh_schedule(db, site_id, schedule):
            return schedule
        if not schedule:
            if not await self.has_sufficient_data(db, site_id):
                return None
            return await self.learn_schedule(db, site_id)
        return await self.learn_schedule(db, site_id)
