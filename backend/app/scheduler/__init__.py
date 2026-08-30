"""APScheduler jobs and the recovery cycle.

The scheduled run and the manual `POST /api/admin/run-cycle` trigger call the SAME
function. A demo path that diverges from the production path is a demo that proves
nothing.
"""
