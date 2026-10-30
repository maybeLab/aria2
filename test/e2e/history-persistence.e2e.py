import argparse
import collections
import http.server
import json
from pathlib import Path
import sqlite3
import sys
import threading


from history_fixture import Candidate, FixtureHandler, rpc_request


def check_history(candidate, expected):
    for offset, order in ((0, expected), (-1, list(reversed(expected)))):
        records = candidate.call("aria2.tellStopped", [offset, 1000])
        gids = [record["gid"] for record in records]
        if gids != order:
            raise AssertionError("Expected unique history order; rows={}, unique={}, expected={}".format(
                len(gids), len(set(gids)), len(expected)))
        for start in range(len(expected) + 1):
            page_offset = start if offset == 0 else -start - 1
            page = candidate.call("aria2.tellStopped", [page_offset, 2, ["gid", "status"]])
            assert [record["gid"] for record in page] == order[start:start + 2]
    assert candidate.call("aria2.tellStopped", [0, 0]) == []
    assert candidate.call("aria2.tellStopped", [-2147483648, 2]) == []
    statistics = candidate.call("aria2.getGlobalStat")
    assert int(statistics["numStopped"]) == len(expected), statistics
    assert int(statistics["numStoppedTotal"]) >= len(expected), statistics
    assert int(statistics["numActive"]) == 0, statistics
    assert int(statistics["numWaiting"]) == 0, statistics


def run_case(executable, directory, memory_limit):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    server.request_counts = collections.Counter()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    candidate = Candidate(executable, directory)
    expected = []
    try:
        candidate.start()
        candidate.call("aria2.changeGlobalOption", [{"max-download-result": str(memory_limit)}])
        for position in range(6):
            uri = "http://127.0.0.1:{}/complete-{}.bin".format(server.server_port, position)
            gid = candidate.call("aria2.addUri", [[uri]])
            candidate.wait_for(gid, "complete")
            expected.append(gid)
        check_history(candidate, expected)
        candidate.stop()
        candidate.start()
        check_history(candidate, expected)
        for gid in expected:
            assert candidate.call("aria2.tellStatus", [gid])["status"] == "complete"
            assert len(candidate.call("aria2.getFiles", [gid])) == 1
            assert isinstance(candidate.call("aria2.getOption", [gid]), dict)
        overlap_uri = "http://127.0.0.1:{}/paused-overlap.bin".format(server.server_port)
        candidate.call("aria2.addUri", [[overlap_uri], {"gid": expected[0], "pause": "true"}])
        assert candidate.call("aria2.tellStatus", [expected[0]])["status"] == "paused"
        visible = candidate.call("aria2.tellStopped", [-1, 1000])
        assert len(visible) == len(expected) - 1
        assert expected[0] not in [record["gid"] for record in visible]
        assert int(candidate.call("aria2.getGlobalStat")["numStopped"]) == len(expected) - 1
        candidate.call("aria2.remove", [expected[0]])
        check_history(candidate, expected)
        unauthorized = rpc_request(candidate.port, candidate.secret, "aria2.tellStatus",
                                   [expected[0]], authenticated=False)
        assert "error" in unauthorized
        for gid in ("not-a-gid", expected[0][:8], "0" * 16):
            try:
                candidate.call("aria2.tellStatus", [gid])
            except RuntimeError:
                pass
            else:
                raise AssertionError("Unexpected historical lookup success: " + gid)
        try:
            candidate.call("aria2.pause", [expected[0]])
        except RuntimeError:
            pass
        else:
            raise AssertionError("Historical record became a mutable task")
        candidate.stop(crash=True)
        candidate.start()
        check_history(candidate, expected)
        assert sum(server.request_counts.values()) == len(expected), server.request_counts
        candidate.stop()
        with sqlite3.connect(candidate.database) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        return {"memory_limit": memory_limit, "history_count": len(expected),
                "pagination": "passed", "graceful_restart": "passed",
                "forced_restart": "passed", "historical_details": "passed",
                "authentication": "passed", "downloads_replayed": False}
    finally:
        candidate.stop()
        server.shutdown()
        server.server_close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--expect-failure", action="store_true")
    arguments = parser.parse_args()
    arguments.directory.mkdir(parents=True, exist_ok=True)
    try:
        results = [run_case(arguments.executable, arguments.directory / ("memory-" + str(limit)), limit)
                   for limit in (2, 1000, 0)]
    except Exception as error:
        result = {"passed": False, "error": str(error), "expected_failure": arguments.expect_failure}
        (arguments.directory / "verification.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result))
        if not arguments.expect_failure:
            raise
        return
    if arguments.expect_failure:
        raise AssertionError("Baseline unexpectedly passed")
    result = {"passed": True, "cases": results}
    (arguments.directory / "verification.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
