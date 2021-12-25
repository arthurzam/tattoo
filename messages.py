from typing import NamedTuple, Tuple, List
from datetime import datetime
import pickle
import base64


class Worker(NamedTuple):
    name: str
    arch: str

    def canonical_arch(self):
        return self.arch.removeprefix('~')


class BugJob(NamedTuple):
    bug_number: int


class BugJobDone(NamedTuple):
    bug_number: int
    success: bool


class GlobalJob(NamedTuple):
    bugs: List[int]


class CompletedJobsRequest(NamedTuple):
    since: datetime


class CompletedJobsResponse(NamedTuple):
    passes: List[Tuple[int, str]]
    failed: List[Tuple[int, str]]


class DoScan():
    pass


def dump(obj) -> bytes:
    return base64.b64encode(pickle.dumps(obj)) + b'\n'


def load(data: bytes):
    return pickle.loads(base64.b64decode(data.removesuffix(b'\n')))


socket_filename = 'arch-tester.socket'