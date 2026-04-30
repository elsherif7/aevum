"""
Rate limiting for YouTube API to prevent quota exhaustion.

Implements token bucket algorithm with configurable limits.
"""

import time
from collections import deque
from threading import Lock


class RateLimiter:
    """
    Token bucket rate limiter for API calls.
    
    Thread-safe implementation to prevent quota exhaustion attacks.
    """
    
    def __init__(self, max_calls: int, time_window: int):
        """
        Args:
            max_calls: Maximum calls allowed in time window
            time_window: Time window in seconds
        """
        self.max_calls = max_calls
        self.time_window = time_window
        self.calls = deque()
        self.lock = Lock()
    
    def allow_request(self) -> bool:
        """
        Check if request is allowed under rate limit.
        
        Thread-safe: uses lock to prevent race conditions.
        
        Returns:
            True if request allowed, False if rate limit exceeded
        """
        with self.lock:
            now = time.time()
            
            # Remove calls outside time window
            while self.calls and self.calls[0] < now - self.time_window:
                self.calls.popleft()
            
            if len(self.calls) < self.max_calls:
                self.calls.append(now)
                return True
            
            return False
    
    def wait_time(self) -> float:
        """
        Return seconds to wait before next allowed request.
        
        Returns:
            Seconds to wait (0.0 if request would be allowed now)
        """
        with self.lock:
            if not self.calls:
                return 0.0
            
            oldest_call = self.calls[0]
            wait = (oldest_call + self.time_window) - time.time()
            return max(0.0, wait)
    
    def reset(self):
        """Clear all tracked calls (for testing)."""
        with self.lock:
            self.calls.clear()


# Global rate limiter: 100 requests per hour (conservative)
# YouTube API quota is 10,000 units/day
# Each video = 1 unit, each playlist = 1-3 units
# 100/hour = 2,400/day max, well under quota but prevents abuse
youtube_limiter = RateLimiter(max_calls=100, time_window=3600)
