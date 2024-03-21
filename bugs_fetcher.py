from typing import Iterable, Iterator
from nattka.bugzilla import BugCategory, BugInfo, NattkaBugzilla

try:
    from nattka.bugzilla import BugRuntimeTestingState
    MANUAL_TESTING = BugRuntimeTestingState.MANUAL
except ImportError:
    MANUAL_TESTING = 'Manual'

import messages

def read_api_key():
    try:
        with open('bugs.key', encoding='utf-8') as f:
            return f.read().strip()
    except Exception:
        return None

nattka_bugzilla = NattkaBugzilla(api_key=read_api_key())

def check_bug(bug: BugInfo, depends_bugs: dict[int, BugInfo], worker: messages.Worker) -> bool:
    if getattr(bug, 'runtime_testing_required', None) == MANUAL_TESTING:
        return False
    if (bug.category == BugCategory.KEYWORDREQ) != worker.is_rekeyword():
        return False
    for dep in bug.depends:
        if dep := depends_bugs.get(dep):
            if dep.category not in (BugCategory.KEYWORDREQ, BugCategory.STABLEREQ):
                # if not another keyword/stable request, then it has a blocker bug
                return False
            if not dep.sanity_check or "CC-ARCHES" not in dep.keywords:
                # if the blocker is not ready, then this one is also not ready
                return False
            if worker.canonical_arch() in (cc.removesuffix('@gentoo.org') for cc in dep.cc):
                # if any of the blockers has the arch, then it's not ready
                return False
    return True

def collect_bugs(bugs_no: Iterable[int], *workers: messages.Worker) -> Iterator[tuple[messages.Worker, list[int]]]:
    bugs = nattka_bugzilla.find_bugs(
        bugs=bugs_no,
        unresolved=True,
        sanity_check=[True],
        cc={f'{worker.canonical_arch()}@gentoo.org' for worker in workers},
    )

    if all_depends := frozenset().union(*(bug.depends for bug in bugs.values())):
        depends_bugs = nattka_bugzilla.find_bugs(bugs=all_depends, unresolved=True)
    else:
        depends_bugs = {}

    for worker in workers:
        if ok_bugs := [bug_no for bug_no, bug in bugs.items() if check_bug(bug, depends_bugs, worker)]:
            yield worker, ok_bugs
