"""LMS 조회 결과의 짧은 메모리 캐시(프로세스 안에서만).

목록 화면은 매번 LMS에서 새로 받고 그 결과를 여기에 넣는다. 다운로드 링크를 만들 때는 방금 본 목록을 다시 쓴다.
묶음 다운로드(항목 N개)가 항목마다 모듈 목록·위키 페이지 전체를 다시 받지 않게 하려는 것이다.
값은 사용자 본인 세션으로 받은 것이고 키에 사용자 ID가 들어 있어 다른 사용자와 섞이지 않는다.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Hashable
from typing import Any

LISTING_TTL_SECONDS = 600.0


class TtlCache:
    def __init__(self, ttl: float, max_entries: int = 1024, clock=time.monotonic) -> None:
        self.ttl, self.max_entries, self.clock = ttl, max_entries, clock
        self._items: OrderedDict[Hashable, tuple[float, Any]] = OrderedDict()

    def get(self, key: Hashable) -> Any | None:
        hit = self._items.get(key)
        if hit is None:
            return None
        if self.clock() - hit[0] >= self.ttl:
            del self._items[key]
            return None
        return hit[1]

    def put(self, key: Hashable, value: Any) -> None:
        self._items[key] = (self.clock(), value)
        self._items.move_to_end(key)
        while len(self._items) > self.max_entries:
            self._items.popitem(last=False)

    def clear(self) -> None:
        self._items.clear()


listing_cache = TtlCache(LISTING_TTL_SECONDS)
