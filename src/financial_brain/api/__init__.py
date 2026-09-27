"""HTTP surface over ``services``. Optional: needs the ``api`` extra.

Deliberately thin. Every route is one call into ``services`` plus a status mapping, so there is no
behaviour here to test separately from the services it exposes - which is the point of having a
services layer at all.
"""
