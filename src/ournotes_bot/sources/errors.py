# L3
# Input: Validated source operations reporting an unavailable or invalid chart/resource.
# Output: Single ChartDataError identity, re-exported by the existing chart entrypoint.
# Pos: Data / Sources pure exception contract; see L2-2.md.
# Effects/Dependencies: None; no data, network, cache or composition imports.
"""Shared exception contract; preserve existing chart caller catch behaviour."""


class ChartDataError(RuntimeError):
    pass
