from typing import Iterable, Iterator
from nattka.bugzilla import BugCategory, BugInfo, NattkaBugzilla

import messages

nattka_bugzilla = NattkaBugzilla(api_key=None)

def is_ready(bug: BugInfo) -> bool:
    if bug.resolved:
        return False
    if len(bug.depends) > 0:
        results = nattka_bugzilla.find_bugs(bugs=bug.depends, unresolved=True)
        if results.items():
            return False
    return True

def check_bug(bug: BugInfo, worker: messages.Worker) -> bool:
    return (
        (bug.category == BugCategory.KEYWORDREQ) == worker.is_rekeyword() and
        worker.canonical_arch() in (cc.removesuffix('@gentoo.org') for cc in bug.cc)
    )

def collect_bugs(bugs_no: Iterable[int], *workers: messages.Worker) -> Iterator[tuple[messages.Worker, list[int]]]:
    bugs = nattka_bugzilla.find_bugs(
        bugs=bugs_no,
        unresolved=True,
        sanity_check=[True],
        cc={f'{worker.canonical_arch()}@gentoo.org' for worker in workers},
    )
    bugs = {bug_no: bug for bug_no, bug in bugs.items() if is_ready(bug)}
    for worker in workers:
        if ok_bugs := [bug_no for bug_no, bug in bugs.items() if check_bug(bug, worker)]:
            yield worker, ok_bugs
