from asyncio import Queue
from heapq import heappop, heappush
from itertools import count
from typing import NamedTuple


class BugsQueueInnerItem(NamedTuple):
    priority: int
    count: int
    bug: int


class BugsQueueItem(NamedTuple):
    bug: int
    priority: int = 0


class BugsQueue(Queue):
    def _init(self, maxsize: int):
        self._queue: list[BugsQueueInnerItem] = []
        self.counter = count()

    def _get(self) -> int:
        return heappop(self._queue).bug

    def _put(self, item: BugsQueueItem):
        heappush(self._queue, BugsQueueInnerItem(**item._asdict(), count=next(self.counter)))

    @property
    def bugs(self):
        return tuple(item.bug for item in self._queue)
