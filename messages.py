from typing import NamedTuple
from datetime import datetime
import pickle
import base64


class Worker(NamedTuple):
    name: str
    arch: str

    def canonical_arch(self) -> str:
        return self.arch.removeprefix('~')

    def is_rekeyword(self) -> bool:
        return self.arch.startswith('~')


class BugJob(NamedTuple):
    bug_number: int


class BugJobDone(NamedTuple):
    bug_number: int
    success: bool


class GlobalJob(NamedTuple):
    bugs: list[int]
    priority: int = 0


class CompletedJobsRequest(NamedTuple):
    since: datetime

CompletedJobsType = list[tuple[int, str]]

class CompletedJobsResponse(NamedTuple):
    passes: CompletedJobsType
    failed: CompletedJobsType


class DoScan:
    pass


class GetStatus:
    pass


class TesterStatus(NamedTuple):
    bugs_queue: tuple[int, ...]
    merging_atoms: tuple[str, ...]


class ManagerStatus(NamedTuple):
    load: tuple[float, float, float]
    cpu_count: int | None
    testers: dict[Worker, TesterStatus]


def dump(obj) -> bytes:
    return base64.b64encode(pickle.dumps(obj)) + b'\n'


def load(data: bytes):
    return pickle.loads(base64.b64decode(data.removesuffix(b'\n')))


SOCKET_FILENAME = 'tattoo.socket'
