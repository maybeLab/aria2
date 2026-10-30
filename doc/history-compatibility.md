# Persistent history compatibility

This fork keeps stopped-task history compatible with the standard aria2 RPC
interfaces when SQLite persistence is enabled. It builds on motrixapp/aria2
commit 643082908a29fc8d27f559ef51e8a87952458486, including its security fixes.

- Stopped-task pagination deduplicates GIDs before slicing, prefers in-memory
  snapshots, and excludes GIDs that are active or waiting.
- Database duplicates resolve to the most recently inserted snapshot without
  deleting historical database rows or changing the schema.
- Global statistics include unique database-only stopped tasks after restart.
- Read-only status, files, and options queries accept full historical GIDs.
  Historical options expose only the persisted directory; options that were
  never stored cannot be reconstructed.
- Mutating RPC methods and authentication retain their existing restrictions.

The History compatibility workflow runs CppUnit and isolated history tests on
Linux, cross-compiles the Windows x64 binary using the upstream dependency
checksums and patches, then tests the exact binary on Windows. Only successful
Windows verification publishes the aria2-history-fix-windows-x64 artifact,
including its SHA256, source revision, license, and history test results.

Run the isolated checks with Python 3, passing the aria2 executable and a fresh
test directory to test/e2e/history-persistence.e2e.py. The test uses synthetic
loopback downloads and its own database, not user configuration or history.
No release tags or GitHub Releases are created by this workflow.

Pushes to main run only this focused workflow. The inherited platform matrix
remains available for pull requests and manual dispatch instead of building
all operating systems and architectures on every history-fix commit.
