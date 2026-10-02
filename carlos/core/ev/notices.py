import asyncio


RANKS = {'BACKGROUND': 0, 'NORMAL': 1, 'HIGH': 2, 'EMERGENCY': 3}


class NoticeQueue(asyncio.Queue):
    def __init__(self, maxsize=32):
        if type(maxsize) is not int or maxsize < 1:
            raise ValueError('Notice queue must have a positive capacity')
        super().__init__(maxsize=maxsize)
        self._dropped = {priority: 0 for priority in RANKS}

    @staticmethod
    def priority(event):
        return event.priority if event.priority in RANKS else 'NORMAL'

    @classmethod
    def rank(cls, event):
        return RANKS[cls.priority(event)]

    def _get(self):
        index = max(range(len(self._queue)), key=lambda i: self.rank(self._queue[i]))
        event = self._queue[index]
        del self._queue[index]
        return event

    def offer(self, event):
        try:
            self.put_nowait(event)
            return True
        except asyncio.QueueFull:
            index = min(range(len(self._queue)), key=lambda i: self.rank(self._queue[i]))
            previous = self._queue[index]
            if self.rank(event) < self.rank(previous):
                self._dropped[self.priority(event)] += 1
                return False
            self._dropped[self.priority(previous)] += 1
            # Same queued job slot, so join still waits for the new notice.
            del self._queue[index]
            self._queue.append(event)
            return True

    def metrics(self):
        return {'capacity': self.maxsize, 'queued': self.qsize(),
                'dropped': sum(self._dropped.values()),
                'dropped_by_priority': dict(self._dropped),
                'scope': 'Current process desktop notification queue; no message contents'}
