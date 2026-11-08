"""Per-platform lead fetchers. Each module exposes SOURCES: a list of zero-arg functions returning lists of
lead dicts (see common.lead). sweep.py imports every module here and merges the results."""
