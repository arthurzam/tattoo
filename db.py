from datetime import datetime
from typing import Iterator
import sqlite3

import messages


class DB():
    db_file = 'arch-tester.db'

    def __init__(self) -> None:
        results_tables = """
            CREATE TABLE IF NOT EXISTS tests (
                arch TEXT NOT NULL,
                bug_no INTEGER NOT NULL,
                state INTEGER NOT NULL,
                machine_name TEXT NOT NULL,
                time_date DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                PRIMARY KEY (arch, bug_no)
            );
        """
        self.conn = sqlite3.connect(DB.db_file)
        with self.conn:
            self.conn.execute(results_tables)

    def report_job(self, worker: messages.Worker, job: messages.BugJobDone):
        insert_query = """
            REPLACE INTO tests (arch, machine_name, bug_no, state) VALUES (?, ?, ?, ?);
        """
        with self.conn:
            self.conn.execute(insert_query, (worker.canonical_arch(), worker.name, job.bug_number, int(job.success)))
    
    def get_reportes(self, since: datetime) -> messages.CompletedJobsResponse:
        select_query = """
            SELECT arch, bug_no, state FROM tests WHERE time_date > ? ;
        """
        passes, failed = [], []
        with self.conn:
            for row in self.conn.execute(select_query, (since, )):
                (passes if row[2] else failed).append((row[1], row[0]))
        return messages.CompletedJobsResponse(passes, failed)
    
    def filter_not_tested(self, arch: str, bugs: Iterator[int]) -> Iterator[int]:
        bugs = frozenset(bugs)
        select_query = f"""
            SELECT bug_no FROM tests WHERE bug_no in {tuple(bugs)} AND arch = ?;
        """
        with self.conn:
            done = {row[0] for row in self.conn.execute(select_query, [arch])}
            return bugs - done